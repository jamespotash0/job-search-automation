#!/usr/bin/env python3
"""The role and location gates, against real board strings.

These two functions decide what you ever see. Every bug found in them so far was
silent — a posting that should have appeared simply didn't, and nothing in the
output said so. Fixtures are real titles/locations captured from the live boards;
the labels are the INTENDED behaviour, so a regression fails here rather than
quietly re-recording itself.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("JOB_IGNORE_PROFILE", "1")   # test the code, not a local profile
sys.path.insert(0, os.path.dirname(HERE))

import companies_digest as C          # noqa: E402
from harness import case, check, equal, main   # noqa: E402

FIX = json.load(open(os.path.join(HERE, "fixtures", "postings.json")))


@case
def test_role_gate_matches_labels():
    wrong = []
    for f in FIX["titles"]:
        got = C.title_is_target(f["title"])
        if got != f["keep"]:
            wrong.append(f"{'kept' if got else 'dropped'} {f['title']!r} "
                         f"(expected {'keep' if f['keep'] else 'drop'} - {f['why']})")
    check(not wrong, f"{len(wrong)} of {len(FIX['titles'])} titles misclassified:\n"
                     + "\n        ".join(wrong))


@case
def test_location_tiers_match_labels():
    wrong = []
    for f in FIX["locations"]:
        got = C.location_tier(f["location"], "")
        if got != f["tier"]:
            wrong.append(f"{f['location']!r}: got {got or 'DROP'!r}, "
                         f"want {f['tier'] or 'DROP'!r}")
    check(not wrong, f"{len(wrong)} of {len(FIX['locations'])} locations misclassified:\n"
                     + "\n        ".join(wrong))


@case
def test_gtm_and_growth_titles_are_out():
    """Removed on request 2026-08-24. They were in the target list for a month,
    so a careless profile recompile could put them back."""
    for t in ["GTM Associate, AI Product", "Growth Associate",
              "Revenue Operations Manager", "Head of Go-To-Market"]:
        check(not C.title_is_target(t), f"{t!r} should not pass the role gate")


@case
def test_apostrophe_variants_all_match():
    """Job boards punctuate this title every possible way."""
    for t in ["Founder's Associate", "Founder’s Associate", "Founders Associate",
              "Founding Associate, Product", "Founder's Office"]:
        check(C.title_is_target(t), f"{t!r} should pass the role gate")


@case
def test_bare_product_engineer_is_not_a_target():
    """Deliberate: at most startups that title is a full-stack req. Only the
    associate variant is in the target list."""
    check(not C.title_is_target("Product Engineer"), "bare Product Engineer kept")
    check(not C.title_is_target("Senior Product Engineer"), "senior variant kept")
    check(C.title_is_target("Associate Product Engineer"), "associate variant dropped")


@case
def test_seniority_words_win_over_a_target_stem():
    for t in ["Senior Forward Deployed Engineer", "Staff Product Manager",
              "Principal Product Manager", "VP of Product", "Head of Product",
              "Product Lead", "GTM Lead", "Director, Product Management"]:
        check(not C.title_is_target(t), f"{t!r} is too senior but passed")


@case
def test_nyc_beats_a_second_city_in_the_same_string():
    """A posting listing both should rank as NYC, not SF."""
    equal(C.location_tier("New York, NY or San Francisco, CA", ""), "nyc")
    equal(C.location_tier("San Francisco, CA or Austin, TX", ""), "secondary")


@case
def test_state_scoped_remote_is_not_us_remote():
    """The distinction the state rule exists for: 'Remote, TX' means Texas
    RESIDENTS and is a drop, while 'Austin, TX' is an on-site US role that is
    kept and ranked last. Same state token, opposite outcomes."""
    for l in ["Remote, TX", "Remote, Texas", "Remote - California", "Remote, Oregon"]:
        equal(C.location_tier(l, ""), "", f"{l} should drop")
    for l in ["Austin, TX", "Nashville, TN", "Boulder, CO"]:
        equal(C.location_tier(l, ""), "us-other", f"{l} should be kept as us-other")
    for l in ["Remote, United States", "Remote (US)", "Remote", "United States"]:
        equal(C.location_tier(l, ""), "us-remote", f"{l} should be US-remote")


@case
def test_two_letter_state_abbreviations_do_not_eat_english_words():
    """The reason the abbreviation list omits OR/IN/ME/HI/OK/DE/LA/PA/ID: a false
    positive here silently drops a real posting."""
    equal(C.location_tier("Remote (US) or hybrid", ""), "us-remote")
    equal(C.location_tier("New York, NY - in office", ""), "nyc")


@case
def test_body_text_fallback_when_no_location_field():
    """HN 'Who's Hiring' comments have no location field."""
    equal(C.location_tier("", "We are a startup in New York City hiring a PM"), "nyc")
    equal(C.location_tier("", "SF-based team, come work in San Francisco"), "secondary")
    equal(C.location_tier("", "Fully remote role, US only"), "us-remote")
    equal(C.location_tier("", "Our office is in Berlin, Germany"), "")


@case
def test_sf_substring_does_not_fire_inside_a_word():
    """'sf' is a secondary-location keyword; it must not match Dusseldorf."""
    equal(C.location_tier("Dusseldorf, Germany", ""), "")


if __name__ == "__main__":
    main("gates")
