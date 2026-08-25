#!/usr/bin/env python3
"""
setup_profile.py — turn your resume + plain-English preferences into the keyword
filters both digests use.

Reads profile.json (see profile.example.json), sends your resume + your
`roles` / `startup_types` sentences to Claude, and writes profile.compiled.json:
  { target_titles, skills, domains, focus_keywords, years,
    location_keywords, remote_ok, prefer_larger }

companies_digest.py and funding_digest.py load that file automatically if present, so
you configure everything here without editing any code. Re-run whenever you
change profile.json or your resume.

Usage:
    python setup_profile.py                 # uses profile.json
    python setup_profile.py my_profile.json

Requires ANTHROPIC_API_KEY (env or a local .env file). PDFs are sent to Claude
directly — no PDF library needed. .txt / .md resumes are sent as text.
"""

import os
import re
import sys
import json
import base64
import urllib.request

SETUP_MODEL = os.environ.get("SETUP_MODEL", "claude-sonnet-5")
OUT_FILE = "profile.compiled.json"


def load_dotenv(path=".env"):
    """Minimal .env loader so this works locally without exporting vars."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


def resume_block(path):
    """Return a Claude content block for the resume, or None."""
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:
        print(f"[warn] could not read resume {path}: {e} — continuing without it")
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return {"type": "document",
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.b64encode(raw).decode()}}
    # treat everything else as text
    text = raw.decode("utf-8", "replace")[:20000]
    return {"type": "text", "text": f"RESUME:\n{text}"}


def parse_json(text):
    text = (text or "").strip().strip("`")
    text = re.sub(r"^json", "", text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def compile_profile(profile):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        sys.exit("[error] ANTHROPIC_API_KEY not set (env or .env). Needed to compile the profile.")

    instructions = (
        "You configure a keyword-based job-search filter. Using the attached RESUME "
        "(if provided) and the stated preferences, output ONLY a JSON object (no prose, "
        "no code fences) with these lowercase-string-array keys unless noted:\n"
        '  "target_titles": 12-25 SHORT lowercase title STEMS matched as substrings '
        "against posting titles, so favor high-recall 1-3 word stems like "
        "\"product manager\",\"apm\",\"forward deployed\",\"deployment strategist\","
        "\"solutions engineer\",\"implementation\",\"product operations\",\"ai strategist\". "
        "Do NOT use long fully-qualified titles (\"senior customer solutions engineer\") and do "
        "NOT add redundant variants of a stem already present (skip \"junior product manager\" "
        "if \"product manager\" is there). Match the seniority the person wants. "
        "Do NOT include pure software-engineering/coding stems (software engineer, backend, "
        "frontend, full-stack, data/ML engineer, devops) unless the person explicitly wants them.\n"
        '  "skills": 20-40 skills/tools taken from the RESUME that would appear in a matching '
        "job description (e.g. \"sql\",\"figma\",\"roadmap\",\"discovery\").\n"
        '  "domains": 10-20 industry/domain keywords they have exposure to or want '
        "(e.g. \"ai\",\"fintech\",\"proptech\",\"b2b\").\n"
        '  "focus_keywords": 8-15 keywords to filter FUNDING news to the company types they want.\n'
        '  "years": integer approximate years of professional experience.\n\n'
        f"Roles wanted: {profile.get('roles','')}\n"
        f"Company / startup types wanted: {profile.get('startup_types','')}\n"
        f"Company size preference: {profile.get('company_size','any')}\n"
        f"Stated years of experience (may be blank): {profile.get('years_experience','')}\n\n"
        "Return ONLY the JSON object."
    )

    content = []
    rb = resume_block(profile.get("resume_file", ""))
    if rb:
        content.append(rb)
    content.append({"type": "text", "text": instructions})

    body = {"model": SETUP_MODEL, "max_tokens": 1500,
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    ai = parse_json(text)
    if not ai.get("target_titles"):
        sys.exit(f"[error] Claude did not return usable titles. Raw:\n{text[:500]}")

    size = (profile.get("company_size") or "any").lower()
    compiled = {
        "target_titles": [t.lower() for t in ai.get("target_titles", [])],
        "skills": [s.lower() for s in ai.get("skills", [])],
        "domains": [d.lower() for d in ai.get("domains", [])],
        "focus_keywords": [k.lower() for k in ai.get("focus_keywords", [])],
        "years": int(profile.get("years_experience") or ai.get("years") or 3),
        "location_keywords": [l.lower() for l in profile.get("locations", [])],
        "remote_ok": bool(profile.get("remote_ok", False)),
        "prefer_larger": size == "large",
        "max_age_days": int(profile.get("max_age_days", 21)),
        # Time in product-titled roles, kept separate from total professional
        # experience: "3+ years of product management experience" is compared
        # against this, "3+ years of experience" against `years`.
        "years_pm": float(profile.get("years_pm")
                          or profile.get("years_experience") or 3),
        # Hard ceiling on the experience bar a posting may ask for. Absent =
        # keep the code default.
        "max_years": (int(profile["max_years"])
                      if profile.get("max_years") is not None else None),
    }
    if compiled["max_years"] is None:
        del compiled["max_years"]
    return compiled


# Placeholder values shipped in profile.example.json — a profile still holding
# these hasn't been filled in by the forker yet.
PLACEHOLDERS = {
    "resume_file": "docs/your_resume.pdf",
}


def check_filled(profile, path):
    """Exit if the profile still holds unedited example placeholders."""
    unfilled = [k for k, v in PLACEHOLDERS.items() if str(profile.get(k, "")).strip() == v]
    for k in ("roles", "startup_types"):
        if str(profile.get(k, "")).strip().lower().startswith("one sentence"):
            unfilled.append(k)
    if unfilled:
        sys.exit(
            f"[error] {path} still has example placeholders: {', '.join(sorted(set(unfilled)))}.\n"
            "        This profile is set per-forker, not shipped as a default — edit "
            f"{path} with your own resume path, roles, and startup_types, then re-run."
        )


def main():
    load_dotenv()
    path = sys.argv[1] if len(sys.argv) > 1 else "profile.json"
    try:
        with open(path) as f:
            profile = json.load(f)
    except FileNotFoundError:
        sys.exit(
            f"[error] {path} not found. This profile is set per-forker, not shipped as a default.\n"
            f"        cp profile.example.json {path}   # then edit it and re-run."
        )
    except Exception as e:
        sys.exit(f"[error] could not read {path}: {e}")
    profile.pop("_help", None)
    check_filled(profile, path)

    compiled = compile_profile(profile)
    with open(OUT_FILE, "w") as f:
        json.dump(compiled, f, indent=2)

    print(f"[ok] wrote {OUT_FILE}")
    print(f"     titles={len(compiled['target_titles'])}  skills={len(compiled['skills'])}"
          f"  domains={len(compiled['domains'])}  focus={len(compiled['focus_keywords'])}")
    print(f"     locations={compiled['location_keywords']}  remote_ok={compiled['remote_ok']}"
          f"  prefer_larger={compiled['prefer_larger']}  max_age_days={compiled['max_age_days']}"
          f"  years_pm={compiled['years_pm']}"
          + (f"  max_years={compiled['max_years']}" if "max_years" in compiled else ""))
    # NOT "commit it": profile.compiled.json is gitignored on purpose, because
    # this repo is public and the file is derived from your resume. Scheduled
    # runs read it from a secret instead.
    print("     Both digests load this automatically on your machine.")
    print("     Next:  python tests/check_profile.py"
          "        # confirm the compiled filters still catch real postings")
    print("            gh secret set PROFILE_COMPILED_JSON < profile.compiled.json"
          "   # for Actions")
    print("     Do NOT commit it — it is gitignored, and derived from your resume.")


if __name__ == "__main__":
    main()
