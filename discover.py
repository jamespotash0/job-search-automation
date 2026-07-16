#!/usr/bin/env python3
"""
discover.py — find companies hiring YOUR roles from the open web (LinkedIn
"we're hiring" posts, "who is hiring" articles/threads, hiring-roundup
newsletters) and auto-add the ones on a public ATS to the shared watchlist, so
the job digest starts scoring their roles — often the same day.

How: Claude + web search (same mechanism as enrich.py). LinkedIn itself can't be
scraped (it bans bots), but web search surfaces the public posts/articles that
NAME who's hiring. We extract company names and probe their Greenhouse / Lever /
Ashby boards via watchlist.py; hits land in ats_watchlist.json, which the job
digest already reads.

Run it before the job digest (e.g. once/day). Requires ANTHROPIC_API_KEY.
Detection is cached per company (watchlist "checked" list) so nothing is
re-probed, and no company is added twice.
"""

import os
import re
import json
import urllib.request

import watchlist as wl

MAX_COMPANIES = int(os.environ.get("DISCOVER_MAX", "30"))


def load_dotenv(path=".env"):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


def _profile():
    try:
        with open("profile.compiled.json") as f:
            return json.load(f)
    except Exception:
        return {}


def discover(roles, locations):
    """Ask Claude (with web search) for companies currently hiring these roles."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")   # read after load_dotenv()
    model = os.environ.get("DISCOVER_MODEL", os.environ.get("AI_MODEL", "claude-sonnet-5"))
    if not api_key:
        print("[discover] ANTHROPIC_API_KEY not set — skipping")
        return []
    loc = ", ".join(locations) if locations else "anywhere"
    prompt = (
        f"Use web search to find companies that are CURRENTLY hiring for roles like: {roles}. "
        f"Focus on {loc}. Strongly prefer small / early-stage startups (seed to Series B); "
        "exclude large public companies. Look especially at:\n"
        "- LinkedIn posts where founders/employees say 'we're hiring' / 'my team is hiring'\n"
        "- 'who is hiring' articles, threads, and hiring-roundup newsletters (last ~3 weeks)\n"
        "- startup job boards and recently-funded-company lists\n"
        "Return ONLY a JSON array (no prose, no code fences) of objects:\n"
        '[{"name":"Company Name","domain":"company.com"}]\n'
        f"Up to {MAX_COMPANIES} companies. Use \"\" for a domain you cannot determine. "
        "Do NOT include software-engineering-only shops; focus on companies with "
        "product / deployment / solutions / GTM roles."
    )
    body = {
        # max_tokens must be generous: interleaved thinking + web-search results
        # eat the budget, and a too-small cap stops the turn before the final JSON.
        "model": model,
        "max_tokens": 6000,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    }
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json", "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=300) as r:   # web search is slow
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"[discover] web search failed: {e}")
        return []
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        print("[discover] no company list returned")
        return []
    try:
        comps = json.loads(m.group(0))
    except Exception:
        print("[discover] could not parse company list")
        return []
    return comps[:MAX_COMPANIES]


def main():
    load_dotenv()
    p = _profile()
    roles = ", ".join(p.get("target_titles", [])) or (
        "product manager, associate product manager, AI product manager, product "
        "operations, product strategy, forward deployed engineer, deployment "
        "strategist, AI strategist, solutions engineer, implementation")
    locations = p.get("location_keywords") or ["new york"]

    comps = discover(roles, locations)
    print(f"[discover] {len(comps)} candidate companies from web search")
    added = []
    for c in comps:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        try:
            found = wl.detect_and_add(name, (c.get("domain") or "").strip() or None)
        except Exception as e:
            print(f"   {name}: probe error {e}")
            continue
        if found:
            added += found
        print(f"   {name[:30]:30} -> {found or '— no public ATS token found'}")
    print(f"[discover] added {len(added)} ATS token(s) to {wl.WATCHLIST_FILE}; "
          f"the job digest will pick them up on its next run.")


if __name__ == "__main__":
    main()
