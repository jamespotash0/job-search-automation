#!/usr/bin/env python3
"""Screening facts (years / comp / work model / equity / sponsorship) and the
HTML stripping they depend on.

Both were shipped the same day and both had a bug that only real board data
exposed: the JD was truncated to 1500 chars (so the requirements block was never
in the text at all), and strip_html unescaped AFTER stripping (so Greenhouse's
escaped markup came back as visible `<li>` litter).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("JOB_IGNORE_PROFILE", "1")   # test the code, not a local profile
sys.path.insert(0, os.path.dirname(HERE))

import companies_digest as C          # noqa: E402
from harness import case, check, equal, main   # noqa: E402

FIX = json.load(open(os.path.join(HERE, "fixtures", "jds.json")))


@case
def test_facts_match_labels():
    wrong = []
    for c in FIX["cases"]:
        got = C.screen_facts(c["jd"])
        if got != c["expect"]:
            wrong.append(f"{c['origin']}\n          got  {got}\n          want {c['expect']}")
    check(not wrong, f"{len(wrong)} of {len(FIX['cases'])} JDs misread:\n        "
                     + "\n        ".join(wrong))


@case
def test_fundraise_and_arr_figures_are_not_salaries():
    """'$12,000,000 Series A' is not a pay band. The salary window is what stops
    the digest printing 'comp ~$12,000k'."""
    equal(C.screen_facts("We raised $12,000,000 and passed $30,000,000 ARR").get("comp"), None)
    equal(C.screen_facts("Salary $130,000 - $165,000")["comp"], "$130k–$165k")


@case
def test_a_stated_range_beats_a_bare_minimum():
    f = C.screen_facts("We want 4-6 years of product management experience. "
                       "Our CTO has 15+ years in the field.")
    equal(f["years"], "4–6 yrs")
    equal(f["years_min"], 4)


@case
def test_hybrid_wins_over_the_word_remote():
    """A hybrid JD nearly always says 'remote' somewhere too."""
    f = C.screen_facts("This hybrid role is 3 days a week in office, remote otherwise.")
    equal(f["work_model"], "Hybrid · 3d in office")


@case
def test_office_days_can_be_a_range_and_precede_the_count():
    """The Courted APM JD: never says "hybrid", puts the office word BEFORE the
    count, and gives a range. It read as a bare "Onsite" — harsher than the job."""
    f = C.screen_facts("The team is NYC-based and in the office 3-4 days per week.")
    equal(f["work_model"], "Hybrid \u00b7 3\u20134d in office")


@case
def test_five_days_in_office_stays_onsite():
    """The <5 correction must not turn a genuinely onsite role into hybrid."""
    f = C.screen_facts("This is an onsite role: in the office 5 days a week.")
    equal(f["work_model"], "Onsite \u00b7 5d/wk")


@case
def test_strip_html_handles_escaped_markup():
    """Greenhouse returns the JD as HTML-ESCAPED markup. Stripping before
    unescaping matches no tags, then the unescape puts them back as text."""
    got = C.strip_html("&lt;ul&gt;&lt;li&gt;5+ years&lt;/li&gt;&lt;/ul&gt;&nbsp;done")
    check("<" not in got and ">" not in got, f"tags survived: {got!r}")
    equal(got, "5+ years done")


@case
def test_strip_html_handles_plain_markup_too():
    equal(C.strip_html("<p>Plain <b>html</b> too</p>"), "Plain html too")


@case
def test_jd_window_is_big_enough_to_reach_the_requirements():
    """1500 chars put the requirements block and the pay band out of reach on a
    typical JD; both live near the bottom."""
    check(C.JD_CHARS >= 6000, f"JD_CHARS={C.JD_CHARS} is too small to see requirements")


@case
def test_the_fit_sentence_does_not_repeat_the_chips():
    """A card stated the experience bar THREE times: as a chip, in this
    sentence, and again in a trailing gap line. The chips own years and
    location; this sentence owns what the work actually is."""
    item = {"breakdown": C.score_breakdown(
                "Product Manager",
                "5+ years of product management experience. Roadmap, PRDs, "
                "Figma. AI workflow automation."),
            "facts": C.screen_facts("5+ years of product management experience"),
            "loc_tier": "nyc", "funding": {}}
    line = C.fit_sentence(item).lower()
    for banned in ("yrs", "years", "stretch", "nyc", "us-remote"):
        check(banned not in line, f"fit sentence repeats {banned!r}: {line!r}")
    check(line, "fit sentence should still say what the work is")


@case
def test_the_card_states_the_experience_bar_exactly_once():
    item = {"title": "Product Manager", "company": "acme", "url": "http://x",
            "location": "Remote", "source": "Lever", "posted_ts": None,
            "loc_tier": "us-remote", "funding": {}, "work_mode": "remote",
            "facts": C.screen_facts("5+ years of product management experience")}
    item["breakdown"] = C.score_breakdown(
        "Product Manager", "5+ years of product management experience. Roadmap.")
    item["q"] = item["breakdown"]["total"]
    text = re.sub(r"<[^>]+>", " ", C.card(item)).lower()
    equal(text.count("5+ yrs"), 1, f"the bar is stated more than once: {text}")
    # ...and the location is not printed twice either ("Remote" + "US-remote").
    check(text.count("remote") <= 2, f"location repeated: {text}")


@case
def test_matched_terms_are_cased_the_way_people_write_them():
    """These are printed verbatim in the digest. Blanket .upper() gave "SAAS";
    blanket sentence-case gave "Ai"."""
    for raw, want in [("ai", "AI"), ("saas", "SaaS"), ("b2b", "B2B"),
                      ("ai agents", "AI agents"), ("prd", "PRD"),
                      ("typescript", "TypeScript"), ("roadmap", "roadmap"),
                      ("real estate", "real estate")]:
        equal(C._pretty_term(raw), want)


@case
def test_a_remote_role_is_not_labelled_remote_twice():
    """"Remote" as a location and "Remote" as a work mode is the same redundancy
    as "Remote" followed by "US-remote"."""
    item = {"title": "Product Manager", "company": "acme", "url": "http://x",
            "location": "Remote", "source": "Lever", "posted_ts": None,
            "loc_tier": "us-remote", "funding": {}, "work_mode": "remote",
            "facts": {"work_model": "Remote"}}
    item["breakdown"] = C.score_breakdown("Product Manager", "2+ years. Roadmap.")
    item["q"] = item["breakdown"]["total"]
    text = re.sub(r"<[^>]+>", " ", C.card(item)).lower()
    equal(text.count("remote"), 1, f"remote appears more than once: {text}")


if __name__ == "__main__":
    main("facts")
