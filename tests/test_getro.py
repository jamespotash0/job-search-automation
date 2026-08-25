#!/usr/bin/env python3
"""Getro token extraction — the bulk feeder for the ATS watchlist.

`extract_token` reads a company's ATS token out of a real apply URL, which is
why getro.py is more reliable than discover.py's guessing. But a wrong token is
not inert: it lands in ats_watchlist.json and the companies digest then fetches
that board three times a day, forever, getting nothing.

The docstring in getro.py records two such tokens that got through — a company
called "j" from `apply.workable.com/j/ABC123`, and "en-US" from Rippling's
localised paths. Both are pinned below.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("JOB_IGNORE_PROFILE", "1")   # test the code, not a local profile
sys.path.insert(0, os.path.dirname(HERE))

import getro as G                     # noqa: E402
import watchlist as W                 # noqa: E402
from harness import case, check, equal, main   # noqa: E402


@case
def test_extracts_the_token_from_each_supported_ats():
    cases = [
        ("https://boards.greenhouse.io/acmeai/jobs/4123", ("greenhouse", "acmeai")),
        ("https://job-boards.greenhouse.io/acmeai/jobs/41", ("greenhouse", "acmeai")),
        ("https://boards.greenhouse.io/embed/job_board?for=acmeai&token=1",
         ("greenhouse", "acmeai")),
        ("https://jobs.lever.co/acme-ai/abc-123", ("lever", "acme-ai")),
        ("https://jobs.ashbyhq.com/acme/9f8e7d", ("ashby", "acme")),
        ("https://apply.workable.com/acme-inc/j/ABC123/", ("workable", "acme-inc")),
        ("https://jobs.smartrecruiters.com/AcmeInc/744000", ("smartrecruiters", "AcmeInc")),
        ("https://ats.rippling.com/acme/jobs/1234-pm", ("rippling", "acme")),
    ]
    for url, want in cases:
        equal(G.extract_token(url), want, url)


@case
def test_smartrecruiters_keeps_its_case_others_lowercase():
    """SmartRecruiters tokens are case-SENSITIVE in the API path; the rest are
    not. Lowercasing a SmartRecruiters token yields a board that 404s forever."""
    equal(G.extract_token("https://jobs.smartrecruiters.com/AcmeInc/744")[1], "AcmeInc")
    equal(G.extract_token("https://JOBS.LEVER.CO/AcmeAI/abc")[1], "acmeai")


@case
def test_the_two_junk_tokens_that_actually_got_through():
    """Regression: these are named in getro.py's own docstring."""
    # `apply.workable.com/j/ABC123` has no account segment at all.
    equal(G.extract_token("https://apply.workable.com/j/ABC123"), None)
    # Rippling's localised path put the locale where the token goes.
    equal(G.extract_token("https://ats.rippling.com/en-US/jobs/123"), None)


@case
def test_stopwords_never_become_a_company():
    for tok in sorted(G.TOKEN_STOPWORDS):
        url = f"https://jobs.ashbyhq.com/{tok}/abc123"
        equal(G.extract_token(url), None, f"{tok!r} should be rejected")


@case
def test_non_ats_urls_return_nothing():
    for url in ["", None, "https://acme.com/careers", "https://linkedin.com/jobs/view/1",
                "https://www.indeed.com/viewjob?jk=abc", "not a url"]:
        equal(G.extract_token(url), None, repr(url))


@case
def test_headcount_buckets_are_not_headcounts():
    """Getro's `head_count` is a 1-6 SIZE BUCKET. Treating it as a literal count
    would rank a 15,000-person company as a 6-person startup — inverting exactly
    the preference the digest is built around."""
    b = G.HEADCOUNT_BUCKETS
    vals = [b[i] for i in sorted(b)]
    check(vals == sorted(vals), f"buckets must increase with the bucket id: {vals}")
    check(b[1] < 50 and b[6] > 5000, f"bucket range looks wrong: {b}")


@case
def test_harvest_pulls_tokens_and_sizes_off_real_job_shapes():
    jobs = [
        {"url": "https://jobs.ashbyhq.com/acme/1",
         "organization": {"name": "Acme AI", "head_count": 2}},
        {"url": "https://boards.greenhouse.io/foocorp/jobs/2",
         "organization": {"name": "Foo Corp", "head_count": 4}},
        {"url": "https://apply.workable.com/j/NOPE",          # junk token
         "organization": {"name": "Bar Ltd", "head_count": 1}},
        {"url": "https://acme.com/careers",                    # not an ATS
         "organization": {"name": "Baz", "head_count": None}},
    ]
    tokens, sizes = G.harvest(jobs)
    equal(tokens.get("ashby"), {"acme"})
    equal(tokens.get("greenhouse"), {"foocorp"})
    check("workable" not in tokens, f"junk workable token harvested: {tokens}")
    # A size is still recorded for a company whose URL yielded no token — the
    # two are independent, and the size is useful the moment any board matches.
    equal(sizes.get("bar ltd"), G.HEADCOUNT_BUCKETS[1])
    check("baz" not in sizes, "a null head_count must not become a size")


@case
def test_every_provider_getro_emits_is_one_the_watchlist_stores():
    """getro.py extracts six providers; watchlist.PROVIDERS must hold all six or
    add_tokens silently discards a whole ATS."""
    emitted = {p for p, _ in G.TOKEN_PATTERNS}
    missing = emitted - set(W.PROVIDERS)
    check(not missing, f"watchlist cannot store tokens for: {missing}")


@case
def test_every_stored_provider_has_a_fetcher_in_the_digest():
    """...and the reverse: a token nothing fetches is a board never read."""
    import companies_digest as C
    for prov in W.PROVIDERS:
        check(hasattr(C, f"fetch_{prov}"),
              f"watchlist stores {prov!r} tokens but companies_digest has no "
              f"fetch_{prov}() to read them")


if __name__ == "__main__":
    main("getro")
