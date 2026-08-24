#!/usr/bin/env python3
"""Faithfulness check for the X/Grok source.

The companies digest prints `quote` as a verbatim quotation attributed to a
named person ("posted by @someone"). Nothing in the pipeline verifies that the
string actually appears in the tweet — so a paraphrase from the model becomes
words put in a real founder's mouth, in an email that invites you to reply to
them. This fetches each lead's tweet and checks.

Deterministic where the URL resolves: no LLM judge, just substring matching on
normalized text. Where it can't resolve (X blocks unauthenticated fetches from
some IPs, or the model returned a Google-search fallback URL), it reports
UNVERIFIABLE rather than passing — an unchecked quote is not a checked one.

    python tests/verify_grok_quotes.py              # live: run a Grok search first
    python tests/verify_grok_quotes.py leads.json   # replay a saved capture

Exit code is 1 if any lead's quote is CONTRADICTED (fetched fine, quote absent).
"""
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import companies_digest as C          # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; quote-check)"}
# r.jina.ai renders the page server-side; x.com itself returns a JS shell with no
# tweet text in it, which would make every lead look UNVERIFIABLE.
READER = "https://r.jina.ai/"


def norm(s):
    """Fold the transformations that are not the model's fault: smart quotes,
    entities, collapsed whitespace, and the unicode ellipsis X inserts."""
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-").replace("…", "...")
    s = re.sub(r"&\w+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch(url):
    try:
        req = urllib.request.Request(READER + url, headers=UA)
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        return f"__ERROR__ {e}"


def longest_common_run(needle, hay):
    """Fraction of the quote's longest contiguous run that survives in the page.

    A strict `in` test fails on a single character of drift (a stray comma, a
    truncated trailing word) and would cry wolf constantly. Requiring a long
    contiguous run still catches an invented or reworded quote, which shares no
    long run with the source at all.
    """
    if not needle:
        return 0.0
    best = 0
    for i in range(len(needle)):
        for j in range(len(needle), i + best, -1):
            if needle[i:j] in hay:
                best = j - i
                break
    return best / len(needle)


# A quote can drift a little in transit and still be honest; it cannot share
# only a fragment with the source. Below this, treat it as not the same text.
MIN_OVERLAP = 0.75


def check(lead):
    quote = (lead.get("quote") or "").strip()
    url = (lead.get("url") or lead.get("tweet_url") or "").strip()
    who = lead.get("company") or lead.get("title") or "?"
    if not quote:
        return ("SKIP", who, "no quote returned")
    if "x.com" not in url and "twitter.com" not in url:
        return ("UNVERIFIABLE", who, f"not a tweet URL ({url[:60]})")
    page = fetch(url)
    if page.startswith("__ERROR__"):
        return ("UNVERIFIABLE", who, page[10:80])
    overlap = longest_common_run(norm(quote), norm(page))
    if overlap >= MIN_OVERLAP:
        return ("OK", who, f"{overlap:.0%} of the quote found verbatim")
    return ("CONTRADICTED", who,
            f"only {overlap:.0%} contiguous overlap - quote: {quote[:80]!r}")


def main():
    if len(sys.argv) > 1:
        leads = json.load(open(sys.argv[1]))
        print(f"[replay] {len(leads)} leads from {sys.argv[1]}")
    else:
        if not os.environ.get("XAI_API_KEY"):
            C.load_dotenv()
        leads = C.fetch_grok_x()
        print(f"[live] {len(leads)} leads from the X/Grok source")
        out = os.environ.get("GROK_CAPTURE")
        if out:
            json.dump(leads, open(out, "w"), indent=2)
            print(f"[live] saved to {out}")

    tally, bad = {}, 0
    for lead in leads:
        verdict, who, why = check(lead)
        tally[verdict] = tally.get(verdict, 0) + 1
        if verdict == "CONTRADICTED":
            bad += 1
        print(f"  {verdict:14} {who[:28]:28} {why}")

    print("[result] " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    if bad:
        print(f"[FAIL] {bad} quote(s) not found in the source tweet - the digest "
              f"would attribute them to a named person anyway.")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
