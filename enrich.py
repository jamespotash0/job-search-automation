#!/usr/bin/env python3
"""
enrich.py — company enrichment for the funding digest.

For each raise (top N only, to control cost/quota):
  1. Anthropic API + web-search tool researches the company across the web
     (company site, Crunchbase, LinkedIn, a16z/Sequoia/YC/Bessemer, etc.) and
     returns: website, domain, one-line description, founders, investors.
  2. Hunter.io finds a real founder email (until your monthly free cap), then
     falls back to pattern-guessing (marked UNVERIFIED).

Persisted state (via GitHub Actions cache):
  - enrich_cache.json   : results per company, so we never re-spend on repeats
  - hunter_usage.json   : monthly Hunter call counter, resets each month

Requires secrets: ANTHROPIC_API_KEY, HUNTER_API_KEY (optional).
"""

import os
import re
import json
import urllib.request
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HUNTER_API_KEY    = os.environ.get("HUNTER_API_KEY", "")

# Model for research. Change if you prefer another. web_search is server-side.
AI_MODEL = os.environ.get("AI_MODEL", "claude-haiku-4-5-20251001")

# Rationing
ENRICH_TOP_N        = int(os.environ.get("ENRICH_TOP_N", "8"))   # companies/run
HUNTER_MONTHLY_CAP  = int(os.environ.get("HUNTER_MONTHLY_CAP", "50"))

ENRICH_CACHE = "enrich_cache.json"
HUNTER_USAGE = "hunter_usage.json"

UA = {"User-Agent": "Mozilla/5.0 (funding-digest)"}


# --------------------------------------------------------------------------
# persisted state
# --------------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[warn] save {path}: {e}")


def hunter_calls_left():
    u = _load(HUNTER_USAGE, {})
    month = datetime.now().strftime("%Y-%m")
    if u.get("month") != month:
        return HUNTER_MONTHLY_CAP
    return max(0, HUNTER_MONTHLY_CAP - u.get("count", 0))


def _bump_hunter():
    u = _load(HUNTER_USAGE, {})
    month = datetime.now().strftime("%Y-%m")
    if u.get("month") != month:
        u = {"month": month, "count": 0}
    u["count"] = u.get("count", 0) + 1
    _save(HUNTER_USAGE, u)


# --------------------------------------------------------------------------
# AI research (Anthropic + web search)
# --------------------------------------------------------------------------
def ai_lookup(company):
    if not ANTHROPIC_API_KEY:
        return {}
    prompt = (
        f'Research the startup "{company}" that recently raised venture funding. '
        "Use web search across the company website, Crunchbase, LinkedIn, and VC "
        "sites (a16z, Sequoia, Y Combinator, Bessemer, General Catalyst, etc.). "
        "Return ONLY a JSON object, no prose, no code fences:\n"
        '{"website":"","domain":"","description":"one concise sentence on what they do",'
        '"founders":["Full Name"],"investors":["Firm"]}\n'
        "Use empty strings/arrays for anything you cannot verify."
    )
    body = {
        "model": AI_MODEL,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search",
                   "max_uses": 3}],
    }
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={
                "content-type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
        return _parse_json(text)
    except Exception as e:
        print(f"[warn] ai_lookup {company}: {e}")
        return {}


def _parse_json(text):
    if not text:
        return {}
    text = text.strip().strip("`")
    text = re.sub(r"^json", "", text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Hunter.io email lookup + pattern fallback
# --------------------------------------------------------------------------
def _get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return json.loads(r.read().decode())


def hunter_domain_search(domain):
    """Returns (pattern, best_email) or (None, None). Costs one Hunter call."""
    if not (HUNTER_API_KEY and domain):
        return None, None
    if hunter_calls_left() <= 0:
        return None, None
    try:
        d = _get_json(f"https://api.hunter.io/v2/domain-search"
                      f"?domain={domain}&api_key={HUNTER_API_KEY}")
        _bump_hunter()
        data = d.get("data", {})
        pattern = data.get("pattern")  # e.g. "{first}" or "{first}.{last}"
        best = None
        for e in data.get("emails", []):
            pos = (e.get("position") or "").lower()
            seniority = (e.get("seniority") or "").lower()
            if any(k in pos for k in ("founder", "ceo", "co-founder")) or seniority == "executive":
                best = e.get("value")
                break
        return pattern, best
    except Exception as e:
        print(f"[warn] hunter {domain}: {e}")
        return None, None


def pattern_to_email(pattern, first, last, domain):
    if not (pattern and domain and first):
        return ""
    first, last = first.lower(), (last or "").lower()
    email = (pattern
             .replace("{first}", first).replace("{last}", last)
             .replace("{f}", first[:1]).replace("{l}", last[:1]))
    if "{" in email:
        return ""
    return f"{email}@{domain}"


def guess_email(domain, founder):
    if not (domain and founder):
        return ""
    parts = founder.strip().split()
    first = parts[0].lower()
    return f"{first}@{domain}"   # simplest common pattern


def find_email(domain, founders):
    """Returns (email, status)."""
    founder = founders[0] if founders else ""
    first = founder.split()[0] if founder else ""
    last = founder.split()[-1] if len(founder.split()) > 1 else ""

    pattern, best = hunter_domain_search(domain)
    if best:
        return best, "verified (Hunter)"
    if pattern:
        e = pattern_to_email(pattern, first, last, domain)
        if e:
            return e, "pattern (Hunter)"
    e = guess_email(domain, founder)
    if e:
        return e, "guessed (UNVERIFIED)"
    return "", ""


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def enrich(company):
    """Full enrichment for one company, cached. Returns a dict."""
    cache = _load(ENRICH_CACHE, {})
    key = company.lower().strip()
    if key in cache:
        return cache[key]

    info = ai_lookup(company)
    domain = info.get("domain") or ""
    email, status = ("", "")
    if domain:
        email, status = find_email(domain, info.get("founders", []))

    result = {
        "website": info.get("website", ""),
        "domain": domain,
        "description": info.get("description", ""),
        "founders": info.get("founders", []),
        "investors": info.get("investors", []),
        "email": email,
        "email_status": status,
        "ts": datetime.now().timestamp(),
    }
    cache[key] = result
    _save(ENRICH_CACHE, cache)
    return result
