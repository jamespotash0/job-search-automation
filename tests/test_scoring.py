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
    check("title" not in hits, "an off-target title scored")
    # The total is NOT zero, and should not be: a posting that states no years
    # and no round earns the neutral unknown fraction on those two dimensions,
    # because silence is not the same as a bad answer. What must be zero is
    # every dimension that depends on matching text.
    earned = {l for l in hits} - {"seniority fit", "stage"}
    check(not earned, f"innocent text earned a text-matched dimension: {earned}")


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
    # Worth less than a named round, more than nothing. Asserted as a relation,
    # not a magic number, so a reweighting does not have to edit this test.
    check(0 < pts < C.WEIGHTS["stage"], f"soft tells should be a partial bonus, got {pts}")


@case
def test_a_named_round_beats_the_soft_tells():
    """Hard and soft must never stack — the round is the better evidence."""
    bd = C.score_breakdown("Product Manager",
                           "Seed-stage and high-growth, shipping fast from the ground up.")
    label, pts = _stage_part(bd)
    equal(label, "early stage")
    equal(pts, C.WEIGHTS["stage"], "a named round should earn the full stage weight")


@case
def test_late_stage_suppresses_the_soft_tells():
    """A scale-up describes itself in exactly the same vocabulary."""
    bd = C.score_breakdown("Product Manager",
                           "A high-growth publicly traded company shipping fast "
                           "from the ground up.")
    label, pts = _stage_part(bd)
    equal(label, "late stage")
    # Under a weighted 0..1 model a zero IS the penalty: there is nothing below
    # "this dimension contributes nothing".
    equal(pts, 0, "late stage should earn nothing")


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


# ---------------------------------------------------------------- v2 scoring


@case
def test_two_spellings_of_one_skill_count_once():
    """A JD using "roadmap" and "roadmapping" wants one thing, not two. Without
    this a posting that repeats itself outscored one that named more skills."""
    jd = ("Own the roadmap and roadmapping, write PRDs and a PRD, "
          "work on UI and UX and ui/ux, drive go-to-market and GTM.")
    hits = C._hits(C._skill_re(), C._norm(jd))
    equal(sorted(hits), ["go-to-market", "prd", "roadmap", "ui"])


@case
def test_synonyms_never_rewrite_what_you_matched_on():
    """The email PRINTS these terms, so a mapping that changes meaning lies.
    Only spelling variants may collapse."""
    for near_synonym in ("real estate", "machine learning"):
        equal(C._canon(near_synonym), near_synonym,
              f"{near_synonym!r} must not be rewritten to something else")


@case
def test_verbosity_does_not_buy_points():
    """The defect that motivated v2: skills were presence-counted over the whole
    posting, so padding a JD raised its score. Measured corr(JD length, score)
    was +0.39 across 1,826 real postings."""
    core = ("Forward Deployed Product Manager. What you bring: 2+ years of "
            "experience, roadmap ownership, PRDs, Figma, SQL. Seed-stage AI "
            "workflow automation startup.")
    padded = core + " " + ("We value ownership, impact and craft. " * 120)
    a = C.score_breakdown("Forward Deployed Product Manager", core)["total"]
    b = C.score_breakdown("Forward Deployed Product Manager", padded)["total"]
    check(abs(a - b) <= 3, f"padding moved the score {a} -> {b}")


@case
def test_the_score_is_bounded_and_comparable():
    """A 240-char X/Grok lead and an 8,000-char ATS posting for the same role
    must be rankable against each other."""
    total = sum(C.WEIGHTS.values())
    equal(total, 100, "weights should sum to 100 so the score reads as a percent")
    bd = C.score_breakdown(
        "Forward Deployed Product Manager",
        "Seed-stage AI workflow startup. 2+ years. Roadmap, PRDs, Figma, SQL, "
        "discovery, prioritization.")
    check(0 <= bd["total"] <= total, f"score {bd['total']} outside 0..{total}")


@case
def test_seniority_has_no_dead_zone():
    """3-4 years used to score ZERO -- matching neither JUNIOR_SIGNALS (stopped
    at 2+) nor SENIOR_SIGNALS (started at 5+). Half the corpus scored 0 on the
    largest single signal, and the band closest to the candidate scored worst."""
    prev = None
    for yrs in (1, 2, 3, 4, 5, 6, 8, 10):
        f = C.screen_facts(f"We want {yrs}+ years of experience")
        frac, _ = C.seniority_fraction(f)
        check(frac is not None, f"{yrs}+ years produced no verdict")
        check(frac > 0, f"{yrs}+ years scored zero")
        if prev is not None:
            check(frac <= prev, f"fit should not RISE as the bar rises ({yrs})")
        prev = frac


@case
def test_a_stretch_is_ranked_down_but_never_gated_out():
    """The chosen posture: a stated bar is soft, especially at seed-Series B.
    Gating on it hid half the in-lane market."""
    f = C.screen_facts("We want 10+ years of experience")
    frac, _ = C.seniority_fraction(f)
    check(0 < frac < 0.5, f"a 10-year bar should rank low but stay visible: {frac}")


@case
def test_the_years_qualifier_picks_which_experience_counts():
    """"3+ years of product management experience" is compared against your PM
    years; "3+ years of experience" against your total. A reader assumes the
    first counts product roles only."""
    pm = C.screen_facts("3+ years of product management experience")
    gen = C.screen_facts("3+ years of professional experience")
    equal(pm.get("years_domain"), "pm")
    equal(gen.get("years_domain"), "general")
    check(C.seniority_fraction(pm)[0] <= C.seniority_fraction(gen)[0],
          "a PM-qualified bar should be at least as demanding as a general one")


@case
def test_the_first_qualifier_wins_when_a_sentence_names_both():
    """"10+ years of overall professional experience, with 5+ years in a product
    management role" states the GENERAL bar first. Reading the whole window
    mislabelled it as PM and compared 10 years against the PM number."""
    f = C.screen_facts("10+ years of overall professional experience, with 5+ "
                       "years in a product management role")
    equal(f.get("years_min"), 10)
    equal(f.get("years_domain"), "general")


@case
def test_title_score_does_not_double_count_overlapping_stems():
    """"Forward Deployed Engineer" matches two stems and "Product Manager" one,
    but both are exact target matches. The old scorer paid 24 vs 12 -- measuring
    the stem list, not the fit."""
    a = dict((l, p) for l, p, _ in
             C.score_breakdown("Forward Deployed Engineer", "")["parts"])["title"]
    b = dict((l, p) for l, p, _ in
             C.score_breakdown("Product Manager", "")["parts"])["title"]
    check(a <= b * 1.4, f"overlapping stems still inflate the title score: {a} vs {b}")
    check(b > 0, "an exact target title scored nothing")


@case
def test_hyphenated_titles_score_the_same_as_spaced_ones():
    """"Forward-Deployed Engineer" matched no stem at all -- scoring 0 AND being
    dropped by the gate."""
    for a, b in [("Forward-Deployed Engineer", "Forward Deployed Engineer"),
                 ("Forward-Deployed Product Manager", "Forward Deployed Product Manager")]:
        check(C.title_is_target(a), f"{a!r} was dropped by the gate")
        equal(C.score_breakdown(a, "")["total"], C.score_breakdown(b, "")["total"],
              f"{a!r} scored differently from {b!r}")


if __name__ == "__main__":
    main("scoring")
