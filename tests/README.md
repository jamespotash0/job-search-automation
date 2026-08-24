# tests

No network, no API keys, ~1 second. The harness is stdlib-only, but the
modules under test are not — `funding_digest` imports `feedparser` at module
scope, so run `pip install -r requirements.txt` first (CI does). `python tests/run.py` from the
repo root, or `python tests/run.py gates` to run one module.

| file | covers |
|---|---|
| `test_gates.py` | `title_is_target`, `location_tier` — what you ever get to see |
| `test_facts.py` | `screen_facts`, `strip_html`, `fit_sentence` — the screening line |
| `test_scoring.py` | `score_breakdown` and the substring traps it keeps falling into |
| `verify_grok_quotes.py` | **not** part of `run.py` — hits the network, see below |

## Fixtures are real, labels are intentional

`fixtures/postings.json` and `fixtures/jds.json` hold strings captured from the
live Greenhouse / Ashby / Lever boards. The *labels* are the intended behaviour,
written by hand — not a recording of what the code returned. A fixture generated
from current output cannot catch a regression; it just re-records one.

To refresh the captured strings after the boards move on, dump a live fetch and
pick new examples by hand. Do not regenerate the labels from code output.

## What this suite is for

Every bug it now guards against was silent in production for weeks — a posting
that should have appeared simply didn't, and nothing in the digest said so:

- `"ai"` matched inside "em**ai**l", `"ui"` inside "b**ui**lding", `"api"`
  inside "c**api**tal" — nearly every posting collected free points.
- `"lead "` (with a trailing space) missed every title *ending* in "Lead".
- The JD was truncated to 1500 characters, so the requirements block and the pay
  band — which live at the bottom of a JD — were never in the scored text.
- `strip_html` unescaped *after* stripping, so Greenhouse's escaped markup came
  back as visible `<li>` litter in the text we score and quote.
- `"Remote, TX"` counted as US-remote.
- `"Forward Deployed Investor"` matched the `forward deployed` stem.

The suite is verified by mutation: each of those bugs was reintroduced and the
suite failed. A test that cannot fail is not a test.

## verify_grok_quotes.py

Separate because it hits the network and costs an xAI call.

The digest prints the X/Grok `quote` field as a verbatim quotation attributed to
a named person. Nothing else in the pipeline checks that the string is in the
tweet — so a paraphrase from the model becomes words put in a real founder's
mouth, in an email inviting you to reply to them.

```
python tests/verify_grok_quotes.py              # live search, then verify
GROK_CAPTURE=leads.json python tests/verify_grok_quotes.py   # ...and save the leads
python tests/verify_grok_quotes.py leads.json   # replay a saved capture, free
```

Verdicts: `OK` (quote found in the tweet), `CONTRADICTED` (fetched fine, quote
absent — exit code 1), `UNVERIFIABLE` (couldn't fetch, or the lead has no tweet
URL), `SKIP` (no quote returned). `UNVERIFIABLE` is deliberately not a pass.

### First run, 2026-08-24: it found real fabrications

Two independent live runs, minutes apart:

| run | leads | OK | CONTRADICTED |
|---|---|---|---|
| 1 | 13 | 11 | 2 (LendAPI 69%, Greptile 6%) |
| 2 | 12 | 10 | 2 (Hellyeah.ai 7%, Assembled 10%) |

A reproducible ~15%. The failures were not near-misses:

- `"PM grass counts with us — we have a Senior Tech Product Manager opening…"` —
  garbled phrasing that appears nowhere in the tweet.
- `"Hiring: Deployment Strategist — Assembled"` — a synthesized headline, not
  anyone's words.

Because of this, `companies_digest.card()` no longer renders `quote` as a
quotation, and the attribution line reads "summarized from a post by @handle"
rather than "posted by". The field still feeds the scoring blob, where being
approximate costs nothing. Re-run this check before ever presenting Grok output
as someone's words again.
