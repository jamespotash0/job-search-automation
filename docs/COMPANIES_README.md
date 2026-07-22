# Companies digest (3x daily)

Newest postings for your target roles — Product Manager, Associate PM, AI PM,
Forward Deployed Engineer, Deployment Strategist, AI Strategist, and adjacent —
**filtered to junior/early-career** and **ranked by how well each matches your
resume** (with company size as a tiebreak). Emailed at 9am, 1pm, and 4pm ET.

This is a separate digest from the funding one — separate script, separate
schedule, separate email subject ("Companies digest — …").

## How ranking works
Each posting gets a **qualification score** (the green number in the email):
- **Title match** — is it one of your target titles? (senior/staff/lead/director/VP auto-excluded)
- **Seniority fit** — "2+ years / associate / early career" boosts; "5+/7+ years" penalizes
- **Skill overlap** — your resume skills/tools found in the JD (SQL, Figma, TypeScript, discovery, GTM, 0-to-1, etc.)
- **Domain fit** — AI, workflow, B2B/SaaS, PropTech, accessibility/telecom, dev tools

Ties break by **company size** (`PREFER_LARGER = True` → bigger first; set
`False` to favor small/early — which fits your profile better). Size only works
for companies you list in `COMPANY_SIZE`; unknown companies rank neutral.

## Sources (all free)
- **Remotive** & **Arbeitnow** — open job APIs with full descriptions (work out of the box)
- **Hacker News "Who is hiring"** — monthly thread, great for startups/AI roles
- **Greenhouse / Lever / Ashby** — poll the public boards of companies you're
  targeting. **This is the power feature.** Add tokens to `GREENHOUSE_COMPANIES`,
  `LEVER_COMPANIES`, `ASHBY_COMPANIES`. Find a token in the careers URL:
  `boards.greenhouse.io/<TOKEN>`, `jobs.lever.co/<TOKEN>`, `jobs.ashbyhq.com/<TOKEN>`.
  Seed this list from your funding digest — companies that just raised are hiring.
- **Workday** — many mid/large companies run Workday. Add entries to
  `WORKDAY_COMPANIES`. A Workday board needs three parts from its careers URL
  `https://<tenant>.<wd>.myworkdayjobs.com/<site>`, e.g.
  `{"tenant":"nvidia","wd":"wd5","site":"NVIDIAExternalCareerSite"}` (or the
  shorthand string `"nvidia/wd5/NVIDIAExternalCareerSite"`). It pulls full
  descriptions and only spends a detail call on postings whose title already
  passes your filter (capped by `WORKDAY_MAX_DETAIL`). Workday boards skew
  senior/large, so most postings get filtered — best for a few specific targets.
  Unlike the token ATSs, Workday can't be auto-detected by the loop (it needs the
  tenant+datacenter+site triple), so add these by hand.
- **Web discovery** (`discover.py`) — Claude web-searches LinkedIn "we're hiring"
  posts, "who is hiring" articles, and hiring roundups for companies hiring your
  roles, then probes each company's Greenhouse/Lever/Ashby board and adds hits to
  `ats_watchlist.json` automatically. Runs once/day in the funding workflow
  (needs `ANTHROPIC_API_KEY`). LinkedIn can't be scraped directly, so this reads
  the public posts *about* who's hiring rather than LinkedIn itself.

**No SWE roles:** titles like "…Software Engineer", backend/frontend/full-stack,
data/ML engineer, devops, etc. are hard-excluded (`SWE_EXCLUDE`) — but
"Forward Deployed Engineer" and "Solutions Engineer" are kept.

## Setup
Same repo and secrets as the funding digest (`EMAIL_USER`, `EMAIL_PASS`,
`EMAIL_TO`). Just add:
- `companies_digest.py`
- `.github/workflows/companies-digest.yml`

Then: Actions tab → "Companies digest (3x daily)" → Run workflow to test. Dedup
across the day's 3 runs is handled by a cached `seen_jobs.json` (auto-managed).

**Quiet by default:** because dedup means most new postings land in the 9am run,
a run that turns up nothing new **skips the email** rather than sending an empty
"no new postings" digest. A genuinely fresh midday role still reaches you
same-day. Set `SEND_WHEN_EMPTY=1` (env / Actions secret) to always send — e.g.
as a daily heartbeat or while debugging.

## Tuning (top of companies_digest.py)
- `RESUME` — your titles/skills/domains/years. **Pre-loaded from James Potash's
  resume** (PM · PropTech/B2B SaaS · AI workflow · accessibility/telecom · NYC).
  Edit as your resume evolves — this vocabulary is what each JD is scored against.
- `REMOTIVE_SEARCHES` — the queries hit on the always-on source.
- `GREENHOUSE_/LEVER_/ASHBY_COMPANIES` — token watchlist (biggest quality lever).
- `WORKDAY_COMPANIES` — Workday boards (tenant/wd/site); `WORKDAY_MAX_DETAIL`
  caps per-board detail fetches.
- `COMPANY_SIZE` + `PREFER_LARGER` — the size tiebreak.
- `SEEN_TTL_DAYS` — how long before a still-open role can resurface (default 14).
- `SEND_WHEN_EMPTY` (env) — `1` to email even when a run finds nothing new;
  default off, so empty midday runs stay silent.

## Honest limits
- LinkedIn/Indeed can't be scraped freely, so out-of-the-box coverage skews
  remote/tech (Remotive/Arbeitnow/HN). **The ATS watchlist is what makes this
  genuinely good** — spend 20 min adding tokens for 15–20 target companies.
- Company headcount isn't exposed by most sources; fill `COMPANY_SIZE` for the
  companies you care about.
- Resume matching is keyword-based — a strong first-pass filter, not judgment.
  Skim the top 5, ignore the tail.

## NYC + small-first (current settings)
- `REQUIRE_NYC = True` — keeps only postings whose location is NYC **or** whose
  description says the company is NYC-based. Set `REMOTE_OK = True` to also keep
  remote roles. Edit `NYC_KEYWORDS` to widen/narrow the area.
- `PREFER_LARGER = False` — small/early companies rank first.
- Stage bias: postings mentioning seed / Series A / Series B / founding get a
  score boost; obviously-late/huge-company language gets a penalty.
- Because the always-on remote sources (Remotive/Arbeitnow) are rarely NYC, most
  NYC results will come from your **ATS watchlist** — which the funding digest
  now seeds with NYC seed/A/B companies automatically. Add a few NYC company
  tokens by hand to prime it.
