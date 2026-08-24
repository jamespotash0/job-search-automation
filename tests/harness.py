#!/usr/bin/env python3
"""A ~60-line test runner, so the suite has no dependency beyond the stdlib.

The repo's whole install is `pip install feedparser`; adding pytest to run forty
assertions would triple that for no benefit. Usage:

    python tests/run.py            # everything
    python tests/run.py gates      # just the files matching "gates"
"""
import sys
import traceback

_CASES = []


def case(fn):
    """Decorator: register a test function. Name it test_<thing>."""
    _CASES.append(fn)
    return fn


class Failure(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Failure(msg)


def equal(got, want, msg=""):
    if got != want:
        raise Failure(f"{msg + ': ' if msg else ''}got {got!r}, want {want!r}")


def run(label=""):
    """Run every registered case. Returns the number of failures."""
    passed, failed = 0, []
    for fn in _CASES:
        name = fn.__name__.replace("test_", "").replace("_", " ")
        try:
            fn()
            passed += 1
        except Failure as e:
            failed.append((name, str(e)))
        except Exception:
            failed.append((name, traceback.format_exc().strip().splitlines()[-1]))
    for name, err in failed:
        print(f"  FAIL  {name}\n        {err}")
    total = passed + len(failed)
    mark = "ok" if not failed else "FAILED"
    print(f"[{mark}] {label or 'tests'}: {passed}/{total} passed")
    return len(failed)


def main(label=""):
    sys.exit(1 if run(label) else 0)
