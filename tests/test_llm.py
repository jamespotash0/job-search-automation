#!/usr/bin/env python3
"""The provider shim. No network: this pins SELECTION and REQUEST SHAPE, which
is where a two-provider seam actually goes wrong."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("JOB_IGNORE_PROFILE", "1")
sys.path.insert(0, os.path.dirname(HERE))

import llm                            # noqa: E402
from harness import case, check, equal, main   # noqa: E402


def _with_env(**env):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER"):
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items() if v})


@case
def test_provider_selection():
    _with_env()
    equal(llm.provider(), None, "no keys")
    _with_env(ANTHROPIC_API_KEY="x")
    equal(llm.provider(), "anthropic")
    _with_env(OPENAI_API_KEY="y")
    equal(llm.provider(), "openai")
    _with_env(ANTHROPIC_API_KEY="x", OPENAI_API_KEY="y")
    equal(llm.provider(), "anthropic", "Anthropic wins a tie")
    _with_env(ANTHROPIC_API_KEY="x", OPENAI_API_KEY="y", LLM_PROVIDER="openai")
    equal(llm.provider(), "openai", "explicit choice beats the tie-break")
    _with_env()


@case
def test_an_explicit_provider_without_its_key_is_not_configured():
    """Returning "openai" here would send requests with no Authorization header
    and fail at the HTTP layer, where the cause is much harder to see."""
    _with_env(LLM_PROVIDER="openai")
    equal(llm.provider(), None)
    _with_env(LLM_PROVIDER="openai", ANTHROPIC_API_KEY="x")
    equal(llm.provider(), None, "the OpenAI key is what OpenAI needs")
    _with_env()


@case
def test_request_shape_per_provider():
    """Both providers get a web-search tool, but their request bodies differ in
    every field that matters: url, auth header, prompt key, token key."""
    seen = {}

    def fake_post(url, body, headers, timeout):
        seen.update(url=url, body=body, headers=headers)
        return {"content": [{"type": "text", "text": "hi"}], "output_text": "hi"}

    real, llm._post = llm._post, fake_post
    try:
        _with_env(ANTHROPIC_API_KEY="k")
        equal(llm.research("q", max_tokens=123, max_uses=7), "hi")
        equal(seen["url"], llm.ANTHROPIC_URL)
        equal(seen["headers"]["x-api-key"], "k")
        equal(seen["body"]["max_tokens"], 123)
        equal(seen["body"]["tools"][0]["name"], "web_search")
        equal(seen["body"]["tools"][0]["max_uses"], 7)
        check("anthropic-version" in seen["headers"], "missing version header")

        _with_env(OPENAI_API_KEY="k2")
        equal(llm.research("q", max_tokens=123), "hi")
        equal(seen["url"], llm.OPENAI_URL)
        equal(seen["headers"]["Authorization"], "Bearer k2")
        equal(seen["body"]["input"], "q")
        equal(seen["body"]["max_output_tokens"], 123)
        equal(seen["body"]["tools"][0]["type"], "web_search")
    finally:
        llm._post = real
        _with_env()


@case
def test_web_search_can_be_turned_off():
    """The eval's judge must NOT search: it grades using only the site text it
    was given, and a searching judge would just re-derive the answer."""
    seen = {}
    real, llm._post = llm._post, lambda u, b, h, t: (seen.update(body=b) or
                                                     {"output_text": ""})
    try:
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            _with_env(**{key: "k"})
            llm.research("q", web_search=False)
            check("tools" not in seen["body"], f"{key}: search tool sent anyway")
    finally:
        llm._post = real
        _with_env()


@case
def test_openai_falls_back_to_walking_the_output_list():
    """output_text is a convenience field; a response with tool calls in it may
    only carry the answer inside the message items."""
    payload = {"output": [{"type": "web_search_call"},
                          {"type": "message",
                           "content": [{"type": "output_text", "text": "answer"}]}]}
    real, llm._post = llm._post, lambda *a, **k: payload
    try:
        _with_env(OPENAI_API_KEY="k")
        equal(llm.research("q"), "answer")
    finally:
        llm._post = real
        _with_env()


@case
def test_unconfigured_returns_empty_rather_than_raising():
    _with_env()
    equal(llm.research("q"), "")
    check("no LLM provider" in llm.describe(), llm.describe())


if __name__ == "__main__":
    main("llm")
