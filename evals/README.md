# evals

Accuracy checks for the parts of the pipeline a model decides. They cost API
calls and hit the network, so they are **not** in `tests/run.py` — run them by
hand when you change a prompt, a model, or a gate.

```
python evals/eval_enrich.py                     # the 18-company fixture set
python evals/eval_enrich.py --limit 3           # cheap smoke test
python evals/eval_enrich.py --companies "Ramp,Mistral AI"
python evals/eval_enrich.py --out results.json
```

See also `tests/verify_grok_quotes.py`, which checks X/Grok quotes against the
source tweets.

## What eval_enrich.py measures

`enrich.ai_lookup` researches each company and returns `hq_country`, `category`
and `domain`. Two gates in `funding_digest` act on that, with **different
stakes** — a distinction the code got wrong until 2026-08-24:

| gate | field | what a wrong answer costs |
|---|---|---|
| `off_sector_reject` | `category` | the company vanishes from the digest **entirely** — no email, no job watching |
| `us_only_reject` | `hq_country` | the outreach email only; the job board is still watched |

That second row is the fix: **geography for a job is about the ROLE, not the
company.** A London-headquartered startup with a New York office posts New York
roles, and `companies_digest` already gates every posting on its own `location`.
The funding digest used to drop such a company before the watchlist write, so
its NYC roles were never fetched at all.

Both failures are silent. Nothing in any email says a company was dropped, which
is the whole reason this eval exists.

## Why it isn't just a second model call

Asking another model with web search to grade the first one mostly measures
whether two models make the same mistakes — shared training data, often the same
sources. Agreement there is weak evidence. This corroborates against evidence
the researcher did not choose:

| field | corroborated by | model involved |
|---|---|---|
| `domain` | resolve it; does the live page name the company? | no |
| `hq_city` / `hq_country` | fetch the company's **own** site (`/about`, `/contact`) and look for the claimed location | no |
| `category` | a judge that sees **only** the fetched site text, never web search, and may answer "insufficient" | yes, different evidence |

The HQ check is deliberately conservative. Absence of an address is extremely
common and is **not** evidence of a wrong answer, so it reports `UNVERIFIABLE`.
`CONTRADICTED` is raised only when the site names a different country in an
HQ-ish context (`headquartered in`, `based in`, `our office`) — an early version
flagged Ramp as contradicted because its marketing site mentions Canada, which
says nothing about where the company is based.

## Ground truth

`fixtures/companies.json` is hand-labelled and is the weakest link — one
person's understanding of where a company is based. The harness doesn't trust it
blindly: it corroborates against each company's own website, so a bad label
tends to surface as a disagreement rather than a silent pass. Two entries are
deliberately unlabelled hard cases (Taktile, Deel) — observed, not graded.

## Results, 2026-08-24

18 companies. **0 wrong gate decisions.** Every labelled company landed on the
correct side of both gates: Synthesia / Mistral / Cohere / DeepL correctly lost
their outreach email on geo (and keep their boards watched for US roles);
Anduril / Commonwealth Fusion / Recursion / Boom correctly dropped on sector.

| check | run 1 | run 2 (after fixes) |
|---|---|---|
| `domain` | 17 OK, 1 suspect | **18 OK** |
| `hq` | 6 OK, 12 unverifiable | **10 OK, 8 unverifiable** |
| `category` | 14 OK, 2 contradicted, 2 unverifiable | **15 OK, 1 quibble, 2 unverifiable** |

### Read this before trusting the green number

**The HQ check still only has power on about half the set.** Eight companies
report UNVERIFIABLE because their own site never states where they are — adding
`/privacy` and `/terms` (a privacy policy has to carry a legal address) moved
that from 12 to 8, but the remainder is a real ceiling. So this eval validates
`domain` and `category` well and `hq_country` only partially. Absence of
evidence is reported as UNVERIFIABLE and never counted as a pass.

**Category disagreements are graded by consequence, not by label.** The judge
preferred `b2b saas` over `ai/software` for Harvey — both are in
KEEP_CATEGORIES, the company survives either way, so that is a QUIBBLE and not
an error. Only a disagreement that would move a company across the gate counts.

**The judge is not always right, and it says so about itself.** On Synthesia it
labelled `consumer software` while its own reasoning argued `b2b saas` — labels
on opposite sides of the gate. Believing it would have manufactured a finding
out of noise, so a self-inconsistent verdict is now UNVERIFIABLE.

**The researcher is non-deterministic.** Boom Supersonic came back
`hardware/devices` on run 1 (wrong; the judge caught it) and
`space/aerospace/defense` on run 2 (right). Same prompt, same model. A single
clean run is therefore not proof — the value here is repeated runs, not one.
