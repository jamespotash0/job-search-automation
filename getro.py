#!/usr/bin/env python3
"""
getro.py — the watchlist's bulk feeder.

Getro powers the portfolio job boards of hundreds of VC funds, and its search
API is public and keyless. Every job it returns carries the company's REAL
apply URL — which, for most portfolio companies, is a Greenhouse / Lever /
Ashby / Workable / SmartRecruiters / Rippling board. So one cheap call yields
ATS tokens by the dozen, already attached to a live posting.

That is the difference from discover.py: discovery asks an LLM to name
companies and then GUESSES their token (candidate_tokens); this reads the token
straight out of a URL that is, by construction, correct. discover.py stays the
better tool for companies that never reach a VC board — the two feed the same
ats_watchlist.json.

Also harvested: `head_count` (a 1-6 size bucket, not a headcount) and `stage`,
which fill COMPANY_SIZE in the companies digest — the "honest limit" its
docstring calls out.

Usage:
    python3 getro.py            # sweep + ingest into ats_watchlist.json
    python3 getro.py --scan 800 # widen the id scan for this run
"""

import os
import re
import sys
import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import watchlist as wl

API = "https://api.getro.com/api/v2/collections/{}/search/jobs"
HDR = {"Content-Type": "application/json", "Accept": "application/json",
       "Origin": "https://jobs.getro.com",
       "User-Agent": "Mozilla/5.0 (companies-digest)"}

STATE_FILE = "getro_networks.json"

# Getro answered 73 sequential and 60 concurrent probes with no 429 and no
# throttling (~26 req/s at 12 workers), so this is polite rather than forced.
WORKERS = int(os.environ.get("GETRO_WORKERS", "10"))
# Live collection ids are sparse (~17% of the 600-4000 range), so the id space is
# walked a slice per run and the hits are cached — a full rescan every run would
# be thousands of calls to relearn what we already know.
SCAN_PER_RUN = int(os.environ.get("GETRO_SCAN", "400"))
SCAN_MAX_ID = int(os.environ.get("GETRO_SCAN_MAX_ID", "4200"))
PAGES_PER_NETWORK = int(os.environ.get("GETRO_PAGES", "4"))   # 100 jobs/page

# Extra network ids to always sweep — put the funds whose portfolios match your
# lane here once you know their ids (the scan prints them as it finds them).
PINNED_NETWORKS = [int(x) for x in
                   os.environ.get("GETRO_NETWORKS", "").replace(",", " ").split() if x.isdigit()]

# ---------------------------------------------------------------------------
# Token extraction. Each pattern anchors on the path segment that FOLLOWS the
# token, because the bare host form matches junk: `apply.workable.com/j/ABC123`
# has no account in it and yielded a company called "j", and Rippling's
# localised paths yielded "en-US". Requiring the trailing segment drops both.
# ---------------------------------------------------------------------------
TOKEN_PATTERNS = [
    # The trailing separator set must include "&": the embed form is written
    # `?for=<token>&token=123`, so without it the token is followed by "&" and
    # the whole pattern fails — silently skipping every embedded Greenhouse board.
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]{2,})(?:[/?&#]|$)", re.I)),
    ("lever",      re.compile(r"jobs\.lever\.co/([a-z0-9_.-]{2,})/", re.I)),
    ("ashby",      re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]{2,})/", re.I)),
    ("workable",   re.compile(r"apply\.workable\.com/([a-z0-9_-]{2,})/j/", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]{2,})/", re.I)),
    ("rippling",   re.compile(r"ats\.rippling\.com/([a-z0-9_-]{2,})/jobs/", re.I)),
]
# Path segments that are never a company token.
TOKEN_STOPWORDS = {"j", "jobs", "job", "en-us", "en", "embed", "search", "www", "api"}


def extract_token(url):
    """(provider, token) for an ATS apply URL, or None."""
    for provider, pat in TOKEN_PATTERNS:
        m = pat.search(url or "")
        if m:
            tok = m.group(1)
            if tok.lower() in TOKEN_STOPWORDS:
                return None
            return (provider, tok if provider == "smartrecruiters" else tok.lower())
    return None


# --------------------------------------------------------------------- API --
def _post(cid, page, hits=100, timeout=20):
    body = json.dumps({"hitsPerPage": hits, "page": page, "filters": {}}).encode()
    req = urllib.request.Request(API.format(cid), data=body, headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def probe_network(cid):
    """(cid, job_count) if the collection is live, else None. A dead id answers
    400; a live-but-empty one answers 200 with count 0."""
    try:
        res = (_post(cid, 0, hits=1) or {}).get("results") or {}
        cnt = res.get("count") or 0
        return (cid, cnt) if cnt else None
    except Exception:
        return None


def fetch_network(cid, pages=None):
    """All jobs from one network, up to `pages` x 100."""
    out = []
    for p in range(pages or PAGES_PER_NETWORK):
        try:
            jobs = ((_post(cid, p) or {}).get("results") or {}).get("jobs") or []
        except Exception as e:
            print(f"[warn] getro {cid} page {p}: {e}")
            break
        if not jobs:
            break
        out += jobs
        if len(jobs) < 100:
            break
    return out


# ------------------------------------------------------------------- state --
def _load_state():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("live", {})       # {"1200": job_count}
    d.setdefault("scan_cursor", 1)
    return d


def _save_state(d):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(d, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[warn] save getro state: {e}")


def scan(state, count=None):
    """Walk the next slice of the id space, recording the live collections."""
    count = count or SCAN_PER_RUN
    start = int(state.get("scan_cursor") or 1)
    if start > SCAN_MAX_ID:
        start = 1                                   # wrap: pick up new networks
    ids = [i for i in range(start, min(start + count, SCAN_MAX_ID + 1))
           if str(i) not in state["live"]]
    if not ids:
        state["scan_cursor"] = start + count
        return []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        hits = [h for h in ex.map(probe_network, ids) if h]
    for cid, cnt in hits:
        state["live"][str(cid)] = cnt
    state["scan_cursor"] = start + count
    print(f"[getro] scanned ids {start}-{start + count - 1} in {time.time() - t0:.1f}s "
          f"— {len(hits)} live (+{sum(c for _, c in hits):,} jobs), "
          f"{len(state['live'])} known")
    return hits


# --------------------------------------------------------- size / ingestion --
# `head_count` is a bucket, not a count: Cisco and Applied Materials are 6,
# Fireblocks and Mapbox are 4, ModernFi is 2. size_score() in the companies
# digest wants an approximate employee number, so map to bucket midpoints.
HEADCOUNT_BUCKETS = {1: 5, 2: 25, 3: 120, 4: 600, 5: 3000, 6: 15000}


def harvest(jobs):
    """(tokens_by_provider, sizes_by_company) from a batch of Getro jobs."""
    tokens, sizes = {}, {}
    for j in jobs:
        org = j.get("organization") or {}
        name = (org.get("name") or "").strip()
        hc = org.get("head_count")
        if name and isinstance(hc, int):
            sizes[name.lower()] = HEADCOUNT_BUCKETS.get(hc, 0) or None
        hit = extract_token(j.get("url") or "")
        if hit:
            tokens.setdefault(hit[0], set()).add(hit[1])
    return tokens, {k: v for k, v in sizes.items() if v}


def ingest(scan_count=None, pages=None):
    state = _load_state()
    scan(state, scan_count)
    _save_state(state)

    nets = sorted({int(c) for c in state["live"]} | set(PINNED_NETWORKS))
    if not nets:
        print("[getro] no live networks known yet — run again to widen the scan")
        return {}
    print(f"[getro] pulling {len(nets)} networks x {pages or PAGES_PER_NETWORK} pages")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        jobs = [j for lst in ex.map(lambda c: fetch_network(c, pages), nets) for j in lst]
    print(f"[getro] {len(jobs):,} jobs in {time.time() - t0:.1f}s")

    tokens, sizes = harvest(jobs)
    added = wl.add_tokens(tokens)
    wl.add_sizes(sizes)
    total = sum(len(v) for v in added.values())
    for prov, toks in sorted(added.items()):
        if toks:
            print(f"[getro] +{len(toks)} {prov}: {', '.join(sorted(toks)[:8])}"
                  f"{' ...' if len(toks) > 8 else ''}")
    print(f"[getro] {total} new companies on the watchlist, "
          f"{len(sizes)} company sizes recorded, {wl.token_count()} tokens total")
    return added


def main():
    n = None
    if "--scan" in sys.argv:
        try:
            n = int(sys.argv[sys.argv.index("--scan") + 1])
        except Exception:
            pass
    ingest(scan_count=n)


if __name__ == "__main__":
    main()
