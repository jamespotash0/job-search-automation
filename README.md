# Job-search automation

Two daily email digests + a loop that connects them, to fuel founder-direct
outreach and resume-matched job hunting. Runs free/near-free on GitHub Actions.

```
funding_digest.py ──detects ATS──► ats_watchlist.json ──feeds──► job_digest.py
     (who just raised,                (shared file)              (newest roles,
      enriched w/ contacts)                                       ranked vs resume)
```

## The three pieces
1. **Funding digest** (`funding_digest.py`) — daily email of startups that just
   raised, filtered to your focus, with AI company summaries + founder contacts
   on the top matches. → your outreach targets.
2. **Job digest** (`job_digest.py`) — 3x/day email of the newest postings for
   your target roles, filtered to junior/early-career and ranked by how well
   each matches your resume. → your apply list.
3. **The loop** (`watchlist.py`) — companies from the funding digest auto-flow
   into the job digest's company watchlist.

## Files
| File | What it is |
|------|-----------|
| `funding_digest.py` | Funding digest script |
| `enrich.py` | AI + Hunter.io enrichment (imported by funding digest) |
| `job_digest.py` | Job digest script |
| `watchlist.py` | The loop (imported by both digests) |
| `.github/workflows/daily-digest.yml` | Funding digest schedule (8am ET) |
| `.github/workflows/job-digest.yml` | Job digest schedule (9am/1pm/4pm ET) |
| `requirements.txt` | Python deps |

Per-piece detail lives in `docs/ENRICHMENT_README.md`, `docs/JOBS_README.md`,
`docs/LOOP_README.md`.

---

## Use it yourself (fork & configure)

This repo is a template — everyone runs their own copy with their own email,
keys, and job criteria. Nothing personal is committed; all secrets come from
environment variables / GitHub Actions secrets.

1. **Fork** this repo (or click *Use this template* / clone it).
2. **Add your secrets.** Locally: `cp .env.example .env` and fill it in. For the
   scheduled runs: in *your* fork, **Settings → Secrets and variables → Actions**
   and add the same keys (see the secrets table below). Only `EMAIL_*` are
   required; the API keys are optional.
3. **Make it yours — easy path (recommended): a profile from your resume.**
   No code editing. Copy `profile.example.json` → `profile.json`, drop your
   resume in `docs/`, and fill in five things:
   ```jsonc
   {
     "resume_file": "docs/your_resume.pdf",   // .pdf / .txt / .md
     "locations": ["new york", "remote"],      // [] = anywhere
     "remote_ok": false,
     "roles": "one sentence: the roles you want",
     "startup_types": "one sentence: the companies you want",
     "company_size": "small"                    // small | large | any
   }
   ```
   Then run once:
   ```bash
   python setup_profile.py        # Claude reads your resume + sentences
   ```
   It writes `profile.compiled.json` (title/skill/domain/location filters), which
   **both digests load automatically**. Commit that file so GitHub Actions uses
   it. Re-run whenever your resume or `profile.json` changes. (Needs
   `ANTHROPIC_API_KEY`; your resume never leaves the Anthropic API call.)

   **Advanced path:** skip the profile and edit the config blocks directly —
   `RESUME` / `PREFER_LARGER` / the `GREENHOUSE_/LEVER_/ASHBY_/WORKDAY_COMPANIES`
   watchlists in `job_digest.py`, and `FOCUS_KEYWORDS` / `LOCATION_KEYWORDS` in
   `funding_digest.py`. If no `profile.compiled.json` is present, these apply.
   (This repo's committed defaults are tuned to James as a worked example.)
4. **Run it.** Actions tab → pick a workflow → *Run workflow*, or run locally
   (see below). The schedules live in `.github/workflows/*.yml` (cron in UTC).

---

## Setup with Claude Code (recommended)

You have this whole folder. Easiest path: open it in Claude Code and let it do
the GitHub wiring for you.

### 1. Create the repo structure
In the project folder, arrange files like this (the workflow files MUST live
under `.github/workflows/`):

```
your-repo/
├── funding_digest.py
├── enrich.py
├── job_digest.py
├── watchlist.py
├── requirements.txt
└── .github/
    └── workflows/
        ├── daily-digest.yml
        └── job-digest.yml
```

A prompt you can hand Claude Code:
> "Move daily-digest.yml and job-digest.yml into .github/workflows/, leave the
>  .py files at the repo root, then init a git repo, create a private GitHub repo,
>  and push."

### 2. Add secrets
In the GitHub repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add:

| Secret | Required? | What |
|--------|-----------|------|
| `EMAIL_USER` | yes | Gmail address that sends the digests |
| `EMAIL_PASS` | yes | Gmail **app password** (not your login) |
| `EMAIL_TO` | yes | Where digests go (can equal EMAIL_USER) |
| `ANTHROPIC_API_KEY` | for AI summaries | from console.anthropic.com |
| `HUNTER_API_KEY` | for real emails | from hunter.io (optional) |

Gmail app password: enable 2-Step Verification → Google Account → Security →
App passwords → generate.

### 3. Test each digest
GitHub **Actions** tab → pick a workflow → **Run workflow**. Check your inbox in
~1 min. With no email secrets set, the script prints the digest to the run log
instead (good for a dry run).

### 4. Tune to you
- `funding_digest.py` CONFIG: stage/sector/location keywords, `ENRICH_TOP_N`.
- `job_digest.py` `RESUME` block: your titles/skills/domains. `PREFER_LARGER`
  (True = big companies rank first; **set False for small/early**, your best fit).
- Add ATS tokens for target companies to `GREENHOUSE_COMPANIES` etc. — or let the
  loop fill them in automatically. For Workday-based companies, add
  tenant/wd/site entries to `WORKDAY_COMPANIES`.

`RESUME` in `job_digest.py` is pre-loaded from James Potash's resume, and the ATS
lists are seeded with small/early-stage NYC startups in that lane (edra, probook,
valon, credal, rogo, alloy, …). The funding digest pulls from Google News +
TechCrunch + VentureBeat + EU-Startups + Tech.eu, plus a **From your newsletters**
section from any Substack in `SUBSTACK_FEEDS` (currently *next play*).

---

## Run locally (optional, no GitHub)
```bash
python3 -m venv .venv                       # create an isolated environment
.venv/bin/python -m pip install -r requirements.txt
export EMAIL_USER=... EMAIL_PASS=... EMAIL_TO=...
export ANTHROPIC_API_KEY=...   # optional
export HUNTER_API_KEY=...       # optional
.venv/bin/python funding_digest.py
.venv/bin/python job_digest.py
```
(Or activate the venv first with `source .venv/bin/activate`, then use plain
`python`.)
Then schedule with cron. (GitHub Actions is easier — it's free and needs no
machine left on.)

## Costs
- Digests + email + job sources: **free**.
- `ANTHROPIC_API_KEY`: a few cents/day at `ENRICH_TOP_N=8` (lower it to spend less).
- `HUNTER_API_KEY`: free tier ~50/mo; the script rations it and falls back to
  pattern-guessed emails (marked UNVERIFIED) after the cap.

## Honest limits (read once)
- Free job sources skew remote/tech; the **ATS watchlist is what makes the job
  digest good** — seed it from the funding digest + add a few by hand.
- No free source gives verified founder emails at scale; guessed ones are
  guesses — confirm before you send.
- LinkedIn/most VC sites can't be scraped; the AI reads them via web search, and
  LinkedIn appears as a click-to-search link.
- Keyword matching is a strong first filter, not judgment. Skim the top few.
