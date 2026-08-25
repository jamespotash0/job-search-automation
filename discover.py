#!/usr/bin/env python3
"""
discover.py — find companies hiring YOUR roles from the open web (LinkedIn
"we're hiring" posts, "who is hiring" articles/threads, hiring-roundup
newsletters) and auto-add the ones on a public ATS to the shared watchlist, so
the companies digest starts scoring their roles — often the same day.

How: Claude + web search (same mechanism as enrich.py). LinkedIn itself can't be
scraped (it bans bots), but web search surfaces the public posts/articles that
NAME who's hiring. We extract company names and probe their Greenhouse / Lever /
Ashby boards via watchlist.py; hits land in ats_watchlist.json, which the job
digest already reads.

Run it before the companies digest (e.g. once/day). Requires an LLM key --
ANTHROPIC_API_KEY or OPENAI_API_KEY; see llm.py.
Detection is cached per company (watchlist "checked" list) so nothing is
re-probed, and no company is added twice.
"""

import os
import re
import json
import urllib.request

import llm as _llm
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


# Two complementary search angles. The general one sweeps "who's hiring"; the
# funded one works the other direction — start from companies that just closed a
# round, then check whether the hiring that round pays for has started. The
# funding digest only sees a raise the day it's announced; this catches the
# company three weeks later, when the reqs actually go up.
ANGLES = {
    "hiring": (
        "- LinkedIn posts where founders/employees say 'we're hiring' / 'my team is hiring'\n"
        "- X/Twitter 'we're hiring' posts from founders and early employees\n"
        "- 'who is hiring' articles, threads, and hiring-roundup newsletters (last ~3 weeks)\n"
        "- Ashby / Greenhouse / Lever public job-board listings for these titles in the area\n"
    ),
    "funded": (
        "- startups that announced a pre-seed/seed/Series A/Series B round in the LAST 90 DAYS "
        "and are NOW hiring — check their careers page or ATS board, not just the funding news\n"
        "- 'X raises $Y' coverage from TechCrunch / Axios Pro Rata / Fortune Term Sheet / "
        "Business Insider, then the company's open roles\n"
        "- recent YC / a16z / Sequoia / First Round / Union Square Ventures portfolio additions\n"
        "- 'newly funded startups hiring' roundups and VC talent-page job boards "
        "(e.g. jobs.a16z.com, jobs.gv.com, USV, Bessemer, Lerer Hippeau)\n"
    ),
}


def discover(roles, locations, exclude=None, angle="hiring", secondary=None):
    """Ask Claude (with web search) for companies currently hiring these roles.

    `exclude` is the list of companies we've already probed — passed into the
    prompt so web search spends its slots on genuinely NEW companies instead of
    re-surfacing names we'd only skip. `angle` picks which set of search
    surfaces to sweep (see ANGLES). Raises on a hard failure (no key, web
    search error, unparseable response) so the caller can flag the run.
    """
    # Provider-agnostic: whichever of ANTHROPIC_API_KEY / OPENAI_API_KEY is set.
    if not _llm.provider():
        raise RuntimeError("no LLM provider configured — set ANTHROPIC_API_KEY "
                           "or OPENAI_API_KEY")
    # DISCOVER_MODEL overrides for this call only; unset means the provider
    # default in llm.py. Deliberately NOT defaulted to a Claude model name here,
    # which would be sent to OpenAI verbatim on an OpenAI-configured fork.
    model = os.environ.get("DISCOVER_MODEL") or None
    loc = ", ".join(locations) if locations else "anywhere"
    loc_line = f"Focus on {loc}"
    if secondary:
        loc_line += (f" — that is the priority. Also include {', '.join(secondary)} "
                     "companies, but only after you've exhausted the primary area")
    # Cap the exclusion list so it can't balloon the prompt as `checked` grows;
    # the most-recently-added names are the ones web search is likeliest to hit.
    exclude = list(exclude or [])[-80:]
    skip_line = (
        "SKIP these companies — we've already checked them, do not return any of "
        f"them: {', '.join(exclude)}.\n" if exclude else "")
    prompt = (
        f"Use web search to find companies that are CURRENTLY hiring for roles like: {roles}. "
        f"{loc_line}. Strongly prefer small / early-stage startups (seed to Series B); "
        "exclude large public companies. Search several DIFFERENT angles (don't stop after "
        "one) — spend your searches across:\n"
        + ANGLES.get(angle, ANGLES["hiring"])
        + skip_line +
        "Return ONLY a JSON array (no prose, no code fences) of objects:\n"
        '[{"name":"Company Name","domain":"company.com"}]\n'
        f"Up to {MAX_COMPANIES} companies. Use \"\" for a domain you cannot determine. "
        "Do NOT include software-engineering-only shops; focus on companies with "
        "product / forward-deployed / product-operations roles."
    )
    # max_tokens must be generous: interleaved thinking + web-search results eat
    # the budget, and a too-small cap stops the turn before the final JSON.
    # More search slots = more distinct angles covered per run (cost is a few
    # cents; this is the loop that feeds the whole companies digest).
    try:
        text = _llm.research(prompt, max_tokens=8000, max_uses=8, timeout=300,
                             model=model or None)
    except Exception as e:
        raise RuntimeError(f"web search failed: {e}")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise RuntimeError("no company list returned")
    try:
        comps = json.loads(m.group(0))
    except Exception:
        raise RuntimeError("could not parse company list")
    return comps[:MAX_COMPANIES]


STATUS_FILE = "discover_status.json"


def _write_status(**kw):
    """Leave a breadcrumb the funding digest reads to banner today's run.

    Written every run (success OR failure) so a silent outage still shows up in
    the 8am email instead of looking identical to a quiet day. Ephemeral within
    the CI job — not persisted to the state branch."""
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(kw, f)
    except Exception as e:
        print(f"[discover] could not write status: {e}")


def main():
    load_dotenv()
    p = _profile()
    roles = ", ".join(p.get("target_titles", [])) or (
        "product manager, associate product manager, AI product manager, technical "
        "product manager, founding product manager, product owner, product "
        "operations, product strategy, forward deployed engineer, forward deployed "
        "product manager, associate forward deployed engineer, associate product "
        "engineer, deployment strategist, founder's associate, founding associate, "
        "AI product builder")
    locations = p.get("location_keywords") or ["new york"]
    # Secondary metro: searched too, ranked behind NYC. Mirrors the companies
    # digest's JOB_SECONDARY_LOCATIONS gate — no point discovering companies
    # whose postings that gate would drop.
    secondary = [x.strip() for x in os.environ.get(
        "DISCOVER_SECONDARY_LOCATIONS", "san francisco,bay area").split(",") if x.strip()]

    # Run both angles: who's-hiring, and just-raised-and-now-hiring. Each is its
    # own web-search budget, so the funded sweep doesn't compete for slots with
    # the general one — that competition is why funded companies used to get one
    # search's worth of attention at most.
    angles = [a.strip() for a in os.environ.get(
        "DISCOVER_ANGLES", "hiring,funded").split(",") if a.strip()]
    comps, errors, seen_names = [], [], set()
    for angle in angles:
        try:
            got = discover(roles, locations, exclude=wl.get_checked(),
                           angle=angle, secondary=secondary)
        except Exception as e:
            print(f"[discover] angle '{angle}' failed: {e}")
            errors.append(f"{angle}: {e}")
            continue
        fresh = [c for c in got
                 if (c.get("name") or "").lower().strip() not in seen_names]
        seen_names.update((c.get("name") or "").lower().strip() for c in got)
        comps += fresh
        print(f"[discover] angle '{angle}': {len(got)} candidates ({len(fresh)} new)")

    if not comps and errors:
        # Every angle hard-failed (no key / web search down / unparseable).
        # Record it and exit non-zero so the run is flagged, not swallowed.
        print(f"[discover] FAILED: {'; '.join(errors)}")
        _write_status(ok=False, error="; ".join(errors), probed=0, added=0,
                      watchlist_total=wl.token_count())
        raise SystemExit(1)

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

    # Give the board-less companies from earlier runs another look — a startup
    # that had no public ATS when we first probed it often opens one weeks later.
    try:
        wl.reprobe_pending()
    except Exception as e:
        print(f"[discover] re-probe failed: {e}")

    total = wl.token_count()
    added_names = [f"{prov}:{tok}" for prov, tok in added]
    print(f"[discover] added {len(added)} ATS token(s) to {wl.WATCHLIST_FILE}; "
          f"the companies digest will pick them up on its next run.")
    _write_status(ok=True, error="", probed=len(comps), added=len(added),
                  added_names=added_names, watchlist_total=total)


if __name__ == "__main__":
    main()
