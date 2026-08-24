#!/usr/bin/env python3
"""
watchlist.py — the loop between the funding digest and the companies digest.

When the funding digest enriches a freshly-raised company, it calls
detect_and_add(name, domain). We probe the public ATS boards (Greenhouse /
Lever / Ashby); if the company is on one, we save its token to a shared
ats_watchlist.json. The companies digest then reads get_tokens() and starts scoring
that company's postings against your resume automatically.

No scraping — these are the same public board APIs the companies digest already uses.
Detection results are cached (per company) so we never re-probe.

The shared file travels between the two GitHub Actions workflows via a cache
with the key prefix "watchlist-" (caches are repo-scoped, so both see it).
"""

import os
import re
import json
import urllib.request
from datetime import datetime, timezone

WATCHLIST_FILE = "ats_watchlist.json"
UA = {"User-Agent": "Mozilla/5.0 (watchlist)"}

CORP_SUFFIXES = {"inc", "llc", "corp", "co", "ltd", "limited", "labs", "technologies"}

# Every ATS we can read a public board from. The first three are also probed by
# NAME (see PROBES) when the funding digest finds a company; the rest are only
# ever populated from an exact token lifted out of a real apply URL by getro.py,
# because guessing a SmartRecruiters identifier from a company name is mostly a
# way to burn requests on 404s.
PROVIDERS = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters", "rippling"]


def _load():
    try:
        with open(WATCHLIST_FILE) as f:
            d = json.load(f)
    except Exception:
        d = {}
    for prov in PROVIDERS:
        d.setdefault(prov, [])
    d.setdefault("checked", [])
    # Approximate employee counts, harvested by getro.py from the VC-board API.
    # The companies digest reads these into COMPANY_SIZE so its size ranking has
    # real data instead of the hand-filled dict its docstring apologises for.
    d.setdefault("sizes", {})
    # Companies we probed but found no public board for — a freshly-funded startup
    # usually opens its Greenhouse/Ashby weeks AFTER the round is announced, and
    # "checked" alone means we'd never look again. Kept as {name: {...}} and
    # re-probed by reprobe_pending() until it lands or ages out.
    d.setdefault("pending", {})
    # Companies known to have raised recently, so the companies digest can flag
    # and boost their postings: {name: {round, amount, date, url, tokens}}.
    d.setdefault("funded", {})
    # The PERMANENT stage fact, separate from the recency flag above: what round
    # a company is at, kept forever. "funded" ages out at FUNDED_MAX_DAYS — and
    # until this existed, the round died with it, so a company we had personally
    # recorded closing a Series A scored no stage bonus six months later just
    # because its JD never said "Series A" out loud. Recency and stage are two
    # different facts and only one of them expires.
    d.setdefault("stage", {})
    # Backfill from any funding rows already on disk, so the fact survives the
    # first age-out after this upgrade rather than starting empty.
    for k, v in (d.get("funded") or {}).items():
        if v.get("round") and k not in d["stage"]:
            d["stage"][k] = {"name": v.get("name") or k, "round": v["round"],
                             "date": v.get("date") or "", "tokens": v.get("tokens", []),
                             "source": "funding digest"}
    return d


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_since(datestr):
    try:
        d = datetime.strptime(datestr, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return 0
    return (datetime.now(timezone.utc) - d).days


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


def _post(url, payload, timeout=12):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**UA, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
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


def probe_workable(token):
    try:
        d = _post(f"https://apply.workable.com/api/v1/accounts/{token}/jobs", {"query": ""})
        return bool(d.get("results"))
    except Exception:
        return False


def probe_smartrecruiters(token):
    try:
        d = _get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1")
        return bool(d.get("totalFound"))
    except Exception:
        return False


def probe_rippling(token):
    try:
        d = _get(f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs")
        return isinstance(d, list) and len(d) > 0
    except Exception:
        return False


PROBE_BY_PROVIDER = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "ashby": probe_ashby,
    "workable": probe_workable,
    "smartrecruiters": probe_smartrecruiters,
    "rippling": probe_rippling,
}

# Name-guessing probes stay on the three boards whose tokens are predictable
# from a company name. Adding the others here would multiply candidate_tokens()
# by six for almost no extra hits.
PROBES = [("greenhouse", probe_greenhouse),
          ("lever", probe_lever),
          ("ashby", probe_ashby)]


def _probe_company(d, name, domain=None):
    """Probe one company's candidate tokens against every ATS, mutating `d`.
    Returns the list of (provider, token) newly added."""
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
    return found


def detect_and_add(name, domain=None, funding=None):
    """Detect a company's ATS and add its token to the shared watchlist.

    `funding` — optional {round, amount, date, url} for a company that just
    raised. Recorded either way, so the companies digest can flag its postings
    even if the board only shows up on a later re-probe.

    Returns list of (provider, token) found. Cached per company.
    """
    d = _load()
    key = (name or "").lower().strip()
    if not key:
        return []
    if key in d["checked"]:
        # Already probed — but a NEW raise is still news, and a company sitting in
        # `pending` should keep its slot rather than be silently forgotten.
        if funding:
            _record_funding(d, key, name, funding, d["pending"].get(key, {}).get("tokens", []))
            _save(d)
        return []

    found = _probe_company(d, name, domain)
    d["checked"].append(key)
    if found:
        d["pending"].pop(key, None)
    else:
        d["pending"][key] = {"name": name, "domain": domain or "",
                             "first_seen": _today(), "tries": 1}
    if funding:
        _record_funding(d, key, name, funding, [t for _, t in found])
    _save(d)
    if found:
        print(f"[watchlist] {name}: {found}")
    return found


# How long a board-less company stays worth re-probing, and how many times.
PENDING_MAX_DAYS = int(os.environ.get("WATCHLIST_PENDING_DAYS", "75"))
PENDING_MAX_TRIES = int(os.environ.get("WATCHLIST_PENDING_TRIES", "12"))
# How long a raise keeps flagging a company's postings in the companies digest.
FUNDED_MAX_DAYS = int(os.environ.get("WATCHLIST_FUNDED_DAYS", "180"))


def _record_funding(d, key, name, funding, tokens):
    entry = d["funded"].get(key, {})
    entry.update({k: v for k, v in {
        "name": name,
        "round": (funding.get("round") or "").strip(),
        "amount": (funding.get("amount") or "").strip(),
        "date": funding.get("date") or _today(),
        "url": funding.get("url") or "",
    }.items() if v or k == "date"})
    entry["tokens"] = sorted(set(entry.get("tokens", []) + list(tokens)))
    d["funded"][key] = entry
    # Mirror the round into the permanent stage record. This one is never aged
    # out — a company does not stop being a Series A because the news got old.
    if entry.get("round"):
        st = d["stage"].get(key, {})
        st.update({"name": name, "round": entry["round"],
                   "date": entry.get("date") or _today(),
                   "source": st.get("source") or "funding digest"})
        st["tokens"] = sorted(set(st.get("tokens", []) + list(tokens)))
        d["stage"][key] = st


def reprobe_pending(limit=None):
    """Re-probe companies that had no public board when we first saw them.

    This is the fix for the loop's biggest leak: a startup announces a Series A,
    we probe the same day, find nothing, and mark it `checked` forever — even
    though the board (and the roles the raise pays for) appears a month later.

    Returns list of (name, [(provider, token), ...]) that landed this run.
    """
    limit = limit or int(os.environ.get("WATCHLIST_REPROBE_MAX", "40"))
    d = _load()
    pend = d["pending"]
    # Oldest-first, so nothing starves behind a steady stream of new raises.
    order = sorted(pend.items(), key=lambda kv: (kv[1].get("first_seen") or "", kv[0]))
    landed, dropped, tried = [], 0, 0
    for key, meta in order:
        age = _days_since(meta.get("first_seen") or _today())
        if age > PENDING_MAX_DAYS or meta.get("tries", 0) >= PENDING_MAX_TRIES:
            pend.pop(key, None)
            dropped += 1
            continue
        if tried >= limit:
            continue
        tried += 1
        meta["tries"] = meta.get("tries", 0) + 1
        meta["last_try"] = _today()
        found = _probe_company(d, meta.get("name") or key, meta.get("domain") or None)
        if found:
            pend.pop(key, None)
            landed.append((meta.get("name") or key, found))
            if key in d["funded"]:
                d["funded"][key]["tokens"] = sorted(
                    set(d["funded"][key].get("tokens", []) + [t for _, t in found]))
            print(f"[watchlist] re-probe hit — {meta.get('name') or key}: {found}")

    # Age out stale funding flags so the digest never says "just raised" about a
    # round from last year. The matching d["stage"] row deliberately survives:
    # the raise stops being NEWS, it does not stop being TRUE.
    for key in [k for k, v in d["funded"].items()
                if _days_since(v.get("date") or _today()) > FUNDED_MAX_DAYS]:
        d["funded"].pop(key, None)

    _save(d)
    print(f"[watchlist] re-probed {tried} pending compan{'y' if tried == 1 else 'ies'}: "
          f"{len(landed)} landed, {dropped} aged out, {len(pend)} still pending")
    return landed


def get_funded():
    """For the companies digest: {normalized name: {round, amount, date, url,
    tokens}} for companies that raised inside FUNDED_MAX_DAYS."""
    return dict(_load()["funded"])


def get_stage():
    """For the companies digest: {normalized name: {name, round, date, tokens}}
    for every company whose stage we know. Unlike get_funded() this never
    expires — it answers "what stage are they at", not "did they just raise"."""
    return dict(_load().get("stage") or {})


def set_stage(name, round_name, tokens=None, date=None):
    """Assert a company's stage by hand, for the case the funding digest never
    saw the round (it predates the loop, or the raise was never announced where
    we look). `python watchlist.py stage courted "Series A"`."""
    d = _load()
    key = (name or "").lower().strip()
    if not key or not (round_name or "").strip():
        return {}
    st = d["stage"].get(key, {})
    st.update({"name": name, "round": round_name.strip(),
               "date": date or st.get("date") or _today(), "source": "manual"})
    st["tokens"] = sorted(set(st.get("tokens", []) + list(tokens or [])))
    d["stage"][key] = st
    _save(d)
    return st


def add_tokens(tokens_by_provider, verify=True):
    """Bulk-add exact ATS tokens (from getro.py), skipping ones we already hold.

    `verify` probes each candidate before it lands, so a board that has since
    been closed or renamed never reaches the digest as a permanent 404. Probes
    run concurrently — a sweep can offer several hundred new tokens at once and
    a serial check would dominate the run.

    Returns {provider: [tokens actually added]}.
    """
    d = _load()
    pending = []
    for provider, toks in (tokens_by_provider or {}).items():
        if provider not in PROVIDERS:
            continue
        have = {t.lower() for t in d[provider]}
        for t in toks:
            if t and t.lower() not in have:
                have.add(t.lower())
                pending.append((provider, t))
    if not pending:
        return {}

    if verify:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=int(os.environ.get("WATCHLIST_WORKERS", "10"))) as ex:
            ok = list(ex.map(lambda pt: PROBE_BY_PROVIDER[pt[0]](pt[1]), pending))
        pending = [pt for pt, good in zip(pending, ok) if good]

    added = {}
    for provider, tok in pending:
        d[provider].append(tok)
        added.setdefault(provider, []).append(tok)
    if added:
        _save(d)
    return added


def add_sizes(sizes):
    """Record {company name (lowercased): approx employees} for size ranking."""
    if not sizes:
        return 0
    d = _load()
    before = len(d["sizes"])
    d["sizes"].update({k: v for k, v in sizes.items() if k and v})
    _save(d)
    return len(d["sizes"]) - before


def get_sizes():
    """For the companies digest: {company name: approx employees}."""
    return dict(_load().get("sizes") or {})


def get_tokens():
    """For the companies digest: current auto-detected tokens by provider."""
    d = _load()
    return {prov: d.get(prov, []) for prov in PROVIDERS}


def get_checked():
    """Company names already probed (so discovery can skip re-searching them)."""
    return list(_load()["checked"])


def token_count():
    """Total distinct ATS tokens currently on the watchlist."""
    d = _load()
    return sum(len(d.get(prov, [])) for prov in PROVIDERS)


if __name__ == "__main__":
    # Tiny CLI, for the one thing you do by hand: assert a stage the funding
    # digest never saw.  python watchlist.py stage courted "Series A"
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == "stage":
        print(set_stage(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "stages":
        for k, v in sorted(get_stage().items()):
            print(f"{v.get('round','?'):<12} {k}  ({v.get('source','')})")
    else:
        print("usage: watchlist.py stage <company> <round> | watchlist.py stages")
