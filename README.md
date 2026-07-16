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

`RESUME` in `job_digest.py` is pre-loaded from James Potash's resume; the funding
digest pulls from Google News + TechCrunch + VentureBeat + EU-Startups + Tech.eu.

---

## Run locally (optional, no GitHub)
```bash
pip install -r requirements.txt
export EMAIL_USER=... EMAIL_PASS=... EMAIL_TO=...
export ANTHROPIC_API_KEY=...   # optional
export HUNTER_API_KEY=...       # optional
python funding_digest.py
python job_digest.py
```
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
