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


def load_dotenv(path=".env"):
    """Minimal .env loader. Callers that import this module usually load .env
    themselves first; setdefault means whoever gets there first wins and this is
    a no-op on GitHub Actions, where the values arrive as repo secrets."""
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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HUNTER_API_KEY    = os.environ.get("HUNTER_API_KEY", "")

# Model for research. Change if you prefer another. web_search is server-side.
AI_MODEL = os.environ.get("AI_MODEL", "claude-haiku-4-5-20251001")

# Rationing
ENRICH_TOP_N        = int(os.environ.get("ENRICH_TOP_N", "8"))   # companies/run
HUNTER_MONTHLY_CAP  = int(os.environ.get("HUNTER_MONTHLY_CAP", "50"))

ENRICH_CACHE = "enrich_cache.json"
HUNTER_USAGE = "hunter_usage.json"

# Bump when the shape of an enrich() result changes; entries cached at an older
# version are re-fetched instead of returned with fields the callers now expect.
SCHEMA_VERSION = 2

# Objective sector buckets the researcher must choose from. Kept here (not in the
# digest) because the answer is a fact about the company and is safe to cache;
# whether a bucket is a good fit is a per-profile judgement the caller makes.
CATEGORIES = [
    "ai/software", "b2b saas", "devtools/infra", "fintech",
    "proptech/construction", "healthtech", "consumer software",
    "biotech/pharma", "hardware/devices", "semiconductor/datacenter",
    "space/aerospace/defense", "energy/climate", "materials/manufacturing",
    "consumer goods", "other",
]

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
        '"founders":["Full Name"],"investors":["Firm"],'
        '"hq_city":"","hq_country":"","category":""}\n'
        "hq_city/hq_country are the HEADQUARTERS, not where an investor or a "
        "satellite office sits; give hq_country as a full name like "
        '"United States", "India", "United Kingdom".\n'
        f"category must be exactly one of: {', '.join(CATEGORIES)}. Pick by what "
        "the company SELLS, not what it uses internally — a company applying AI to "
        "design drugs is biotech/pharma, and one selling software that happens to "
        "run on GPUs is ai/software.\n"
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
    # A placeholder that resolved to nothing leaves a hole: "{first}.{last}"
    # with no last name becomes "jane." -> jane.@acme.ai, which is malformed and
    # would still be sent. Reject anything with an empty name component.
    if not email or email.startswith((".", "-", "_")) or email.endswith((".", "-", "_")):
        return ""
    if re.search(r"[._-]{2,}", email):
        return ""
    return f"{email}@{domain}"


def guess_emails(domain, founder):
    """Ranked list of the most likely email formats for a founder, most-likely
    first. Only used as the UNVERIFIED fallback (no Hunter verification / no
    observed domain pattern). Order reflects real-world prevalence at startups:
        1. first@domain          (jane@acme.ai)      — most common
        2. first.last@domain     (jane.smith@acme.ai)
        3. {f}last@domain        (jsmith@acme.ai)
        4. last@domain           (smith@acme.ai)
    """
    if not (domain and founder):
        return []
    parts = [p for p in re.sub(r"[^a-z .-]", "", founder.lower()).split() if p]
    if not parts:
        return []
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    cands = [f"{first}@{domain}"]
    if last:
        cands += [f"{first}.{last}@{domain}",
                  f"{first[:1]}{last}@{domain}",
                  f"{last}@{domain}"]
    seen, out = set(), []
    for c in cands:                       # dedupe, keep order
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def find_email(domain, founders):
    """Returns (email, status, alts). `alts` holds the other likely guesses,
    ranked, and is only populated for the UNVERIFIED name-based fallback."""
    founder = founders[0] if founders else ""
    first = founder.split()[0] if founder else ""
    last = founder.split()[-1] if len(founder.split()) > 1 else ""

    pattern, best = hunter_domain_search(domain)
    if best:
        return best, "verified (Hunter)", []
    if pattern:
        e = pattern_to_email(pattern, first, last, domain)
        if e:
            return e, "pattern (Hunter)", []
    guesses = guess_emails(domain, founder)
    if guesses:
        return guesses[0], "guessed (UNVERIFIED)", guesses[1:]
    return "", "", []


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def enrich(company):
    """Full enrichment for one company, cached. Returns a dict."""
    cache = _load(ENRICH_CACHE, {})
    key = company.lower().strip()
    hit = cache.get(key)
    if hit and hit.get("v") == SCHEMA_VERSION:
        return hit

    info = ai_lookup(company)
    domain = info.get("domain") or ""
    email, status, alts = ("", "", [])
    if domain:
        email, status, alts = find_email(domain, info.get("founders", []))

    category = (info.get("category") or "").strip().lower()
    if category not in CATEGORIES:
        category = ""          # model went off-menu; caller treats as unknown

    result = {
        "v": SCHEMA_VERSION,
        "website": info.get("website", ""),
        "domain": domain,
        "description": info.get("description", ""),
        "founders": info.get("founders", []),
        "investors": info.get("investors", []),
        "hq_city": (info.get("hq_city") or "").strip(),
        "hq_country": (info.get("hq_country") or "").strip(),
        "category": category,
        "email": email,
        "email_status": status,
        "email_alts": alts,
        "ts": datetime.now().timestamp(),
    }
    cache[key] = result
    _save(ENRICH_CACHE, cache)
    return result
