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
import smtplib
import html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

import feedparser

try:
    import enrich as _enrich          # AI + Hunter enrichment (optional)
except Exception:
    _enrich = None

# --------------------------------------------------------------------------
# CONFIG — tune these to your search
# --------------------------------------------------------------------------

# Enrich the top N ranked raises with company info + founder email. Set to 0
# to disable enrichment entirely (pure free headlines).
ENRICH_TOP_N = 8

# How far back to look. Run daily -> keep at ~28h so nothing slips through gaps.
LOOKBACK_HOURS = 28

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
]

# Focus keywords. Items matching these float to the top ("Best matches").
# Everything else still shows under "Other recent raises".
FOCUS_KEYWORDS = [
    "ai", "artificial intelligence", "agent", "llm", "automation", "workflow",
    "b2b", "saas", "developer", "infrastructure", "data", "platform",
]

# Location terms that earn a bonus (leave list empty to ignore location).
LOCATION_KEYWORDS = ["new york", "nyc", "brooklyn", "manhattan"]

# A headline must contain at least one of these to count as a funding item.
FUNDING_SIGNALS = [
    "raise", "raised", "raises", "secures", "secured", "closes", "closed",
    "seed", "series a", "funding", "led by", "backed by", "$",
]

# Extra static RSS feeds to include (venture/funding coverage). All verified free
# + machine-readable. (Finsmes is Cloudflare-walled and topstartups.io is
# client-rendered with no API, so neither can be pulled reliably — their coverage
# still reaches you via the Google News queries above.)
EXTRA_FEEDS = [
    # US venture coverage
    "https://techcrunch.com/category/venture/feed/",
    "https://venturebeat.com/feed",   # note: no trailing slash (the "/" 308-loops)
    # Europe-heavy — broad coverage; comment these out if you only want US raises.
    "https://www.eu-startups.com/feed/",
    "https://tech.eu/feed/",
]

# Max items to include in the email.
MAX_ITEMS = 40

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


def is_funding(title: str, summary: str) -> bool:
    blob = (title + " " + summary).lower()
    return any(sig in blob for sig in FUNDING_SIGNALS)


def score(title: str, summary: str) -> int:
    blob = (title + " " + summary).lower()
    s = 0
    s += sum(2 for k in FOCUS_KEYWORDS if k in blob)
    s += sum(3 for k in LOCATION_KEYWORDS if k in blob)
    if "series a" in blob or "seed" in blob:
        s += 1
    return s


def collect():
    feeds = [google_news_rss(q) for q in STAGE_QUERIES] + EXTRA_FEEDS
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    items, seen = [], set()

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
                continue
            if not is_funding(title, summary):
                continue

            key = norm_title(title)
            if key in seen:
                continue
            seen.add(key)

            source = ""
            if hasattr(e, "source") and getattr(e.source, "title", None):
                source = e.source.title
            items.append({
                "title": title,
                "summary": summary[:240],
                "link": link,
                "source": source,
                "score": score(title, summary),
                "time": entry_time(e),
            })

    items.sort(key=lambda x: (x["score"], x["time"]), reverse=True)
    return items[:MAX_ITEMS]


def build_html(items):
    today = datetime.now().strftime("%A, %b %d")
    if not items:
        return f"<h2>Funding digest — {today}</h2><p>No new raises matched today. " \
               f"Widen your keywords or check back tomorrow.</p>"

    best = [i for i in items if i["score"] >= 3]
    other = [i for i in items if i["score"] < 3]

    def block(rows):
        out = []
        for i in rows:
            src = f" · <span style='color:#888'>{html.escape(i['source'])}</span>" if i["source"] else ""
            enr = i.get("enrich") or {}
            extra = ""
            if enr:
                bits = []
                if enr.get("description"):
                    bits.append(f"<div style='color:#333;font-size:13px;margin-top:4px'>"
                                f"<b>What they do:</b> {html.escape(enr['description'])}</div>")
                line = []
                if enr.get("website"):
                    w = html.escape(enr["website"])
                    line.append(f"<a href='{w}' style='color:#1a5fb4'>{html.escape(enr.get('domain') or 'site')}</a>")
                if enr.get("founders"):
                    line.append("Founder: " + html.escape(", ".join(enr["founders"][:2])))
                if enr.get("investors"):
                    line.append("Backed by: " + html.escape(", ".join(enr["investors"][:3])))
                if line:
                    bits.append(f"<div style='color:#555;font-size:12px;margin-top:3px'>"
                                + " · ".join(line) + "</div>")
                if enr.get("email"):
                    color = "#2a7" if "verified" in enr.get("email_status", "") else "#b06"
                    bits.append(f"<div style='font-size:12px;margin-top:3px'>"
                                f"✉ <a href='mailto:{html.escape(enr['email'])}' style='color:{color}'>"
                                f"{html.escape(enr['email'])}</a> "
                                f"<span style='color:#999'>({html.escape(enr.get('email_status',''))})</span></div>")
                # click-to-search links (no scraping)
                from urllib.parse import quote_plus
                comp = i.get("company") or i["title"]
                li = "https://www.google.com/search?q=" + quote_plus(f"{comp} founder linkedin")
                cb = "https://www.google.com/search?q=" + quote_plus(f"{comp} crunchbase")
                bits.append(f"<div style='font-size:12px;margin-top:3px'>"
                            f"<a href='{li}' style='color:#888'>find on LinkedIn</a> · "
                            f"<a href='{cb}' style='color:#888'>Crunchbase</a></div>")
                extra = "".join(bits)
            out.append(
                f"<li style='margin:0 0 18px'>"
                f"<a href='{html.escape(i['link'])}' style='font-weight:600;color:#1a5fb4;text-decoration:none'>"
                f"{html.escape(i['title'])}</a>{src}"
                f"<div style='color:#444;font-size:13px;margin-top:3px'>{html.escape(i['summary'])}</div>"
                f"{extra}"
                f"</li>"
            )
        return "<ul style='list-style:none;padding:0;margin:0'>" + "".join(out) + "</ul>"

    parts = [f"<h2 style='font-family:sans-serif'>Funding digest — {today}</h2>",
             f"<p style='font-family:sans-serif;color:#666;font-size:13px'>"
             f"{len(items)} raises in the last {LOOKBACK_HOURS}h. "
             f"Top matches first — these are your outreach targets today.</p>"]
    if best:
        parts.append("<h3 style='font-family:sans-serif'>Best matches</h3>" + block(best))
    if other:
        parts.append("<h3 style='font-family:sans-serif'>Other recent raises</h3>" + block(other))
    return "<div style='max-width:640px'>" + "".join(parts) + "</div>"


def send_email(html_body):
    if not (EMAIL_USER and EMAIL_PASS and EMAIL_TO):
        print("[info] email creds not set — printing digest instead:\n")
        print(re.sub(r"<[^>]+>", "", html_body))
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Funding digest — {datetime.now().strftime('%b %d')}"
    msg["From"] = EMAIL_USER
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

    if ENRICH_TOP_N and _enrich is not None:
        try:
            import watchlist as _wl
        except Exception:
            _wl = None
        for i in items[:ENRICH_TOP_N]:
            company = guess_company(i["title"])
            i["company"] = company
            try:
                i["enrich"] = _enrich.enrich(company)
                print(f"[enrich] {company}: {i['enrich'].get('email_status') or 'no email'}")
            except Exception as e:
                print(f"[warn] enrich {company}: {e}")
            # LOOP: detect this company's ATS and feed the job digest's watchlist
            if _wl is not None:
                try:
                    _wl.detect_and_add(company, (i.get("enrich") or {}).get("domain"))
                except Exception as e:
                    print(f"[warn] watchlist {company}: {e}")
    else:
        print("[info] enrichment disabled or enrich.py missing")

    send_email(build_html(items))
