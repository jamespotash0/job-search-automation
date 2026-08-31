# Job-search automation

Two daily email digests plus a loop that connects them: one finds startups that
just raised (your outreach list), the other finds the newest job postings ranked
against your resume (your apply list), and companies discovered by the first feed
the second. Runs free or near-free on GitHub Actions.

```
getro.py ──┐                                    ┌──► companies_digest.py
discover.py┼──► ats_watchlist.json ◄──────┐     │    (newest roles, scored
funding_digest.py ──detects ATS + raise ──┘     │     against your resume)
     (who just raised, enriched with contacts) ─┘
```

## The pieces

| Piece | What it does |
|---|---|
| **Funding digest** (`funding_digest.py`) | Daily email of startups that just raised, filtered to your sector/geography, with AI company summaries and founder contacts on the top matches. |
| **Companies digest** (`companies_digest.py`) | Daily email of the newest postings for your target roles, gated on title/seniority/location and ranked by resume fit. Runs that find nothing new stay silent (`SEND_WHEN_EMPTY=1` to always send). |
| **The loop** (`watchlist.py`) | Shared `ats_watchlist.json`. Companies from the funding digest flow into the job digest, and their postings get a 🚀 badge plus a score bonus. |
| **Bulk feed** (`getro.py`) | Sweeps the public Getro API — the portfolio job boards of hundreds of VC funds — and reads real ATS tokens out of apply URLs. Keyless. This is what makes the watchlist big. |
| **Long tail** (`discover.py`) | Web-searches "we're hiring" posts and hiring roundups, probes each company's ATS, adds the hits. Needs `ANTHROPIC_API_KEY`. |

Per-piece detail: [docs/ENRICHMENT_README.md](docs/ENRICHMENT_README.md),
[docs/COMPANIES_README.md](docs/COMPANIES_README.md),
[docs/LOOP_README.md](docs/LOOP_README.md).

---

## Quick start

You need a Gmail address (or any SMTP account) and about ten minutes. Everything
else is optional.

### 1. Fork and install

```bash
git clone <your-fork> && cd claude_job_automation
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt   # one dependency: feedparser
```

### 2. Set your secrets

```bash
cp .env.example .env      # then edit it
```

`.env` is gitignored. Only the email keys are required:

| Key | Required? | What |
|---|---|---|
| `EMAIL_USER` | yes | Gmail address that sends the digests |
| `EMAIL_PASS` | yes | Gmail **app password** (Google Account → Security → 2-Step Verification → App passwords), not your login password |
| `EMAIL_TO` | yes | Where digests go; can equal `EMAIL_USER`, or a comma-separated list |
| `ANTHROPIC_API_KEY` **or** `OPENAI_API_KEY` | optional | AI company summaries + `discover.py`. Either provider works — both have server-side web search, and `llm.py` picks whichever key is set. Set `LLM_PROVIDER` only if you have both. |
| `HUNTER_API_KEY` | optional | Verified founder emails. From hunter.io (free tier ~50/mo) |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | optional | Extra job aggregator. Free from developer.adzuna.com |
| `GROK_API_KEY` (or `XAI_API_KEY`) | optional | Founder "we're hiring" posts on X. From console.x.ai |

Without email keys the scripts print the digest to stdout instead of sending —
which is the easiest dry run there is.

### 3. Make it match your search

Copy `profile.example.json` → `profile.json`, drop your resume in `docs/`, and
fill in five fields:

```jsonc
{
  "resume_file": "docs/your_resume.pdf",   // .pdf / .txt / .md
  "locations": ["new york", "remote"],      // [] = anywhere
  "remote_ok": false,
  "roles": "one sentence: the roles you want",
  "startup_types": "one sentence: the companies you want",
  "company_size": "small"                   // small | large | any
}
```

Then compile it once (needs `ANTHROPIC_API_KEY`; your resume is sent only to the
Anthropic API, never committed):

Two more worth setting, because they do most of the ranking:

```jsonc
  "years_experience": 3,    // compared against whatever bar a posting states
  "max_years": 5            // postings asking for more than this are filtered out
```

Then compile it once (needs `ANTHROPIC_API_KEY`; your resume is sent only to the
Anthropic API, never committed):

```bash
.venv/bin/python setup_profile.py
.venv/bin/python tests/check_profile.py    # <- do not skip this
```

`setup_profile.py` writes `profile.compiled.json` — the title / skill / domain /
location filters both digests load at startup. A model writes that list from your
one-sentence `roles`, so it can miss a stem you needed. `check_profile.py` runs
the compiled filters over 34 hand-labelled real postings and names anything they
now misclassify; widen `roles` and re-compile if it flags something.

Re-run both whenever your resume or `profile.json` changes. Both files are
gitignored — this repo is public and they're personal.

Skip this step and the digests use the filters baked into the scripts, which are
the repo author's job search rather than yours. They say so on startup.

### 4. Check it before you run it

```bash
.venv/bin/python doctor.py           # local + GitHub checks
.venv/bin/python doctor.py --local   # skip the GitHub half
```

Every problem it looks for is **silent** — nothing errors, you just get wrong
results: a missing `PROFILE_COMPILED_JSON` secret means the scheduled runs use
someone else's filters, an empty watchlist means three postings instead of
thirty, a compiled profile older than your resume means last month's filters.
`✗` means wrong results, `!` means an optional source is off.

### 5. Run it

```bash
.venv/bin/python funding_digest.py     # who just raised
.venv/bin/python companies_digest.py   # what they and 400 other boards posted
.venv/bin/python getro.py              # bulk-fill the watchlist (run this first, once)
```

### 6. Put it on a schedule

Push your fork and enable Actions. Three workflows ship with it:

| Workflow | Schedule | Runs |
|---|---|---|
| `daily-digest.yml` | 8am ET | `getro.py` → `discover.py` → `funding_digest.py` |
| `companies-digest.yml` | ~9am ET | `companies_digest.py` (+ a second "X Hiring Posts" email when that source is on) |
| `tests.yml` | every push | `tests/run.py` |

In your fork: **Settings → Secrets and variables → Actions → Secrets**, add the
same keys from step 2. Then add one more, **`PROFILE_COMPILED_JSON`**: paste the
entire contents of your local `profile.compiled.json`. Without it the scheduled
runs fall back to the generic keywords baked into the scripts, and you get a
digest tuned to someone else's job hunt.

State (`ats_watchlist.json`, `seen_jobs.json`, `enrich_cache.json`,
`hunter_usage.json`, `getro_networks.json`) persists on a `state` branch between
runs, so the watchlist grows and you're never emailed the same posting twice.

Trigger a run by hand from the **Actions** tab → pick a workflow → **Run
workflow**. Check your inbox in about a minute.

---

## Tuning

Three layers, each overriding the one before it:

```
code defaults  <  profile.compiled.json  <  environment variables
```

You can skip this section entirely. Every knob has a working default, and the
defaults are what this repo runs on — an unset variable is not a missing
setting. Come back when a digest shows you the wrong things.

When you do, **everything tunable is documented in [`.env.example`](.env.example)**
— scoring weights, title gates, geography tiers, which sources run, caps, and
cost controls, each with its default written next to it. Uncomment only the line
you want to change; the rest keep their defaults.

Locally that means editing `.env`. For scheduled runs, set the same names as
repo **variables** (Settings → Secrets and variables → Actions → *Variables*
tab, not Secrets) — both digest workflows forward every repo variable into the
run, so retuning a schedule never needs a code change.

A few worth knowing about:

| Want | Set |
|---|---|
| Different city | `JOB_PRIMARY_LOCATIONS=austin,texas` + `FUNDING_LOCATIONS=austin` |
| Senior roles, not junior | `JOB_SENIORITY_EXCLUDE=intern,junior` and swap `JOB_JUNIOR_SIGNALS` / `JOB_SENIOR_SIGNALS` |
| Engineering roles | `JOB_SWE_EXCLUDE=` (empty clears the gate) + `JOB_TARGET_TITLES=...` |
| Later-stage companies | `JOB_EARLY_ROUNDS=series b,series c,series d` |
| A fuller / quieter inbox | `JOB_BEST_MATCH_MIN` (score cutoff), `JOB_MAX_ITEMS`, `JOB_FRESH_ONLY_DAYS` |
| Cap how senior a role may ask for | `JOB_MAX_YEARS=3` (0 disables; unstated bars always pass) |
| Only genuinely new postings | `JOB_FRESH_ONLY_DAYS=10` — stops a newly-added board dumping its back catalogue |
| Keep late-stage companies | `JOB_EXCLUDE_LATE_STAGE=0` |
| Rename the sender / subjects | `DIGEST_FROM_NAME`, `DIGEST_SUBJECT_JOBS`, `DIGEST_SUBJECT_X` |
| Lower API spend | `FUNDING_ENRICH_TOP_N=8`, or `=0` to turn AI summaries off |
| Fewer sources | `JOB_USE_MUSE=0`, `JOB_USE_ADZUNA=0`, … (one flag per source) |

Lists are comma-separated, and an explicitly empty value means an empty list —
`JOB_SECONDARY_LOCATIONS=` turns that tier off rather than restoring the default.
Booleans take `1/true/yes/on` or `0/false/no/off`.

### How a posting is scored

The score answers one question: **does this role's experience bar fit yours.**
Keyword overlap is the tiebreak between roles that fit it about equally.

| Dimension | Weight | Env |
|---|---|---|
| Experience bar vs your years | 65 | `JOB_W_SENIORITY` |
| Resume skills, matched in the JD's requirements block | 25 | `JOB_W_SKILLS`, `JOB_SKILL_SATURATION` |
| Domain overlap | 10 | `JOB_W_DOMAINS`, `JOB_DOMAIN_SATURATION` |

Two things multiply that total rather than adding to it:

| Multiplier | Value | Env |
|---|---|---|
| Your metro / doable from home / would need a move | 1.0 / 0.95 / 0.55 | `JOB_ELSEWHERE_FACTOR`, `JOB_RELOCATION_FACTOR` |
| Bar is in another field — "3 years in banking operations" | ×0.2 | `JOB_FIELD_MISMATCH_FACTOR` |

**Title, location and stage are filters. They decide whether you see a posting;
they never add points.** Anything that adds points to every posting that passed
a gate raises the floor under all of them, including the bad ones. Geography
multiplies for the same reason — it separates two good roles without lifting a
weak local one, and it asks whether you could do the job from where you live,
not which metro it is in. An SF company hiring remote ranks with the local ones.

At three years of experience, on otherwise identical postings:

| JD asks for | Score |
|---|---|
| 3+ years | 92 |
| 4+ years | 77 |
| 5+ years | 55 |
| 6+ years | 29 |
| 8+ years | 1 |
| 3+ years in banking operations | 18 |

A stated bar ranks, it never gates. `JOB_MAX_YEARS` is the hard cut — postings
asking for more are dropped, and postings stating **no** bar always pass, since
roughly half state nothing.

**Expect a great match to read 75–90, and 100 essentially never.** If everything
looks like a 90, the keyword dimensions are saturating too easily; raise
`JOB_SKILL_SATURATION`. If the "Best matches" section is thin, lower
`JOB_BEST_MATCH_MIN`.

Cards show a verdict — Strong fit / Good fit / Worth a look / A stretch — the
facts you would screen on, and one line on the work. Chips are coloured by
verdict: green suits you, grey is neutral, amber is partly against, red is
against.

**X/Twitter hiring posts are not scored.** They arrive as their own email,
newest first, with no verdict: a founder saying "we're hiring" often never says
for what and states no bar, so a number would be measuring the format rather
than the lead.

The funding digest scores headlines on its own weights (`FUNDING_FOCUS_POINTS`,
`FUNDING_LOC_POINTS`, `FUNDING_LOC2_POINTS`, `FUNDING_ROUND_POINTS`) with its own
cutoff (`FUNDING_BEST_SCORE`).

### The ATS watchlist

The free job sources skew remote/tech; **the ATS watchlist is what makes the job
digest good**. It fills itself three ways — `getro.py` in bulk, the funding
digest as companies raise, `discover.py` for the long tail — and you can seed it
by hand from any careers URL:

```
boards.greenhouse.io/<TOKEN>    jobs.lever.co/<TOKEN>    jobs.ashbyhq.com/<TOKEN>
```

Add tokens to `GREENHOUSE_COMPANIES` / `LEVER_COMPANIES` / `ASHBY_COMPANIES` in
`companies_digest.py`; Workday boards need a tenant/wd/site entry in
`WORKDAY_COMPANIES`. Pin the funds whose portfolios match your lane with
`GETRO_NETWORKS="1200 803"`, and widen a single sweep with
`python getro.py --scan 800`.

---

## Developer guide

### Layout

| File | Role |
|---|---|
| `funding_digest.py` | Feeds → raise detection → geo/sector gate → score → enrich → email |
| `companies_digest.py` | Sources → title/location gates → score → dedupe → email |
| `enrich.py` | Anthropic web-search research + Hunter.io email lookup, cached |
| `watchlist.py` | `ats_watchlist.json` reader/writer: funded flags, stage facts, ATS probing, re-probe queue |
| `getro.py` | Getro network sweep; also harvests company-size buckets |
| `discover.py` | AI web-search company discovery |
| `setup_profile.py` | Resume + `profile.json` → `profile.compiled.json` |
| `llm.py` | One seam so a fork runs on **Anthropic or OpenAI** — both do server-side web search, shaped differently |
| `doctor.py` | Pre-flight: every setup failure in this repo is silent, so it checks them and prints the fix |
| `tests/` | Unit suite, no network. `run.py` pins `JOB_IGNORE_PROFILE=1` so it tests the code, not your profile |
| `tests/check_profile.py` | The other half: your **compiled** profile vs 34 labelled real postings |
| `evals/` | Accuracy checks for the model-decided parts (costs API calls) |

State files, all JSON on disk and on the `state` branch in CI:
`ats_watchlist.json` (companies + ATS tokens + funded/stage facts),
`seen_jobs.json` (already-emailed postings, `JOB_SEEN_TTL_DAYS`),
`enrich_cache.json` (per-company research, versioned by `SCHEMA_VERSION`),
`hunter_usage.json` (monthly quota counter), `getro_networks.json` (known
network ids so a sweep doesn't re-probe dead ones).

### Conventions

- **Stdlib plus `feedparser`.** That's the whole dependency list, including the
  test runner. Don't add one without a strong reason.
- **Config reads the environment first.** Every knob is `env_int` / `env_flag` /
  `env_list` with the default inline, and every one is documented in
  `.env.example`. A new tunable that isn't in that file is invisible to forkers.
- **Environment beats the compiled profile**, which beats the code default. The
  profile is applied at import, then the env overrides are re-applied under it.
- **Match keywords on word boundaries** (`kw_matcher`), never as substrings.
  This is the repo's oldest recurring bug: `ai` fires inside "em**ai**l" and
  "r**ai**ses", `ui` inside "b**ui**lding", `sf` inside "Dussel**dorf**". Title
  stems are the deliberate exception — they're meant to match inside a longer
  title.
- **Show the arithmetic.** Scores carry a `parts` breakdown that the email
  prints. If a change makes the parts stop summing to the total, the email is
  lying — there's a test for exactly that.
- **Gates before spend.** Anything that costs an API call (enrichment, Hunter,
  discovery) runs after the free filters, and caches its result.

### Tests

```bash
python tests/run.py            # everything, ~1s, no network, no keys
python tests/run.py gates      # just the modules matching "gates"
```

`tests/fixtures/` holds strings captured from live Greenhouse / Ashby / Lever
boards; the **labels are hand-written intent**, not recorded output. Never
regenerate them from what the code currently returns — that re-records a bug
instead of catching it. `tests/README.md` has the rationale.

Accuracy checks that need the network and an API key live in `evals/` and are
deliberately outside `tests/run.py`:

```bash
python evals/eval_enrich.py --limit 3      # cheap smoke test
python tests/verify_grok_quotes.py         # checks X quotes against source posts
```

`run.py` sets `JOB_IGNORE_PROFILE=1` so the suite tests the **code**, not
whatever profile is on your machine — otherwise it would test one thing locally
and another in CI. To check the profile itself:

```bash
python tests/check_profile.py              # your compiled filters vs 34 labelled postings
```

### Adding a job source

1. Write `fetch_<source>()` in `companies_digest.py` returning dicts with
   `title`, `company`, `location`, `url`, `content`, `source`, and `posted_ts`
   when the API gives a date.
2. Add a `USE_<SOURCE> = env_flag("JOB_USE_<SOURCE>", True)` toggle next to the
   others, and gate it on its API key if it needs one.
3. Call it in the collection block, document the toggle in `.env.example`, and
   add the key to the workflow's `env:` if it needs a secret.

Everything downstream — gates, scoring, dedupe, email — is source-agnostic.

---

## Costs — you run this on your own accounts

**Every fork uses its own API keys and its own billing.** Nothing here talks to
a shared service, a proxy, or an account belonging to anyone else: the keys you
put in your `.env` and your repo secrets are the only credentials in play, the
calls bill to your accounts, and the digests go to your own inbox. Nobody else
can spend your quota and you cannot spend theirs.

Two paid APIs, both optional, both yours to sign up for:

| Key | Provider | Roughly what it costs |
|---|---|---|
| `ANTHROPIC_API_KEY` **or** `OPENAI_API_KEY` | console.anthropic.com / platform.openai.com | The biggest line, and the only setting here that can run up a bill. It is **capped at 10 raises per run by default** — set `FUNDING_ENRICH_TOP_N=all` for every raise (up to 40/day), an integer to change the cap, or `0` to turn enrichment off entirely and need no LLM key at all. `discover.py` adds one sweep per angle per run (2 by default). |
| `GROK_API_KEY` | console.x.ai | ~$0.005 per X search — cents per month. |

`HUNTER_API_KEY` (hunter.io) has a free tier of ~50 lookups/month; the script
rations against `hunter_usage.json` and falls back to pattern-guessed emails
(marked UNVERIFIED) once the cap is hit, so it will not silently start charging
you.

Everything else — the job boards, The Muse, Adzuna, Getro, Google News, SMTP —
is free or keyless.

`setup_profile.py` is the one Anthropic-only step, because it sends your resume
PDF and Claude reads PDFs natively. A `.txt` or `.md` resume compiles on either
provider.

GitHub Actions minutes come out of your own account's allowance, which is free
for public repositories.

Set `FUNDING_ENRICH_TOP_N=0` and leave `GROK_API_KEY` unset and this costs
nothing at all — you lose the AI company summaries and the X hiring posts, and
keep every job board.

This is MIT-licensed and provided as is, with no warranty (see `LICENSE`). It is
a personal tool published in the hope it is useful, not a service — nobody is
operating it on your behalf or responsible for what it costs or finds.

## Honest limits

- **Location:** hybrid, on-site and remote are all kept. Ranking prefers your
  primary metro (+10), then US-remote (+5, because you can do it from home
  today), then the secondary metro (+4), then on-site elsewhere in the US (+1).
  Only non-US roles and state-scoped remote ("Remote, TX" = Texas residents) are
  dropped. Every row carries a `hybrid` / `on-site` chip, amber when the office
  isn't in your metro.
- The email is the **top N by score**, sorted before it's cut. A role posted to
  several cities collapses to its best-ranked location, so a company advertising
  NYC + SF shows up as the NYC one.
- Two sections, capped separately: **Best matches** (`JOB_BEST_MATCH_MIN` and up,
  `JOB_MAX_ITEMS`) and **Worth a look** (below it, `JOB_MAX_ITEMS_REST`). Separate
  caps on purpose — one combined cap never reached the sub-threshold postings, so
  they were dropped rather than relegated.
- A big watchlist fetches a LOT to keep a little: ~380 boards yield ~21k
  postings, of which a few hundred clear the title gate and ~130 the location
  gate. That's the ATS approach working as intended — but it does mean your
  title and location gates decide almost everything that reaches your inbox.
- No free source gives verified founder emails at scale; guessed ones are
  guesses. Confirm before you send.
- LinkedIn and most VC sites can't be scraped; the AI reads them via web search,
  and LinkedIn appears as a click-to-search link.
- Keyword matching is a strong first filter, not judgment. Skim the top few.

This repo's committed defaults are tuned to one person's search (NYC,
early-career product roles) as a worked example. `profile.json` plus the
environment variables are how you make it yours without touching the code.
