#!/usr/bin/env python3
"""Accuracy eval for enrich.ai_lookup — the researcher whose output silently
deletes companies from the funding digest.

`off_sector_reject` drops a company whose category isn't in KEEP_CATEGORIES —
that one deletes it from BOTH the outreach email and the job watchlist.
`us_only_reject` drops a company whose hq_country isn't the US, and since the
role-vs-company fix it affects the outreach email ONLY: the company's board is
still watched, because the geography that matters for a job is the ROLE's, and
companies_digest gates every posting on its own location.

So the two fields carry different stakes, and the report separates them:

  category wrong -> the company vanishes from the digest entirely
  hq wrong       -> you lose a cold-email opportunity, but not the jobs

Both still fail silently: nothing in any email says a company was dropped.

WHY THIS ISN'T JUST A SECOND MODEL CALL
---------------------------------------
Asking another model with web search to grade the first one mostly measures
whether two models make the same mistakes — they share training data and often
the same sources, so agreement is weak evidence. This eval corroborates against
evidence the researcher did not choose:

  domain    -> resolve it; does the live page name the company?     (no model)
  hq        -> fetch the company's OWN site (/about, /contact) and
               look for the claimed city/country.                   (no model)
  category  -> a judge model that sees ONLY the fetched site text,
               never web search, and must justify from that text.   (model, but
               different evidence, and it can say the text is insufficient)

The headline metric is not per-field accuracy. It is GATE IMPACT: how many
companies this run would have wrongly dropped, and how many it would have
wrongly kept.

    python evals/eval_enrich.py                       # run the fixture set
    python evals/eval_enrich.py --companies "Acme,Foo"
    python evals/eval_enrich.py --out results.json

Needs ANTHROPIC_API_KEY. Costs roughly one web-search research call plus one
short judge call per company.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import enrich as E                    # noqa: E402
import funding_digest as F            # noqa: E402
import watchlist as W                 # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (compatible; enrich-eval)"}
READER = "https://r.jina.ai/"         # renders JS-heavy marketing sites to text

US_NAMES = {"united states", "usa", "us", "u.s.", "u.s.a.", "america",
            "united states of america"}


# --------------------------------------------------------------- page fetching
def fetch_text(url, timeout=40):
    """Readable text for a URL, or "" — via r.jina.ai, because most startup
    landing pages are JS shells with no prose in the raw HTML."""
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        req = urllib.request.Request(READER + url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return ""


def site_corpus(domain, website):
    """The company's own words: home page plus the pages that carry an address."""
    base = (website or domain or "").strip()
    if not base:
        return ""
    root = re.sub(r"^https?://", "", base).rstrip("/")
    parts = [fetch_text(root)]
    # /privacy and /terms are the highest-yield pages for an HQ: a marketing site
    # rarely states where the company is, but a privacy policy nearly always
    # carries a registered legal address because it has to.
    for path in ("about", "company", "contact", "privacy", "terms",
                 "legal", "privacy-policy"):
        if sum(len(p) for p in parts) > 60_000:
            break
        parts.append(fetch_text(f"{root}/{path}"))
    return "\n".join(p for p in parts if p)


# ------------------------------------------------------------------ the checks
def check_domain(enr):
    """Does the claimed domain resolve, and does the live page name the company?"""
    domain = (enr.get("domain") or "").strip()
    if not domain:
        return "MISSING", "no domain returned"
    if not re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$", domain, re.I):
        return "MALFORMED", f"{domain!r} is not a domain"
    text = fetch_text(domain)
    if not text:
        return "UNVERIFIABLE", f"{domain} did not fetch"
    # Match on the distinctive part of the name: a site belonging to "Anduril
    # Industries" writes "Anduril", and "Acme Inc." writes "Acme". Requiring the
    # full legal name flags real matches as suspect.
    raw = (enr.get("_company") or "").lower()
    stripped = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|co|technologies|"
                      r"labs?|industries|group|holdings|ai|io)\b", " ", raw)
    flat = re.sub(r"[^a-z0-9]", "", text.lower())
    for cand in (re.sub(r"[^a-z0-9]", "", raw),
                 re.sub(r"[^a-z0-9]", "", stripped)):
        if len(cand) >= 3 and cand in flat:
            return "OK", f"{domain} names the company"
    return "SUSPECT", f"{domain} resolves but never names {enr.get('_company')!r}"


# A country name has to appear in an HQ-ish context to count as a contradiction.
# Every startup marketing site mentions a dozen countries — Ramp's site says
# "Canada" because it sells there, which is not a claim about its headquarters.
_HQ_CONTEXT = (r"(?:headquarter\w*|head office|hq|based in|located in|our office"
               r"|offices? in|registered in|address)")
_ADDRESSY = re.compile(r"(?:suite|floor|street|st\.|ave|avenue|road|blvd|\b\d{5}\b)", re.I)


def _city_variants(city):
    """'New York City' should match a site that writes 'New York, NY'."""
    c = (city or "").strip().lower()
    if not c:
        return []
    out = {c}
    out.add(re.sub(r"\s+city$", "", c))
    out.add(c.split(",")[0].strip())
    return [v for v in out if len(v) > 2]


def check_hq(enr, corpus):
    """Is the claimed HQ corroborated by the company's own site?

    Three outcomes, and the middle one is the honest default: absence of an
    address is extremely common and is NOT evidence of a wrong answer.

      OK            the site names the claimed city or country
      CONTRADICTED  the site names a DIFFERENT country in an HQ context
      UNVERIFIABLE  the site says nothing locating the company
    """
    city = (enr.get("hq_city") or "").strip()
    country = (enr.get("hq_country") or "").strip()
    if not country:
        return "MISSING", "no hq_country returned (gate fails open, company kept)"
    if not corpus:
        return "UNVERIFIABLE", "company site did not fetch"
    low = re.sub(r"\s+", " ", corpus.lower())

    for v in _city_variants(city):
        if v in low:
            return "OK", f"site mentions {v!r}"
    if country.lower() in low:
        return "OK", f"site mentions {country}"
    if country.lower() in US_NAMES and re.search(r"\b(usa|u\.s\.|united states)\b", low):
        return "OK", "site names the US"

    # Only now consider a contradiction, and only in an HQ-ish context.
    for other in ("united kingdom", "canada", "india", "germany", "france",
                  "singapore", "australia", "israel", "netherlands", "sweden",
                  "spain", "ireland", "japan", "brazil", "mexico",
                  "united states"):
        if other == country.lower():
            continue
        near = re.search(rf"{_HQ_CONTEXT}[^.]{{0,80}}{re.escape(other)}"
                         rf"|{re.escape(other)}[^.]{{0,40}}{_HQ_CONTEXT}", low)
        if near:
            return "CONTRADICTED", (f"claimed {country}; site says "
                                    f"{near.group(0)[:70].strip()!r}")
    # The site said nothing useful. Ask the company's job board instead.
    board = check_hq_via_board(enr, enr.get("_company") or "")
    if board and board[0] in ("OK", "CONTRADICTED"):
        return board[0], "board: " + board[1]
    if board:
        return "UNVERIFIABLE", "board: " + board[1]
    if _ADDRESSY.search(low):
        return "UNVERIFIABLE", f"site has an address but not {city or country}"
    return "UNVERIFIABLE", "site never locates the company"


# --------------------------------- HQ evidence source 2: the company's own board
# The marketing site is a weak source — half of them never say where the company
# is. Its job board is a far better one, and it is independent of BOTH the
# researcher's web search and the site copy: it is operational data the company
# maintains for a different purpose. It is also the more decision-relevant
# answer, since what matters for a job is where the ROLES are.
_FETCHERS = {}


def _fetchers():
    if not _FETCHERS:
        import companies_digest as C
        for prov in W.PROVIDERS:
            fn = getattr(C, f"fetch_{prov}", None)
            if fn:
                _FETCHERS[prov] = fn
    return _FETCHERS


def ats_job_locations(company, domain):
    """Counter of posting locations from the company's public ATS board, or None.

    Reuses the watchlist's own probe/fetch machinery, so this measures exactly
    what the companies digest would see.
    """
    for tok in W.candidate_tokens(company, domain):
        for prov, probe in W.PROBES:
            try:
                if not probe(tok):
                    continue
            except Exception:
                continue
            fn = _fetchers().get(prov)
            if not fn:
                continue
            try:
                jobs = fn(tok)
            except Exception:
                return None
            if jobs:
                return collections.Counter(
                    (j.get("location") or "?").strip() for j in jobs)
    return None


def check_hq_via_board(enr, company):
    """Corroborate the claimed HQ against where the company posts jobs."""
    locs = ats_job_locations(company, enr.get("domain"))
    if not locs:
        return None
    total = sum(locs.values())
    blob = " | ".join(locs).lower()
    city, country = (enr.get("hq_city") or ""), (enr.get("hq_country") or "")

    # Some boards mark it outright — Ramp posts "New York, NY (HQ)".
    for loc, n in locs.most_common():
        if "(hq)" in loc.lower() or "headquarter" in loc.lower():
            hit = any(v in loc.lower() for v in _city_variants(city))
            return (("OK" if hit else "CONTRADICTED"),
                    f"board marks {loc!r} as HQ ({n}/{total} roles)")

    # An office in the claimed city is evidence; a couple of roles there is not
    # evidence it is the HEADQUARTERS. Grade by share, so "2/104 roles in San
    # Francisco" stops counting as a clean pass.
    for v in _city_variants(city):
        n = sum(c for l, c in locs.items() if v in l.lower())
        if not n:
            continue
        share = n / total if total else 0
        top = locs.most_common(1)[0]
        if share >= 0.25 or v in top[0].lower():
            return "OK", f"{n}/{total} roles in {v!r} on its own board"
        return "WEAK", (f"only {n}/{total} roles in {v!r}; board is mostly "
                        f"{top[0]!r} - an office there, not necessarily the HQ")
    # City unconfirmed. The gate only reads hq_country, so fall back to country
    # level — and reuse companies_digest.location_tier as the US detector rather
    # than writing a second, untested one ("SF Office", "Remote (US)", "Austin,
    # TX" and "California" all have to count).
    if country.lower() in US_NAMES:
        import companies_digest as C
        us = sum(c for l, c in locs.items() if C.location_tier(l, ""))
        share = us / total if total else 0
        # A majority, not a single hit: 2 US roles out of 46 corroborates nothing.
        if share >= 0.5:
            return "OK", f"{us}/{total} roles US-located on its own board"
        if share > 0:
            return "WEAK", (f"claimed {country} but only {us}/{total} roles are "
                            f"US-located")
        return "CONTRADICTED", f"claimed {country}; no US roles on its own board"
    top = locs.most_common(1)[0]
    return "WEAK", f"claimed {city or country}; board is mostly {top[0]!r} ({top[1]}/{total})"


JUDGE_PROMPT = """You are grading one field of an automated company researcher.

Below is text scraped from a company's OWN website. Using ONLY that text — do
not use outside knowledge — decide whether this sector label is defensible:

    claimed category: {category}

Allowed categories: {categories}

Grade by what the company SELLS, not what it uses internally: a company applying
AI to design drugs is biotech/pharma; one selling software that happens to run on
GPUs is ai/software.

If the text is too thin to tell, say so — "insufficient" is a valid answer and is
better than a guess.

Return ONLY JSON: {{"verdict":"agree|disagree|insufficient","better":"<category or empty>","why":"one sentence"}}

--- SITE TEXT ---
{text}
--- END ---"""


def judge_category(enr, corpus):
    """Second model, deliberately handicapped: only the company's own site text,
    no web search. If it can't tell from that, it must say so."""
    cat = (enr.get("category") or "").strip()
    if not cat:
        return "MISSING", "no category returned (gate fails open, company kept)"
    if not corpus:
        return "UNVERIFIABLE", "company site did not fetch"
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return "UNVERIFIABLE", "ANTHROPIC_API_KEY not set"
    body = {
        "model": os.environ.get("EVAL_JUDGE_MODEL", "claude-sonnet-5"),
        "max_tokens": 300,
        "messages": [{"role": "user", "content": JUDGE_PROMPT.format(
            category=cat, categories=", ".join(E.CATEGORIES),
            text=corpus[:12000])}],
    }
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        return "UNVERIFIABLE", f"judge call failed: {e}"
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")
    v = E._parse_json(text)
    verdict = (v.get("verdict") or "").lower()
    if verdict == "agree":
        return "OK", v.get("why", "")
    if verdict == "disagree":
        better = (v.get("better") or "").strip().lower()
        why = (v.get("why") or "").lower()
        # Guard against the judge contradicting ITSELF. On Synthesia it set
        # better="consumer software" while its own reasoning argued for "b2b
        # saas" — and those fall on opposite sides of the gate, so believing the
        # label would have manufactured a finding out of judge noise. When the
        # prose names a different allowed category than the label, the verdict
        # is not trustworthy enough to call the researcher wrong.
        named = [c for c in E.CATEGORIES if c in why and c != cat]
        if better and named and better not in named:
            return "UNVERIFIABLE", (f"judge inconsistent: label {better!r} but its "
                                    f"reasoning argues {named[0]!r}")
        # A disagreement only MATTERS if it would move the company across the
        # sector gate. "ai/software vs b2b saas" is a taxonomy quibble — both are
        # in KEEP_CATEGORIES, the company survives either way, and counting it as
        # an error buries the disagreements that actually change an outcome.
        claimed_in = cat in F.KEEP_CATEGORIES
        better_in = better in F.KEEP_CATEGORIES
        if better and claimed_in == better_in:
            return "QUIBBLE", (f"judge prefers {better}; same side of the gate, "
                               f"no effect")
        return "CONTRADICTED", f"judge says {better or '?'}: {v.get('why', '')}"
    return "UNVERIFIABLE", v.get("why", "judge could not tell from the site text")


# ------------------------------------------------------------- the gate impact
def gate_impact(enr, hq_verdict, cat_verdict, label):
    """What the two gates DO to this company, and whether that looks wrong.

    `label` is the fixture's hand-written expectation ("keep"/"drop"/"") — the
    only place ground truth enters. Where there is no label, a wrong gate is
    still inferable from a CONTRADICTED corroboration.
    """
    geo = F.us_only_reject(enr)
    sector = F.off_sector_reject(enr)
    dropped = bool(geo or sector)
    # Only the sector gate removes a company from the job watchlist; the geo gate
    # now costs it the outreach email alone.
    why = ((f"no-email(geo:{geo})" if geo else "")
           + (f" dropped-everywhere(sector:{sector})" if sector else ""))

    if label == "keep" and dropped:
        return ("WRONGLY DROPPED" if sector else "WRONGLY UN-EMAILED"), why.strip()
    if label == "drop" and not dropped:
        return "WRONGLY KEPT", "survived both gates"
    if hq_verdict == "CONTRADICTED" or cat_verdict == "CONTRADICTED":
        return ("SUSPECT DROP" if dropped else "SUSPECT KEEP"), why.strip() or "-"
    return ("dropped" if dropped else "kept"), why.strip() or "-"


# ------------------------------------------------------------------------ main
def load_fixtures(path):
    with open(path) as f:
        return json.load(f)["companies"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", help="comma-separated names, instead of the fixtures")
    ap.add_argument("--fixtures", default=os.path.join(HERE, "fixtures", "companies.json"))
    ap.add_argument("--out", help="write full results as JSON")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # funding_digest has no .env loader of its own; companies_digest does.
        import companies_digest as _cd
        _cd.load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set - this eval needs it.")
        return 2
    E.ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

    if args.companies:
        cases = [{"name": n.strip(), "expect": ""} for n in args.companies.split(",")]
    else:
        cases = load_fixtures(args.fixtures)
    if args.limit:
        cases = cases[:args.limit]

    print(f"[eval] {len(cases)} companies, judge="
          f"{os.environ.get('EVAL_JUDGE_MODEL', 'claude-sonnet-5')}, "
          f"researcher={E.AI_MODEL}\n")

    results, tally = [], {}
    for c in cases:
        name = c["name"]
        # Bypass the cache: we are measuring the researcher, not the cache.
        enr = E.ai_lookup(name)
        enr["_company"] = name
        corpus = site_corpus(enr.get("domain"), enr.get("website"))

        dv, dwhy = check_domain(enr)
        hv, hwhy = check_hq(enr, corpus)
        cv, cwhy = judge_category(enr, corpus)
        gate, gwhy = gate_impact(enr, hv, cv, c.get("expect", ""))

        for k, v in (("domain", dv), ("hq", hv), ("category", cv)):
            tally.setdefault(k, {}).setdefault(v, 0)
            tally[k][v] += 1
        tally.setdefault("gate", {}).setdefault(gate, 0)
        tally["gate"][gate] += 1

        print(f"  {name}")
        print(f"     researched : {enr.get('hq_city') or '?'}, "
              f"{enr.get('hq_country') or '?'} | {enr.get('category') or '?'} | "
              f"{enr.get('domain') or '?'}")
        print(f"     domain     : {dv:14} {dwhy}")
        print(f"     hq         : {hv:14} {hwhy}")
        print(f"     category   : {cv:14} {cwhy}")
        print(f"     GATE       : {gate:14} {gwhy}")
        print()
        results.append({"name": name, "expect": c.get("expect", ""),
                        "enrich": {k: v for k, v in enr.items() if k != "_company"},
                        "domain": [dv, dwhy], "hq": [hv, hwhy],
                        "category": [cv, cwhy], "gate": [gate, gwhy]})

    print("=" * 68)
    for field in ("domain", "hq", "category", "gate"):
        line = ", ".join(f"{k}={v}" for k, v in sorted(tally.get(field, {}).items()))
        print(f"  {field:9} {line}")

    g = tally.get("gate", {})
    wrong = sum(v for k, v in g.items() if k.startswith("WRONGLY"))
    suspect = sum(v for k, v in g.items() if k.startswith("SUSPECT"))
    print("=" * 68)
    print(f"  {wrong} wrong gate decision(s), {suspect} suspect, out of {len(cases)}")
    if g.get("WRONGLY DROPPED"):
        print(f"  {g['WRONGLY DROPPED']} company(ies) removed from the digest "
              f"ENTIRELY by a wrong sector call - invisible in production.")
    if g.get("WRONGLY UN-EMAILED"):
        print(f"  {g['WRONGLY UN-EMAILED']} lost their outreach email to a wrong "
              f"HQ call; their job boards are still watched.")

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"tally": tally, "results": results}, f, indent=1)
        print(f"  full results -> {args.out}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
