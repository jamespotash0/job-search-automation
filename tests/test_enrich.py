#!/usr/bin/env python3
"""Enrichment's pure functions, and the two gates its output drives.

`us_only_reject` and `off_sector_reject` are the highest-consequence code in the
repo: they delete companies from the funding digest, and a wrong deletion is
completely invisible — you just never hear about the company. Both are pure
functions of the enrich dict, so they can be pinned exactly.

The email guessers matter for a different reason: their output is what cold
outreach gets sent to. A wrong pattern doesn't error, it just bounces.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("JOB_IGNORE_PROFILE", "1")   # test the code, not a local profile
sys.path.insert(0, os.path.dirname(HERE))

import enrich as E                    # noqa: E402
import funding_digest as F            # noqa: E402
from harness import case, check, equal, main   # noqa: E402


# --------------------------------------------------------------- the geo gate
@case
def test_us_variants_all_survive_the_geo_gate():
    for c in ["United States", "USA", "US", "U.S.", "U.S.A.", "America",
              "united states", "  United States  "]:
        equal(F.us_only_reject({"hq_country": c}), "", f"{c!r} should be kept")


@case
def test_foreign_hq_is_rejected():
    for c in ["United Kingdom", "India", "Germany", "Canada", "Israel"]:
        check(F.us_only_reject({"hq_country": c, "hq_city": "X"}),
              f"{c!r} should be rejected")


@case
def test_unknown_hq_fails_open():
    """A failed lookup is not evidence of being foreign. This is the single most
    consequential branch in the file: flipping it to fail-closed would silently
    delete every company the researcher couldn't resolve."""
    equal(F.us_only_reject({}), "")
    equal(F.us_only_reject({"hq_country": ""}), "")
    equal(F.us_only_reject({"hq_country": "   "}), "")


# ------------------------------------------------------------ the sector gate
@case
def test_kept_sectors_survive():
    for c in sorted(F.KEEP_CATEGORIES):
        equal(F.off_sector_reject({"category": c}), "", f"{c!r} should be kept")


@case
def test_off_sectors_are_rejected():
    for c in ["biotech/pharma", "hardware/devices", "space/aerospace/defense",
              "energy/climate", "materials/manufacturing", "consumer goods"]:
        check(F.off_sector_reject({"category": c}), f"{c!r} should be rejected")


@case
def test_unknown_sector_fails_open():
    equal(F.off_sector_reject({}), "")
    equal(F.off_sector_reject({"category": ""}), "")


@case
def test_every_kept_category_is_a_real_category():
    """KEEP_CATEGORIES lives in funding_digest, CATEGORIES in enrich. A typo in
    either silently drops a whole sector, because the gate compares strings."""
    unknown = F.KEEP_CATEGORIES - set(E.CATEGORIES)
    check(not unknown, f"KEEP_CATEGORIES names sectors enrich never emits: {unknown}")


# ------------------------------------------------------------- email guessing
@case
def test_guess_emails_is_ranked_most_likely_first():
    got = E.guess_emails("acme.ai", "Jane Smith")
    equal(got, ["jane@acme.ai", "jane.smith@acme.ai", "jsmith@acme.ai",
                "smith@acme.ai"])


@case
def test_guess_emails_handles_one_word_and_messy_names():
    equal(E.guess_emails("acme.ai", "Cher"), ["cher@acme.ai"])
    check("jean-luc@acme.ai" in E.guess_emails("acme.ai", "Jean-Luc Picard")[0:1]
          or E.guess_emails("acme.ai", "Jean-Luc Picard")[0].startswith("jean"),
          E.guess_emails("acme.ai", "Jean-Luc Picard"))
    equal(E.guess_emails("acme.ai", ""), [])
    equal(E.guess_emails("", "Jane Smith"), [])


@case
def test_pattern_to_email_fills_every_placeholder_form():
    equal(E.pattern_to_email("{first}", "Jane", "Smith", "acme.ai"), "jane@acme.ai")
    equal(E.pattern_to_email("{first}.{last}", "Jane", "Smith", "acme.ai"),
          "jane.smith@acme.ai")
    equal(E.pattern_to_email("{f}{last}", "Jane", "Smith", "acme.ai"), "jsmith@acme.ai")


@case
def test_pattern_to_email_refuses_a_half_filled_address():
    """An unresolved placeholder must yield "" — never 'jane.{last}@acme.ai',
    which would go out in a real email."""
    equal(E.pattern_to_email("{first}.{last}", "Jane", "", "acme.ai"), "")
    equal(E.pattern_to_email("{middle}", "Jane", "Smith", "acme.ai"), "")
    equal(E.pattern_to_email("", "Jane", "Smith", "acme.ai"), "")


# ------------------------------------------------------- model output parsing
@case
def test_parse_json_survives_the_ways_models_wrap_output():
    want = {"domain": "acme.ai"}
    for text in ['{"domain":"acme.ai"}',
                 '```json\n{"domain":"acme.ai"}\n```',
                 'Here is the result:\n{"domain":"acme.ai"}\nHope that helps!',
                 '  \n {"domain":"acme.ai"} \n ']:
        equal(E._parse_json(text), want, f"failed on {text!r}")


@case
def test_parse_json_returns_empty_rather_than_raising():
    for text in ["", None, "no json here", "{broken", "[1,2,3]"]:
        equal(E._parse_json(text), {}, f"failed on {text!r}")


# --------------------------------------------------- company name from a title
@case
def test_guess_company_pulls_the_name_off_a_real_headline():
    equal(F.guess_company("Acme AI raises $12M Series A - TechCrunch"), "Acme AI")
    equal(F.guess_company("Exclusive: Foo Corp secures $5M seed"), "Foo Corp")
    equal(F.guess_company("Bar Inc. closed a $30M round"), "Bar Inc.")


@case
def test_headlines_that_are_not_a_raise_never_reach_enrichment():
    """The stale enrich_cache entries were headline fragments, not companies:
    'the ai compute gap: enterprises are buying infrastructure'. Each one cost a
    web-search call to research a company that does not exist. is_funding is the
    gate that has to stop them before guess_company ever runs."""
    for t in ["The AI compute gap: enterprises are buying infrastructure they can't use",
              "Agentic orchestration: enterprise AI organizations have a decision to make",
              "Thinking Machines open sources first multimodal language model",
              "Daily funding roundup - March 3",
              "10 startups that raised this week",
              "Resilience I Fund closes at $200M"]:
        check(not F.is_funding(t, ""), f"{t!r} should not count as a raise")


# ------------------------------------- what the geo gate is allowed to affect
@case
def test_geo_gate_is_about_the_company_not_the_role():
    """The two gates answer different questions and must not be conflated.

    Whether to cold-email a founder is about the COMPANY (a UK-HQ startup is not
    somewhere you'd write to). Whether to watch a board is about the ROLE, which
    does not exist yet — a London-HQ company with a New York office posts New
    York roles, and companies_digest gates each posting on its OWN location.

    Dropping the company before the watchlist write meant those NYC roles were
    never even fetched. This pins the source so it cannot silently go back.
    """
    src = open(os.path.join(os.path.dirname(HERE), "funding_digest.py")).read()
    body = src[src.index('if __name__ == "__main__":'):]
    watch = body.index("for i in watchable:")
    check("us_only_reject" not in body[watch:watch + 400],
          "the watchlist loop must not be filtered by the HQ country gate")
    check("emailable" in body and "watchable" in body,
          "funding_digest should keep the email list and the watch list separate")


@case
def test_role_location_is_what_the_companies_digest_gates_on():
    """companies_digest must gate on the POSTING's location, never a company HQ."""
    import companies_digest as C
    # A posting in New York stays, whatever the company is.
    equal(C.location_tier("New York, NY", "Our headquarters are in London, UK"), "nyc")
    # ...and a London posting drops, whatever the company is.
    equal(C.location_tier("London, UK", "We are a New York company"), "")


@case
def test_board_hq_corroboration_needs_a_majority():
    """The eval's job-board HQ check. Reusing location_tier as the US detector is
    the point: "SF Office", "Remote (US)", "Austin, TX" and "California" all have
    to count as US, and there is already a tested classifier for that.

    The majority rule matters — an early version returned OK for Abridge on 2 of
    46 roles, which corroborates nothing."""
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "evals"))
    import collections
    import eval_enrich as V

    def verdict(locs, city, country="United States"):
        counter = collections.Counter(locs)
        V.ats_job_locations = lambda *a, **k: counter        # no network
        return V.check_hq_via_board({"domain": "x.com", "hq_city": city,
                                     "hq_country": country}, "X")[0]

    # An explicit (HQ) marker is the strongest signal either way.
    equal(verdict(["New York, NY (HQ)"] * 9 + ["London"], "New York"), "OK")
    equal(verdict(["London (HQ)"] * 9, "New York"), "CONTRADICTED")
    # City named on the board, as a meaningful share.
    equal(verdict(["San Francisco"] * 5 + ["London"] * 5, "San Francisco"), "OK")
    # ...but a couple of roles in a city is an office, not proof of an HQ.
    equal(verdict(["London"] * 102 + ["San Francisco"] * 2, "San Francisco"), "WEAK")
    # City absent -> country level, and a majority is required.
    equal(verdict(["SF Office"] * 40 + ["London"] * 6, "Pittsburgh"), "OK")
    equal(verdict(["London"] * 44 + ["Austin, TX"] * 2, "Pittsburgh"), "WEAK")
    equal(verdict(["London"] * 20 + ["Paris"] * 20, "Pittsburgh"), "CONTRADICTED")


if __name__ == "__main__":
    main("enrich")
