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
os.environ.setdefault("JOB_IGNORE_PROFILE", "1")   # test the code, not a local profile
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
def test_location_and_funding_award_no_points():
    """Both are preferences. location_tier() keeps or drops and the tier is shown
    as a chip; a recent raise is shown as a badge. Neither may move the score --
    a NYC posting that wants ten years is still a role you will not get, and a
    location bonus made exactly that kind of posting look competitive."""
    check(not hasattr(C, "LOC_BONUS") or not any(C.LOC_BONUS.values()),
          "location still awards points")
    equal(C.FUNDED_BONUS, 0, "a recent raise still moves the score")
    # ...but both must still be usable as filters / labels.
    equal(C.location_tier("New York, NY", ""), "nyc")
    check("NYC" in C.LOC_LABEL.values(), "the location tier label is gone")


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
def test_stage_earns_no_points_at_all():
    """Stage is a PREFERENCE, so it filters instead of scoring. It used to add up
    to 10, which helped build a floor under postings that fit badly: an 8+ year
    PM req at a big lab scored 58 on title + skills + stage no matter how far off
    the experience bar was."""
    for body in ("Seed-stage and high-growth, shipping fast from the ground up.",
                 "A high-growth publicly traded company.",
                 "We move fast."):
        bd = C.score_breakdown("Product Manager", body)
        labels = {l for l, _, _ in bd["parts"]}
        check(not (labels & {"early stage", "early stage (soft)", "late stage", "stage"}),
              f"stage scored: {labels}")


@case
def test_the_score_has_only_the_three_dimensions_that_measure_fit():
    """Title, location and stage are filters. Anything else in `parts` means a
    floor is creeping back under postings that do not fit."""
    equal(set(C.WEIGHTS), {"seniority fit", "resume skills", "your domains"})
    bd = C.score_breakdown("Forward Deployed Product Manager",
                           "Seed-stage NYC startup. 2+ years. Roadmap, PRDs, Figma.")
    check({l for l, _, _ in bd["parts"]} <= set(C.WEIGHTS),
          f"unexpected scored dimension: {[l for l, _, _ in bd['parts']]}")


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
    """A score is only half an explanation: the email prints these as the plain
    reason a role is not a better match."""
    bd = C.score_breakdown("Product Manager", "Join our team.")
    got = {label for label, _ in bd["misses"]}
    for want in ("seniority", "skills", "domains"):
        check(want in got, f"missing miss {want!r}; got {got}")


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
def test_the_email_shows_no_arithmetic():
    """The card used to print "+26 seniority fit | +17 strong skills match" and a
    dimmed list of zero-weighted misses. That is a window into the machinery, not
    information you can act on."""
    item = {"title": "Product Manager", "company": "acme", "url": "http://x",
            "location": "New York, NY", "source": "Ashby", "posted_ts": None,
            "loc_tier": "nyc", "funding": {}, "facts": {"years": "5+ yrs",
            "years_min": 5, "years_domain": "pm"}}
    item["breakdown"] = C.score_breakdown("Product Manager",
                                          "5+ years of product management experience")
    item["q"] = item["breakdown"]["total"]
    html_out = C.card(item)
    for banned in ("Why ", "+65", "+25", "&minus;", "\u2212"):
        check(banned not in html_out, f"card still prints arithmetic: {banned!r}")
    check("Strong fit" in html_out or "Good fit" in html_out
          or "Worth a look" in html_out or "A stretch" in html_out,
          "card shows no plain-language fit label")


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
    # Only the keyword dimensions may be empty-handed here; the seniority
    # dimension legitimately carries its own "no bar stated" explanation.
    keyword_terms = [t for label, _, terms in innocent["parts"]
                     if label in ("resume skills", "your domains") for t in terms]
    check(not keyword_terms, f"phantom hits: {keyword_terms}")


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
def test_title_is_a_filter_and_never_a_score():
    """title_is_target() already decided the posting is in lane. Paying points
    for the title again just raised the floor under every posting that survived
    -- and paid MORE for titles that happened to match two overlapping stems
    ("Forward Deployed Engineer" 24 vs "Product Manager" 12, both exact)."""
    for t in ("Forward Deployed Engineer", "Product Manager",
              "Forward Deployed Product Manager"):
        labels = {l for l, _, _ in C.score_breakdown(t, "")["parts"]}
        check("title" not in labels, f"{t!r} scored points for its title")


@case
def test_hyphenated_titles_score_the_same_as_spaced_ones():
    """"Forward-Deployed Engineer" matched no stem at all -- scoring 0 AND being
    dropped by the gate."""
    for a, b in [("Forward-Deployed Engineer", "Forward Deployed Engineer"),
                 ("Forward-Deployed Product Manager", "Forward Deployed Product Manager")]:
        check(C.title_is_target(a), f"{a!r} was dropped by the gate")
        equal(C.score_breakdown(a, "x")["total"], C.score_breakdown(b, "x")["total"],
              f"{a!r} scored differently from {b!r}")


@case
def test_a_bar_years_above_yours_scores_near_zero():
    """Ranking a role low still left it in the list. A role wanting eight years
    is not a role you get with two or three, and the score should say so."""
    kw = ("Own the roadmap, write PRDs, run discovery, cross-functional in "
          "Figma, SQL. AI agents, B2B SaaS workflow automation.")
    scores = {}
    for y in (2, 4, 6, 8, 10):
        scores[y] = C.score_breakdown(
            "Product Manager", f"{y}+ years of product management experience. {kw}")["total"]
    check(scores[2] >= 90, f"a role at your level should score high: {scores[2]}")
    check(scores[8] <= 5, f"an 8-year bar should be near zero, got {scores[8]}")
    check(scores[10] <= scores[8], "the curve should not turn back up")
    ordered = [scores[y] for y in (2, 4, 6, 8, 10)]
    equal(ordered, sorted(ordered, reverse=True), f"not monotonic: {scores}")


@case
def test_keywords_cannot_rescue_a_role_you_are_years_short_of():
    """Keywords SCALE WITH the experience fit rather than adding beside it.
    Additively, a role wanting eight years still collected the full keyword
    weight and landed around 20."""
    loaded = ("10+ years of product management experience. Roadmap, roadmapping, "
              "PRDs, discovery, prioritization, Figma, SQL, Notion, Jira, B2B, "
              "SaaS, AI, agents, LLM, workflow, automation, fintech, proptech.")
    bare = "10+ years of product management experience."
    a = C.score_breakdown("Product Manager", loaded)["total"]
    b = C.score_breakdown("Product Manager", bare)["total"]
    check(a <= 10, f"a keyword-stuffed 10-year role still scored {a}")
    check(a - b <= 8, f"keywords moved a hopeless role by {a - b}")


@case
def test_the_years_filter_lets_unknown_through():
    """Half of all postings state no bar. Silence is not a ten-year requirement,
    so an unstated bar must never be filtered out."""
    check(C.screen_facts("Join our team and build the future.").get("years_min") is None,
          "this fixture should state no bar")
    check(C.MAX_YEARS > 0, "the filter should be on by default")


@case
def test_parts_still_sum_to_the_total_after_scaling():
    """Keyword scaling multiplies each part, so the arithmetic must still close
    even though the email no longer prints it."""
    for jd in ("2+ years of product experience. Roadmap, PRDs, Figma.",
               "8+ years of product management experience. Roadmap, PRDs.",
               "Join our team."):
        bd = C.score_breakdown("Product Manager", jd)
        equal(sum(p[1] for p in bd["parts"]), bd["total"], jd[:40])


@case
def test_selling_to_big_companies_is_not_being_one():
    """The stage filter conflated "is a big company" with "sells to big
    companies" -- which is how most seed-stage AI startups describe themselves.
    On the real corpus it dropped three early-stage roles on the strength of
    "deploy them in secure environments to fortune 500 companies"."""
    for jd in ("We deploy them in secure environments to fortune 500 companies. "
               "We are a fast-moving team of engineers.",
               "The leading provider of agentic tools for frontier LLMs, "
               "fortune 500 organizations, and b2b saas companies.",
               "Our investors have taken companies to IPO before."):
        equal(C.late_stage_prose(jd), [], f"false positive on: {jd[:52]}")


@case
def test_a_company_describing_itself_as_late_stage_still_counts():
    check(C.late_stage_prose("We are a publicly traded company on NASDAQ: ACME."),
          "missed a real public company")
    check(C.late_stage_prose("We recently raised a $250 million Series E round."),
          "missed a real late round")
    check(C.late_stage_prose("We are a fortune 500 company with thousands of employees."),
          "missed a self-described large company")


@case
def test_the_late_stage_filter_is_off_by_default():
    """Dropping a company for its stage costs real roles, and the prose evidence
    is weak. It is opt-in."""
    check(not C.EXCLUDE_LATE_STAGE,
          "the late-stage filter should default off (JOB_EXCLUDE_LATE_STAGE=1 opts in)")


@case
def test_max_years_follows_the_repo_config_precedence():
    """code default < profile.compiled.json < env var. This one matters because
    the repo is public: the loose default is for forkers, and the personal 0-3
    window lives in profile.json, which ships as the PROFILE_COMPILED_JSON
    secret rather than in git."""
    import json as _json
    import subprocess
    import tempfile
    root = os.path.dirname(HERE)
    compiled = {"target_titles": ["product manager"], "skills": ["roadmap"],
                "domains": ["ai"], "years": 3, "years_pm": 2.5,
                "location_keywords": ["new york"], "remote_ok": False,
                "prefer_larger": False, "max_age_days": 21, "max_years": 3}
    path = os.path.join(root, "profile.compiled.json")
    pre_existing = os.path.exists(path)
    if pre_existing:                      # never clobber a real profile
        return
    probe = "import companies_digest as C;print(C.MAX_YEARS)"

    def run(env_extra):
        env = {k: v for k, v in os.environ.items() if k != "JOB_MAX_YEARS"}
        env.update(env_extra)
        r = subprocess.run([sys.executable, "-c", probe], cwd=root,
                           capture_output=True, text=True, env=env)
        return r.stdout.strip().splitlines()[-1]

    equal(run({}), "5", "code default")
    with open(path, "w") as f:
        _json.dump(compiled, f)
    try:
        equal(run({}), "3", "profile should beat the code default")
        equal(run({"JOB_MAX_YEARS": "4"}), "4", "env should beat the profile")
    finally:
        os.remove(path)


@case
def test_geography_asks_whether_you_would_have_to_move():
    """Not "which metro". A San Francisco company hiring REMOTE is as workable
    from Brooklyn as a New York company hiring on-site; ranking by metro put the
    two SF roles together and split the two workable ones apart."""
    for tier, mode, moving in [
            ("nyc", "onsite", False), ("nyc", "remote", False),
            ("nyc", "hybrid", False), ("us-remote", "remote", False),
            ("secondary", "remote", False),      # SF company, remote role
            ("secondary", "hybrid", True), ("secondary", "onsite", True),
            ("us-other", "onsite", True), ("us-other", "remote", False)]:
        equal(C.needs_relocation(tier, mode), moving, f"{tier}/{mode}")


@case
def test_nyc_onsite_and_sf_remote_rank_together():
    jd = ("2+ years of product experience. Roadmap, PRDs, Figma. Seed-stage "
          "AI workflow startup, B2B SaaS.")
    nyc = C.score_breakdown("Product Manager", jd, "nyc", "onsite")["total"]
    sf_remote = C.score_breakdown("Product Manager", jd, "secondary", "remote")["total"]
    sf_onsite = C.score_breakdown("Product Manager", jd, "secondary", "onsite")["total"]
    equal(nyc, sf_remote, "a role you can do from home should not be discounted")
    check(sf_onsite < nyc * 0.8, f"a move should cost real ground: {sf_onsite} vs {nyc}")


@case
def test_unknown_geography_is_never_penalised():
    """score_breakdown is called without geography in plenty of places. Unknown
    must mean "no opinion", not "assume they'd have to move" -- the same
    fail-open rule the funding digest's HQ gate uses."""
    equal(C.needs_relocation(None, None), False)
    equal(C.needs_relocation("", "onsite"), False)


@case
def test_an_unqualified_city_is_treated_as_onsite():
    """A posting that just says "Austin, TX" with no work mode means on-site."""
    equal(C.needs_relocation("us-other", None), True)
    equal(C.needs_relocation("us-other", ""), True)


@case
def test_relocation_scales_rather_than_subtracts():
    """Same reason keywords scale: a role you'd have to move for is worth less
    across the board, not "the same minus a fixed amount" -- which would flip
    sign on a weak posting."""
    weak = "Join our team."
    strong = ("2+ years of product experience. Roadmap, PRDs, Figma, SQL, "
              "discovery. Seed-stage AI workflow startup, B2B SaaS.")
    for jd in (weak, strong):
        home = C.score_breakdown("Product Manager", jd, "nyc", "onsite")["total"]
        away = C.score_breakdown("Product Manager", jd, "secondary", "onsite")["total"]
        check(0 <= away <= home, f"{away} should sit between 0 and {home}")
        bd = C.score_breakdown("Product Manager", jd, "secondary", "onsite")
        equal(sum(p[1] for p in bd["parts"]), bd["total"], "parts must still sum")


if __name__ == "__main__":
    main("scoring")
