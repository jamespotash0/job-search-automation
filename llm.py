#!/usr/bin/env python3
"""
llm.py — one small seam so a fork can run on Anthropic OR OpenAI.

Three places in this repo ask a model to research something on the live web:
enrich.py (what does this company do, where is it), discover.py (who is hiring),
and the enrichment eval's judge. All three need SERVER-SIDE web search, which
both providers offer and shape differently — that difference is the only reason
this file exists.

    provider()            -> "anthropic" | "openai" | None
    research(prompt, ...) -> the model's final text, whichever provider is set

Selection: LLM_PROVIDER if set, else whichever API key is present. With both
keys set, Anthropic wins unless LLM_PROVIDER says otherwise.

Not covered here: setup_profile.py, which sends your resume PDF. Anthropic reads
PDFs natively; making that portable is a separate job, and it is documented as
Anthropic-only rather than silently half-working.
"""
import json
import os
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/responses"

# Defaults chosen so a fork works without picking a model. Override per provider.
ANTHROPIC_MODEL = os.environ.get("AI_MODEL", "claude-haiku-4-5-20251001")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1")


def provider():
    """Which provider this checkout is configured for, or None."""
    want = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if want in ("anthropic", "openai"):
        # Trust the explicit choice, but only if its key is actually present.
        key = "ANTHROPIC_API_KEY" if want == "anthropic" else "OPENAI_API_KEY"
        return want if os.environ.get(key) else None
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _anthropic(prompt, max_tokens, web_search, max_uses, timeout, model):
    body = {"model": model or ANTHROPIC_MODEL, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if web_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search",
                          "max_uses": max_uses}]
    data = _post(ANTHROPIC_URL, body,
                 {"content-type": "application/json",
                  "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                  "anthropic-version": "2023-06-01"}, timeout)
    # Search results and tool calls arrive as their own blocks; the answer is the
    # text ones concatenated.
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")


def _openai(prompt, max_tokens, web_search, max_uses, timeout, model):
    body = {"model": model or OPENAI_MODEL, "input": prompt,
            "max_output_tokens": max_tokens}
    if web_search:
        # OpenAI's hosted search has no per-call use cap, so max_uses is
        # deliberately ignored rather than faked.
        body["tools"] = [{"type": "web_search"}]
    data = _post(OPENAI_URL, body,
                 {"Content-Type": "application/json",
                  "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, timeout)
    if data.get("output_text"):
        return data["output_text"]
    out = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    out.append(c.get("text", ""))
    return "".join(out)


def research(prompt, max_tokens=800, web_search=True, max_uses=3, timeout=90,
             model=None):
    """Ask the configured provider, return its final text ("" if unconfigured).

    Raises whatever the HTTP call raises — callers here already treat a failed
    lookup as "unknown" rather than fatal, and swallowing it inside this shim
    would hide which provider is broken.
    """
    p = provider()
    if p == "anthropic":
        return _anthropic(prompt, max_tokens, web_search, max_uses, timeout, model)
    if p == "openai":
        return _openai(prompt, max_tokens, web_search, max_uses, timeout, model)
    return ""


def describe():
    """One line for doctor.py and startup logs."""
    p = provider()
    if not p:
        return "no LLM provider configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY)"
    return f"{p} ({ANTHROPIC_MODEL if p == 'anthropic' else OPENAI_MODEL})"
