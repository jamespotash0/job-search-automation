#!/usr/bin/env python3
"""
Job digest — newest postings for your target roles, ranked by how well they
match your resume (primary) and company size (secondary tiebreak).

FREE sources, no paid API:
  - Remotive API (remote tech jobs, includes descriptions)   [default on]
  - Arbeitnow API (open job board)                            [default on]
  - Hacker News "Who is hiring" thread via Algolia            [optional]
  - Greenhouse / Lever / Ashby public boards for a WATCHLIST  [you add tokens]

Runs 3x/day (9am, 1pm, 4pm ET) via GitHub Actions. A seen-cache prevents the
1pm/4pm runs from repeating what the 9am run already sent.

Honest limits: free sources rarely expose company headcount, so "size" ranking
is best-effort via the COMPANY_SIZE lookup you fill in. Qualification match is
the reliable ranker.
"""

import os
import re
import json
import html
import smtplib
import hashlib
import urllib.request
from urllib.parse import quote_plus
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

# ==========================================================================
# YOUR RESUME PROFILE  — drives the qualification score
# ==========================================================================
RESUME = {
    # Titles you're targeting (matched against posting titles).
    "target_titles": [
        "product manager", "associate product manager", "apm", "product owner",
        "ai product manager", "technical product manager", "founding product manager",
        "founding pm", "technical program manager",
        "forward deployed engineer", "forward deployed", "deployment strategist",
        "ai strategist", "solutions engineer",
        "technical solutions", "implementation", "product operations",
        "product strategy",
    ],
    # Skills / tools from your resume (keyword overlap with the JD).
    "skills": [
        "roadmap", "roadmapping", "prd", "prds", "ai prototyping", "prototyping",
        "ux", "ui", "ui/ux", "product discovery", "discovery", "competitive analysis",
        "gtm", "go-to-market", "stakeholder", "cross-functional", "agile", "scrum",
        "sprint", "standup", "user research", "prioritization", "acceptance criteria",
        "self-serve", "vendor management", "customer success", "onboarding",
        "jira", "sql", "figma", "confluence", "notion", "hubspot", "posthog",
        "airtable", "git", "typescript", "python", "java",
        "0 to 1", "0-to-1", "zero to one", "multi-tenant", "b2b", "saas",
        "api", "quote-to-cash", "quoting",
    ],
    # Domains you have real exposure to (bonus points).
    "domains": [
        "ai", "artificial intelligence", "workflow", "automation", "agent",
        "llm", "proptech", "real estate", "construction", "vendor",
        "accessibility", "captioning", "telecom", "enterprise",
        "developer", "infrastructure", "fintech", "b2b", "saas",
    ],
    # ~ years of experience. Postings asking for much more get penalized.
    "years": 3,
}

# Titles that mean "too senior" — hard-exclude if present.
SENIORITY_EXCLUDE = [
    "senior", "sr.", "sr ", "staff", "principal", "lead ", "director",
    "head of", "vp", "vice president", " ii", " iii", "manager of managers",
    "group product", "chief",
]

# Software-engineering (coding) roles to hard-exclude — you want product /
# deployment / solutions, NOT SWE. Note "Forward Deployed Engineer" and
# "Solutions Engineer" are KEPT (not coding-IC roles); only titles like
# "...Software Engineer", backend/frontend/full-stack, data/ML eng, etc. drop.
SWE_EXCLUDE = [
    "software engineer", "software developer", "swe ", "backend", "back-end",
    "front end", "front-end", "frontend", "full stack", "full-stack", "fullstack",
    "web developer", "mobile engineer", "ios engineer", "android engineer",
    "data engineer", "machine learning engineer", "ml engineer", "devops",
    "site reliability", " sre", "security engineer", "firmware", "game developer",
    "developer advocate",
]

# Words in the JD body that signal a junior/early-career fit (boost).
JUNIOR_SIGNALS = [
    "2+ years", "1+ year", "0-2", "1-3 years", "2-3 years", "associate",
    "early career", "early-career", "entry level", "entry-level", "new grad",
    "junior", "recent graduate", "1-2 years",
]

# Words that signal it wants a lot more experience than you have (penalty).
SENIOR_SIGNALS = ["5+ years", "6+ years", "7+ years", "8+ years", "10+ years",
                  "minimum of 5", "minimum of 6", "minimum of 7"]

# ==========================================================================
# SOURCES
# ==========================================================================
USE_REMOTIVE = True
USE_ARBEITNOW = True
USE_HN_WHOISHIRING = True

USE_WORKDAY = True

REMOTIVE_SEARCHES = ["product manager", "associate product manager",
                     "technical product manager", "ai product manager",
                     "forward deployed", "deployment strategist", "ai strategist",
                     "solutions engineer", "product operations", "product owner"]

# ATS watchlist — add ATS tokens for companies you're targeting (e.g. from the
# funding digest). Find the token in a company's careers URL:
#   boards.greenhouse.io/<TOKEN>   jobs.lever.co/<TOKEN>   jobs.ashbyhq.com/<TOKEN>
# Seeded with SMALL / early-stage (seed–Series B) NYC startups whose live boards
# carry PM / FDE / deployment / AI-strategist / solutions roles in your lane —
# each verified against the ATS's public API, location-checked to NYC, and kept
# small (≤~65 open roles, i.e. not the 400-role giants). The loop auto-adds more
# as companies raise; trim/extend freely.
GREENHOUSE_COMPANIES = [
    "alloy",         # fintech/identity · founding Forward Deployed Engineer
    "vestwell",      # fintech/retirement · Implementations Manager
    "lithic",        # fintech/cards (Privacy.com) · NYC
    "northspyre",    # PROPTECH · NYC (your Stringbean domain)
    "vts",           # PROPTECH · NYC (your domain)
    "honeycomb",     # PROPTECH/insurance · NYC (your domain)
]
LEVER_COMPANIES = []        # e.g. ["somestartup"]
ASHBY_COMPANIES = [
    # NYC-HQ, fitting roles now
    "edra",          # AI · AI Strategist (New York)
    "probook",       # AI · Deployment Strategist, Implementation Manager (Manhattan)
    "credal",        # AI/enterprise · Founding GTM Deployment Strategist (Brooklyn)
    "normalcomputing",  # AI · Forward Deployed Engineer (NYC)
    "valon",         # PROPTECH/mortgage · Deployment Strategist, Implementation (3 NYC roles)
    "rogo",          # fintech/AI · Product Manager | Agents (NYC)
    "kalshi",        # fintech/markets · Product Manager, Growth/Payments (NYC)
    "rho",           # fintech · Product Manager (NYC)
    "highbeam",      # fintech/SMB · NYC
    # SF/London-HQ but they DO staff NYC — the per-role location gate keeps only
    # their NYC postings, so seeding them just means "watch for NYC roles here".
    "decagon",       # AI agents · 24 NYC roles live
    "merge",         # unified API · 11 NYC roles live
    "distyl",        # AI/enterprise deployment (AI Strategist, AI Operator)
    "context",       # AI · Forward Deployed Engineer, Deployment Strategist
    "meticulous",    # AI/dev tools · Forward Deployed Engineer
    "parafin",       # fintech/embedded · Forward Deployed Engineer
    "method",        # fintech/payments · Solutions Engineer
    "stytch",        # dev tools/auth · Solutions Engineer
]

# Workday boards. Unlike the token-based ATSs above, a Workday board needs THREE
# parts you read from the careers URL:
#   https://<tenant>.<wd>.myworkdayjobs.com/<site>
# e.g. https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
#   ->  {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"}
# You can also use the shorthand string "tenant/wd/site". Big/late companies
# dominate Workday, so most matches will be filtered by the seniority gate — it's
# most useful for a handful of specific mid/large targets you want to track.
WORKDAY_COMPANIES = [
    # {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"},
    # "someco/wd1/Careers",
]
WORKDAY_SEARCH = "product"   # server-side search to cut noise before scoring
WORKDAY_MAX_DETAIL = 25      # cap job-detail fetches per board (time/bandwidth)

# Company size lookup (lowercased name -> approx employees). Fill in the ones
# you care about; unknown companies rank neutral.
COMPANY_SIZE = {
    # "ramp": 1000, "somestartup": 25,
}
PREFER_LARGER = False   # False = small/early companies rank higher (your fit).

# --- Location: keep only NYC-based roles / companies ---------------------
REQUIRE_NYC = True
REMOTE_OK = False   # True = also keep remote roles (may include NYC startups)
NYC_KEYWORDS = [
    "new york", "nyc", "new york city", "brooklyn", "manhattan",
    "ny,", " ny ", ", ny", "queens", "n.y.",
]

# --- Stage bias: nudge seed / Series A / B to the top -------------------
EARLY_STAGE_SIGNALS = [
    "seed", "series a", "series b", "pre-seed", "pre seed", "early stage",
    "early-stage", "founding team", "stealth", "backed by", "raised our",
]
LATE_STAGE_SIGNALS = [
    "publicly traded", "public company", "fortune 500", "nasdaq:", "nyse:",
    "10,000 employees", "20,000 employees", "enterprise-scale",
]

MAX_ITEMS = 30
SEEN_FILE = "seen_jobs.json"
SEEN_TTL_DAYS = 14

# Recency: drop postings older than this many days, using the board's own
# posted/updated timestamp. Newer postings also rank higher (tiebreak after the
# resume match). Sources that don't expose a date are kept and shown without an
# age. Set to 0 to disable age filtering entirely. Overridable via profile.json.
MAX_AGE_DAYS = 21

# ==========================================================================
# Optional compiled profile (from setup_profile.py). If profile.compiled.json
# exists it overrides RESUME + location/size/recency settings above — so anyone
# can configure everything from profile.json + their resume without editing code.
# Absent = the defaults above apply (this repo's default is tuned to James).
# ==========================================================================
def _apply_profile():
    global NYC_KEYWORDS, REMOTE_OK, REQUIRE_NYC, PREFER_LARGER, MAX_AGE_DAYS
    try:
        with open("profile.compiled.json") as f:
            p = json.load(f)
    except Exception:
        return
    for k in ("target_titles", "skills", "domains", "years"):
        if p.get(k):
            RESUME[k] = p[k]
    if p.get("location_keywords") is not None:
        NYC_KEYWORDS = [k.lower() for k in p["location_keywords"]]
        REQUIRE_NYC = bool(NYC_KEYWORDS)
    if "remote_ok" in p:
        REMOTE_OK = bool(p["remote_ok"])
    if "prefer_larger" in p:
        PREFER_LARGER = bool(p["prefer_larger"])
    if p.get("max_age_days") is not None:
        MAX_AGE_DAYS = int(p["max_age_days"])
    print(f"[profile] using profile.compiled.json — {len(RESUME['target_titles'])} titles, "
          f"{len(NYC_KEYWORDS)} location keywords, remote_ok={REMOTE_OK}, "
          f"max_age_days={MAX_AGE_DAYS}")


_apply_profile()

# Secrets
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")
EMAIL_TO   = os.environ.get("EMAIL_TO", EMAIL_USER)
SMTP_HOST  = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))

UA = {"User-Agent": "Mozilla/5.0 (job-digest)"}


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def post_json(url, payload, timeout=25):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**UA, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def strip_html(t):
    return html.unescape(re.sub(r"<[^>]+>", " ", t or "")).strip()


def to_ts(value):
    """Best-effort parse of a date/timestamp from any source -> unix seconds, or
    None if unknown. Handles ISO-8601 strings and epoch seconds/milliseconds."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        v = float(value)
        return v / 1000.0 if v > 1e12 else v          # milliseconds -> seconds
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)       # naive -> assume UTC
        return dt.timestamp()
    except Exception:
        return None


def age_label(ts):
    if not ts:
        return ""
    days = (datetime.now(timezone.utc).timestamp() - ts) / 86400.0
    if days < 1:
        return "today"
    if days < 2:
        return "1d ago"
    return f"{int(days)}d ago"


# --------------------------- fetchers (each returns list of normalized dicts)
def fetch_remotive():
    out = []
    for q in REMOTIVE_SEARCHES:
        try:
            data = get_json(f"https://remotive.com/api/remote-jobs?search={quote_plus(q)}")
            for j in data.get("jobs", [])[:40]:
                out.append({
                    "title": j.get("title", ""),
                    "company": j.get("company_name", ""),
                    "location": j.get("candidate_required_location", "Remote"),
                    "url": j.get("url", ""),
                    "content": strip_html(j.get("description", ""))[:1200],
                    "posted_ts": to_ts(j.get("publication_date")),
                    "source": "Remotive",
                })
        except Exception as e:
            print(f"[warn] remotive '{q}': {e}")
    return out


def fetch_arbeitnow():
    out = []
    try:
        data = get_json("https://www.arbeitnow.com/api/job-board-api")
        for j in data.get("data", [])[:100]:
            out.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location", ""),
                "url": j.get("url", ""),
                "content": strip_html(j.get("description", ""))[:1200],
                "posted_ts": to_ts(j.get("created_at")),
                "source": "Arbeitnow",
            })
    except Exception as e:
        print(f"[warn] arbeitnow: {e}")
    return out


def fetch_hn():
    out = []
    try:
        s = get_json("https://hn.algolia.com/api/v1/search_by_date"
                     "?query=Ask%20HN%20Who%20is%20hiring&tags=story&hitsPerPage=1")
        if not s.get("hits"):
            return out
        story_id = s["hits"][0]["objectID"]
        c = get_json(f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}"
                     "&hitsPerPage=200")
        for h in c.get("hits", []):
            body = strip_html(h.get("comment_text", ""))
            if not body:
                continue
            first = body.split("\n")[0][:120]
            out.append({
                "title": first,
                "company": first.split("|")[0].strip()[:60] if "|" in first else "",
                "location": "",
                "url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "content": body[:1200],
                "posted_ts": to_ts(h.get("created_at_i")),
                "source": "HN Who's Hiring",
            })
    except Exception as e:
        print(f"[warn] hn: {e}")
    return out


def fetch_greenhouse(token):
    out = []
    try:
        data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
        for j in data.get("jobs", []):
            out.append({
                "title": j.get("title", ""),
                "company": token,
                "location": (j.get("location") or {}).get("name", ""),
                "url": j.get("absolute_url", ""),
                "content": strip_html(j.get("content", ""))[:1500],
                "posted_ts": to_ts(j.get("updated_at")),
                "source": "Greenhouse",
            })
    except Exception as e:
        print(f"[warn] greenhouse {token}: {e}")
    return out


def fetch_lever(token):
    out = []
    try:
        data = get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
        for j in data:
            out.append({
                "title": j.get("text", ""),
                "company": token,
                "location": (j.get("categories") or {}).get("location", ""),
                "url": j.get("hostedUrl", ""),
                "content": strip_html(j.get("descriptionPlain", j.get("description", "")))[:1500],
                "posted_ts": to_ts(j.get("createdAt")),
                "source": "Lever",
            })
    except Exception as e:
        print(f"[warn] lever {token}: {e}")
    return out


def fetch_ashby(token):
    out = []
    try:
        data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
        for j in data.get("jobs", []):
            out.append({
                "title": j.get("title", ""),
                "company": token,
                "location": j.get("location", ""),
                "url": j.get("jobUrl", ""),
                "content": strip_html(j.get("descriptionPlain", ""))[:1500],
                "posted_ts": to_ts(j.get("publishedAt")),
                "source": "Ashby",
            })
    except Exception as e:
        print(f"[warn] ashby {token}: {e}")
    return out


def _wd_cfg(entry):
    """Accept either a dict or the shorthand string 'tenant/wd/site'."""
    if isinstance(entry, str):
        parts = [p for p in entry.strip().strip("/").split("/") if p]
        if len(parts) == 3:
            return {"tenant": parts[0], "wd": parts[1], "site": parts[2]}
        return {}
    return entry or {}


def fetch_workday(entry):
    """Pull a Workday board via its public cxs JSON API.

    The list endpoint gives title + location but no description, so we fetch the
    per-job detail ONLY for postings whose title already passes the target-title
    filter — bounding detail calls (Workday boards are large and senior-heavy)."""
    out = []
    cfg = _wd_cfg(entry)
    tenant, wd, site = cfg.get("tenant"), cfg.get("wd"), cfg.get("site")
    if not (tenant and wd and site):
        print(f"[warn] workday: incomplete config {entry!r}")
        return out
    base = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    search = cfg.get("search", WORKDAY_SEARCH)
    try:
        listings, offset = [], 0
        for _ in range(10):                       # up to 10 pages x 20 = 200
            data = post_json(base + "/jobs",
                             {"appliedFacets": {}, "limit": 20, "offset": offset,
                              "searchText": search})
            batch = data.get("jobPostings", [])
            listings += batch
            offset += 20
            if not batch or offset >= data.get("total", 0):
                break
        fetched = 0
        for jp in listings:
            title = jp.get("title", "")
            if not title or not title_is_target(title):
                continue                          # skip detail call for non-matches
            if fetched >= WORKDAY_MAX_DETAIL:
                break
            ext = jp.get("externalPath", "")
            try:
                info = (get_json(base + ext) or {}).get("jobPostingInfo", {}) or {}
                fetched += 1
            except Exception as e:
                print(f"[warn] workday detail {tenant}{ext}: {e}")
                continue
            url = info.get("externalUrl") or \
                f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{ext}"
            out.append({
                "title": info.get("title", title),
                "company": tenant,
                "location": info.get("location", jp.get("locationsText", "")),
                "url": url,
                "content": strip_html(info.get("jobDescription", ""))[:1500],
                "posted_ts": to_ts(info.get("startDate")),
                "source": "Workday",
            })
    except Exception as e:
        print(f"[warn] workday {tenant}: {e}")
    return out


# --------------------------- filtering & scoring
def title_is_target(title):
    t = title.lower()
    if any(x in t for x in SENIORITY_EXCLUDE):
        return False
    if any(x in t for x in SWE_EXCLUDE):      # no software-engineering roles
        return False
    return any(x in t for x in RESUME["target_titles"])


def qualification_score(title, content):
    t, c = title.lower(), content.lower()
    blob = t + " " + c
    s = 0
    # role/title match (strong)
    s += sum(12 for x in RESUME["target_titles"] if x in t)
    s = min(s, 40)
    # seniority fit
    if any(x in c for x in JUNIOR_SIGNALS):
        s += 20
    if any(x in c for x in SENIOR_SIGNALS):
        s -= 25
    # skill overlap (capped)
    s += min(sum(3 for k in RESUME["skills"] if k in blob), 24)
    # domain fit (capped)
    s += min(sum(2 for d in RESUME["domains"] if d in blob), 12)
    # stage bias: reward seed/A/B, penalize obviously-late/huge
    if any(x in c for x in EARLY_STAGE_SIGNALS):
        s += 6
    if any(x in c for x in LATE_STAGE_SIGNALS):
        s -= 6
    return s


def is_nyc(location, content):
    loc = (location or "").strip().lower()
    if loc:
        # Trust an explicit location: a posting that says "San Francisco" isn't
        # NYC just because its JD name-drops a New York office.
        if any(k in loc for k in NYC_KEYWORDS):
            return True
        if REMOTE_OK and "remote" in loc:
            return True
        return False
    # No location field (e.g. HN "Who's hiring") — fall back to the body text.
    return any(k in (content or "").lower() for k in NYC_KEYWORDS)


def size_score(company):
    n = COMPANY_SIZE.get((company or "").lower())
    if n is None:
        return 0
    import math
    val = math.log10(max(n, 1))          # ~0..4+
    return val if PREFER_LARGER else -val


def job_id(j):
    return hashlib.sha1((j["company"] + "|" + j["title"] + "|" + j["url"]).encode()).hexdigest()[:16]


# --------------------------- seen cache (dedupe across the day's 3 runs)
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    cutoff = datetime.now().timestamp() - SEEN_TTL_DAYS * 86400
    return {k: v for k, v in data.items() if v > cutoff}


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(seen, f)
    except Exception as e:
        print(f"[warn] could not write seen file: {e}")


# --------------------------- render + send
def build_html(items):
    now = datetime.now().strftime("%A, %b %d — %-I:%M%p")
    if not items:
        return f"<h2>Job digest — {now}</h2><p>No new matching postings this run.</p>"
    rows = []
    for i in items:
        loc = f" · {html.escape(i['location'])}" if i["location"] else ""
        comp = html.escape(i["company"]) if i["company"] else "—"
        sz = COMPANY_SIZE.get((i['company'] or '').lower())
        szlabel = f" · ~{sz} ppl" if sz else ""
        age = age_label(i.get("posted_ts"))
        # "today"/"1d ago" gets a green nudge; older postings stay muted grey.
        agelabel = (f" · <span style='color:{'#2a7' if 'today' in age or '1d' in age else '#999'}'>"
                    f"{age}</span>") if age else ""
        rows.append(
            f"<li style='margin:0 0 15px'>"
            f"<span style='display:inline-block;min-width:34px;font-weight:700;color:#2a7'>"
            f"{i['q']}</span>"
            f"<a href='{html.escape(i['url'])}' style='font-weight:600;color:#1a5fb4;text-decoration:none'>"
            f"{html.escape(i['title'])}</a>"
            f"<div style='color:#555;font-size:13px;margin:2px 0 0 34px'>"
            f"{comp}{loc}{szlabel}{agelabel} · <span style='color:#999'>{html.escape(i['source'])}</span></div>"
            f"</li>"
        )
    return (
        f"<div style='max-width:660px'>"
        f"<h2 style='font-family:sans-serif'>Job digest — {now}</h2>"
        f"<p style='font-family:sans-serif;color:#666;font-size:13px'>"
        f"{len(items)} new postings, ranked by resume match (green number). "
        f"New since the last run only.</p>"
        f"<ul style='list-style:none;padding:0;margin:0;font-family:sans-serif'>"
        + "".join(rows) + "</ul></div>"
    )


def send_email(html_body):
    if not (EMAIL_USER and EMAIL_PASS and EMAIL_TO):
        print("[info] email creds not set — printing digest:\n")
        print(re.sub(r"<[^>]+>", "", html_body))
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Job digest — {datetime.now().strftime('%b %d %-I%p')}"
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
        srv.starttls()
        srv.login(EMAIL_USER, EMAIL_PASS)
        srv.sendmail(EMAIL_USER, [a.strip() for a in EMAIL_TO.split(",")], msg.as_string())
    print(f"[ok] sent job digest to {EMAIL_TO}")


def main():
    # LOOP: pull in ATS tokens auto-detected by the funding digest
    gh, lv, ash = list(GREENHOUSE_COMPANIES), list(LEVER_COMPANIES), list(ASHBY_COMPANIES)
    try:
        import watchlist as _wl
        auto = _wl.get_tokens()
        gh  = list(dict.fromkeys(gh  + auto.get("greenhouse", [])))
        lv  = list(dict.fromkeys(lv  + auto.get("lever", [])))
        ash = list(dict.fromkeys(ash + auto.get("ashby", [])))
        extra = len(gh) - len(GREENHOUSE_COMPANIES) + len(lv) - len(LEVER_COMPANIES) \
                + len(ash) - len(ASHBY_COMPANIES)
        if extra:
            print(f"[loop] +{extra} auto-detected companies from funding digest")
    except Exception as e:
        print(f"[info] watchlist not available: {e}")

    raw = []
    # Remotive is remote-only and Arbeitnow is mostly EU/remote — under a strict
    # NYC gate (REQUIRE_NYC on, REMOTE_OK off) they yield ~nothing, so skip the
    # calls entirely instead of fetching hundreds of postings just to discard
    # them. Flip REMOTE_OK=True (or REQUIRE_NYC=False) to use them again.
    remote_useful = REMOTE_OK or not REQUIRE_NYC
    if USE_REMOTIVE and remote_useful:   raw += fetch_remotive()
    elif USE_REMOTIVE:                   print("[info] skipping Remotive (remote-only vs NYC gate)")
    if USE_ARBEITNOW and remote_useful:  raw += fetch_arbeitnow()
    elif USE_ARBEITNOW:                  print("[info] skipping Arbeitnow (remote/EU vs NYC gate)")
    if USE_HN_WHOISHIRING: raw += fetch_hn()
    for t in gh:  raw += fetch_greenhouse(t)
    for t in lv:  raw += fetch_lever(t)
    for t in ash: raw += fetch_ashby(t)
    if USE_WORKDAY:
        for e in WORKDAY_COMPANIES: raw += fetch_workday(e)
    print(f"[info] fetched {len(raw)} raw postings")

    seen = load_seen()
    now_ts = datetime.now(timezone.utc).timestamp()
    age_cutoff = (now_ts - MAX_AGE_DAYS * 86400) if MAX_AGE_DAYS else 0
    picked, ids_this_run, seen_titles = [], set(), set()

    for j in raw:
        if not j.get("title") or not j.get("url"):
            continue
        # HN comments have freeform titles; only title-filter the structured sources
        if j["source"] != "HN Who's Hiring" and not title_is_target(j["title"]):
            continue
        if j["source"] == "HN Who's Hiring" and not any(
            x in j["content"].lower() for x in RESUME["target_titles"]):
            continue
        # Recency gate: drop postings older than MAX_AGE_DAYS when the source
        # gives a date. Unknown-date sources fall through (kept, no age shown).
        posted = j.get("posted_ts")
        if age_cutoff and posted and posted < age_cutoff:
            continue
        jid = job_id(j)
        if jid in seen or jid in ids_this_run:
            continue
        # Collapse the same role surfaced by two boards (different URL -> different
        # jid) down to one entry, keyed on company + title.
        tkey = (j.get("company", "").lower().strip(), j["title"].lower().strip())
        if tkey in seen_titles:
            continue
        if REQUIRE_NYC and not is_nyc(j.get("location", ""), j.get("content", "")):
            continue
        q = qualification_score(j["title"], j["content"])
        if q <= 0:
            continue
        j["q"] = q
        j["_id"] = jid
        picked.append(j)
        ids_this_run.add(jid)
        seen_titles.add(tkey)

    # Rank by resume match first, then freshest, then company-size preference.
    picked.sort(key=lambda x: (x["q"], x.get("posted_ts") or 0, size_score(x["company"])),
                reverse=True)
    picked = picked[:MAX_ITEMS]

    for j in picked:
        seen[j["_id"]] = now_ts
    save_seen(seen)

    print(f"[info] {len(picked)} new matching postings this run")
    send_email(build_html(picked))


if __name__ == "__main__":
    main()
