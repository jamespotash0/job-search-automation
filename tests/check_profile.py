#!/usr/bin/env python3
"""Validate the compiled profile ON THIS MACHINE against the labelled fixtures.

`tests/run.py` deliberately ignores profile.compiled.json — it tests the code,
and a personal profile would make it test something different here than in CI.
But the compiled profile is exactly what production runs on, and it is written
by a model from a prose sentence, so it drifts. This is the other half: run the
real gate, with the real profile, over the same 34 hand-labelled postings.

    python tests/check_profile.py

Exit code 1 if the profile misclassifies anything. Run it after every
`setup_profile.py`, before pushing PROFILE_COMPILED_JSON.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.pop("JOB_IGNORE_PROFILE", None)      # the opposite of the test suite
sys.path.insert(0, ROOT)

import companies_digest as C          # noqa: E402

FIX = json.load(open(os.path.join(HERE, "fixtures", "postings.json")))


def main():
    path = os.path.join(ROOT, "profile.compiled.json")
    if not os.path.exists(path):
        print("no profile.compiled.json — nothing to check "
              "(run: python setup_profile.py)")
        return 0
    p = json.load(open(path))
    print(f"[profile] {len(p.get('target_titles', []))} titles, "
          f"{len(p.get('skills', []))} skills, {len(p.get('domains', []))} domains, "
          f"years={p.get('years')} years_pm={p.get('years_pm')} "
          f"max_years={p.get('max_years')}\n")

    bad = []
    for f in FIX["titles"]:
        got = C.title_is_target(f["title"])
        if got != f["keep"]:
            bad.append((f, got))
    for f, got in bad:
        verb = "KEEPS" if got else "DROPS"
        print(f"  {verb:5} {f['title']!r}")
        print(f"        expected {'keep' if f['keep'] else 'drop'} — {f['why']}")

    # A stem that matches nothing is dead weight; a stem so short it matches
    # everything is worse. Both are things a model-written list does.
    titles = p.get("target_titles", [])
    stray = [t for t in titles if len(t) < 4 and t not in ("apm", "pm")]
    if stray:
        print(f"  suspiciously short stems (match too much): {stray}")

    print(f"\n{len(FIX['titles']) - len(bad)}/{len(FIX['titles'])} labelled titles "
          f"classified correctly")
    if bad:
        print("\nThe compiled profile is what production uses. Widen the `roles` "
              "sentence in profile.json and re-run setup_profile.py.")
    return 1 if bad or stray else 0


if __name__ == "__main__":
    sys.exit(main())
