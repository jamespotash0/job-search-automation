#!/usr/bin/env python3
"""Scoring, and the substring traps it keeps falling into.

The digest's oldest recurring bug is matching a short keyword as a substring:
"ai" inside "em-ai-l", "ui" inside "b-ui-lding", "api" inside "c-api-tal", "git"
inside "di-git-al". Each one handed nearly every posting free points until the
email started printing which terms actually hit.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import companies_digest as C          # noqa: E402
from harness import case, check, equal, main   # noqa: E402

# Deliberately full of words that CONTAIN resume keywords without being them.
INNOCENT = ("We are building a digital platform for capital markets. Email us. "
            "Our guidelines require rapid iteration and a certain flair. "
            "Maintain the chain of custody. Detail matters.")


@case
def test_short_keywords_do_not_match_inside_words():
    bd = C.score_breakdown("Office Manager", INNOCENT)
    hits = {label: terms for label, _, terms in bd["parts"]}
    check(not hits.get("resume skills"),
          f"phantom skill hits in innocent text: {hits.get('resume skills')}")
    check(not hits.get("your domains"),
          f"phantom domain hits in innocent text: {hits.get('your domains')}")
    equal(bd["total"], 0, "innocent text should score nothing")


@case
def test_genuine_keywords_still_match():
    bd = C.score_breakdown(
        "Associate Product Manager, AI",
        "2+ years. Own the roadmap and PRDs, work cross-functional in Figma and SQL. "
        "Seed-stage B2B SaaS workflow automation for real estate.")
    hits = {label: terms for label, _, terms in bd["parts"]}
    for want in ("roadmap", "figma", "sql"):
        check(want in hits.get("resume skills", []), f"missed skill {want!r}")
    for want in ("ai", "workflow", "automation", "real estate"):
        check(want in hits.get("your domains", []), f"missed domain {want!r}")
    check(bd["total"] > 60, f"expected a strong score, got {bd['total']}")


@case
def test_breakdown_parts_sum_to_the_total():
    """The email prints the parts AND the total; they must agree or the 'Why 83'
    line is lying."""
    for title, body in [("Product Manager", "3+ years, seed stage, AI workflow tools"),
                        ("Forward Deployed Engineer", "LLM agents at enterprise customers"),
                        ("Office Manager", INNOCENT)]:
        bd = C.score_breakdown(title, body)
        equal(sum(p[1] for p in bd["parts"]), bd["total"], f"{title!r} parts vs total")


@case
def test_qualification_score_agrees_with_the_breakdown():
    """qualification_score is kept as a thin wrapper; it must not drift."""
    for title, body in [("Product Manager", "3+ years at a seed-stage AI startup"),
                        ("Deployment Strategist", "Deploy agents onsite in NYC")]:
        equal(C.qualification_score(title, body), C.score_breakdown(title, body)["total"])


@case
def test_senior_requirement_is_penalised():
    junior = C.score_breakdown("Product Manager", "We want 2+ years of experience")
    senior = C.score_breakdown("Product Manager", "We want 8+ years of experience")
    check(senior["total"] < junior["total"],
          f"senior {senior['total']} should score below junior {junior['total']}")


@case
def test_location_and_funding_are_ranking_not_gating():
    """Both bonuses are applied AFTER the total<=0 cut, so neither can resurrect
    an off-resume posting. This asserts the constants that make that true."""
    equal(C.LOC_BONUS["nyc"], 10)
    # NYC first; then a US-remote role (doable from Brooklyn) ahead of SF/Bay
    # (which means relocating); a non-NYC US on-site role ranks last but is still
    # shown. Every acceptable tier must score above the ""/drop tier.
    order = ["nyc", "us-remote", "secondary", "us-other"]
    vals = [C.LOC_BONUS[t] for t in order]
    check(vals == sorted(vals, reverse=True),
          f"location tiers out of order: {list(zip(order, vals))}")
    check(min(vals) > C.LOC_BONUS[""], "every kept tier must outrank a drop")
    check(C.FUNDED_BONUS > 0, "a fresh raise should help, not hurt")


@case
def test_best_match_threshold_actually_splits_the_email():
    """BEST_MATCH_MIN is a tuning knob (JOB_BEST_MATCH_MIN), so its exact value
    is the owner's call and pinning the number would just create friction. What
    must hold is that it is positive and that build_html really splits on it —
    a threshold nothing branches on is a threshold that silently stopped working."""
    check(C.BEST_MATCH_MIN > 0, f"threshold must be positive, got {C.BEST_MATCH_MIN}")
    def item(q):
        return {"title": "Product Manager", "company": "acme", "url": "http://x",
                "location": "New York, NY", "source": "Ashby", "posted_ts": None,
                "q": q, "loc_tier": "nyc", "funding": {}, "facts": {},
                "breakdown": {"parts": [("title", 12, ["product manager"])], "total": 12}}
    html = C.build_html([item(C.BEST_MATCH_MIN), item(C.BEST_MATCH_MIN - 1)])
    check("Best matches" in html, "no Best matches section rendered")
    check("Worth a look" in html, "below-threshold posting was not put in its own section")


def _stage_part(bd):
    for label, pts, terms in bd["parts"]:
        if label.startswith(("early stage", "late stage")):
            return label, pts
    return None, 0


@case
def test_one_soft_stage_tell_earns_nothing():
    """"We move fast" is in half the postings on earth. A lone soft tell must
    not pay, or the bonus stops discriminating between anything."""
    bd = C.score_breakdown("Product Manager", "We ship fast. 10 years of history.")
    label, pts = _stage_part(bd)
    check(label is None, f"a single soft tell paid out: {label} {pts:+d}")


@case
def test_two_soft_stage_tells_earn_the_half_bonus():
    bd = C.score_breakdown("Product Manager",
                           "A high-growth team shipping fast, building from the ground up.")
    label, pts = _stage_part(bd)
    equal(label, "early stage (soft)")
    equal(pts, 3)


@case
def test_a_named_round_beats_the_soft_tells():
    """Hard and soft must never stack — the round is the better evidence."""
    bd = C.score_breakdown("Product Manager",
                           "Seed-stage and high-growth, shipping fast from the ground up.")
    label, pts = _stage_part(bd)
    equal(label, "early stage")
    equal(pts, 6)


@case
def test_late_stage_suppresses_the_soft_tells():
    """A scale-up describes itself in exactly the same vocabulary."""
    bd = C.score_breakdown("Product Manager",
                           "A high-growth publicly traded company shipping fast "
                           "from the ground up.")
    label, pts = _stage_part(bd)
    equal(label, "late stage")
    equal(pts, -6)


@case
def test_ipo_does_not_match_inside_a_longer_word():
    """The late list is word-boundary matched now that "ipo" is on it."""
    bd = C.score_breakdown("Product Manager", "We build apps for the ipod and beyond.")
    label, _ = _stage_part(bd)
    check(label != "late stage", "'ipo' matched inside 'ipod'")


@case
def test_stage_points_rank_the_rounds_against_your_band():
    """Your band is seed-Series B. C is neutral, D+ is a penalty."""
    for r in ("Series A", "seed", "Pre-Seed", "Series B"):
        equal(C.stage_points(r), 6)
    equal(C.stage_points("Series C"), 0)
    for r in ("Series D", "IPO", "acquired"):
        equal(C.stage_points(r), -6)
    check(C.stage_points("") is None, "an unknown round must not score")
    check(C.stage_points(None) is None, "a missing round must not score")


# ---------------------------------------------------------------- misses
# A score is only half an explanation. These guard the other half: the signals
# that scored NOTHING, which are what actually separate a 45 from a 70.


@case
def test_misses_name_the_signals_that_did_not_fire():
    bd = C.score_breakdown("Product Manager", "We sell boots. Apply within.")
    got = {label for label, _ in bd["misses"]}
    for want in ("seniority", "skills", "domains", "stage"):
        check(want in got, f"missing miss {want!r}; got {got}")
    for _, reason in bd["misses"]:
        check(reason and reason[0].islower(), f"reason should read as a clause: {reason!r}")


@case
def test_a_signal_is_never_both_a_hit_and_a_miss():
    bd = C.score_breakdown(
        "Associate Product Manager",
        "2+ years. Own the roadmap and PRDs in Figma and SQL. Seed-stage AI workflow tools.")
    hits = {label for label, _, _ in bd["parts"]}
    misses = {label for label, _ in bd["misses"]}
    # The two vocabularies differ on purpose ("resume skills" vs "skills"), so
    # compare on the shared concept rather than the raw label.
    alias = {"resume skills": "skills", "your domains": "domains",
             "seniority fit": "seniority", "early stage": "stage",
             "early stage (soft)": "stage", "late stage": "stage"}
    for h in hits:
        check(alias.get(h, h) not in misses,
              f"{h!r} reported as both a hit and a miss")


@case
def test_misses_are_worth_zero_and_stay_out_of_the_arithmetic():
    """The email prints parts AND misses; only parts may move the total."""
    bd = C.score_breakdown("Product Manager", "We sell boots.")
    equal(sum(p[1] for p in bd["parts"]), bd["total"], "misses leaked into the total")


@case
def test_strength_label_measures_against_the_cap():
    equal(C.strength_label("resume skills", C.SKILL_MAX), "strong skills match")
    equal(C.strength_label("resume skills", 3), "partial skills match")
    equal(C.strength_label("your domains", C.DOMAIN_MAX // 2), "good domain match")
    # Signals with no ceiling keep their bare label.
    equal(C.strength_label("seniority fit", 20), "seniority fit")
    equal(C.strength_label("just raised", 8), "just raised")


@case
def test_the_why_line_shows_both_hits_and_gaps():
    bd = C.score_breakdown("Product Manager", "2+ years. Roadmap and PRDs.")
    line = C.match_line({"breakdown": bd, "q": bd["total"]})
    check("Why" in line, "no 'Why N' header")
    check("seniority fit" in line, "hits missing from the why line")
    check("no funding stage stated" in line, f"gaps missing from the why line: {line}")


@case
def test_ai_native_skills_are_matched_but_not_as_substrings():
    """The vocabulary a 2026 product req actually uses."""
    bd = C.score_breakdown(
        "Product Manager",
        "Use Claude Code to ship agentic features; own the spec, QA and "
        "multivariate landing page experiments.")
    hits = {label: terms for label, _, terms in bd["parts"]}.get("resume skills", [])
    for want in ("claude code", "agentic", "spec", "qa", "multivariate"):
        check(want in hits, f"missed {want!r}; got {hits}")
    # ...and the traps that come with them.
    innocent = C.score_breakdown(
        "Office Manager",
        "Claudette inspects the specimen per specification in Qatar. "
        "Experimental landings for multivariates.")
    check(not [t for _, _, terms in innocent["parts"] for t in terms],
          f"phantom hits: {innocent['parts']}")


if __name__ == "__main__":
    main("scoring")
