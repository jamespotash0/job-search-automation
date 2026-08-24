#!/usr/bin/env python3
"""
Companies digest — newest postings for your target roles, ranked by how well
they match your resume (primary) and company size (secondary tiebreak).

FREE sources, no paid API:
  - Remotive API (remote tech jobs, includes descriptions)   [default on]
  - Arbeitnow API (open job board)                            [default on]
  - Hacker News "Who is hiring" thread via Algolia            [optional]
  - The Muse (keyless, has locations)                         [default on]
  - Greenhouse / Lever / Ashby / Workday public boards        [watchlist]
  - Workable / SmartRecruiters / Rippling public boards       [watchlist]
Plus, with a free key: Adzuna (aggregator) and X/Grok (founder hiring posts).

The watchlist is fed by getro.py (VC portfolio boards, bulk) and discover.py
(web search, long tail); boards are fetched concurrently because that list runs
to the hundreds.

Runs 3x/day (9am, 1pm, 4pm ET) via GitHub Actions. A seen-cache prevents the
1pm/4pm runs from repeating what the 9am run already sent.

Honest limits: free sources rarely expose company headcount, so "size" ranking
is best-effort via the COMPANY_SIZE lookup you fill in. Qualification match is
the reliable ranker.
"""

import os
import re
import json
import html
import smtplib
import hashlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime, timezone


def load_dotenv(path=".env"):
    """Minimal .env loader so local runs pick up keys without exporting them.
    Runs before the source toggles below read the environment. In GitHub Actions
    there's no .env — those values come from repo secrets — so this is a no-op there."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


load_dotenv()

# Accept either name for the xAI/Grok key: the docs call it XAI_API_KEY, but
# GROK_API_KEY is the intuitive name, so honor both (XAI_API_KEY wins if both set).
if not os.environ.get("XAI_API_KEY") and os.environ.get("GROK_API_KEY"):
    os.environ["XAI_API_KEY"] = os.environ["GROK_API_KEY"]


# ==========================================================================
# SETTINGS FROM THE ENVIRONMENT
# ==========================================================================
# Everything a forker is likely to want different — scoring weights, gates,
# caps, which sources run — reads the environment first and falls back to the
# value written below it, so tuning the bot never means editing Python. Set
# them in .env locally, or as repo secrets/vars for scheduled Actions runs.
# Precedence: code default < profile.compiled.json < environment variable.
def env_int(name, default):
    """Integer setting; the default on anything unset or unparseable."""
    try:
        return int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


def env_flag(name, default):
    """Boolean setting. 1/true/yes/on and 0/false/no/off; unset = the default."""
    v = str(os.environ.get(name, "")).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def env_list(name, default):
    """Comma-separated list, lowercased and stripped. An explicitly EMPTY value
    means an empty list — which is why this can't be `os.environ.get() or x`:
    JOB_SECONDARY_LOCATIONS="" has to be able to turn the tier off."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


# ==========================================================================
# YOUR RESUME PROFILE  — drives the qualification score
# ==========================================================================
RESUME = {
    # Titles you're targeting (matched against posting titles).
    "target_titles": [
        # Core PM + tight product-family variants only — kept deliberately narrow.
        "product manager", "associate product manager", "apm",
        "ai product manager", "technical product manager",
        "founding product manager", "founding pm",
        "product owner", "product strategy",
        # Product operations (incl. "Product Operations Manager" / "Associate").
        "product operations", "product ops",
        # Forward-deployed family — the named titles only, not the wider
        # solutions-engineer / implementation / strategist net. "forward deployed"
        # as a stem already covers the FD PM / associate FDE variants; the explicit
        # entries keep the intent readable and score them a little harder.
        "forward deployed", "forward deployed engineer",
        "forward deployed product manager", "associate forward deployed engineer",
        "deployment strategist",
        # Founder's-associate family — the generalist first-hire role at seed-stage
        # startups, where the work is product in everything but name. Apostrophe
        # variants are all listed because titles arrive punctuated every which way
        # (and _norm() folds the curly apostrophe to a straight one first).
        "founder's associate", "founders associate", "founder associate",
        "founding associate", "founder's office", "founders office",
        "office of the founder",
        # "AI product builder" and its cousins — the title startups reach for when
        # the job is prototype-to-production product work on an AI surface.
        "ai product builder", "product builder", "ai prototyper", "ai builder",
        # Product engineer, associate level only — a product-facing build role, not
        # a general SWE req. Deliberately NOT the bare "product engineer" stem, which
        # is how backend/full-stack reqs are titled at most startups.
        "associate product engineer",
    ],
    # Skills / tools from your resume (keyword overlap with the JD).
    "skills": [
        "roadmap", "roadmapping", "prd", "prds", "ai prototyping", "prototyping",
        "ux", "ui", "ui/ux", "product discovery", "discovery", "competitive analysis",
        "gtm", "go-to-market", "stakeholder", "cross-functional", "agile", "scrum",
        "sprint", "standup", "user research", "prioritization", "acceptance criteria",
        "self-serve", "vendor management", "customer success", "onboarding",
        "jira", "sql", "figma", "confluence", "notion", "hubspot", "posthog",
        "airtable", "git", "typescript", "python", "java",
        "0 to 1", "0-to-1", "zero to one", "multi-tenant", "b2b", "saas",
        "api", "quote-to-cash", "quoting",
        # AI-native product work — the vocabulary a 2026 JD uses for the job you
        # actually do. Without these, a posting whose whole premise is "build
        # product with Claude Code" scored only on figma/ux/ui and read as a
        # weak match (the Book of the Month Product Engineer req is the case).
        "claude code", "claude", "agentic", "ai agents", "prompt engineering",
        "ai-native", "ai native",
        # Spec-to-ship vocabulary. "prd"/"roadmap" above is how a PM job is
        # described; these are how the same work is described at a startup that
        # doesn't use the word PRD.
        "spec", "specs", "qa", "experiment", "experimentation", "a/b", "a/b testing",
        "multivariate", "data analysis", "landing page",
    ],
    # Domains you have real exposure to (bonus points).
    "domains": [
        "ai", "artificial intelligence", "workflow", "automation", "agent",
        "llm", "proptech", "real estate", "construction", "vendor",
        "accessibility", "captioning", "telecom", "enterprise",
        "developer", "infrastructure", "fintech", "b2b", "saas",
    ],
    # ~ years of experience. Postings asking for much more get penalized.
    "years": 3,
}

# --- Title gates. All three are env-overridable comma-separated lists, since a
# fork with a different seniority or lane needs exactly these changed and
# nothing else: a senior candidate sets JOB_SENIORITY_EXCLUDE="intern,junior"
# to invert the gate, and an engineer clears JOB_SWE_EXCLUDE="" outright.
# Titles that mean "too senior" — hard-exclude if present.
SENIORITY_EXCLUDE = env_list("JOB_SENIORITY_EXCLUDE", [
    "senior", "sr.", "sr ", "staff", "principal", "lead ", "director",
    "head of", "vp", "vice president", " ii", " iii", "manager of managers",
    "group product", "chief",
])

# Software-engineering (coding) roles to hard-exclude — you want product /
# deployment / solutions, NOT SWE. Note "Forward Deployed Engineer" and
# "Solutions Engineer" are KEPT (not coding-IC roles); only titles like
# "...Software Engineer", backend/frontend/full-stack, data/ML eng, etc. drop.
SWE_EXCLUDE = env_list("JOB_SWE_EXCLUDE", [
    "software engineer", "software developer", "swe ", "backend", "back-end",
    "front end", "front-end", "frontend", "full stack", "full-stack", "fullstack",
    "web developer", "mobile engineer", "ios engineer", "android engineer",
    "data engineer", "machine learning engineer", "ml engineer", "devops",
    "site reliability", " sre", "security engineer", "firmware", "game developer",
    "developer advocate",
])

# Same-title-stem, wrong job. Rogo posts a "Forward Deployed Investor" and a
# "Forward Deployed Banker" — real NYC roles that match the "forward deployed"
# stem and are finance jobs, not product ones. Found by running the gate over a
# real board dump; there is no way to guess these from first principles.
OFF_LANE_EXCLUDE = env_list("JOB_OFF_LANE_EXCLUDE", [
    "investor", "banker", "trader", "analyst, investment", "recruiter",
    "account executive", "sales representative", "sdr", "bdr",
])

# Words in the JD body that signal a junior/early-career fit (boost).
JUNIOR_SIGNALS = env_list("JOB_JUNIOR_SIGNALS", [
    "2+ years", "1+ year", "0-2", "1-3 years", "2-3 years", "associate",
    "early career", "early-career", "entry level", "entry-level", "new grad",
    "junior", "recent graduate", "1-2 years",
])

# Words that signal it wants a lot more experience than you have (penalty).
SENIOR_SIGNALS = env_list("JOB_SENIOR_SIGNALS",
                          ["5+ years", "6+ years", "7+ years", "8+ years",
                           "10+ years", "minimum of 5", "minimum of 6",
                           "minimum of 7"])

# ==========================================================================
# SOURCES
# ==========================================================================
# Each source is individually switchable — a forker who wants ATS boards only,
# or who burned through an aggregator's free tier, turns one off without
# touching code.
USE_REMOTIVE = env_flag("JOB_USE_REMOTIVE", True)
USE_ARBEITNOW = env_flag("JOB_USE_ARBEITNOW", True)
USE_HN_WHOISHIRING = env_flag("JOB_USE_HN", True)

USE_WORKDAY = env_flag("JOB_USE_WORKDAY", True)

# The Muse — free, keyless job API with location filtering. Reaches startups not
# on our ATS watchlist. https://www.themuse.com/developers/api/v2
USE_MUSE = env_flag("JOB_USE_MUSE", True)

# Adzuna — free aggregator (indexes many boards + company sites). Needs a free
# app id + key from https://developer.adzuna.com ; auto-disables if unset.
USE_ADZUNA = env_flag("JOB_USE_ADZUNA", True) and bool(
    os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))

# X / Grok Live Search — surfaces founder "we're hiring" posts that never hit a
# board (the Courted-APM-on-LinkedIn case). Needs XAI_API_KEY; auto-disables if
# unset. Cost is ~$0.005 per search (xAI X-search tool), so ~cents/month.
USE_GROK_X = env_flag("JOB_USE_GROK_X", True) and bool(os.environ.get("XAI_API_KEY"))

# How much of each job description we keep. 1500 was too small to be honest:
# the requirements block ("5+ years of...") and the pay band live near the BOTTOM
# of a JD, so a 1500-char window scored every posting on its marketing preamble
# and never saw the two facts that actually disqualify one.
JD_CHARS = int(os.environ.get("JOB_JD_CHARS", "8000"))
# Concurrent ATS board fetches. Each board is a different host, so this is
# parallelism across services rather than pressure on any one of them.
BOARD_WORKERS = int(os.environ.get("BOARD_WORKERS", "8"))

REMOTIVE_SEARCHES = env_list("JOB_REMOTIVE_QUERIES", [
    "product manager", "associate product manager",
    "technical product manager", "ai product manager",
    "forward deployed", "forward deployed product manager",
    "associate forward deployed engineer", "deployment strategist",
    "associate product engineer", "product operations",
    "product owner", "founders associate", "ai product builder"])

# Compact query set reused by the aggregators (Muse/Adzuna) and the X search.
# Fewer, broader phrases than REMOTIVE_SEARCHES to stay within free-tier call
# budgets; the resume title/seniority filter downstream does the fine sorting.
JOB_SEARCH_QUERIES = env_list("JOB_SEARCH_QUERIES", [
    "product manager", "associate product manager",
    "ai product manager", "forward deployed engineer",
    "forward deployed product manager", "product operations",
    "deployment strategist", "founders associate",
    "ai product builder"])

# ATS watchlist — add ATS tokens for companies you're targeting (e.g. from the
# funding digest). Find the token in a company's careers URL:
#   boards.greenhouse.io/<TOKEN>   jobs.lever.co/<TOKEN>   jobs.ashbyhq.com/<TOKEN>
# Seeded with SMALL / early-stage (seed–Series B) NYC startups whose live boards
# carry PM / FDE / deployment / AI-strategist / solutions roles in your lane —
# each verified against the ATS's public API, location-checked to NYC, and kept
# small (≤~65 open roles, i.e. not the 400-role giants). The loop auto-adds more
# as companies raise; trim/extend freely.
GREENHOUSE_COMPANIES = [
    "alloy",         # fintech/identity · founding Forward Deployed Engineer
    "vestwell",      # fintech/retirement · Implementations Manager
    "lithic",        # fintech/cards (Privacy.com) · NYC
    "northspyre",    # PROPTECH · NYC (your Stringbean domain)
    "vts",           # PROPTECH · NYC (your domain)
    "honeycomb",     # PROPTECH/insurance · NYC (your domain)
]
LEVER_COMPANIES = []        # e.g. ["somestartup"]
ASHBY_COMPANIES = [
    # NYC-HQ, fitting roles now
    "edra",          # AI · AI Strategist (New York)
    "probook",       # AI · Deployment Strategist, Implementation Manager (Manhattan)
    "credal",        # AI/enterprise · Founding GTM Deployment Strategist (Brooklyn)
    "normalcomputing",  # AI · Forward Deployed Engineer (NYC)
    "valon",         # PROPTECH/mortgage · Deployment Strategist, Implementation (3 NYC roles)
    "rogo",          # fintech/AI · Product Manager | Agents (NYC)
    "kalshi",        # fintech/markets · Product Manager, Growth/Payments (NYC)
    "rho",           # fintech · Product Manager (NYC)
    "highbeam",      # fintech/SMB · NYC
    "eliseai",       # PROPTECH/AI · NYC · Solutions Engineer (you interviewed here)
    # SF/London-HQ but they DO staff NYC — the per-role location gate keeps only
    # their NYC postings, so seeding them just means "watch for NYC roles here".
    "decagon",       # AI agents · 24 NYC roles live
    "merge",         # unified API · 11 NYC roles live
    "distyl",        # AI/enterprise deployment (AI Strategist, AI Operator)
    "context",       # AI · Forward Deployed Engineer, Deployment Strategist
    "meticulous",    # AI/dev tools · Forward Deployed Engineer
    "parafin",       # fintech/embedded · Forward Deployed Engineer
    "method",        # fintech/payments · Solutions Engineer
    "stytch",        # dev tools/auth · Solutions Engineer
    "mangomint",     # AI/vertical SaaS · GTM Associate, AI Product (US-remote)
]

# Workday boards. Unlike the token-based ATSs above, a Workday board needs THREE
# parts you read from the careers URL:
#   https://<tenant>.<wd>.myworkdayjobs.com/<site>
# e.g. https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
#   ->  {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"}
# You can also use the shorthand string "tenant/wd/site". Big/late companies
# dominate Workday, so most matches will be filtered by the seniority gate — it's
# most useful for a handful of specific mid/large targets you want to track.
WORKDAY_COMPANIES = [
    # {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"},
    # "someco/wd1/Careers",
]
WORKDAY_SEARCH = "product"   # server-side search to cut noise before scoring
WORKDAY_MAX_DETAIL = env_int("WORKDAY_MAX_DETAIL", 25)   # job-detail fetches per board

# Company size lookup (lowercased name -> approx employees). Fill in the ones
# you care about; unknown companies rank neutral.
COMPANY_SIZE = {
    # "ramp": 1000, "somestartup": 25,
}
PREFER_LARGER = False   # False = small/early companies rank higher (your fit).
                        # Env: JOB_PREFER_LARGER=1 (applied below the profile).

# --- Location: keep NYC roles, plus US-remote roles ----------------------
REQUIRE_NYC = True
REMOTE_OK = False   # True = enable the global remote boards (Remotive/Arbeitnow)

# Keep US-remote roles too, not just NYC-tagged ones. Many early-startup roles
# (e.g. Mangomint's "GTM Associate, AI Product") are listed as a bare "United
# States" or "Remote — US" with no city, so a strict NYC-substring gate drops
# them even though you could do them from NYC. This widens the gate to: NYC, OR
# US-remote / US-national, while still dropping on-site-elsewhere (SF, Austin)
# and non-US roles. Set JOB_ALLOW_US_REMOTE=0 to go back to strict NYC-only.
ALLOW_US_REMOTE = os.environ.get("JOB_ALLOW_US_REMOTE", "1").lower() not in ("0", "false", "no")

# On-site outside NYC used to be dropped outright. It isn't any more: hybrid,
# on-site and remote are all acceptable, with NYC strongly preferred for the
# ones that put you in an office. So a role on-site in a named US state is KEPT
# and ranked last, rather than never being seen. Set JOB_ALLOW_US_ONSITE=0 to go
# back to dropping it.
ALLOW_US_ONSITE = os.environ.get("JOB_ALLOW_US_ONSITE", "1").lower() not in ("0", "false", "no")

NYC_KEYWORDS = [
    "new york", "nyc", "new york city", "brooklyn", "manhattan",
    "ny,", " ny ", ", ny", "queens", "n.y.",
]

# Secondary metro: roles you'd take, but ranked below NYC. Kept OUT of
# NYC_KEYWORDS on purpose — profile.compiled.json overwrites NYC_KEYWORDS from
# profile.json's `locations`, and folding SF in there would make SF postings
# indistinguishable from NYC ones in the ranking. Set JOB_SECONDARY_LOCATIONS=""
# to go back to NYC-plus-US-remote only, or pass your own comma-separated list.
_SECONDARY_DEFAULT = (
    "san francisco,sf,south san francisco,bay area,san francisco bay area,"
    "palo alto,menlo park,mountain view,redwood city,sunnyvale,san mateo,"
    "oakland,berkeley,santa clara,burlingame,daly city,emeryville"
)
SECONDARY_LOC_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("JOB_SECONDARY_LOCATIONS", _SECONDARY_DEFAULT).split(",")
    if k.strip()
]

# A bare national/remote US location (no city) — treated as US-remote and kept.
US_NATIONAL_LOC = {
    "united states", "usa", "us", "u.s.", "u.s.a.", "u.s.a", "america",
    "united states of america", "remote", "remote us", "us remote",
    "anywhere", "anywhere in the us", "nationwide",
}
US_LOC_HINTS = ["united states", "usa", "u.s.", "us-based", "us based",
                "remote - us", "remote, us", "remote (us", "remote us",
                "us remote", "anywhere in the us", "onsite or remote"]
# A "remote" role scoped to one state is not a US-remote role — "Remote, TX"
# means Texas residents. NY is absent on purpose (NYC_KEYWORDS owns it), and so
# are the ambiguous two-letter abbreviations that collide with English words
# ("or", "in", "me", "hi", "ok", "de", "la", "pa", "id"): those are matched by
# full name only, since a false positive here silently drops a real posting.
_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "dc", "fl", "ga", "ia", "il",
    "ks", "ky", "ma", "md", "mi", "mn", "mo", "ms", "mt", "nc", "nd", "ne",
    "nh", "nj", "nm", "nv", "oh", "sc", "sd", "tn", "tx", "ut", "va", "vt",
    "wa", "wi", "wv", "wy",
}
_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
]
# Abbreviations only in the ", XX" slot a location string actually uses.
_TRAILING_STATE_RE = re.compile(r",\s*([a-z]{2})\b\s*$", re.I)


def _state_scoped(loc):
    """True if this location names one specific non-NY state."""
    m = _TRAILING_STATE_RE.search(loc)
    if m and m.group(1).lower() in _STATE_ABBR:
        return True
    return any(re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", loc) for n in _STATE_NAMES)


# Location terms that mark a role as NOT US — drop these even when "remote".
NON_US_LOC_HINTS = [
    "united kingdom", "uk", "london", "england", "scotland", "ireland", "dublin",
    "canada", "toronto", "vancouver", "ontario", "europe", "european", "emea",
    "germany", "berlin", "munich", "france", "paris", "spain", "madrid",
    "netherlands", "amsterdam", "india", "bangalore", "bengaluru", "mumbai",
    "singapore", "australia", "sydney", "apac", "latam", "brazil", "mexico",
    "israel", "tel aviv", "poland", "portugal", "lisbon", "sweden", "stockholm",
    "dubai", "uae", "nigeria", "kenya", "south africa", "philippines", "worldwide",
]

# --- Stage bias: nudge seed / Series A / B to the top -------------------
EARLY_STAGE_SIGNALS = [
    "seed", "series a", "series b", "pre-seed", "pre seed", "early stage",
    "early-stage", "founding team", "stealth", "backed by", "raised our",
]
LATE_STAGE_SIGNALS = [
    "publicly traded", "public company", "fortune 500", "nasdaq:", "nyse:",
    "10,000 employees", "20,000 employees", "enterprise-scale",
    # Rounds past your stated seed-to-B band, and the tells of a company that
    # already exited. Word-boundary matched (see _late_re) because "ipo" as a
    # substring fires inside "ipod" and "participio".
    "series d", "series e", "series f", "ipo", "publicly listed",
    "1,000 employees", "5,000 employees", "thousands of employees",
]

# Softer early-stage tells — the language a seed-to-B company uses when the JD
# never names a round. The Courted APM posting is the case: "high-growth SaaS
# company shipping fast", "small, focused team", "Everyone builds" — plainly a
# startup, and it scored no stage bonus at all because it said no round out loud.
#
# Individually these are boilerplate: half the postings on earth say they move
# fast. So a single hit earns NOTHING — EARLY_STAGE_SOFT_MIN must land before the
# bonus applies, which is what separates a cluster of startup language from one
# stray recruiter phrase. Any late-stage tell suppresses them outright, since a
# scale-up describes itself in exactly this vocabulary too. Worth half a named
# round, because that is roughly how much less certain the read is.
EARLY_STAGE_SOFT = [
    "small team", "small, focused team", "founding engineer", "first product hire",
    "early employee", "wear many hats", "from the ground up", "everyone builds",
    "high agency", "scrappy", "generalist", "ground floor", "shipping fast",
    "ship fast", "high-growth", "high growth", "startup", "0 to 1", "zero to one",
]
# How many soft tells must land before the bonus applies. Env-tunable:
# raise it to 3 for a stricter read, or set it to 1 to trust a single tell.
EARLY_STAGE_SOFT_MIN = env_int("JOB_EARLY_STAGE_SOFT_MIN", 2)

MAX_ITEMS = int(os.environ.get("JOB_MAX_ITEMS", "30"))
# Separate cap for the below-threshold "Worth a look" section, so relegating a
# posting doesn't cost a slot in the main list.
MAX_ITEMS_REST = int(os.environ.get("JOB_MAX_ITEMS_REST", "15"))
SEEN_FILE = "seen_jobs.json"
# How long a posting stays in the "already emailed you this" cache.
SEEN_TTL_DAYS = env_int("JOB_SEEN_TTL_DAYS", 14)

# Only email when a run actually turns up a new posting. The seen-cache means
# almost everything lands in the morning run — the 1pm/4pm runs then found
# nothing new and still emailed an empty "no new postings" digest, which was pure
# noise. With this on (default), an empty run stays silent; a genuinely fresh
# midday role still reaches you same-day. Set SEND_WHEN_EMPTY=1 to always send
# (e.g. as a daily heartbeat, or while debugging).
SEND_WHEN_EMPTY = os.environ.get("SEND_WHEN_EMPTY", "").lower() in ("1", "true", "yes")

# Recency: drop postings older than this many days, using the board's own
# posted/updated timestamp. Newer postings also rank higher (tiebreak after the
# resume match). Sources that don't expose a date are kept and shown without an
# age. Set to 0 to disable age filtering entirely. Overridable via profile.json.
MAX_AGE_DAYS = 21

# ==========================================================================
# Optional compiled profile (from setup_profile.py). If profile.compiled.json
# exists it overrides RESUME + location/size/recency settings above — so anyone
# can configure everything from profile.json + their resume without editing code.
# Absent = the defaults above apply (this repo's default is tuned to James).
# ==========================================================================
def _apply_profile():
    global NYC_KEYWORDS, REMOTE_OK, REQUIRE_NYC, PREFER_LARGER, MAX_AGE_DAYS
    try:
        with open("profile.compiled.json") as f:
            p = json.load(f)
    except Exception:
        return
    for k in ("target_titles", "skills", "domains", "years"):
        if p.get(k):
            RESUME[k] = p[k]
    if p.get("location_keywords") is not None:
        NYC_KEYWORDS = [k.lower() for k in p["location_keywords"]]
        REQUIRE_NYC = bool(NYC_KEYWORDS)
    if "remote_ok" in p:
        REMOTE_OK = bool(p["remote_ok"])
    if "prefer_larger" in p:
        PREFER_LARGER = bool(p["prefer_larger"])
    if p.get("max_age_days") is not None:
        MAX_AGE_DAYS = int(p["max_age_days"])
    print(f"[profile] using profile.compiled.json — {len(RESUME['target_titles'])} titles, "
          f"{len(NYC_KEYWORDS)} location keywords, remote_ok={REMOTE_OK}, "
          f"max_age_days={MAX_AGE_DAYS}")


_apply_profile()

# The environment gets the last word: a forker running the repo's committed
# profile can still retune geography or recency for one run without recompiling
# it, and Actions can override per-workflow. Unset vars keep whatever the
# profile (or the default above) decided.
RESUME["target_titles"] = env_list("JOB_TARGET_TITLES", RESUME["target_titles"])
RESUME["skills"] = env_list("JOB_SKILLS", RESUME["skills"])
RESUME["domains"] = env_list("JOB_DOMAINS", RESUME["domains"])
RESUME["years"] = env_int("JOB_YEARS", RESUME["years"])
NYC_KEYWORDS = env_list("JOB_PRIMARY_LOCATIONS", NYC_KEYWORDS)
REQUIRE_NYC = env_flag("JOB_REQUIRE_LOCATION", bool(NYC_KEYWORDS))
REMOTE_OK = env_flag("JOB_REMOTE_OK", REMOTE_OK)
PREFER_LARGER = env_flag("JOB_PREFER_LARGER", PREFER_LARGER)
MAX_AGE_DAYS = env_int("JOB_MAX_AGE_DAYS", MAX_AGE_DAYS)

# Secrets
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")
EMAIL_TO   = os.environ.get("EMAIL_TO", EMAIL_USER)
SMTP_HOST  = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))

UA = {"User-Agent": "Mozilla/5.0 (companies-digest)"}


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def post_json(url, payload, timeout=25):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**UA, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def strip_html(t):
    """Markup -> plain text.

    Unescape FIRST, then strip. Greenhouse (and Ashby's `description`) return the
    JD as HTML-escaped markup — `&lt;li&gt;5+ years&lt;/li&gt;` — so stripping
    first matches no tags at all, and the unescape then turns the entities back
    into visible `<li>` litter in the text we score and quote. Two passes,
    because unescaping can itself reveal a tag."""
    t = html.unescape(t or "")
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"[ \t\xa0]+", " ", t).strip()


def to_ts(value):
    """Best-effort parse of a date/timestamp from any source -> unix seconds, or
    None if unknown. Handles ISO-8601 strings and epoch seconds/milliseconds."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        v = float(value)
        return v / 1000.0 if v > 1e12 else v          # milliseconds -> seconds
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)       # naive -> assume UTC
        return dt.timestamp()
    except Exception:
        return None


def age_label(ts):
    if not ts:
        return ""
    days = (datetime.now(timezone.utc).timestamp() - ts) / 86400.0
    if days < 1:
        return "today"
    if days < 2:
        return "1d ago"
    return f"{int(days)}d ago"


# --------------------------- fetchers (each returns list of normalized dicts)
def fetch_remotive():
    out = []
    for q in REMOTIVE_SEARCHES:
        try:
            data = get_json(f"https://remotive.com/api/remote-jobs?search={quote_plus(q)}")
            for j in data.get("jobs", [])[:40]:
                out.append({
                    "title": j.get("title", ""),
                    "company": j.get("company_name", ""),
                    "location": j.get("candidate_required_location", "Remote"),
                    "url": j.get("url", ""),
                    "content": strip_html(j.get("description", ""))[:1200],
                    "posted_ts": to_ts(j.get("publication_date")),
                    "source": "Remotive",
                })
        except Exception as e:
            print(f"[warn] remotive '{q}': {e}")
    return out


def fetch_arbeitnow():
    out = []
    try:
        data = get_json("https://www.arbeitnow.com/api/job-board-api")
        for j in data.get("data", [])[:100]:
            out.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location", ""),
                "url": j.get("url", ""),
                "content": strip_html(j.get("description", ""))[:1200],
                "posted_ts": to_ts(j.get("created_at")),
                "source": "Arbeitnow",
            })
    except Exception as e:
        print(f"[warn] arbeitnow: {e}")
    return out


def fetch_hn():
    out = []
    try:
        s = get_json("https://hn.algolia.com/api/v1/search_by_date"
                     "?query=Ask%20HN%20Who%20is%20hiring&tags=story&hitsPerPage=1")
        if not s.get("hits"):
            return out
        story_id = s["hits"][0]["objectID"]
        c = get_json(f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}"
                     "&hitsPerPage=200")
        for h in c.get("hits", []):
            body = strip_html(h.get("comment_text", ""))
            if not body:
                continue
            first = body.split("\n")[0][:120]
            out.append({
                "title": first,
                "company": first.split("|")[0].strip()[:60] if "|" in first else "",
                "location": "",
                "url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "content": body[:1200],
                "posted_ts": to_ts(h.get("created_at_i")),
                "source": "HN Who's Hiring",
            })
    except Exception as e:
        print(f"[warn] hn: {e}")
    return out


def fetch_muse():
    """The Muse — free, keyless. Location-filtered to New York + Flexible/Remote;
    the downstream US-remote gate keeps only the ones that qualify."""
    out = []
    # Muse categories that hold our target roles. 'Product Management' is the core;
    # the others catch FDE / deployment / solutions-flavored postings.
    cats = ["Product Management", "Project Management", "Sales", "Data Science"]
    from urllib.parse import urlencode
    for page in range(1, 3):                       # 2 pages ≈ 40 postings/loc
        params = [("page", page), ("location", "New York, NY"),
                  ("location", "Flexible / Remote")]
        params += [("category", c) for c in cats]
        try:
            data = get_json("https://www.themuse.com/api/public/jobs?" + urlencode(params))
        except Exception as e:
            print(f"[warn] muse page {page}: {e}")
            break
        results = data.get("results", [])
        if not results:
            break
        for j in results:
            locs = ", ".join(l.get("name", "") for l in j.get("locations", []))
            comp = (j.get("company") or {}).get("name", "")
            out.append({
                "title": j.get("name", ""),
                "company": comp,
                "location": locs,
                "url": (j.get("refs") or {}).get("landing_page", ""),
                "content": strip_html(j.get("contents", ""))[:JD_CHARS],
                "posted_ts": to_ts(j.get("publication_date")),
                "source": "The Muse",
            })
    return out


def fetch_adzuna():
    """Adzuna — free aggregator (indexes many boards + company career pages).
    Queried for US roles in/around New York; the location gate filters further."""
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not (app_id and app_key):
        return []
    from urllib.parse import urlencode
    out = []
    for q in JOB_SEARCH_QUERIES:
        params = urlencode({
            "app_id": app_id, "app_key": app_key,
            "what_phrase": q,                       # exact-phrase title match
            "where": "New York",
            "distance": 80,                         # km — covers the NYC metro
            "max_days_old": MAX_AGE_DAYS or 21,
            "results_per_page": 25, "content-type": "application/json",
        })
        try:
            data = get_json(f"https://api.adzuna.com/v1/api/jobs/us/search/1?{params}")
        except Exception as e:
            print(f"[warn] adzuna '{q}': {e}")
            continue
        for j in data.get("results", []):
            loc = (j.get("location") or {}).get("display_name", "")
            out.append({
                "title": j.get("title", ""),
                "company": (j.get("company") or {}).get("display_name", ""),
                "location": loc,
                "url": j.get("redirect_url", ""),
                "content": strip_html(j.get("description", ""))[:JD_CHARS],
                "posted_ts": to_ts(j.get("created")),
                "source": "Adzuna",
            })
    return out


def fetch_grok_x():
    """xAI Grok X (Twitter) search via the Agent Tools API (the current API — the
    older Live Search / search_parameters form returns 410 Gone). Surfaces recent
    founder/recruiter "we're hiring" posts and returns them as normalized postings
    whose URL is the tweet. Needs XAI_API_KEY (GROK_API_KEY is aliased to it).
    Non-fatal on any error."""
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        return []
    model = os.environ.get("XAI_MODEL", "grok-4.5")
    days = int(os.environ.get("XAI_LOOKBACK_DAYS", "5"))
    roles = ", ".join(JOB_SEARCH_QUERIES)
    prompt = (
        f"Search X for posts from the last {days} days where a founder, recruiter, "
        f"or employee is HIRING for a role like: {roles}. Focus on early-stage US "
        "startups, New York or San Francisco / Bay Area or US-remote. Only real, "
        "currently-open roles (ignore generic threads, advice, and 'looking for "
        "work' posts). Return ONLY a JSON array, no prose, no code fences:\n"
        '[{"company":"","role":"","location":"","tweet_url":"","poster":"@handle",'
        '"poster_title":"their role at the company, e.g. co-founder & CEO",'
        '"posted":"YYYY-MM-DD","quote":"the most relevant sentence from the post, '
        'verbatim, <=200 chars","summary":"2-3 sentences: what the company does, '
        'what the role actually involves, and any seniority/experience bar stated",'
        '"funding":"latest round if the post or their profile mentions it, e.g. '
        '\'Series A, $15M, Mar 2026\'","apply_url":"careers/application link if the '
        'post gives one"}]\n'
        "The summary and quote are what I read to decide whether to reply, so make "
        "them specific — name the product and the actual scope of the job, never "
        "just restate the title. "
        "Use \"\" for anything you cannot determine. Up to 15 items."
    )
    payload = {
        "model": model,
        "input": [{"role": "user", "content": prompt}],
        "tools": [{"type": "x_search"}],
    }
    try:
        req = urllib.request.Request(
            "https://api.x.ai/v1/responses",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"[warn] grok/x search: {e}")
        return []
    # The final answer is the output item of type "message"; earlier items are
    # reasoning + tool calls. Fall back to output_text if present.
    text = data.get("output_text") or ""
    if not text:
        for item in data.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        text += c.get("text", "")
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        print("[info] grok/x: no postings parsed")
        return []
    try:
        leads = json.loads(m.group(0))
    except Exception:
        print("[info] grok/x: unparseable JSON")
        return []
    out = []
    for l in leads:
        role = (l.get("role") or "").strip()
        if not role:
            continue
        company = (l.get("company") or "").strip()
        # A tweet URL is ideal; if the model didn't capture one, fall back to a
        # search so the lead is still actionable (and passes the URL gate).
        url = (l.get("tweet_url") or "").strip()
        if not url:
            url = "https://www.google.com/search?q=" + quote_plus(
                f"{company} {role} hiring")
        summary = (l.get("summary") or "").strip()
        quote = (l.get("quote") or "").strip()
        poster = (l.get("poster") or "").strip()
        if poster and not poster.startswith("@"):
            poster = "@" + poster
        # The scoring blob wants every scrap of text; the display fields are kept
        # separate so the email can lay them out instead of dumping one string.
        out.append({
            "title": role,
            "company": company,
            # Prompt constrains to US/NYC/SF; default blanks to US so the location
            # gate keeps them (a model-supplied non-US location is still honored).
            "location": (l.get("location") or "United States").strip(),
            "url": url,
            "content": " ".join(x for x in (summary, quote, l.get("funding") or "") if x)[:900],
            "posted_ts": to_ts(l.get("posted") or ""),
            "source": "X/Grok",
            "note": summary[:400],
            "quote": quote[:220],
            "poster": poster,
            "poster_title": (l.get("poster_title") or "").strip()[:60],
            "round": (l.get("funding") or "").strip()[:60],
            "apply_url": (l.get("apply_url") or "").strip(),
        })
    print(f"[info] grok/x: {len(out)} hiring posts")
    return out


def fetch_greenhouse(token):
    out = []
    try:
        data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
        for j in data.get("jobs", []):
            out.append({
                "title": j.get("title", ""),
                "company": token,
                "location": (j.get("location") or {}).get("name", ""),
                "url": j.get("absolute_url", ""),
                "content": strip_html(j.get("content", ""))[:JD_CHARS],
                "posted_ts": to_ts(j.get("updated_at")),
                "source": "Greenhouse",
            })
    except Exception as e:
        print(f"[warn] greenhouse {token}: {e}")
    return out


def fetch_lever(token):
    out = []
    try:
        data = get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
        for j in data:
            out.append({
                "title": j.get("text", ""),
                "company": token,
                "location": (j.get("categories") or {}).get("location", ""),
                "url": j.get("hostedUrl", ""),
                "content": strip_html(j.get("descriptionPlain", j.get("description", "")))[:JD_CHARS],
                "posted_ts": to_ts(j.get("createdAt")),
                "source": "Lever",
            })
    except Exception as e:
        print(f"[warn] lever {token}: {e}")
    return out


def fetch_ashby(token):
    out = []
    try:
        data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
        for j in data.get("jobs", []):
            out.append({
                "title": j.get("title", ""),
                "company": token,
                "location": j.get("location", ""),
                "url": j.get("jobUrl", ""),
                "content": strip_html(j.get("descriptionPlain", ""))[:JD_CHARS],
                "posted_ts": to_ts(j.get("publishedAt")),
                "source": "Ashby",
            })
    except Exception as e:
        print(f"[warn] ashby {token}: {e}")
    return out


# --- Workable / SmartRecruiters / Rippling -------------------------------
# Three more public, keyless ATS APIs, added because getro.py surfaces tokens
# for them constantly. All three share a shape the first three don't: the board
# LIST call returns no job description, only titles and locations. The digest
# scores on JD text, so each needs a per-job detail call — bounded by
# BOARD_MAX_DETAIL exactly as fetch_workday() bounds its own, since a large
# board would otherwise spend hundreds of requests on roles the title gate is
# about to discard anyway.
BOARD_MAX_DETAIL = int(os.environ.get("BOARD_MAX_DETAIL", "25"))


def _loc_str(d, *keys):
    """Flatten an ATS location object to the 'City, Region, Country' string the
    location gate expects."""
    if not isinstance(d, dict):
        return str(d or "")
    parts = [str(d.get(k)).strip() for k in keys if d.get(k)]
    return ", ".join(p for p in parts if p and p.lower() != "none")


def fetch_workable(token):
    out = []
    try:
        data = post_json(f"https://apply.workable.com/api/v1/accounts/{token}/jobs",
                         {"query": ""})
        jobs = data.get("results") or []
    except Exception as e:
        print(f"[warn] workable {token}: {e}")
        return out
    fetched = 0
    for j in jobs:
        shortcode = j.get("shortcode") or ""
        if not shortcode:
            continue
        loc = _loc_str(j.get("location") or {}, "city", "region", "country")
        if (j.get("remote") or (j.get("workplace") or "").lower() == "remote") \
                and "remote" not in loc.lower():
            loc = ("Remote, " + loc) if loc else "Remote"
        content, posted = "", j.get("published")
        # Only spend a detail call on a title we'd actually keep.
        if fetched < BOARD_MAX_DETAIL and title_is_target(j.get("title", "")):
            try:
                d = get_json(f"https://apply.workable.com/api/v1/accounts/{token}/jobs/{shortcode}")
                content = strip_html(" ".join(
                    x for x in (d.get("description"), d.get("requirements"), d.get("benefits")) if x))
                posted = d.get("published") or posted
                fetched += 1
            except Exception as e:
                print(f"[warn] workable detail {token}/{shortcode}: {e}")
        out.append({
            "title": j.get("title", ""),
            "company": token,
            "location": loc,
            "url": f"https://apply.workable.com/{token}/j/{shortcode}/",
            "content": content[:JD_CHARS],
            "posted_ts": to_ts(posted),
            "source": "Workable",
        })
    return out


def fetch_smartrecruiters(token):
    out = []
    try:
        data = get_json("https://api.smartrecruiters.com/v1/companies/"
                        f"{token}/postings?limit=100")
        jobs = data.get("content") or []
    except Exception as e:
        print(f"[warn] smartrecruiters {token}: {e}")
        return out
    fetched = 0
    for j in jobs:
        jid = j.get("id") or ""
        company = ((j.get("company") or {}).get("name") or token)
        loc = _loc_str(j.get("location") or {}, "city", "region", "country")
        if (j.get("location") or {}).get("remote") and "remote" not in loc.lower():
            loc = ("Remote, " + loc) if loc else "Remote"
        url = f"https://jobs.smartrecruiters.com/{token}/{jid}"
        content = ""
        if jid and fetched < BOARD_MAX_DETAIL and title_is_target(j.get("name", "")):
            try:
                d = get_json("https://api.smartrecruiters.com/v1/companies/"
                             f"{token}/postings/{jid}")
                sections = (d.get("jobAd") or {}).get("sections") or {}
                content = strip_html(" ".join(
                    (sections.get(k) or {}).get("text", "")
                    for k in ("jobDescription", "qualifications",
                              "additionalInformation", "companyDescription")))
                url = d.get("postingUrl") or url
                fetched += 1
            except Exception as e:
                print(f"[warn] smartrecruiters detail {token}/{jid}: {e}")
        out.append({
            "title": j.get("name", ""),
            "company": company,
            "location": loc,
            "url": url,
            "content": content[:JD_CHARS],
            "posted_ts": to_ts(j.get("releasedDate")),
            "source": "SmartRecruiters",
        })
    return out


def fetch_rippling(token):
    out = []
    try:
        jobs = get_json(f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs")
        if not isinstance(jobs, list):
            return out
    except Exception as e:
        print(f"[warn] rippling {token}: {e}")
        return out
    fetched = 0
    for j in jobs:
        uuid = j.get("uuid") or ""
        loc = (j.get("workLocation") or {}).get("label", "") if isinstance(j.get("workLocation"), dict) else ""
        content, posted, company = "", None, token
        # Rippling's list call carries no date and no JD at all, so an
        # undetailed posting can't clear the recency gate or score. Detail is
        # where everything useful lives.
        if uuid and fetched < BOARD_MAX_DETAIL and title_is_target(j.get("name", "")):
            try:
                d = get_json("https://api.rippling.com/platform/api/ats/v1/board/"
                             f"{token}/jobs/{uuid}")
                desc = d.get("description") or {}
                if isinstance(desc, dict):
                    content = strip_html(" ".join(str(v) for v in desc.values() if v))
                else:
                    content = strip_html(str(desc))
                posted = d.get("createdOn")
                company = d.get("companyName") or token
                if not loc:
                    wls = d.get("workLocations") or []
                    loc = ", ".join(w.get("label", "") for w in wls if isinstance(w, dict))
                fetched += 1
            except Exception as e:
                print(f"[warn] rippling detail {token}/{uuid}: {e}")
        out.append({
            "title": j.get("name", ""),
            "company": company,
            "location": loc,
            "url": j.get("url") or f"https://ats.rippling.com/{token}/jobs/{uuid}",
            "content": content[:JD_CHARS],
            "posted_ts": to_ts(posted),
            "source": "Rippling",
        })
    return out


def _wd_cfg(entry):
    """Accept either a dict or the shorthand string 'tenant/wd/site'."""
    if isinstance(entry, str):
        parts = [p for p in entry.strip().strip("/").split("/") if p]
        if len(parts) == 3:
            return {"tenant": parts[0], "wd": parts[1], "site": parts[2]}
        return {}
    return entry or {}


def fetch_workday(entry):
    """Pull a Workday board via its public cxs JSON API.

    The list endpoint gives title + location but no description, so we fetch the
    per-job detail ONLY for postings whose title already passes the target-title
    filter — bounding detail calls (Workday boards are large and senior-heavy)."""
    out = []
    cfg = _wd_cfg(entry)
    tenant, wd, site = cfg.get("tenant"), cfg.get("wd"), cfg.get("site")
    if not (tenant and wd and site):
        print(f"[warn] workday: incomplete config {entry!r}")
        return out
    base = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    search = cfg.get("search", WORKDAY_SEARCH)
    try:
        listings, offset = [], 0
        for _ in range(10):                       # up to 10 pages x 20 = 200
            data = post_json(base + "/jobs",
                             {"appliedFacets": {}, "limit": 20, "offset": offset,
                              "searchText": search})
            batch = data.get("jobPostings", [])
            listings += batch
            offset += 20
            if not batch or offset >= data.get("total", 0):
                break
        fetched = 0
        for jp in listings:
            title = jp.get("title", "")
            if not title or not title_is_target(title):
                continue                          # skip detail call for non-matches
            if fetched >= WORKDAY_MAX_DETAIL:
                break
            ext = jp.get("externalPath", "")
            try:
                info = (get_json(base + ext) or {}).get("jobPostingInfo", {}) or {}
                fetched += 1
            except Exception as e:
                print(f"[warn] workday detail {tenant}{ext}: {e}")
                continue
            url = info.get("externalUrl") or \
                f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{ext}"
            out.append({
                "title": info.get("title", title),
                "company": tenant,
                "location": info.get("location", jp.get("locationsText", "")),
                "url": url,
                "content": strip_html(info.get("jobDescription", ""))[:JD_CHARS],
                "posted_ts": to_ts(info.get("startDate")),
                "source": "Workday",
            })
    except Exception as e:
        print(f"[warn] workday {tenant}: {e}")
    return out


# --------------------------- filtering & scoring
# "lead" as a standalone word is senior, but the SENIORITY_EXCLUDE substring
# "lead " (trailing space) misses a title that ENDS in "Lead" (e.g. "GTM Lead",
# "Product Lead"). Match it on a word boundary instead — \blead\b won't fire on
# "leading"/"leader"/"leadership".
_LEAD_RE = re.compile(r"\blead\b")


def _norm(text):
    """Lowercase and fold the punctuation job boards vary on, so one entry in
    target_titles matches "Founder's Associate", "Founder\u2019s Associate" and
    "Founders Associate" alike."""
    return (text or "").lower().replace("\u2019", "'").replace("\u2018", "'") \
                       .replace("\u2013", "-").replace("\u2014", "-")


def title_is_target(title):
    t = _norm(title)
    if any(x in t for x in SENIORITY_EXCLUDE) or _LEAD_RE.search(t):
        return False
    if any(x in t for x in SWE_EXCLUDE):      # no software-engineering roles
        return False
    if any(x in t for x in OFF_LANE_EXCLUDE):  # right stem, wrong profession
        return False
    return any(x in t for x in RESUME["target_titles"])


# --------------------------- screening facts pulled straight from the JD
# The five things you'd otherwise open the posting to learn. All regex, no API
# call: these phrasings are formulaic enough that a model would be overkill.
_YEARS_RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:-|\u2013|\u2014|to)\s*(\d{1,2})\+?\s*(?:\+\s*)?years?", re.I)
_YEARS_MIN_RE = re.compile(
    r"(?:at least|minimum of|min\.?\s*of|\bover\b)?\s*(\d{1,2})\s*\+\s*years?"
    r"|(?:at least|minimum of)\s*(\d{1,2})\s*years?", re.I)
# $120,000 - $160,000 / $120k-$160k / 120k–160k
_MONEY_RE = re.compile(r"\$\s?(\d{2,3})(?:,(\d{3})|\s?k)\b", re.I)
# Days in office. Three phrasings, tried in order — the office word can come
# BEFORE the count ("in the office 3-4 days per week", the Courted JD that read
# as plain "Onsite"), AFTER it ("3 days a week in office"), or be absent when the
# surrounding JD has already established hybrid/onsite. Counts may be a range.
_DAYS_N = r"\d(?:\s*(?:-|\u2013|\u2014|to)\s*\d)?"
_DAYS_IN_OFFICE_RES = [
    re.compile(rf"(?:in|at|from)\s+(?:the\s+)?office[^.\n]{{0,20}}?({_DAYS_N})\s*days?", re.I),
    re.compile(rf"({_DAYS_N})\s*days?\s*(?:a|per)\s*week\s*(?:in|at|from)\b", re.I),
    re.compile(rf"({_DAYS_N})\s*days?\s*(?:a|per)\s*week\b", re.I),
]


def _days_in_office(c):
    """'3' or '3-4' — normalized, or None. Returns the low end too, so a count
    under 5 can correct an 'Onsite' read to what it actually is: hybrid."""
    for rx in _DAYS_IN_OFFICE_RES:
        m = rx.search(c)
        if m:
            d = re.sub(r"\s*(?:-|\u2013|\u2014|to)\s*", "\u2013", m.group(1))
            return d, int(re.match(r"\d", d).group(0))
    return None
_NO_SPONSOR_RE = re.compile(
    r"(?:not|unable to|cannot|can't|do(?:es)?n't)\s+(?:be\s+able\s+to\s+)?"
    r"(?:provide|offer|sponsor|support)[^.]{0,40}(?:visa|sponsor)", re.I)
_SPONSOR_RE = re.compile(r"(?:visa sponsorship (?:is )?available|we (?:do )?sponsor|"
                         r"sponsorship (?:is )?(?:available|offered))", re.I)


def screen_facts(content):
    """Years wanted, comp, work model, equity, sponsorship — the facts that let
    you rule a posting in or out at a glance instead of opening it."""
    c = _norm(content or "")
    f = {}

    # Years: a stated range beats a bare "5+ years", and the FIRST mention is
    # almost always the requirement (later ones are "10 years in the industry"
    # boilerplate about the founders).
    m = _YEARS_RANGE_RE.search(c)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 0 <= lo <= hi <= 25:
            f["years"] = f"{lo}\u2013{hi} yrs"
            f["years_min"] = lo
    if "years" not in f:
        m = _YEARS_MIN_RE.search(c)
        if m:
            n = int(m.group(1) or m.group(2))
            if 0 < n <= 25:
                f["years"] = f"{n}+ yrs"
                f["years_min"] = n

    # Comp: two salary-shaped numbers = a posted band. Anything outside a
    # plausible salary window is a fundraise figure or an ARR brag, not pay.
    nums = []
    for m in _MONEY_RE.finditer(c):
        v = int(m.group(1)) * 1000 + (int(m.group(2)) if m.group(2) else 0)
        if 50_000 <= v <= 500_000:
            nums.append(v)
    if len(nums) >= 2 and max(nums) > min(nums):
        f["comp"] = f"${min(nums) // 1000}k\u2013${max(nums) // 1000}k"
    elif len(nums) == 1:
        f["comp"] = f"~${nums[0] // 1000}k"

    # Work model — check hybrid first: a hybrid JD says "remote" too.
    if "hybrid" in c:
        d = _days_in_office(c)
        f["work_model"] = f"Hybrid \u00b7 {d[0]}d in office" if d else "Hybrid"
    elif re.search(r"fully remote|remote[- ]first|100% remote", c):
        f["work_model"] = "Remote"
    elif re.search(r"\bon[- ]?site\b|\bin[- ]office\b|in the office", c):
        # A JD that never says "hybrid" but asks for 3-4 days in the office IS
        # hybrid; calling it Onsite reads harsher than the job actually is.
        d = _days_in_office(c)
        if d and d[1] < 5:
            f["work_model"] = f"Hybrid \u00b7 {d[0]}d in office"
        else:
            f["work_model"] = f"Onsite \u00b7 {d[0]}d/wk" if d else "Onsite"

    if re.search(r"\bequity\b|stock options|\brsus?\b", c):
        f["equity"] = "Equity"
    if _NO_SPONSOR_RE.search(c):
        f["sponsorship"] = "No visa sponsorship"
    elif _SPONSOR_RE.search(c):
        f["sponsorship"] = "Sponsors visas"
    return f


def fit_sentence(item):
    """One plain-English line: why this posting is in front of you, and the one
    thing most likely to disqualify it. The score says how much; this says why."""
    bd = item.get("breakdown") or {}
    by = {label: terms for label, _, terms in (bd.get("parts") or [])}
    facts = item.get("facts") or {}
    clauses = []

    titles = by.get("title") or []
    if titles:
        # Longest stem = the most specific thing it matched ("associate forward
        # deployed engineer" beats "forward deployed").
        clauses.append(f"hits your <b style=\"font-weight:600\">"
                       f"{html.escape(max(titles, key=len))}</b> target")

    want = facts.get("years_min")
    if want is not None:
        mine = RESUME.get("years", 0)
        if want <= mine:
            clauses.append(f"wants {facts['years']}, you have {mine}")
        elif want <= mine + 1:
            clauses.append(f"wants {facts['years']} vs your {mine} \u2014 close")
        else:
            clauses.append(f"wants {facts['years']} vs your {mine} \u2014 a stretch")
    elif by.get("seniority fit"):
        clauses.append("reads early-career")

    doms = by.get("your domains") or []
    if doms:
        clauses.append("in " + ", ".join(doms[:3]))
    if by.get("early stage"):
        clauses.append("early-stage")
    if item.get("funding"):
        clauses.append(html.escape(funding_label(item["funding"])))
    tier = LOC_LABEL.get(item.get("loc_tier"), "")
    if tier:
        clauses.append(tier)
    if not clauses:
        return ""
    line = clauses[0][0].upper() + clauses[0][1:] + ", " + ", ".join(clauses[1:]) \
        if len(clauses) > 1 else clauses[0][0].upper() + clauses[0][1:]
    return line.rstrip(", ") + "."


# Built on first use, not at import: _apply_profile() may have replaced
# RESUME["skills"]/["domains"] from profile.compiled.json by then.
_SKILL_RE = _DOMAIN_RE = None


def _skill_re():
    global _SKILL_RE
    if _SKILL_RE is None:
        _SKILL_RE = kw_matcher(RESUME["skills"])
    return _SKILL_RE


def _domain_re():
    global _DOMAIN_RE
    if _DOMAIN_RE is None:
        _DOMAIN_RE = kw_matcher(RESUME["domains"])
    return _DOMAIN_RE


_SOFT_RE = _LATE_RE = None


def _soft_re():
    global _SOFT_RE
    if _SOFT_RE is None:
        _SOFT_RE = kw_matcher(EARLY_STAGE_SOFT)
    return _SOFT_RE


def _late_re():
    global _LATE_RE
    if _LATE_RE is None:
        _LATE_RE = kw_matcher(LATE_STAGE_SIGNALS)
    return _LATE_RE


def _hits(rx, blob):
    """Distinct matched terms, in first-seen order (the email prints these)."""
    seen, out = set(), []
    for m in rx.finditer(blob):
        k = m.group(0).lower()
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ==========================================================================
# SCORING WEIGHTS
# ==========================================================================
# What each signal is worth. These are the numbers that decide the ranking, so
# they're the first thing a fork with a different job hunt needs to change —
# e.g. a senior candidate flips the seniority weights, and someone hunting one
# specific title raises JOB_TITLE_POINTS and drops the domain weight. The
# per-signal caps stop one long keyword list from swamping the total.
TITLE_POINTS = env_int("JOB_TITLE_POINTS", 12)          # per target-title hit
TITLE_MAX = env_int("JOB_TITLE_MAX", 40)
JUNIOR_POINTS = env_int("JOB_SENIORITY_FIT_POINTS", 20)  # JD wants your years
# Penalties are given as positive magnitudes and always subtracted, so a stray
# "JOB_TOO_SENIOR_PENALTY=25" can't accidentally become a reward.
SENIOR_PENALTY = -abs(env_int("JOB_TOO_SENIOR_PENALTY", 25))
SKILL_POINTS = env_int("JOB_SKILL_POINTS", 3)           # per resume skill hit
SKILL_MAX = env_int("JOB_SKILL_MAX", 24)
DOMAIN_POINTS = env_int("JOB_DOMAIN_POINTS", 2)         # per domain hit
DOMAIN_MAX = env_int("JOB_DOMAIN_MAX", 12)
EARLY_STAGE_POINTS = env_int("JOB_EARLY_STAGE_POINTS", 6)
EARLY_SOFT_POINTS = env_int("JOB_EARLY_STAGE_SOFT_POINTS", 3)
LATE_STAGE_PENALTY = -abs(env_int("JOB_LATE_STAGE_PENALTY", 6))


def score_breakdown(title, content):
    """The qualification score AND the evidence behind it.

    Same arithmetic as before, but it now keeps WHICH resume terms actually hit,
    so the digest can show its work instead of printing a bare number you have to
    take on faith. Returns
        {"total": int,
         "parts":  [(label, points, [terms]), ...],   # what moved the number
         "misses": [(label, reason), ...]}            # what scored NOTHING

    `misses` exists because a score is only half an explanation. A 45 with
    "+20 seniority fit, +9 skills" tells you what fired; it does not tell you the
    posting named no round and matched none of your domains, which is the actual
    reason it isn't a 70. Misses are worth 0 by construction and are kept OUT of
    `parts`, so the printed arithmetic still sums to the total.
    """
    t, c = _norm(title), _norm(content)
    blob = t + " " + c
    parts = []
    misses = []

    # role/title match (strong) — the reason the posting is here at all
    titles_hit = [x for x in RESUME["target_titles"] if x in t]
    title_pts = min(TITLE_POINTS * len(titles_hit), TITLE_MAX)
    if title_pts:
        parts.append(("title", title_pts, titles_hit))
    else:
        # Only reachable from a source that bypasses the title gate (HN's
        # "Who is hiring" thread matches on body text). Everywhere else this
        # miss is impossible — the posting would never have been collected.
        misses.append(("title", "matches none of your target titles"))

    # seniority fit
    jr = [x for x in JUNIOR_SIGNALS if x in c]
    sr = [x for x in SENIOR_SIGNALS if x in c]
    if jr:
        parts.append(("seniority fit", JUNIOR_POINTS, jr[:3]))
    if sr:
        parts.append(("too senior", SENIOR_PENALTY, sr[:3]))
    if not jr and not sr:
        misses.append(("seniority", "no years-of-experience stated"))

    # skill overlap (capped). Word-boundary matched, NOT substring: "ui" is
    # inside "b-ui-lding", "ai" is inside "em-ai-l", "api" is inside "c-api-tal"
    # and "git" is inside "di-git-al" — as substrings they handed nearly every
    # posting free points, which is exactly the bug the funding digest already
    # fixed for its focus keywords. Title stems stay substrings on purpose: they
    # are meant to match inside a longer title.
    skills_hit = _hits(_skill_re(), blob)
    skill_pts = min(SKILL_POINTS * len(skills_hit), SKILL_MAX)
    if skill_pts:
        parts.append(("resume skills", skill_pts, skills_hit))
    else:
        misses.append(("skills", "no overlap with your resume skills"))

    # domain fit (capped)
    domains_hit = _hits(_domain_re(), blob)
    domain_pts = min(DOMAIN_POINTS * len(domains_hit), DOMAIN_MAX)
    if domain_pts:
        parts.append(("your domains", domain_pts, domains_hit))
    else:
        misses.append(("domains", "not in a domain you've worked in"))

    # stage bias: reward seed/A/B, penalize obviously-late/huge. A named round
    # wins outright; the soft tells are the fallback for a JD that never states
    # one, and they are ignored entirely when anything says late-stage.
    early = [x for x in EARLY_STAGE_SIGNALS if x in c]
    late = _hits(_late_re(), c)
    if early:
        parts.append(("early stage", EARLY_STAGE_POINTS, early[:3]))
    elif not late:
        soft = _hits(_soft_re(), c)
        if len(soft) >= EARLY_STAGE_SOFT_MIN:
            parts.append(("early stage (soft)", EARLY_SOFT_POINTS, soft[:3]))
    if late:
        parts.append(("late stage", LATE_STAGE_PENALTY, late[:3]))
    if not early and not late and not any(p[0].startswith("early stage") for p in parts):
        misses.append(("stage", "no funding stage stated"))

    return {"total": sum(p[1] for p in parts), "parts": parts, "misses": misses}


def qualification_score(title, content):
    return score_breakdown(title, content)["total"]


# Extra points for a posting at a company that raised recently — the whole
# premise of the loop is that a fresh round is what turns into a new req.
FUNDED_BONUS = int(os.environ.get("JOB_FUNDED_BONUS", "8"))

_FUNDED = {}
_FUNDED_BY_TOKEN = {}


def _load_funded():
    """Index the funding digest's recently-raised companies two ways: by
    normalized name (Muse/Adzuna/HN give real names) and by ATS token (the board
    fetchers set company=<token>)."""
    global _FUNDED, _FUNDED_BY_TOKEN
    try:
        import watchlist as _wl
        _FUNDED = _wl.get_funded()
    except Exception as e:
        print(f"[info] funding flags unavailable: {e}")
        return
    _FUNDED_BY_TOKEN = {t: v for v in _FUNDED.values() for t in v.get("tokens", [])}
    if _FUNDED:
        print(f"[loop] {len(_FUNDED)} recently-funded companies flagged "
              f"({len(_FUNDED_BY_TOKEN)} with a known ATS board)")


# What each round is worth as a stage signal, from the permanent stage record
# rather than from whatever the JD chose to say about itself. Your band is seed
# through Series B; C is neutral; D and beyond is the same penalty a JD bragging
# about being public would earn.
# Which rounds count as "your band" is itself a fork-level choice: someone
# targeting growth-stage companies sets JOB_EARLY_ROUNDS="series b,series c,
# series d" and JOB_LATE_ROUNDS="ipo,public,acquired". Anything named in neither
# list is neutral (0), which is what "series c" is by default.
EARLY_ROUNDS = env_list("JOB_EARLY_ROUNDS",
                        ["pre-seed", "pre seed", "seed", "series a", "series b"])
LATE_ROUNDS = env_list("JOB_LATE_ROUNDS",
                       ["series d", "series e", "series f", "series g",
                        "ipo", "public", "acquired"])
# Rounds we rank as neither: a known fact worth 0, which still beats the JD's
# own prose about itself (see stage_points -> None, which means "no fact").
NEUTRAL_ROUNDS = env_list("JOB_NEUTRAL_ROUNDS", ["series c"])
STAGE_POINTS = {r: EARLY_STAGE_POINTS for r in EARLY_ROUNDS}
STAGE_POINTS.update({r: 0 for r in NEUTRAL_ROUNDS if r not in STAGE_POINTS})
STAGE_POINTS.update({r: LATE_STAGE_PENALTY for r in LATE_ROUNDS if r not in STAGE_POINTS})

_STAGE = {}
_STAGE_BY_TOKEN = {}


def _load_stage():
    """Company -> known round, indexed by name and by ATS token (board fetchers
    set company=<token>), exactly like the funded index."""
    global _STAGE, _STAGE_BY_TOKEN
    try:
        import watchlist as _wl
        _STAGE = _wl.get_stage()
    except Exception as e:
        print(f"[info] stage facts unavailable: {e}")
        return
    _STAGE_BY_TOKEN = {t: v for v in _STAGE.values() for t in v.get("tokens", [])}
    if _STAGE:
        print(f"[stage] {len(_STAGE)} companies with a known round")


def stage_note(company):
    """The known round for a posting's company, or {}."""
    c = (company or "").lower().strip()
    if not c:
        return {}
    return _STAGE_BY_TOKEN.get(c) or _STAGE.get(c) or {}


def stage_points(round_name):
    """Points for a named round, or None if we don't rank it."""
    r = _norm(round_name or "").strip()
    for k in sorted(STAGE_POINTS, key=len, reverse=True):
        if k in r:
            return STAGE_POINTS[k]
    return None


def funding_note(company):
    """The raise behind a posting's company, or {} — matched on ATS token first
    (exact) and then on the normalized company name."""
    c = (company or "").lower().strip()
    if not c:
        return {}
    return _FUNDED_BY_TOKEN.get(c) or _FUNDED.get(c) or {}


def funding_label(fund):
    """'raised $12M Series A' — the short badge shown next to a posting."""
    if not fund:
        return ""
    bits = " ".join(x for x in (fund.get("amount"), fund.get("round")) if x)
    return f"raised {bits}".strip() if bits else "recently funded"


def kw_matcher(keywords):
    """Word-boundary matcher for a keyword list. Substring matching is what makes
    a two-letter term like "sf" fire inside "Dusseldorf" — and it is the same bug
    the funding digest hit with "ai" inside "raises"."""
    if not keywords:
        return re.compile(r"(?!)")             # matches nothing
    alts = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])", re.I)


_SECONDARY_RE = kw_matcher(SECONDARY_LOC_KEYWORDS)

# Ranking bonus per location tier, added to the qualification score. NYC first —
# on-site, hybrid or remote, it's the one place an office is a plus rather than a
# move. US-remote now outranks SF/Bay on purpose: a remote role is doable from
# NYC today, whereas on-site in the Bay means relocating, which is the same cost
# as any other out-of-town office. On-site elsewhere in the US ranks last but is
# no longer thrown away.
LOC_BONUS = {
    "nyc": env_int("JOB_LOC_BONUS_PRIMARY", 10),
    "us-remote": env_int("JOB_LOC_BONUS_REMOTE", 5),
    "secondary": env_int("JOB_LOC_BONUS_SECONDARY", 4),
    "us-other": env_int("JOB_LOC_BONUS_OTHER", 1),
    "": 0,
}
LOC_LABEL = {"nyc": "NYC", "secondary": "SF/Bay", "us-remote": "US-remote",
             "us-other": "US on-site"}

# Work arrangement, shown as a chip so an on-site role is never a surprise. All
# three are acceptable, so this labels rather than filters — the geography tier
# above is what does the ranking.
def work_mode(location, content):
    """"hybrid" / "remote" / "onsite" — best-effort from the location line, with
    the JD as a fallback. Hybrid wins over remote when a posting says both,
    because "remote-friendly, hybrid in NYC" means you're expected in an office."""
    loc = (location or "").lower().strip()
    if "hybrid" in loc:
        return "hybrid"
    if "remote" in loc or "anywhere" in loc:
        return "remote"
    # A bare "United States" / "Nationwide" is a national posting, not an office
    # in a city called United States.
    if loc in US_NATIONAL_LOC:
        return "remote"
    body = (content or "")[:4000].lower()
    if "hybrid" in body:
        return "hybrid"
    if not loc and "remote" in body:
        return "remote"
    return "onsite"


# Postings at or above this land in "Best matches"; the rest go in a quieter
# second section rather than being dropped — a 30-point posting is still a lead.
#
# 40, not 50: at 50 a genuinely good posting that simply never named a funding
# round or asked for a specific number of years sat in "Worth a look", because
# those two signals alone are worth 26 points. The gates upstream (title,
# seniority, location) have already thrown away everything off-lane, so what
# reaches the scorer is a shortlist — and the line between its halves should
# reflect resume fit, not whether the JD happened to mention its Series A.
BEST_MATCH_MIN = env_int("JOB_BEST_MATCH_MIN", 40)


def location_tier(location, content):
    """Which geography bucket a posting falls in — "" means it doesn't qualify.

    "nyc"       — the focus metro.
    "secondary" — SF / Bay Area (SECONDARY_LOC_KEYWORDS), kept but ranked lower.
    "us-remote" — US-national or remote-with-no-non-US-marker (needs ALLOW_US_REMOTE).
    "us-other"  — a named US city outside NYC/the Bay: on-site, kept but ranked
                  last (needs ALLOW_US_ONSITE). Note "Remote, TX" is NOT this —
                  a state-scoped remote role means residents of that state.
    """
    loc = (location or "").strip().lower()
    if loc:
        # Trust an explicit location: a posting that says "San Francisco" isn't
        # NYC just because its JD name-drops a New York office.
        if any(k in loc for k in NYC_KEYWORDS):
            return "nyc"
        if any(t in loc for t in NON_US_LOC_HINTS):
            return ""                          # remote-EU / remote-worldwide → drop
        if _SECONDARY_RE.search(loc):
            return "secondary"                 # on-site SF/Bay — allowed, ranked below NYC
        if not ALLOW_US_REMOTE:
            return ""
        if _state_scoped(loc):
            # A named US state. "Remote, TX" is still a drop — that means Texas
            # RESIDENTS, not a role you can take from Brooklyn. But "Austin, TX"
            # is an on-site role in the US, which is now kept and ranked last.
            if "remote" in loc or "anywhere" in loc:
                return ""
            return "us-other" if ALLOW_US_ONSITE else ""
        if loc in US_NATIONAL_LOC:
            return "us-remote"                 # bare "United States" / "Remote"
        if any(t in loc for t in US_LOC_HINTS):
            return "us-remote"                 # "Remote - US", "US-based", ...
        if "remote" in loc or "anywhere" in loc:
            return "us-remote"                 # remote, no non-US marker → assume US-ok
        return ""                              # a city we didn't match → on-site elsewhere
    # No location field (e.g. HN "Who's hiring") — fall back to the body text.
    body = (content or "").lower()
    if any(k in body for k in NYC_KEYWORDS):
        return "nyc"
    if any(t in body for t in NON_US_LOC_HINTS):
        return ""
    if _SECONDARY_RE.search(body):
        return "secondary"
    if ALLOW_US_REMOTE and "remote" in body:
        return "us-remote"
    return ""


def location_ok(location, content):
    """Keep NYC, SF/Bay, and (when ALLOW_US_REMOTE) US-remote roles. Drops
    on-site-elsewhere (Austin, Seattle) and anything explicitly non-US."""
    return bool(location_tier(location, content))


# Back-compat alias (older callers referenced is_nyc).
is_nyc = location_ok


def size_score(company):
    n = COMPANY_SIZE.get((company or "").lower())
    if n is None:
        return 0
    import math
    val = math.log10(max(n, 1))          # ~0..4+
    return val if PREFER_LARGER else -val


def job_id(j):
    return hashlib.sha1((j["company"] + "|" + j["title"] + "|" + j["url"]).encode()).hexdigest()[:16]


# --------------------------- seen cache (dedupe across the day's 3 runs)
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    cutoff = datetime.now().timestamp() - SEEN_TTL_DAYS * 86400
    return {k: v for k, v in data.items() if v > cutoff}


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(seen, f)
    except Exception as e:
        print(f"[warn] could not write seen file: {e}")


# --------------------------- render + send
# --------------------------- email (Apple-flavoured: hairlines, generous
# whitespace, one accent colour, system font stack). Every style is inline —
# Gmail strips <style> blocks — and the layout is tables so it survives Outlook.
FONT = ("-apple-system,BlinkMacSystemFont,'SF Pro Text','Helvetica Neue',"
        "Helvetica,Arial,sans-serif")
INK, INK2, INK3 = "#1d1d1f", "#6e6e73", "#86868b"   # primary / secondary / tertiary
HAIRLINE, CANVAS, CARD = "#e5e5ea", "#f5f5f7", "#ffffff"
ACCENT, GOOD, WARM = "#0066cc", "#248a3d", "#a2600d"


def _chip(text, color=INK2, bg=CANVAS, bold=False):
    return (f"<span style=\"display:inline-block;padding:3px 9px;margin:0 6px 6px 0;"
            f"border-radius:999px;background:{bg};color:{color};font-size:12px;"
            f"line-height:16px;font-weight:{600 if bold else 500};"
            f"white-space:nowrap\">{text}</span>")


def _outline_chip(text, color=INK, border=HAIRLINE):
    return (f"<span style=\"display:inline-block;padding:3px 9px;margin:0 6px 6px 0;"
            f"border:1px solid {border};border-radius:7px;background:{CARD};"
            f"color:{color};font-size:12px;line-height:16px;font-weight:500;"
            f"white-space:nowrap\">{text}</span>")


# How close a capped signal came to its ceiling, as a word. "+9 skills" means
# nothing on its own — against a cap of 24 it means "partial", and that is the
# difference between a posting worth opening and one worth skipping.
_CAPS = {"title": lambda: TITLE_MAX,
         "resume skills": lambda: SKILL_MAX,
         "your domains": lambda: DOMAIN_MAX}
_MATCH_WORD = {"title": "title match", "resume skills": "skills match",
               "your domains": "domain match"}


def strength_label(label, pts):
    """'strong skills match' / 'partial title match', or the bare label for a
    signal that has no ceiling to be measured against (stage, location, funding)."""
    cap = _CAPS.get(label)
    if not cap or pts <= 0:
        return label
    ceiling = cap() or 0
    if ceiling <= 0:
        return label
    frac = pts / ceiling
    word = "strong" if frac >= 0.75 else "good" if frac >= 0.4 else "partial"
    return f"{word} {_MATCH_WORD[label]}"


def match_line(item):
    """'Why 83' — the resume evidence behind the score, in plain language.

    This is the answer to "how is it matching my resume": every component that
    moved the number, with the actual terms that hit, and then — dimmed, worth
    zero — the signals that did NOT fire. A missing title match or an unstated
    stage is as much of an explanation as a hit, and it used to be invisible.
    Nothing is hidden in code.
    """
    bd = item.get("breakdown") or {}
    parts = bd.get("parts") or []
    misses = bd.get("misses") or []
    if not parts and not misses:
        return ""
    bits = []
    for label, pts, terms in parts:
        label = strength_label(label, pts)
        shown = ", ".join(terms[:4])
        if len(terms) > 4:
            shown += f" +{len(terms) - 4}"
        color = "#b3261e" if pts < 0 else INK2
        sign = "+" if pts > 0 else "\u2212"
        detail = (" \u00b7 " + html.escape(shown)) if shown else ""
        piece = (f"<span style=\"color:{color}\"><b style=\"font-weight:600\">"
                 f"{sign}{abs(pts)}</b> {html.escape(label)}{detail}</span>")
        bits.append(piece)
    sep = (f"<span style=\"color:{HAIRLINE}\">&nbsp;&nbsp;|&nbsp;&nbsp;</span>")
    body = sep.join(bits)
    if misses:
        # Second line, dimmed and explicitly zero-weighted, so it reads as
        # "here is what it didn't get credit for" and never as arithmetic.
        gaps = "&nbsp;&nbsp;&middot;&nbsp;&nbsp;".join(
            f"no {html.escape(label)} \u2014 {html.escape(reason)}"
            if label in ("title", "skills", "domains") else
            f"{html.escape(label)}: {html.escape(reason)}"
            for label, reason in misses)
        body += (f"<div style=\"margin:6px 0 0;color:{HAIRLINE}\">"
                 f"<b style=\"font-weight:600\">0</b> {gaps}</div>")
    return (f"<div style=\"font-family:{FONT};margin:10px 0 0;padding:10px 12px;"
            f"background:{CANVAS};border-radius:10px;font-size:12px;"
            f"line-height:21px;color:{INK3}\">"
            f"<span style=\"color:{INK3};font-weight:600\">Why {item['q']}</span>"
            f"{sep}" + body + "</div>")


def context_block(item):
    """The extra colour an X/Grok lead carries: who posted, what they said, and
    where to apply. A tweet is a lead, not a job page — without the quote and the
    poster you cannot tell whether it is worth a reply."""
    if not (item.get("note") or item.get("poster")):
        return ""
    rows = []
    who = " ".join(x for x in (item.get("poster", ""), item.get("poster_title", "")) if x)
    if who:
        # "summarized from" not "posted by": what follows is Grok's reading of
        # the post, not the poster's own words. See the note below.
        rows.append(f"<div style=\"font-size:12px;color:{INK3};margin:0 0 6px\">"
                    f"summarized from a post by {html.escape(who)}</div>")
    # The `quote` field is deliberately NOT rendered as a quotation.
    # tests/verify_grok_quotes.py fetched the source tweets across two live runs:
    # 2 of 13 and 2 of 12 quotes did not appear in the tweet at all — one was
    # garbled ("PM grass counts with us"), one was a synthesized headline
    # ("Hiring: Deployment Strategist — Assembled"). At a ~15% fabrication rate,
    # printing it in quotation marks next to a real person's handle would put
    # words in their mouth in an email that invites you to reply to them. The
    # field still feeds the scoring blob, where being approximate costs nothing.
    if item.get("note"):
        rows.append(f"<div style=\"font-size:13px;line-height:20px;color:{INK2}\">"
                    f"{html.escape(item['note'])}</div>")
    links = []
    if item.get("apply_url"):
        links.append(f"<a href=\"{html.escape(item['apply_url'])}\" "
                     f"style=\"color:{ACCENT};text-decoration:none\">Apply \u2192</a>")
    if item.get("url"):
        links.append(f"<a href=\"{html.escape(item['url'])}\" "
                     f"style=\"color:{ACCENT};text-decoration:none\">See the post \u2192</a>")
    if links:
        rows.append(f"<div style=\"margin:8px 0 0;font-size:13px\">"
                    + "&nbsp;&nbsp;\u00b7&nbsp;&nbsp;".join(links) + "</div>")
    return (f"<div style=\"font-family:{FONT};margin:10px 0 0;padding:12px 14px;"
            f"background:{CANVAS};border-radius:10px\">" + "".join(rows) + "</div>")


# Board fetchers set company=<ats token> ("david-ai", "usenourish"), which reads
# like a database key in the email. Not a rename — just presentation.
_TOKEN_UPPER = {"ai", "ml", "hq", "io", "us", "gtm", "api", "crm", "vc"}


def pretty_company(name):
    n = (name or "").strip()
    if not n or " " in n:
        return n                                   # already a real display name
    words = [w for w in re.split(r"[-_]+", n) if w]
    return " ".join(w.upper() if w.lower() in _TOKEN_UPPER else w.capitalize()
                    for w in words)


def card(item, dim=False):
    esc = html.escape
    title_size = "15px" if dim else "17px"
    chips = []
    if item.get("location"):
        chips.append(_chip(esc(item["location"])))
    sz = COMPANY_SIZE.get((item.get("company") or "").lower())
    if sz:
        chips.append(_chip(f"~{sz} people"))
    tier = LOC_LABEL.get(item.get("loc_tier"), "")
    if tier and tier != "NYC":
        chips.append(_chip(tier))
    # On-site and hybrid are worth stating outright; "remote" is already obvious
    # from the location line and the US-remote tier chip.
    mode = item.get("work_mode")
    if mode in ("onsite", "hybrid"):
        away = mode == "onsite" and item.get("loc_tier") not in ("nyc", "")
        chips.append(_chip("hybrid" if mode == "hybrid" else "on-site",
                           color=WARM if away else INK2,
                           bg="#fdf4e3" if away else CANVAS))
    age = age_label(item.get("posted_ts"))
    if age:
        fresh = age in ("today", "1d ago")
        chips.append(_chip(age, color=GOOD if fresh else INK2,
                           bg="#eaf6ee" if fresh else CANVAS, bold=fresh))
    # 🚀 marks a posting at a company the funding digest saw raise recently — the
    # reason the req exists, and the best cold-outreach hook you get.
    if item.get("funding"):
        chips.append(_chip("\U0001F680 " + esc(funding_label(item["funding"])),
                           color=WARM, bg="#fdf3e3", bold=True))
    elif item.get("round"):
        chips.append(_chip(esc(item["round"]), color=WARM, bg="#fdf3e3"))
    chips.append(_chip(esc(item.get("source", "")), color=INK3))

    # Screening facts get their own outlined row, visually separate from the
    # metadata chips: these are the ones you actually rule the job out on.
    facts = item.get("facts") or {}
    fchips = []
    if facts.get("years"):
        stretch = facts.get("years_min", 0) > RESUME.get("years", 0) + 1
        fchips.append(_outline_chip(
            esc(facts["years"]) + (" \u26a0" if stretch else ""),
            color=WARM if stretch else INK, border="#f0dcb4" if stretch else HAIRLINE))
    for k in ("comp", "work_model", "equity", "sponsorship"):
        if facts.get(k):
            warn = k == "sponsorship" and facts[k].startswith("No")
            fchips.append(_outline_chip(esc(facts[k]),
                                        color=WARM if warn else INK,
                                        border="#f0dcb4" if warn else HAIRLINE))
    facts_row = (f"<div style=\"margin:9px 0 -6px\">{''.join(fchips)}</div>"
                 if fchips else "")

    fit = fit_sentence(item)
    fit_row = (f"<div style=\"font-family:{FONT};margin:11px 0 0;font-size:13px;"
               f"line-height:20px;color:{INK}\">{fit}</div>") if fit else ""

    comp = esc(pretty_company(item.get("company"))) or "\u2014"
    score_color = GOOD if item["q"] >= BEST_MATCH_MIN else INK3
    score_bg = "#eaf6ee" if item["q"] >= BEST_MATCH_MIN else CANVAS
    score_text = str(item["q"]).replace("-", "\u2212")
    return (
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" "
        f"style=\"background:{CARD};border:1px solid {HAIRLINE};border-radius:14px;"
        f"margin:0 0 10px\"><tr>"
        f"<td style=\"padding:16px 18px\">"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\"><tr>"
        f"<td valign=\"top\" style=\"font-family:{FONT}\">"
        f"<a href=\"{esc(item['url'])}\" style=\"color:{INK};text-decoration:none;"
        f"font-size:{title_size};font-weight:600;line-height:22px\">{esc(item['title'])}</a>"
        f"<div style=\"margin:3px 0 9px;font-size:13px;color:{INK2}\">"
        f"{comp}</div>"
        f"<div style=\"margin:0 0 -6px\">{''.join(chips)}</div>"
        f"{facts_row}"
        f"</td>"
        f"<td valign=\"top\" width=\"52\" align=\"right\" style=\"font-family:{FONT}\">"
        f"<span style=\"display:inline-block;min-width:32px;padding:4px 9px;"
        f"border-radius:9px;background:{score_bg};color:{score_color};"
        f"font-size:15px;font-weight:600;text-align:center\">{score_text}</span>"
        f"</td></tr></table>"
        f"{fit_row}"
        f"{context_block(item)}"
        f"{match_line(item)}"
        f"</td></tr></table>"
    )


def section(label, sub, items, dim=False):
    if not items:
        return ""
    return (
        f"<div style=\"font-family:{FONT};margin:26px 0 12px\">"
        f"<div style=\"font-size:13px;font-weight:600;color:{INK};"
        f"letter-spacing:.02em\">{label}"
        f"<span style=\"color:{INK3};font-weight:400\">&nbsp;&nbsp;{sub}</span></div>"
        f"</div>" + "".join(card(i, dim=dim) for i in items)
    )


def scoring_footer():
    """How the number is built, stated once at the bottom so the digest is
    self-explanatory six months from now."""
    rules = [
        ("Title match", "+12 each, max 40", "your target titles, matched in the job title"),
        ("Seniority", "+20 / \u221225", "junior signals in the JD vs 5+ years asks"),
        ("Resume skills", "+3 each, max 24", "roadmap, PRDs, Figma, SQL, B2B\u2026"),
        ("Your domains", "+2 each, max 12", "AI, workflow, proptech, fintech\u2026"),
        ("Stage", "+6 / +3 / \u22126", "known round (seed\u2013B) beats JD prose; "
         "+3 when only the prose says startup"),
        ("Location", "+10 / +5 / +2", "NYC / SF\u2013Bay / US-remote"),
        ("Just raised", f"+{FUNDED_BONUS}", "the funding digest saw them close a round"),
    ]
    rows = "".join(
        f"<tr><td style=\"padding:3px 14px 3px 0;color:{INK2};font-weight:600;"
        f"white-space:nowrap\">{k}</td>"
        f"<td style=\"padding:3px 14px 3px 0;color:{INK};white-space:nowrap\">{v}</td>"
        f"<td style=\"padding:3px 0;color:{INK3}\">{d}</td></tr>"
        for k, v, d in rules)
    return (
        f"<div style=\"font-family:{FONT};margin:30px 0 0;padding:16px 18px;"
        f"background:{CARD};border:1px solid {HAIRLINE};border-radius:14px\">"
        f"<div style=\"font-size:13px;font-weight:600;color:{INK};margin:0 0 10px\">"
        f"How the score works</div>"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" "
        f"style=\"font-size:12px;line-height:18px\">{rows}</table>"
        f"<div style=\"margin:11px 0 0;font-size:12px;color:{INK3};line-height:18px\">"
        f"Everything below {BEST_MATCH_MIN} still made it past the title, seniority "
        f"and location gates \u2014 it just matched fewer things on your resume.</div>"
        f"</div>")


def _shell(inner):
    return (
        f"<div style=\"background:{CANVAS};padding:28px 0;margin:0\">"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" "
        f"style=\"background:{CANVAS}\"><tr><td align=\"center\" style=\"padding:0 16px\">"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"640\" "
        f"style=\"width:100%;max-width:640px\"><tr><td>{inner}</td></tr></table>"
        f"</td></tr></table></div>")


def build_html(items):
    now = datetime.now().strftime("%A, %B %-d")
    clock = datetime.now().strftime("%-I:%M %p").lower()
    header = (
        f"<div style=\"font-family:{FONT}\">"
        f"<div style=\"font-size:26px;font-weight:600;color:{INK};letter-spacing:-.02em\">"
        f"Companies digest</div>"
        f"<div style=\"margin:5px 0 0;font-size:13px;color:{INK3}\">{now} \u00b7 {clock}</div>"
        f"</div>")
    if not items:
        return _shell(header + (
            f"<div style=\"font-family:{FONT};margin:22px 0 0;padding:20px 18px;"
            f"background:{CARD};border:1px solid {HAIRLINE};border-radius:14px;"
            f"font-size:14px;color:{INK2}\">Nothing new this run.</div>"))

    best = [i for i in items if i["q"] >= BEST_MATCH_MIN]
    rest = [i for i in items if i["q"] < BEST_MATCH_MIN]
    summary = (f"{len(items)} new posting{'' if len(items) == 1 else 's'}"
               + (f" \u00b7 {len(best)} strong match{'' if len(best) == 1 else 'es'}"
                  if best else ""))
    header += (f"<div style=\"font-family:{FONT};margin:9px 0 0;font-size:13px;"
               f"color:{INK2}\">{summary}</div>")
    return _shell(
        header
        + section("Best matches", f"{BEST_MATCH_MIN}+ on your resume", best)
        + section("Worth a look", f"under {BEST_MATCH_MIN}", rest, dim=True)
        + scoring_footer())


def from_header():
    """'Job Bot (@Name) <sending@addr>' so the digest doesn't show up as yourself.
    Handle comes from DIGEST_HANDLE, else the first name in the sending address."""
    handle = os.environ.get("DIGEST_HANDLE", "").strip()
    if not handle:
        m = re.match(r"[A-Za-z]+", (EMAIL_USER or "").split("@")[0])
        handle = m.group(0).capitalize() if m else "you"
    return formataddr((f"Job Bot (@{handle})", EMAIL_USER))


def send_email(html_body):
    if not (EMAIL_USER and EMAIL_PASS and EMAIL_TO):
        print("[info] email creds not set — printing digest:\n")
        print(re.sub(r"<[^>]+>", "", html_body))
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Companies digest — {datetime.now().strftime('%b %d %-I%p')}"
    msg["From"] = from_header()
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
        srv.starttls()
        srv.login(EMAIL_USER, EMAIL_PASS)
        srv.sendmail(EMAIL_USER, [a.strip() for a in EMAIL_TO.split(",")], msg.as_string())
    print(f"[ok] sent companies digest to {EMAIL_TO}")


def main():
    # LOOP: pull in ATS tokens auto-detected by the funding digest
    gh, lv, ash = list(GREENHOUSE_COMPANIES), list(LEVER_COMPANIES), list(ASHBY_COMPANIES)
    wk, sr, rip = [], [], []
    try:
        import watchlist as _wl
        auto = _wl.get_tokens()
        gh  = list(dict.fromkeys(gh  + auto.get("greenhouse", [])))
        lv  = list(dict.fromkeys(lv  + auto.get("lever", [])))
        ash = list(dict.fromkeys(ash + auto.get("ashby", [])))
        # Workable / SmartRecruiters / Rippling have no hand-maintained seed
        # list — every token comes from getro.py reading a real apply URL.
        wk  = list(dict.fromkeys(auto.get("workable", [])))
        sr  = list(dict.fromkeys(auto.get("smartrecruiters", [])))
        rip = list(dict.fromkeys(auto.get("rippling", [])))
        extra = len(gh) - len(GREENHOUSE_COMPANIES) + len(lv) - len(LEVER_COMPANIES) \
                + len(ash) - len(ASHBY_COMPANIES) + len(wk) + len(sr) + len(rip)
        if extra:
            print(f"[loop] +{extra} auto-detected companies from funding digest")
        # Real company sizes harvested from the VC boards, so size_score() ranks
        # on data instead of the mostly-empty hand-filled COMPANY_SIZE dict.
        sizes = _wl.get_sizes()
        if sizes:
            for name, n in sizes.items():
                COMPANY_SIZE.setdefault(name, n)
            print(f"[loop] {len(sizes)} company sizes from the watchlist")
    except Exception as e:
        print(f"[info] watchlist not available: {e}")
    _load_funded()
    _load_stage()

    raw = []
    # Remotive is remote-only and Arbeitnow is mostly EU/remote — under a strict
    # NYC gate (REQUIRE_NYC on, REMOTE_OK off) they yield ~nothing, so skip the
    # calls entirely instead of fetching hundreds of postings just to discard
    # them. Flip REMOTE_OK=True (or REQUIRE_NYC=False) to use them again.
    remote_useful = REMOTE_OK or not REQUIRE_NYC
    if USE_REMOTIVE and remote_useful:   raw += fetch_remotive()
    elif USE_REMOTIVE:                   print("[info] skipping Remotive (remote-only vs NYC gate)")
    if USE_ARBEITNOW and remote_useful:  raw += fetch_arbeitnow()
    elif USE_ARBEITNOW:                  print("[info] skipping Arbeitnow (remote/EU vs NYC gate)")
    if USE_HN_WHOISHIRING: raw += fetch_hn()
    # Free aggregators that DO expose location, so they survive the NYC/US-remote
    # gate: The Muse (keyless) and Adzuna (free app id/key). These reach roles at
    # companies not on our ATS watchlist — e.g. a LinkedIn-surfaced posting that
    # Google-for-Jobs/Adzuna also indexes.
    if USE_MUSE:   raw += fetch_muse()
    if USE_ADZUNA: raw += fetch_adzuna()
    # X / Grok: founder "we're hiring" posts that never hit a job board.
    if USE_GROK_X: raw += fetch_grok_x()
    # Board fetches run concurrently. Serially, a watchlist in the hundreds —
    # which getro.py produces in a single sweep — would not finish inside the
    # Actions run; these are independent read-only calls to six different hosts,
    # so the only real limit is politeness per host.
    boards = ([(fetch_greenhouse, t) for t in gh]
              + [(fetch_lever, t) for t in lv]
              + [(fetch_ashby, t) for t in ash]
              + [(fetch_workable, t) for t in wk]
              + [(fetch_smartrecruiters, t) for t in sr]
              + [(fetch_rippling, t) for t in rip])
    if USE_WORKDAY:
        boards += [(fetch_workday, e) for e in WORKDAY_COMPANIES]
    if boards:
        t0 = datetime.now(timezone.utc).timestamp()
        with ThreadPoolExecutor(max_workers=BOARD_WORKERS) as ex:
            for lst in ex.map(lambda fa: fa[0](fa[1]), boards):
                raw += lst
        print(f"[info] {len(boards)} boards fetched in "
              f"{datetime.now(timezone.utc).timestamp() - t0:.1f}s")
    print(f"[info] fetched {len(raw)} raw postings")

    seen = load_seen()
    now_ts = datetime.now(timezone.utc).timestamp()
    age_cutoff = (now_ts - MAX_AGE_DAYS * 86400) if MAX_AGE_DAYS else 0
    picked, ids_this_run, seen_titles = [], set(), set()

    for j in raw:
        if not j.get("title") or not j.get("url"):
            continue
        # HN comments have freeform titles; only title-filter the structured sources
        if j["source"] != "HN Who's Hiring" and not title_is_target(j["title"]):
            continue
        if j["source"] == "HN Who's Hiring" and not any(
            x in j["content"].lower() for x in RESUME["target_titles"]):
            continue
        # Recency gate: drop postings older than MAX_AGE_DAYS when the source
        # gives a date. Unknown-date sources fall through (kept, no age shown).
        posted = j.get("posted_ts")
        if age_cutoff and posted and posted < age_cutoff:
            continue
        jid = job_id(j)
        if jid in seen or jid in ids_this_run:
            continue
        # The same role surfaced by two boards — or posted by one company to
        # several cities — is collapsed to one entry keyed on company + title.
        # That happens AFTER scoring and sorting, not here: a multi-city posting
        # like "Oscar / Product Manager, Network" lands as NYC, SF and on-site
        # variants, and dropping on first sight kept whichever the board
        # happened to return first. Now the NYC one wins because it scores highest.
        j["_tkey"] = (j.get("company", "").lower().strip(), j["title"].lower().strip())
        tier = location_tier(j.get("location", ""), j.get("content", ""))
        if REQUIRE_NYC and not tier:
            continue
        bd = score_breakdown(j["title"], j["content"])
        if bd["total"] <= 0:
            continue
        # Geography and "this company just raised" are ranking signals, not gates —
        # applied after the total<=0 cut so neither can resurrect an off-resume
        # posting. Both go in `parts` so the email can show the whole arithmetic.
        fund = funding_note(j.get("company", ""))
        loc_pts = LOC_BONUS.get(tier, 0)
        if loc_pts:
            bd["parts"].append((LOC_LABEL.get(tier, tier), loc_pts, []))

        # Stage: what the company IS, not what the JD says. A known round
        # replaces the text-derived guess outright — Courted is a Series A
        # whether or not the posting mentions it, and the prose tells were only
        # ever a fallback for companies we have no fact about.
        stage = stage_note(j.get("company", ""))
        stage_pts = stage_points(stage.get("round")) if stage else None
        if stage_pts is not None:
            bd["parts"] = [p for p in bd["parts"]
                           if not p[0].startswith(("early stage", "late stage"))]
            if stage_pts:
                label = "early stage" if stage_pts > 0 else "late stage"
                bd["parts"].append((label, stage_pts, [stage["round"]]))
            bd["total"] = sum(p[1] for p in bd["parts"]) - loc_pts
        if fund:
            bd["parts"].append(("just raised", FUNDED_BONUS, []))
        j["loc_tier"] = tier
        j["work_mode"] = work_mode(j.get("location", ""), j.get("content", ""))
        j["funding"] = fund
        j["stage"] = stage
        j["breakdown"] = bd
        j["facts"] = screen_facts(j.get("content", ""))
        j["q"] = bd["total"] + loc_pts + (FUNDED_BONUS if fund else 0)
        j["_id"] = jid
        picked.append(j)
        ids_this_run.add(jid)

    # Rank by resume match first, then freshest, then company-size preference.
    picked.sort(key=lambda x: (x["q"], x.get("posted_ts") or 0, size_score(x["company"])),
                reverse=True)
    # Now collapse duplicates, keeping the best-scoring variant of each role —
    # which, given the location bonus, is the NYC posting whenever there is one.
    deduped = []
    for j in picked:
        if j["_tkey"] in seen_titles:
            continue
        seen_titles.add(j["_tkey"])
        deduped.append(j)
    if len(deduped) < len(picked):
        print(f"[info] collapsed {len(picked) - len(deduped)} duplicate postings "
              f"(same company + title, kept the best-ranked location)")
    # The two sections are capped SEPARATELY. One combined cap made the
    # "Worth a look" section unreachable: there are consistently more than
    # MAX_ITEMS postings scoring above BEST_MATCH_MIN, so a single [:MAX_ITEMS]
    # slice never reached the sub-threshold ones and they were dropped rather
    # than relegated. Capping each section means "below BEST_MATCH_MIN goes in
    # the quieter area" is actually true, and the quiet area stays bounded.
    best = [j for j in deduped if j["q"] >= BEST_MATCH_MIN][:MAX_ITEMS]
    rest = [j for j in deduped if j["q"] < BEST_MATCH_MIN][:MAX_ITEMS_REST]
    print(f"[info] {len(best)} at/above {BEST_MATCH_MIN} (cap {MAX_ITEMS}), "
          f"{len(rest)} below (cap {MAX_ITEMS_REST}), "
          f"from a pool of {len(deduped)}")
    picked = best + rest

    for j in picked:
        seen[j["_id"]] = now_ts
    save_seen(seen)

    print(f"[info] {len(picked)} new matching postings this run")
    if picked or SEND_WHEN_EMPTY:
        send_email(build_html(picked))
    else:
        print("[info] nothing new this run — skipping email (set SEND_WHEN_EMPTY=1 to override)")


if __name__ == "__main__":
    main()
