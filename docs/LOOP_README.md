# The loop — funding digest ↔ companies digest

Auto-connects your two automations so companies flow from "just raised" into your
resume-scored job feed with no manual work.

## How it works
1. Funding digest enriches a freshly-raised company (gets its domain).
2. `watchlist.py` probes the public Greenhouse / Lever / Ashby board APIs to see
   if that company is on one, and if so saves its token to `ats_watchlist.json`.
   (Same public APIs the companies digest already uses — no scraping.)
3. The companies digest reads that file and starts scoring that company's postings
   against your resume — often the same day.

So: **company raises in the morning → its open roles show up ranked in your
afternoon companies digest.** You never copy an ATS token by hand.

## Files added
- `watchlist.py` — detection + shared-file logic (imported by both digests).
- `ats_watchlist.json` — the shared file (auto-created, travels via cache).

## How the file moves between the two workflows
GitHub Actions caches are **repo-scoped**, so both workflows share it via the
key prefix `watchlist-`:
- The funding workflow writes it and saves it under `watchlist-<run_id>`.
- The job workflow restores the latest `watchlist-*` before running.
Both cache steps are already added to the two workflow files.

## Good to know
- Detection is cached per company (`checked` list), so no company is probed twice.
- Token guessing isn't perfect — some companies use other ATSs or custom career
  pages and won't be detected. That's fine; they just won't auto-add.
- You can still hand-add companies to `GREENHOUSE_COMPANIES` etc. in
  companies_digest.py; auto-detected tokens merge on top of your manual list.
- To inspect what's been collected, open `ats_watchlist.json` (downloadable from
  a workflow run's cache, or just watch the companies digest logs for
  "+N auto-detected companies").

## Deploy order
Add `watchlist.py` alongside the other scripts. No new secrets. The loop is
active the next time both workflows run.
