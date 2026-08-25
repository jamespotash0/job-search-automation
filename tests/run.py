#!/usr/bin/env python3
"""Run every test module in tests/. `python tests/run.py` from the repo root."""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    # Pin the subject under test to the repo defaults. A personal
    # profile.compiled.json on disk would otherwise silently replace the target
    # titles, skills and domains, so the suite would be testing one thing here
    # and a different thing in CI.
    os.environ["JOB_IGNORE_PROFILE"] = "1"
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    mods = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    mods = [m for m in mods if only in os.path.basename(m)]
    if not mods:
        print(f"no test modules matching {only!r}")
        return 1
    failed = 0
    for m in mods:
        r = subprocess.run([sys.executable, m], cwd=ROOT)
        failed += 1 if r.returncode else 0
    print(f"\n{len(mods) - failed}/{len(mods)} module(s) green")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
