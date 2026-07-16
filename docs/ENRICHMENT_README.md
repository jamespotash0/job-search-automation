# Funding digest — enrichment add-on

Adds AI company research + founder-contact lookup to the top-ranked raises in
your daily funding digest.

## What each enriched raise now shows
- **What they do** — one-line description (AI researches the company via web search)
- **Website / domain**, **founder name(s)**, **who backed them** (investors)
- **Founder email** — real via Hunter.io until your monthly cap, then a
  pattern-based guess clearly marked **UNVERIFIED**
- **Click-to-search links** for LinkedIn + Crunchbase (no scraping — LinkedIn
  bans bots, so these are one-tap searches you run yourself)

Enrichment covers the **top N ranked raises per run** (default 8) to control
cost and quota. Set `ENRICH_TOP_N = 0` in funding_digest.py to turn it off.

## New secrets to add (repo → Settings → Secrets → Actions)
- `ANTHROPIC_API_KEY` — powers the AI research + web search. Get one at
  console.anthropic.com. Small per-run cost (a few cents/day at N=8).
- `HUNTER_API_KEY` — optional, for real founder emails. Free tier is limited.

## About the Hunter free tier — you WILL hit the cap
A daily digest enriching 8 companies = ~240 lookups/month, but the free tier is
~50. So the script **rations Hunter automatically**:
- Counts calls in `hunter_usage.json`, resets monthly, hard-stops at
  `HUNTER_MONTHLY_CAP` (default 50 — set it to your real quota).
- Never looks up the same company twice (`enrich_cache.json`).
- Once the cap is hit, it falls back to **pattern-guessed emails** (first@domain),
  labelled UNVERIFIED so you know to confirm before sending.

Practically: Hunter covers roughly your first ~50 companies each month (the
freshest, highest-ranked ones), then it's educated guesses. That's the honest
ceiling of the free tier — there's no free source for verified founder emails
at scale.

## Cost control knobs (top of the files)
- `ENRICH_TOP_N` (funding_digest.py) — how many raises get enriched per run.
- `HUNTER_MONTHLY_CAP` (enrich.py env) — your Hunter quota.
- `AI_MODEL` (enrich.py env) — defaults to a small/cheap model.

## Honest limits
- AI research is web-search-grounded but not infallible; verify before outreach.
- Founder emails, when guessed, are guesses. Treat UNVERIFIED as "likely, check it."
- LinkedIn and most VC portfolio sites can't be scraped reliably/legally, so the
  AI *reads* them via search instead, and LinkedIn is a click-to-search link.
