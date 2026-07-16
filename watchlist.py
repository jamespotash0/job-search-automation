#!/usr/bin/env python3
"""
watchlist.py — the loop between the funding digest and the job digest.

When the funding digest enriches a freshly-raised company, it calls
detect_and_add(name, domain). We probe the public ATS boards (Greenhouse /
Lever / Ashby); if the company is on one, we save its token to a shared
ats_watchlist.json. The job digest then reads get_tokens() and starts scoring
that company's postings against your resume automatically.

No scraping — these are the same public board APIs the job digest already uses.
Detection results are cached (per company) so we never re-probe.

The shared file travels between the two GitHub Actions workflows via a cache
with the key prefix "watchlist-" (caches are repo-scoped, so both see it).
"""

import re
import json
import urllib.request

WATCHLIST_FILE = "ats_watchlist.json"
UA = {"User-Agent": "Mozilla/5.0 (watchlist)"}

CORP_SUFFIXES = {"inc", "llc", "corp", "co", "ltd", "limited", "labs", "technologies"}


def _load():
    try:
        with open(WATCHLIST_FILE) as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("greenhouse", [])
    d.setdefault("lever", [])
    d.setdefault("ashby", [])
    d.setdefault("checked", [])
    return d


def _save(d):
    try:
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(d, f, indent=2)
    except Exception as e:
        print(f"[warn] save watchlist: {e}")


def candidate_tokens(name, domain=None):
    """Guess likely ATS tokens from a company name / domain."""
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    words = [w for w in n.split() if w and w not in CORP_SUFFIXES]
    cands = []
    if words:
        cands.append("".join(words))          # acmeai
        cands.append("-".join(words))          # acme-ai
        cands.append(words[0])                 # acme
    if domain:
        root = domain.lower().split(".")[0]
        if root:
            cands.append(root)                 # domain root
    # dedupe, keep order, cap
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:4]


# --------------------------- probes (each returns True if token is valid) ----
def _get(url, timeout=12):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


def probe_greenhouse(token):
    try:
        d = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
        return bool(d.get("jobs"))
    except Exception:
        return False


def probe_lever(token):
    try:
        d = _get(f"https://api.lever.co/v0/postings/{token}?mode=json")
        return isinstance(d, list) and len(d) > 0
    except Exception:
        return False


def probe_ashby(token):
    try:
        d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
        return bool(d.get("jobs"))
    except Exception:
        return False


PROBES = [("greenhouse", probe_greenhouse),
          ("lever", probe_lever),
          ("ashby", probe_ashby)]


def detect_and_add(name, domain=None):
    """Detect a company's ATS and add its token to the shared watchlist.
    Returns list of (provider, token) found. Cached per company."""
    d = _load()
    key = (name or "").lower().strip()
    if not key or key in d["checked"]:
        return []

    found = []
    for token in candidate_tokens(name, domain):
        for provider, probe in PROBES:
            if token in d[provider]:
                continue
            if probe(token):
                d[provider].append(token)
                found.append((provider, token))
                break          # this token matched a provider; try next token
        if found:
            break              # one good hit per company is enough

    d["checked"].append(key)
    _save(d)
    if found:
        print(f"[watchlist] {name}: {found}")
    return found


def get_tokens():
    """For the job digest: current auto-detected tokens by provider."""
    d = _load()
    return {"greenhouse": d["greenhouse"], "lever": d["lever"], "ashby": d["ashby"]}


def get_checked():
    """Company names already probed (so discovery can skip re-searching them)."""
    return list(_load()["checked"])


def token_count():
    """Total distinct ATS tokens currently on the watchlist."""
    d = _load()
    return len(d["greenhouse"]) + len(d["lever"]) + len(d["ashby"])
