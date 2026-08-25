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
| **Companies digest** (`companies_digest.py`) | 3x/day email of the newest postings for your target roles, gated on title/seniority/location and ranked by resume fit. Runs that find nothing new stay silent (`SEND_WHEN_EMPTY=1` to always send). |
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
| `ANTHROPIC_API_KEY` | optional | AI company summaries + `discover.py`. From console.anthropic.com |
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
  "years_experience": 3,    // total relevant experience — vs "N years of experience"
  "years_pm": 2.5,          // years in your TARGET title — vs "N years of PRODUCT experience"
  "max_years": 5            // postings asking for more than this are filtered out
```

Then compile it once (needs `ANTHROPIC_API_KEY`; your resume is sent only to the
Anthropic API, never committed):

```bash
.venv/bin/python setup_profile.py
.venv/bin/python tests/check_profile.py    # <- do not skip this
```

`setup_profile.py` writes `profile.compiled.json` — the title / skill / domain /
location filters both digests load at startup. That list is written by a model
from your one-sentence `roles`, so **it drifts**: a real compile once produced a
list with no bare `forward deployed` stem, which silently dropped every "Forward
Deployed AI Engineer" posting. `check_profile.py` runs your compiled filters over
34 hand-labelled real postings and tells you if that happened.

Re-run both whenever your resume or `profile.json` changes. Both files are
gitignored, since this repo is public and they're personal.

**If you skip this step** the digests fall back to the filters baked into the
scripts, which are the repo author's job search, not yours. They now say so
loudly on startup rather than quietly producing a plausible digest for someone
else's career.

### 4. Check it before you run it

```bash
.venv/bin/python doctor.py           # local + GitHub checks
.venv/bin/python doctor.py --local   # skip the GitHub half
```

Every problem it looks for is **silent in production**: a missing
`PROFILE_COMPILED_JSON` secret doesn't error, it emails you someone else's job
search; an empty watchlist doesn't error, it emails you three postings instead
of thirty; a compiled profile older than your resume doesn't error, it quietly
uses last month's filters. `✗` means wrong results, `!` means a source is off.

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
| `companies-digest.yml` | ~9am / 1pm / 4pm ET | `companies_digest.py` |
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
| A fuller / quieter inbox | `JOB_BEST_MATCH_MIN` (score cutoff), `JOB_MAX_ITEMS`, `JOB_MAX_AGE_DAYS` |
| Lower API spend | `FUNDING_ENRICH_TOP_N=8`, or `=0` to turn AI summaries off |
| Fewer sources | `JOB_USE_MUSE=0`, `JOB_USE_ADZUNA=0`, … (one flag per source) |

Lists are comma-separated, and an explicitly empty value means an empty list —
`JOB_SECONDARY_LOCATIONS=` turns that tier off rather than restoring the default.
Booleans take `1/true/yes/on` or `0/false/no/off`. Penalties (`JOB_TOO_SENIOR_PENALTY`,
`JOB_LATE_STAGE_PENALTY`) are given as positive numbers and always subtract.

### How a posting is scored

Every email row prints its own arithmetic, so you can see which knob to turn:

| Signal | Default | Env |
|---|---|---|
| Target-title hit | +12 each, cap 40 | `JOB_TITLE_POINTS` / `JOB_TITLE_MAX` |
| JD asks for your years | +20 | `JOB_SENIORITY_FIT_POINTS` |
| JD asks for far more | −25 | `JOB_TOO_SENIOR_PENALTY` |
| Resume skill hit | +3 each, cap 24 | `JOB_SKILL_POINTS` / `JOB_SKILL_MAX` |
| Domain hit | +2 each, cap 12 | `JOB_DOMAIN_POINTS` / `JOB_DOMAIN_MAX` |
| Seed–Series B | +6 (+3 if only inferred from startup language) | `JOB_EARLY_STAGE_POINTS`, `JOB_EARLY_STAGE_SOFT_POINTS` |
| Series D+ / public | −6 | `JOB_LATE_STAGE_PENALTY` |
| Location tier | NYC +10, US-remote +5, SF/Bay +4, US on-site +1 | `JOB_LOC_BONUS_*` |
| Company just raised | +8 | `JOB_FUNDED_BONUS` |

Title, skill and domain terms come from `profile.compiled.json` (or
`JOB_TARGET_TITLES` / `JOB_SKILLS` / `JOB_DOMAINS`). Title gates run *before*
scoring: a posting that fails `title_is_target` or the location gate is never
scored at all.

Every row in the email carries a **Why N** line showing both halves of that
arithmetic — what hit, and what didn't:

```
Why 72  |  +12 partial title match · associate product engineer
        |  +20 seniority fit · 1-2 years
        |  +24 strong skills match · claude code, spec, figma, qa +7
        |  +6 good domain match · automation, agent, workflow
   0  stage: no funding stage stated
```

Capped signals (title, skills, domains) are labelled *partial* / *good* /
*strong* against their ceiling, because `+9 skills` means nothing until you know
the cap is 24. The dimmed `0` line lists signals that scored **nothing** — an
unstated funding stage, no domain overlap, a title that matched none of your
targets. Those are worth zero by construction and stay out of the sum, so the
printed parts always add up to the total. If a posting scores lower than you
expect, that line is where the answer is.

The funding digest scores headlines on the same principle, with its own weights
(`FUNDING_FOCUS_POINTS`, `FUNDING_LOC_POINTS`, `FUNDING_LOC2_POINTS`,
`FUNDING_ROUND_POINTS`) and its own section cutoff (`FUNDING_BEST_SCORE`).

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
| `tests/` | Stdlib-only unit suite, no network |
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

`run.py` sets `JOB_IGNORE_PROFILE=1` so the suite tests the **code**. Without it
the same command tested your personal profile locally and the repo defaults in
CI — two subjects, one green tick. To check the profile itself:

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
| `ANTHROPIC_API_KEY` | console.anthropic.com | The biggest line. With `FUNDING_ENRICH_TOP_N` unset, **every** raise gets a web-search call, so it scales with the day's volume (often 10–40 raises). Set it to an integer to cap spend, or `0` to turn summaries off. `discover.py` adds one sweep per angle per run. |
| `GROK_API_KEY` | console.x.ai | ~$0.005 per X search — cents per month. |

`HUNTER_API_KEY` (hunter.io) has a free tier of ~50 lookups/month; the script
rations against `hunter_usage.json` and falls back to pattern-guessed emails
(marked UNVERIFIED) once the cap is hit, so it will not silently start charging
you.

Everything else — the job boards, The Muse, Adzuna, Getro, Google News, SMTP —
is free or keyless. **There is no OpenAI dependency**; the two LLM calls go to
Anthropic and xAI.

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
