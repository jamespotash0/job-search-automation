#!/usr/bin/env python3
"""
doctor.py — check this checkout is actually set up, before it wastes a run.

Every failure mode this catches is silent in production. A missing profile
secret does not error, it emails you somebody else's job search. An empty
watchlist does not error, it emails you three postings instead of thirty. A
stale compiled profile does not error, it quietly uses last month's filters.

    python doctor.py            # local + scheduled checks
    python doctor.py --local    # skip the GitHub checks (no gh needed)

Exit code 1 if anything is broken enough to give you wrong results.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OK, WARN, FAIL = "ok", "warn", "FAIL"
_MARK = {OK: "  ✓", WARN: "  !", FAIL: "  ✗"}
results = []


def check(level, what, detail="", fix=""):
    results.append((level, what, detail, fix))
    print(f"{_MARK[level]:>4} {what}" + (f" — {detail}" if detail else ""))
    if fix and level != OK:
        print(f"       fix: {fix}")


def _load(path, default=None):
    try:
        with open(os.path.join(ROOT, path)) as f:
            return json.load(f)
    except Exception:
        return default


def _env_file():
    """Keys present in .env, without reading their values."""
    keys = set()
    try:
        with open(os.path.join(ROOT, ".env")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if v.strip():
                        keys.add(k.strip())
    except FileNotFoundError:
        return None
    return keys


def local_checks():
    print("\nLocal runs")
    try:
        import feedparser  # noqa: F401
        check(OK, "dependencies installed")
    except ImportError:
        check(FAIL, "dependencies missing", "feedparser not importable",
              "pip install -r requirements.txt")

    env = _env_file()
    if env is None:
        check(WARN, ".env missing", "digests will print to stdout instead of sending",
              "cp .env.example .env   # then fill in EMAIL_USER / EMAIL_PASS / EMAIL_TO")
        env = set()
    else:
        missing = [k for k in ("EMAIL_USER", "EMAIL_PASS", "EMAIL_TO") if k not in env]
        if missing:
            check(WARN, "email not configured", f"missing {', '.join(missing)}",
                  "digests print to stdout until these are set — fine as a dry run")
        else:
            check(OK, "email configured")

    for key, what in (("ANTHROPIC_API_KEY", "AI summaries + discover.py"),
                      ("HUNTER_API_KEY", "verified founder emails"),
                      ("ADZUNA_APP_ID", "Adzuna job source"),
                      ("GROK_API_KEY", "X/Grok hiring posts")):
        alt = {"GROK_API_KEY": "XAI_API_KEY"}.get(key)
        have = key in env or (alt and alt in env) or os.environ.get(key)
        check(OK if have else WARN, f"{what}",
              "on" if have else f"off — {key} not set")

    # --- the profile, which is what actually decides your results
    prof = _load("profile.json")
    if not prof:
        check(FAIL, "profile.json missing",
              "digests will use the repo author's filters, not yours",
              "cp profile.example.json profile.json   # then edit it")
        return
    check(OK, "profile.json present")

    resume = prof.get("resume_file", "")
    if resume and not os.path.exists(os.path.join(ROOT, resume)):
        check(FAIL, "resume file missing", resume,
              "put your resume there, or fix resume_file in profile.json")
    else:
        check(OK, "resume found", resume)

    compiled_path = os.path.join(ROOT, "profile.compiled.json")
    compiled = _load("profile.compiled.json")
    if not compiled:
        check(FAIL, "profile.compiled.json missing",
              "the digests fall back to the repo author's filters",
              "python setup_profile.py")
        return
    # Stale beats missing for damage: it looks configured and is not.
    newer = [p for p in (prof.get("resume_file"), "profile.json")
             if p and os.path.exists(os.path.join(ROOT, p))
             and os.path.getmtime(os.path.join(ROOT, p)) > os.path.getmtime(compiled_path)]
    if newer:
        check(WARN, "compiled profile is stale",
              f"{', '.join(newer)} changed after it was compiled",
              "python setup_profile.py && python tests/check_profile.py")
    else:
        check(OK, "compiled profile is current",
              f"{len(compiled.get('target_titles', []))} titles")

    for field, why in (("years_pm", "PM-vs-general experience comparison"),
                       ("max_years", "experience-bar filter")):
        if compiled.get(field) is None:
            check(WARN, f"{field} not compiled", f"{why} falls back to a default",
                  f"add \"{field}\" to profile.json, then re-run setup_profile.py")

    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "check_profile.py")],
                       capture_output=True, text=True, cwd=ROOT)
    last = [l for l in r.stdout.strip().splitlines() if "labelled titles" in l]
    if r.returncode == 0:
        check(OK, "compiled filters classify real postings correctly",
              last[0].strip() if last else "")
    else:
        check(FAIL, "compiled filters misclassify real postings",
              last[0].strip() if last else "see below",
              "python tests/check_profile.py   # then widen `roles` in profile.json")

    wl = _load("ats_watchlist.json", {})
    tokens = sum(len(v) for k, v in wl.items() if isinstance(v, list) and k != "checked")
    if tokens < 20:
        check(WARN, "ATS watchlist nearly empty", f"{tokens} company boards known",
              "python getro.py   # bulk-fills it from public VC job boards")
    else:
        check(OK, "ATS watchlist populated", f"{tokens} company boards")


def github_checks():
    print("\nScheduled runs (GitHub Actions)")
    try:
        repo = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner",
                               "-q", ".nameWithOwner"],
                              capture_output=True, text=True, cwd=ROOT, timeout=25)
    except Exception:
        check(WARN, "gh CLI unavailable", "cannot check repo secrets",
              "install GitHub CLI, or check Settings → Secrets by hand")
        return
    if repo.returncode != 0:
        check(WARN, "no GitHub remote detected", "skipping secret checks")
        return
    check(OK, "repo", repo.stdout.strip())

    got = subprocess.run(["gh", "secret", "list", "-q", ".[].name", "--json", "name"],
                         capture_output=True, text=True, cwd=ROOT)
    have = set(got.stdout.split()) if got.returncode == 0 else set()
    if got.returncode != 0:
        check(WARN, "cannot list secrets", got.stderr.strip()[:80])
        return

    required = {
        "EMAIL_USER": "digests cannot send",
        "EMAIL_PASS": "digests cannot send",
        "EMAIL_TO": "digests cannot send",
        "PROFILE_COMPILED_JSON":
            "scheduled runs use the repo author's filters, NOT your profile.json",
    }
    optional = {
        "ANTHROPIC_API_KEY": "no AI summaries, discover.py disabled",
        "HUNTER_API_KEY": "contact emails stay pattern-guessed",
        "ADZUNA_APP_ID": "Adzuna source off", "ADZUNA_APP_KEY": "Adzuna source off",
        "GROK_API_KEY": "X/Grok source off",
    }
    for name, cost in required.items():
        if name in have:
            check(OK, f"secret {name}")
        else:
            fix = ("gh secret set PROFILE_COMPILED_JSON < profile.compiled.json"
                   if name == "PROFILE_COMPILED_JSON" else f"gh secret set {name}")
            check(FAIL, f"secret {name} missing", cost, fix)
    for name, cost in optional.items():
        if name == "GROK_API_KEY" and "XAI_API_KEY" in have:
            continue
        check(OK if name in have else WARN, f"secret {name}",
              "" if name in have else cost,
              "" if name in have else f"gh secret set {name}")

    branches = subprocess.run(["git", "ls-remote", "--heads", "origin", "state"],
                              capture_output=True, text=True, cwd=ROOT)
    if branches.stdout.strip():
        check(OK, "state branch exists", "dedupe + watchlist survive between runs")
    else:
        check(WARN, "no state branch yet",
              "created on the first scheduled run; until then every run starts "
              "with an empty seen-list, so the first digest will be large")


def main():
    local_only = "--local" in sys.argv
    print("doctor — checking this checkout")
    local_checks()
    if not local_only:
        github_checks()

    fails = sum(1 for lvl, *_ in results if lvl == FAIL)
    warns = sum(1 for lvl, *_ in results if lvl == WARN)
    print(f"\n{fails} broken, {warns} worth knowing, "
          f"{sum(1 for lvl, *_ in results if lvl == OK)} fine")
    if fails:
        print("Anything marked ✗ will give you wrong results silently.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
