#!/usr/bin/env python3
"""
Daily funding digest.

Pulls "just raised" news from FREE sources (Google News RSS + TechCrunch venture
feed), filters to your focus (stage / sector / location), scores + dedupes, and
emails you a digest. No paid API, no key.

Run it on a schedule with GitHub Actions (see daily-digest.yml) or any cron.

Configure via the CONFIG block below, or via environment variables for secrets.
"""

import os
import re
import json
import smtplib
import html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime, timezone, timedelta

import feedparser

def load_dotenv(path=".env"):
    """Minimal .env loader, so a local run picks up keys without exporting them.
    Must run BEFORE `import enrich` — that module reads its API keys at import
    time, so loading the file afterwards would leave enrichment keyless."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


load_dotenv()


# Same three env readers as companies_digest.py — kept local rather than
# imported so each script still runs standalone. See that file for the details.
def env_int(name, default):
    """Integer setting; the default on anything unset or unparseable."""
    try:
        return int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


def env_flag(name, default):
    """Boolean setting. 1/true/yes/on and 0/false/no/off; unset = the default."""
    v = str(os.environ.get(name, "")).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def env_list(name, default):
    """Comma-separated list, lowercased. An explicitly empty value = empty list."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


try:
    import enrich as _enrich          # AI + Hunter enrichment (optional)
except Exception:
    _enrich = None

# --------------------------------------------------------------------------
# CONFIG — tune these to your search
# --------------------------------------------------------------------------

# Enrich raises with an AI company summary + founder email. Each enrichment is
# one web-search LLM call plus an optional Hunter lookup, so this is the only
# setting in the repo that can run up a real bill.
#
#   N     = only the top N ranked raises  (the default, so a fresh fork cannot
#           surprise anyone with a bill on day one)
#   "all" = every collected raise, up to FUNDING_MAX_ITEMS (40). Fullest digest,
#           most spend — opt in deliberately.
#   0     = no enrichment at all: free headlines only, no LLM key needed.
#
# This used to default to "all". A fork that enabled Actions without reading the
# README got up to 40 paid calls a day on their own key, which is a poor way to
# find out what something costs.
_ENRICH_RAW = (os.environ.get("FUNDING_ENRICH_TOP_N") or "").strip().lower()
ENRICH_TOP_N = None if _ENRICH_RAW == "all" else env_int("FUNDING_ENRICH_TOP_N", 10)

# How far back to look. Run daily -> keep at ~28h so nothing slips through gaps.
LOOKBACK_HOURS = env_int("FUNDING_LOOKBACK_HOURS", 28)

# Stage terms you care about (used to build search queries). Google News RSS
# aggregates hundreds of outlets (TechCrunch, VentureBeat, Axios, Finsmes,
# topstartups coverage, etc.), so broad queries here = broad free coverage.
STAGE_QUERIES = [
    "startup raises pre-seed",
    "startup raises seed round",
    "startup raises Series A",
    "startup raises Series B",
    "startup closes seed funding",
    "AI startup raises funding",
    "B2B SaaS startup funding round",
    "New York startup raises seed",
    "New York startup Series A",
    "NYC startup funding",
    "San Francisco startup raises seed",
    "San Francisco startup Series A",
    "Bay Area startup funding round",
]

# Focus keywords. Items matching these float to the top ("Best matches").
# Matched on WORD BOUNDARIES, not as substrings — plain `"ai" in blob` also fires
# on "r-AI-ses"/"em-AI-l"/"ch-AI-n", which handed every funding headline a free
# focus hit and pushed ~80% of the digest into "Best matches".
FOCUS_KEYWORDS = env_list("FUNDING_FOCUS_KEYWORDS", [
    "ai", "artificial intelligence", "agent", "agents", "agentic", "llm",
    "automation", "workflow", "workflows", "b2b", "saas", "developer",
    "infrastructure", "data", "platform",
])

# Location terms that earn a bonus (leave list empty to ignore location).
LOCATION_KEYWORDS = env_list("FUNDING_LOCATIONS",
                             ["new york", "nyc", "brooklyn", "manhattan"])

# Second-choice metro — worth surfacing, worth less than NYC. Kept separate from
# LOCATION_KEYWORDS because profile.compiled.json overwrites that list, and
# because an SF raise should not outrank a New York one at equal focus.
SECONDARY_LOCATION_KEYWORDS = env_list("FUNDING_SECONDARY_LOCATIONS", [
    "san francisco", "sf", "bay area", "palo alto", "menlo park",
    "mountain view", "redwood city", "oakland", "silicon valley",
])

# Geography. "us" = only US-headquartered companies survive; NYC-metro ones then
# float to the top via LOCATION_KEYWORDS. "any" = no geographic filter.
# A cold email is only worth sending somewhere you'd actually take the job, and
# every non-US raise we drop before enrichment is an Anthropic + Hunter call saved.
GEO_MODE = os.environ.get("GEO_MODE", "us")

# Sector buckets (enrich.CATEGORIES) worth an outreach email. Everything else —
# biotech, hardware, space, energy, materials — is dropped after enrichment, once
# we know what the company actually sells. Note healthtech stays IN: "AI agents
# for hospitals" is a software company and a real match; "AI for drug discovery"
# comes back as biotech/pharma and drops.
KEEP_CATEGORIES = {
    "ai/software", "b2b saas", "devtools/infra", "fintech",
    "proptech/construction", "healthtech",
}

# A headline must show a funding VERB *and* an amount-or-round to count. The old
# gate accepted any of ["seed", "funding", "$", ...] anywhere in title+summary,
# which let in survey reports, VC fund closes, acquisitions, and roundups.
RAISE_VERB_RE = re.compile(
    r"\b(raise[sd]?|raising|secure[sd]?|close[sd]?|land[sd]?|nabs?|nabbed|"
    r"bags?|bagged|announces?)\b", re.I)
AMOUNT_RE = re.compile(
    r"[$€£₹¥]\s?\d[\d.,]*\s?(?:bn|b|m|mn|k)?\b"
    r"|\b\d[\d.,]*\s?(?:million|billion|crore|lakh)\b", re.I)
ROUND_RE = re.compile(r"\b(pre-?seed|seed|series\s+[a-e])\b", re.I)

# ...and must not look like one of these, which all carry funding words but are
# not "a startup just raised money and is about to hire":
NOT_A_RAISE_RE = re.compile("|".join([
    r"\b(fund|vehicle)\s+(i{1,3}|iv|v|one|two|three)\b",   # "Acme Fund II"
    # ...and the other word order, which is how these are actually written:
    # "Resilience I Fund closes at $200M". The old pattern named that headline
    # in its own comment and did not match it.
    r"\b(i{1,3}|iv|v)\s+(fund|vehicle)\b",
    r"\b(fund|vehicle)\s+clos(?:es|ed|ing)\b",
    r"\bclos(?:es|ed)\s+[^.]{0,40}\bfund\b",               # a VC closing a fund
    r"\b(vc|venture capital|venture)\s+firm\b",
    r"\bacquir(?:es|ed|ing)\b|\bacquisition\b|\bexits?\s+to\b|\bmerges?\s+with\b",
    r"\bdaily funding\b|\bfunding (?:report|roundup|frenzy)\b|\bweekly roundup\b",
    r"\bstartups that raised\b|\bstartups raise\b|\bround[- ]?up\b",
    r"\bvaluation of\b|\bin talks\b|\bis raising\b|\bplans to raise\b|\beyes\b",
    r"\b\d+%\s+of\s+(?:enterprises|companies|startups)\b",
    r"\bsurvey\b|\breport (?:finds|shows|reveals)\b",
    r"\bthe\b[^:]{0,40}\bgap:",                            # "The AI compute gap: ..."
]), re.I)

# Cheap pre-enrichment geography screen. Authoritative check is the researched HQ
# (see us_only_reject), but catching the obvious ones on the headline alone keeps
# us from paying to enrich a company we'd throw away anyway.
NON_US_CURRENCY = ["€", "£", "₹", "crore", "lakh", "rmb", "yuan", "shekel", "eur ", "gbp "]
NON_US_TERMS = [
    "china", "chinese", "beijing", "shanghai", "shenzhen", "hong kong",
    "india", "indian", "bengaluru", "bangalore", "mumbai", "delhi", "gurugram",
    "uk-based", "british", "london", "cambridge-based", "oxford-based",
    "france", "french", "paris", "grenoble", "germany", "german", "berlin",
    "munich", "belgium", "belgian", "antwerp", "brussels", "netherlands",
    "dutch", "amsterdam", "spain", "spanish", "barcelona", "madrid",
    "sweden", "swedish", "stockholm", "denmark", "danish", "copenhagen",
    "finland", "helsinki", "norway", "oslo", "switzerland", "swiss", "zurich",
    "ireland", "irish", "dublin", "italy", "italian", "milan", "poland",
    "warsaw", "estonia", "tallinn", "israel", "israeli", "tel aviv",
    "singapore", "japan", "japanese", "tokyo", "korea", "korean", "seoul",
    "australia", "australian", "sydney", "melbourne", "anz", "new zealand",
    "canada", "canadian", "toronto", "vancouver", "brazil", "brazilian",
    "são paulo", "sao paulo", "mexico", "nigeria", "lagos", "kenya", "nairobi",
    "south africa", "south african", "johannesburg", "cape town", "egypt",
    "dubai", "uae", "abu dhabi", "saudi", "turkey", "istanbul", "indonesia",
    "jakarta", "vietnam", "philippines", "thailand", "europe", "european",
]
# Outlets whose coverage is essentially all non-US.
NON_US_SOURCES = [
    "eu-startups", "tech.eu", "calcalistech", "ynetnews", "entrackr",
    "indian startup times", "yourstory", "weetracker", "bw disrupt",
    "moneycontrol", "smartcompany", "startupdaily", "sifted", "tech in asia",
    "e27", "krasia", "techcabal", "disrupt africa", "arabian business",
]

# Extra static RSS feeds to include (venture/funding coverage). All verified free
# + machine-readable. (Finsmes is Cloudflare-walled and topstartups.io is
# client-rendered with no API, so neither can be pulled reliably — their coverage
# still reaches you via the Google News queries above.)
EXTRA_FEEDS = [
    # US venture coverage
    "https://techcrunch.com/category/venture/feed/",
    "https://venturebeat.com/feed",   # note: no trailing slash (the "/" 308-loops)
]

# Europe-heavy feeds — only pulled when GEO_MODE allows non-US raises, since
# under GEO_MODE="us" every item they produce gets dropped downstream anyway.
EU_FEEDS = [
    "https://www.eu-startups.com/feed/",
    "https://tech.eu/feed/",
]

# Substack newsletters to pull. Any Substack exposes RSS at <name>.substack.com/feed
# (or a custom-domain newsletter at <domain>/feed). Their posts run through the same
# funding-signal filter below, so only funding-related items survive. Add the ones
# you follow — e.g. deal/roundup newsletters that name who just raised.
SUBSTACK_FEEDS = [
    "https://nextplayso.substack.com/feed",   # "next play" — startups hiring after raising $10-50M
]
# Newsletters post on their own cadence (often weekly) and are already on-topic,
# so they get their own digest section instead of the 28h/funding-signal gate.
SUBSTACK_LOOKBACK_DAYS = env_int("SUBSTACK_LOOKBACK_DAYS", 14)
SUBSTACK_MAX = env_int("SUBSTACK_MAX", 6)

# Optional compiled profile (from setup_profile.py) — overrides the focus /
# location keywords above so the funding digest follows the same profile.json as
# the companies digest. Absent = the defaults above apply.
def _apply_profile():
    global FOCUS_KEYWORDS, LOCATION_KEYWORDS
    if os.environ.get("JOB_IGNORE_PROFILE", "").lower() in ("1", "true", "yes"):
        return                        # tests pin the subject to the repo defaults
    try:
        with open("profile.compiled.json") as f:
            p = json.load(f)
    except FileNotFoundError:
        print("[profile] NO profile.compiled.json — using the focus keywords "
              "baked into funding_digest.py, which are the repo author's. "
              "Run: python setup_profile.py")
        return
    except Exception as e:
        print(f"[profile] could not read profile.compiled.json ({e}) — using "
              f"the built-in keywords. Run: python setup_profile.py")
        return
    if p.get("focus_keywords"):
        FOCUS_KEYWORDS = [k.lower() for k in p["focus_keywords"]]
    if p.get("location_keywords") is not None:
        LOCATION_KEYWORDS = [k.lower() for k in p["location_keywords"]]
    print(f"[profile] using profile.compiled.json — {len(FOCUS_KEYWORDS)} focus keywords")


_apply_profile()

# ...and the environment wins over the profile, same rule as the companies
# digest: an unset var changes nothing, a set one overrides for this run.
FOCUS_KEYWORDS = env_list("FUNDING_FOCUS_KEYWORDS", FOCUS_KEYWORDS)
LOCATION_KEYWORDS = env_list("FUNDING_LOCATIONS", LOCATION_KEYWORDS)

# Max items to include in the email.
MAX_ITEMS = env_int("FUNDING_MAX_ITEMS", 40)

# Score at or above which a raise lands in "Best matches". Scores are computed on
# the headline AND the researched description, so a genuine AI/B2B company clears
# this comfortably while a one-keyword brush-past doesn't.
BEST_SCORE = env_int("FUNDING_BEST_SCORE", env_int("BEST_SCORE", 6))

# Browser-ish UA — some feeds (e.g. VentureBeat) reject feedparser's default UA.
FEED_UA = "Mozilla/5.0 (funding-digest; +https://github.com)"

# --------------------------------------------------------------------------
# Secrets (set as environment variables / GitHub Actions secrets)
# --------------------------------------------------------------------------
EMAIL_USER = os.environ.get("EMAIL_USER", "")   # e.g. yourgmail@gmail.com
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")   # Gmail *app password*, not your login
EMAIL_TO   = os.environ.get("EMAIL_TO", EMAIL_USER)
SMTP_HOST  = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))


def google_news_rss(query: str) -> str:
    from urllib.parse import quote_plus
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def entry_time(entry) -> datetime:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def norm_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\s*-\s*[^-]+$", "", t)  # drop trailing " - Source"
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def kw_matcher(keywords):
    """Word-boundary matcher for a keyword list. Guards the digest's oldest bug:
    substring matching let "ai" hit inside "raises", so every funding headline
    scored a focus point and the Best/Other split stopped meaning anything."""
    if not keywords:
        return re.compile(r"(?!)")            # matches nothing
    alts = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])", re.I)


_FOCUS_RE = kw_matcher(FOCUS_KEYWORDS)
_LOC_RE = kw_matcher(LOCATION_KEYWORDS)
_LOC2_RE = kw_matcher(SECONDARY_LOCATION_KEYWORDS)


def is_funding(title: str, summary: str) -> bool:
    """True only for 'a specific company just closed a round'."""
    blob = f"{title} {summary}"
    if NOT_A_RAISE_RE.search(blob):
        return False
    if not RAISE_VERB_RE.search(blob):
        return False
    return bool(AMOUNT_RE.search(blob) or ROUND_RE.search(blob))


def non_us_hint(title: str, summary: str, source: str) -> str:
    """Cheap pre-enrichment geography screen — returns a reason, or "" if the item
    looks US (or if we simply can't tell from the headline)."""
    blob = f"{title} {summary}".lower()
    for c in NON_US_CURRENCY:
        if c in blob:
            return f"non-USD amount ({c.strip()})"
    m = kw_matcher(NON_US_TERMS).search(blob)
    if m:
        return f"non-US ({m.group(0)})"
    src = (source or "").lower()
    for s in NON_US_SOURCES:
        if s in src:
            return f"non-US outlet ({source})"
    return ""


def us_only_reject(enr: dict) -> str:
    """Authoritative geography check, run on the RESEARCHED headquarters. An
    unknown country is kept — a failed lookup isn't evidence of being foreign."""
    if GEO_MODE != "us":
        return ""
    country = (enr.get("hq_country") or "").strip().lower()
    if not country:
        return ""
    if country in ("united states", "usa", "us", "u.s.", "u.s.a.", "america"):
        return ""
    return f"HQ {enr.get('hq_city') or ''} {enr.get('hq_country')}".strip()


def off_sector_reject(enr: dict) -> str:
    """Drop companies whose product isn't something you'd work on. Runs after
    enrichment because the headline alone never says what they sell."""
    cat = (enr.get("category") or "").strip().lower()
    if not cat:
        return ""                              # unknown — let scoring decide
    return "" if cat in KEEP_CATEGORIES else cat


def extract_round(title: str, summary: str):
    """(stage, amount) as display strings, e.g. ("Seed", "$13M")."""
    blob = f"{title} {summary}"
    stage = ""
    m = ROUND_RE.search(blob)
    if m:
        stage = re.sub(r"\s+", " ", m.group(1)).strip().title()
        stage = stage.replace("Preseed", "Pre-Seed").replace("Pre-Seed", "Pre-seed")
    amount = ""
    m = re.search(r"\$\s?(\d[\d.,]*)\s?(billion|bn|b|million|mn|m|k)?\b", blob, re.I)
    if m:
        num = m.group(1).rstrip(".,")
        unit = (m.group(2) or "").lower()
        suffix = {"billion": "B", "bn": "B", "b": "B",
                  "million": "M", "mn": "M", "m": "M", "k": "K"}.get(unit, "")
        amount = f"${num}{suffix}"
    return stage, amount


# What each signal in a funding headline is worth. BEST_SCORE (above) is the
# line these have to clear, so raising a weight and leaving BEST_SCORE alone
# widens "Best matches" — move both together.
FOCUS_POINTS = env_int("FUNDING_FOCUS_POINTS", 2)      # per distinct focus term
LOC_POINTS = env_int("FUNDING_LOC_POINTS", 3)          # per primary-location term
LOC2_POINTS = env_int("FUNDING_LOC2_POINTS", 2)        # per secondary-metro term
ROUND_POINTS = env_int("FUNDING_ROUND_POINTS", 1)      # names a round at all


def score(title: str, summary: str) -> int:
    blob = f"{title} {summary}"
    s = FOCUS_POINTS * len(set(m.group(0).lower() for m in _FOCUS_RE.finditer(blob)))
    s += LOC_POINTS * len(set(m.group(0).lower() for m in _LOC_RE.finditer(blob)))
    s += LOC2_POINTS * len(set(m.group(0).lower() for m in _LOC2_RE.finditer(blob)))
    if ROUND_RE.search(blob):
        s += ROUND_POINTS
    return s


def rescore(item) -> int:
    """Re-score once enrichment has told us what the company actually does. The
    headline is a thin, marketing-shaped signal; the description is the real one."""
    enr = item.get("enrich") or {}
    blob = " ".join(filter(None, [
        item["title"], item["summary"], enr.get("description", ""),
        enr.get("hq_city", ""), enr.get("category", ""),
    ]))
    return score(blob, "")


def hq_label(enr: dict) -> str:
    city, country = enr.get("hq_city", ""), enr.get("hq_country", "")
    if city:
        return city
    return country or ""


def summary_is_echo(item) -> bool:
    """Google News hands back a summary that is just the headline again plus the
    outlet name. Printing both is how you get 'X raises $13M — X raises $13M'."""
    s, t = norm_title(item.get("summary", "")), norm_title(item.get("title", ""))
    if not s:
        return True
    return s.startswith(t[:60]) or t.startswith(s[:60])


def company_key(name: str) -> str:
    """Canonical identity for a company, so the same raise covered by five
    outlets collapses to one entry (and one enrichment call)."""
    k = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    k = re.sub(r"\b(inc|llc|ltd|corp|co|the|a|an)\b", " ", k)
    k = re.sub(r"\b(ai|io|app|labs?|technologies|tech|health|bio)\b\s*$", " ", k)
    return re.sub(r"\s+", " ", k).strip()


def collect():
    feeds = [google_news_rss(q) for q in STAGE_QUERIES] + EXTRA_FEEDS
    if GEO_MODE != "us":
        feeds += EU_FEEDS
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    groups, seen_titles = {}, set()
    dropped = {"stale": 0, "not_a_raise": 0, "non_us": 0, "dupe": 0}

    for url in feeds:
        try:
            parsed = feedparser.parse(url, agent=FEED_UA)
        except Exception as e:
            print(f"[warn] failed to fetch {url}: {e}")
            continue

        for e in parsed.entries:
            title = clean(getattr(e, "title", ""))
            summary = clean(getattr(e, "summary", ""))
            link = getattr(e, "link", "")
            if not title or not link:
                continue
            if entry_time(e) < cutoff:
                dropped["stale"] += 1
                continue
            if not is_funding(title, summary):
                dropped["not_a_raise"] += 1
                continue

            key = norm_title(title)
            if key in seen_titles:
                continue
            seen_titles.add(key)

            source = ""
            if hasattr(e, "source") and getattr(e.source, "title", None):
                source = e.source.title

            if GEO_MODE == "us":
                why = non_us_hint(title, summary, source)
                if why:
                    dropped["non_us"] += 1
                    continue

            company = guess_company(title)
            ck = company_key(company)
            if not ck:
                continue

            stage, amount = extract_round(title, summary)
            item = {
                "title": title,
                "company": company,
                "summary": summary[:240],
                "link": link,
                "source": source,
                "stage": stage,
                "amount": amount,
                "score": score(title, summary),
                "time": entry_time(e),
                "also": [],          # other outlets covering the same raise
            }

            prior = groups.get(ck)
            if prior is None:
                groups[ck] = item
                continue
            dropped["dupe"] += 1
            # Keep the better-scoring headline; the loser survives as a link, and
            # we merge in whichever copy managed to name the stage/amount.
            keep, drop = (prior, item) if prior["score"] >= item["score"] else (item, prior)
            keep["also"] = prior["also"] + item["also"]
            if drop["source"]:
                keep["also"].append({"source": drop["source"], "link": drop["link"]})
            keep["stage"] = keep["stage"] or drop["stage"]
            keep["amount"] = keep["amount"] or drop["amount"]
            groups[ck] = keep

    items = sorted(groups.values(), key=lambda x: (x["score"], x["time"]), reverse=True)
    print(f"[filter] kept {len(items)} | dropped: "
          + ", ".join(f"{k}={v}" for k, v in dropped.items() if v))
    return items[:MAX_ITEMS]


def collect_substack():
    """Latest posts from SUBSTACK_FEEDS — shown as their own section, NOT run
    through the 28h/funding-signal gate (the whole newsletter is already on-topic
    and posts on a slower, irregular cadence)."""
    if not SUBSTACK_FEEDS:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=SUBSTACK_LOOKBACK_DAYS)
    out, seen = [], set()
    for url in SUBSTACK_FEEDS:
        try:
            parsed = feedparser.parse(url, agent=FEED_UA)
        except Exception as e:
            print(f"[warn] substack {url}: {e}")
            continue
        src = clean(getattr(parsed.feed, "title", "")) or "Substack"
        for e in parsed.entries:
            title = clean(getattr(e, "title", ""))
            link = getattr(e, "link", "")
            if not title or not link or link in seen:
                continue
            if entry_time(e) < cutoff:
                continue
            seen.add(link)
            out.append({"title": title, "link": link, "source": src,
                        "time": entry_time(e)})
    out.sort(key=lambda x: x["time"], reverse=True)
    return out[:SUBSTACK_MAX]


def newsletters_block(letters):
    if not letters:
        return ""
    rows = []
    for l in letters:
        dt = l["time"].strftime("%b %d")
        rows.append(
            f"<li style='margin:0 0 9px'>"
            f"<a href='{html.escape(l['link'])}' style='font-weight:600;color:#1a5fb4;text-decoration:none'>"
            f"{html.escape(l['title'])}</a>"
            f"<span style='color:#999;font-size:12px'> · {html.escape(l['source'])} · {dt}</span>"
            f"</li>")
    return ("<h3 style='font-family:sans-serif'>From your newsletters</h3>"
            "<ul style='list-style:none;padding:0;margin:0'>" + "".join(rows) + "</ul>")


def discovery_banner():
    """A one-line status of the 8am discovery step, read from the breadcrumb
    discover.py leaves. Green when it added companies, grey when it ran but found
    nothing new, red when it failed — so you can tell at a glance that the loop
    that feeds the companies digest is alive. Empty string if discovery didn't run."""
    try:
        with open("discover_status.json") as f:
            st = json.load(f)
    except Exception:
        return ""
    total = st.get("watchlist_total", "?")
    if not st.get("ok"):
        color, msg = "#b00", f"⚠ Discovery FAILED: {html.escape(str(st.get('error') or 'unknown'))}"
    elif st.get("added"):
        names = ", ".join(st.get("added_names", [])[:8])
        color = "#2a7"
        msg = (f"✓ Discovery: probed {st.get('probed', 0)}, "
               f"added {st.get('added')} — {html.escape(names)}")
    else:
        color = "#999"
        msg = f"Discovery ran: probed {st.get('probed', 0)}, no new companies today"
    return (f"<div style='font-family:sans-serif;font-size:12px;color:{color};"
            f"border-left:3px solid {color};padding:2px 0 2px 8px;margin:0 0 12px'>"
            f"{msg} · watchlist now {total} companies</div>")


def build_html(items, letters=None):
    today = datetime.now().strftime("%A, %b %d")
    letters = letters or []
    if not items:
        head = f"<h2 style='font-family:sans-serif'>Funding digest — {today}</h2>"
        if letters:
            body = ("<p style='font-family:sans-serif;color:#666;font-size:13px'>"
                    "No new raises matched today, but here's the latest from your "
                    "newsletters.</p>") + newsletters_block(letters)
            return f"<div style='max-width:640px'>{head}{body}</div>"
        return head + ("<p>No new raises matched today. "
                       "Widen your keywords or check back tomorrow.</p>")

    best = [i for i in items if i["score"] >= BEST_SCORE]
    other = [i for i in items if i["score"] < BEST_SCORE]

    def block(rows):
        out = []
        for i in rows:
            enr = i.get("enrich") or {}
            bits = []

            # Badge line: the three things that decide whether this is worth an
            # email — how big the round was, what stage, and where they sit.
            badges = [b for b in (i.get("stage"), i.get("amount"), hq_label(enr)) if b]
            badge_html = ""
            if badges:
                badge_html = "".join(
                    f"<span style='display:inline-block;background:#f0f3f7;color:#445;"
                    f"font-size:11px;font-weight:600;border-radius:3px;padding:2px 6px;"
                    f"margin:0 4px 0 0'>{html.escape(b)}</span>" for b in badges)

            # What they do. Prefer the researched description; the RSS summary is
            # usually just the headline again ("X raises $13M  X raises $13M").
            desc = enr.get("description") or ""
            if not desc and not summary_is_echo(i):
                desc = i["summary"]
            if desc:
                bits.append(f"<div style='color:#333;font-size:13px;margin-top:5px;"
                            f"line-height:1.45'>{html.escape(desc)}</div>")

            line = []
            if enr.get("investors"):
                line.append("Backed by " + html.escape(", ".join(enr["investors"][:3])))
            if enr.get("founders"):
                line.append(html.escape(", ".join(enr["founders"][:2])))
            if line:
                bits.append(f"<div style='color:#666;font-size:12px;margin-top:4px'>"
                            + " · ".join(line) + "</div>")

            if enr.get("email"):
                verified = "verified" in enr.get("email_status", "")
                color = "#1a7f4b" if verified else "#a35"
                tag = "verified" if verified else html.escape(enr.get("email_status", ""))
                bits.append(f"<div style='font-size:13px;margin-top:6px'>"
                            f"✉ <a href='mailto:{html.escape(enr['email'])}' "
                            f"style='color:{color};font-weight:600;text-decoration:none'>"
                            f"{html.escape(enr['email'])}</a> "
                            f"<span style='color:#aaa;font-size:11px'>{tag}</span></div>")
                alts = enr.get("email_alts") or []
                if alts:
                    alt_links = " · ".join(
                        f"<a href='mailto:{html.escape(a)}' style='color:#a35;text-decoration:none'>"
                        f"{html.escape(a)}</a>" for a in alts[:3])
                    bits.append(f"<div style='font-size:11px;color:#aaa;margin:2px 0 0 14px'>"
                                f"if that bounces: {alt_links}</div>")

            # Footer: every link for this company on one line, article included, so
            # the headline itself doesn't have to be the clickable thing.
            from urllib.parse import quote_plus
            comp = i.get("company") or i["title"]
            links = []
            if enr.get("website"):
                links.append(f"<a href='{html.escape(enr['website'])}' style='color:#1a5fb4;"
                             f"text-decoration:none'>{html.escape(enr.get('domain') or 'site')}</a>")
            links.append("<a href='https://www.google.com/search?q="
                         + quote_plus(f"{comp} founder linkedin")
                         + "' style='color:#999;text-decoration:none'>LinkedIn</a>")
            links.append("<a href='https://www.google.com/search?q="
                         + quote_plus(f"{comp} crunchbase")
                         + "' style='color:#999;text-decoration:none'>Crunchbase</a>")
            src_name = i["source"] or "article"
            links.append(f"<a href='{html.escape(i['link'])}' style='color:#999;"
                         f"text-decoration:none'>{html.escape(src_name)} ↗</a>")
            for a in (i.get("also") or [])[:2]:
                links.append(f"<a href='{html.escape(a['link'])}' style='color:#ccc;"
                             f"text-decoration:none'>{html.escape(a['source'])} ↗</a>")
            bits.append(f"<div style='font-size:11px;margin-top:6px'>"
                        + " · ".join(links) + "</div>")

            out.append(
                f"<li style='margin:0 0 22px;padding:0 0 0 10px;border-left:2px solid #e6e9ee'>"
                f"<div style='font-family:sans-serif;font-size:15px;font-weight:700;"
                f"color:#111;margin-bottom:5px'>{html.escape(comp)}</div>"
                f"{badge_html}"
                f"{''.join(bits)}"
                f"</li>"
            )
        return ("<ul style='list-style:none;padding:0;margin:0;font-family:sans-serif'>"
                + "".join(out) + "</ul>")

    geo = "US" if GEO_MODE == "us" else "global"
    parts = [f"<h2 style='font-family:sans-serif;margin-bottom:2px'>Funding digest — {today}</h2>",
             f"<p style='font-family:sans-serif;color:#777;font-size:13px;margin-top:0'>"
             f"{len(best)} worth an email"
             + (f" · {len(other)} more below" if other else "")
             + f" · {geo} raises, last {LOOKBACK_HOURS}h</p>"]
    if best:
        parts.append("<h3 style='font-family:sans-serif;font-size:13px;color:#888;"
                     "text-transform:uppercase;letter-spacing:.5px;margin:18px 0 10px'>"
                     "Best matches</h3>" + block(best))
    if other:
        parts.append("<h3 style='font-family:sans-serif;font-size:13px;color:#888;"
                     "text-transform:uppercase;letter-spacing:.5px;margin:22px 0 10px'>"
                     "Other recent raises</h3>" + block(other))
    if letters:
        parts.append(newsletters_block(letters))
    return "<div style='max-width:640px'>" + "".join(parts) + "</div>"


def from_header():
    """'Job Bot (@Name) <sending@addr>' so the digest doesn't show up as yourself.
    Handle comes from DIGEST_HANDLE, else the first name in the sending address."""
    handle = os.environ.get("DIGEST_HANDLE", "").strip()
    if not handle:
        m = re.match(r"[A-Za-z]+", (EMAIL_USER or "").split("@")[0])
        handle = m.group(0).capitalize() if m else "you"
    return formataddr((f"Job Bot (@{handle})", EMAIL_USER))


def send_email(html_body):
    if not (EMAIL_USER and EMAIL_PASS and EMAIL_TO):
        print("[info] email creds not set — printing digest instead:\n")
        print(re.sub(r"<[^>]+>", "", html_body))
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Funding digest — {datetime.now().strftime('%b %d')}"
    msg["From"] = from_header()
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(EMAIL_USER, EMAIL_PASS)
        s.sendmail(EMAIL_USER, [a.strip() for a in EMAIL_TO.split(",")], msg.as_string())
    print(f"[ok] sent digest to {EMAIL_TO}")


def guess_company(title):
    """Best-effort company name from a funding headline."""
    t = re.sub(r"\s*-\s*[^-]+$", "", title)         # drop trailing " - Source"
    # take words before a funding verb
    m = re.split(r"\b(raises|raised|secures|secured|closes|closed|lands|nabs|"
                 r"announces|bags)\b", t, flags=re.I)
    head = m[0].strip() if m else t
    head = re.sub(r"^(exclusive|breaking|report)[:\-]\s*", "", head, flags=re.I)
    return head.strip(" ,:").strip()[:60]


if __name__ == "__main__":
    items = collect()
    print(f"[info] collected {len(items)} items")

    if ENRICH_TOP_N != 0 and _enrich is not None:
        try:
            import watchlist as _wl
        except Exception:
            _wl = None
        targets = items if ENRICH_TOP_N is None else items[:ENRICH_TOP_N]
        print(f"[info] enriching {len(targets)} of {len(items)} raises"
              + ("" if ENRICH_TOP_N is None else
                 f" (cap FUNDING_ENRICH_TOP_N={ENRICH_TOP_N}; "
                 f"set it to 'all' for every raise, 0 to disable)"))
        for i in targets:
            company = i["company"]
            try:
                i["enrich"] = _enrich.enrich(company)
                print(f"[enrich] {company}: {i['enrich'].get('email_status') or 'no email'}")
            except Exception as e:
                print(f"[warn] enrich {company}: {e}")

        # Now that we know where each company sits and what it sells, decide what
        # this company is good for. Two DIFFERENT questions, and conflating them
        # was a bug:
        #
        #   "is this worth a cold email?"  -> about the COMPANY. A UK-headquartered
        #       startup is not somewhere you'd cold-email a founder for a job, so
        #       us_only_reject applies here.
        #   "is this worth watching for jobs?" -> about the ROLE, which does not
        #       exist yet. A London-HQ company with a New York office posts New
        #       York roles, and the companies digest gates every posting on its
        #       OWN location anyway. Dropping the company here meant its board was
        #       never probed, so those NYC roles could never be seen at all.
        #
        # So the geo gate now filters the EMAIL only; the watchlist gets everyone
        # still selling something in-sector, wherever they are headquartered.
        emailable, watchable = [], []
        for i in items:
            enr = i.get("enrich") or {}
            why = off_sector_reject(enr)
            if why:
                print(f"[drop] {i['company']}: off-sector — {why}")
                continue
            i["score"] = rescore(i)
            watchable.append(i)
            why = us_only_reject(enr)
            if why:
                print(f"[drop-email] {i['company']}: HQ outside US — {why} "
                      f"(still watching its board for US roles)")
                continue
            emailable.append(i)
        watchable.sort(key=lambda x: (x["score"], x["time"]), reverse=True)
        items = sorted(emailable, key=lambda x: (x["score"], x["time"]), reverse=True)
        print(f"[info] {len(items)} raises in the email, "
              f"{len(watchable)} companies going to the job watchlist")

        # LOOP: detect each surviving company's ATS and feed the companies digest's
        # watchlist. The round itself travels with the company, so the companies
        # digest can flag "🚀 raised $12M Series A" next to the reqs it pays for.
        if _wl is not None:
            for i in watchable:                     # NOT `items` — see above
                try:
                    _wl.detect_and_add(
                        i["company"],
                        (i.get("enrich") or {}).get("domain"),
                        funding={"round": i.get("stage") or "", "amount": i.get("amount") or "",
                                 "date": i["time"].strftime("%Y-%m-%d"), "url": i.get("link", "")},
                    )
                except Exception as e:
                    print(f"[warn] watchlist {i['company']}: {e}")

            # ...then give the companies that had NO public board when we first saw
            # them another look. A seed-stage startup usually opens its Greenhouse
            # weeks after the announcement, and without this they stay invisible
            # forever — probed once on announcement day, then cached as "checked".
            try:
                _wl.reprobe_pending()
            except Exception as e:
                print(f"[warn] watchlist re-probe: {e}")
    else:
        print("[info] enrichment disabled or enrich.py missing")

    letters = collect_substack()
    print(f"[info] {len(letters)} newsletter posts")
    send_email(discovery_banner() + build_html(items, letters))
