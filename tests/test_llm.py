"""Provider adapters (Anthropic / OpenAI-compatible / Ollama) against mocked HTTP."""

import json

import httpx
import pytest

from fishbowl import llm

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@pytest.fixture
def mock_http(monkeypatch):
    calls = []
    handlers = {}

    def transport(request: httpx.Request):
        calls.append(request)
        return handlers["fn"](request)

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(transport)
        return real(*a, **kw)

    monkeypatch.setattr(llm.httpx, "AsyncClient", factory)

    def set_handler(fn):
        handlers["fn"] = fn
        return calls

    return set_handler


def test_provider_autodetect(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FISHBOWL_LLM"):
        monkeypatch.delenv(k, raising=False)
    assert llm.LLMSettings.from_env().provider == "ollama"
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert llm.LLMSettings.from_env().provider == "openai"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert llm.LLMSettings.from_env().provider == "anthropic"
    monkeypatch.setenv("FISHBOWL_LLM", "ollama")
    s = llm.LLMSettings.from_env()
    assert s.provider == "ollama" and s.price_in == 0.0
    monkeypatch.setenv("FISHBOWL_LLM", "bogus")
    with pytest.raises(llm.LLMError):
        llm.LLMSettings.from_env()


async def test_anthropic(monkeypatch, mock_http):
    monkeypatch.setenv("FISHBOWL_LLM", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("FISHBOWL_PRICE_IN", "1")
    monkeypatch.setenv("FISHBOWL_PRICE_OUT", "5")
    calls = mock_http(
        lambda r: httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": "answer", "input": {"ok": True}}],
                "usage": {"input_tokens": 1000, "output_tokens": 200},
            },
        )
    )
    out = await llm.decide("decide", "sys", "user", SCHEMA)
    assert out == {"ok": True}
    body = json.loads(calls[0].content)
    assert calls[0].url.path == "/v1/messages" and calls[0].headers["x-api-key"] == "k"
    assert body["tool_choice"] == {"type": "tool", "name": "answer"} and body["model"] == "claude-haiku-5-5"
    st = llm.stats()
    assert st["calls"] == 1 and st["cost_usd"] == pytest.approx(0.002)


async def test_openai_compatible_with_fallback(monkeypatch, mock_http):
    monkeypatch.setenv("FISHBOWL_LLM", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("FISHBOWL_LLM_BASE_URL", "https://openrouter.example/api/v1")
    monkeypatch.setenv("FISHBOWL_MODEL", "some/model")

    def handler(r):
        body = json.loads(r.content)
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="response_format json_schema not supported")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '```json\n{"ok": false}\n```'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )

    calls = mock_http(handler)
    out = await llm.decide("check", "sys", "user", SCHEMA)
    assert out == {"ok": False}
    assert len(calls) == 2 and str(calls[0].url) == "https://openrouter.example/api/v1/chat/completions"
    assert llm.stats()["cost_usd"] is None  # no prices configured


async def test_ollama(monkeypatch, mock_http):
    monkeypatch.setenv("FISHBOWL_LLM", "ollama")
    monkeypatch.setenv("FISHBOWL_MODEL", "llama3.2")
    calls = mock_http(
        lambda r: httpx.Response(
            200, json={"message": {"content": '{"ok": true}'}, "prompt_eval_count": 50, "eval_count": 7}
        )
    )
    out = await llm.decide("judge", "sys", "user", SCHEMA)
    assert out == {"ok": True}
    body = json.loads(calls[0].content)
    assert calls[0].url.path == "/api/chat" and body["format"] == SCHEMA and body["stream"] is False
    st = llm.stats()
    assert st["cost_usd"] == 0 and st["by_purpose"]["judge"]["in"] == 50


async def test_ollama_down_gives_clear_error(monkeypatch):
    monkeypatch.setenv("FISHBOWL_LLM", "ollama")
    monkeypatch.setenv("FISHBOWL_LLM_BASE_URL", "http://127.0.0.1:9")
    with pytest.raises(llm.LLMError, match="ollama serve"):
        await llm.decide("decide", "s", "u", SCHEMA)
    assert llm.stats()["by_purpose"]["decide"]["errors"] == 1


async def test_missing_key(monkeypatch):
    monkeypatch.setenv("FISHBOWL_LLM", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        await llm.decide("decide", "s", "u", SCHEMA)


# ------------------------------------------------------------ roles and multiple choice

Q = {
    "operation": {"criteria": {"CLICK": "click", "DONE": "done"}},
    "click_target": {"criteria": {"3": "[3] button Search", "7": "[7] link Next"}},
}


def test_roles(monkeypatch):
    for k in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "TYPESAFE_API_KEY",
        "FISHBOWL_LLM",
        "FISHBOWL_FAST",
        "FISHBOWL_SLOW",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    fast, slow = llm.LLMSettings.from_env("fast"), llm.LLMSettings.from_env("slow")
    assert (fast.provider, fast.model) == ("jev", "jev-latest")
    assert (slow.provider, slow.model) == ("anthropic", "claude-sonnet-5-5")
    monkeypatch.setenv("FISHBOWL_SLOW", "jev")
    with pytest.raises(llm.LLMError, match="fast executor"):
        llm.LLMSettings.from_env("slow")
    monkeypatch.setenv("FISHBOWL_SLOW", "ollama")
    monkeypatch.setenv("FISHBOWL_SLOW_MODEL", "qwen3:32b")
    assert llm.LLMSettings.from_env("slow").model == "qwen3:32b"


async def test_jev_choose(monkeypatch, mock_http):
    monkeypatch.setenv("FISHBOWL_FAST", "jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    calls = mock_http(
        lambda r: httpx.Response(200, json={
            "answers": {
                "operation": {"choice": "CLICK", "confidence": 0.93, "probabilities": {"CLICK": 0.93, "DONE": 0.07}},
                "click_target": {"choice": "3", "confidence": 0.88, "probabilities": {"3": 0.88, "7": 0.12}},
            },
            "model": "jev-latest", "usage": {"input_tokens": 420, "output_tokens": 6},
        })
    )  # fmt: skip
    a = await llm.choose("decide", {"step": "search"}, Q)
    assert a["operation"]["choice"] == "CLICK" and a["click_target"] == {
        "choice": "3", "confidence": 0.88, "probabilities": {"3": 0.88, "7": 0.12}
    }  # fmt: skip
    body = json.loads(calls[0].content)
    assert str(calls[0].url) == "https://api.typesafe.ai/v1/systemone"
    assert calls[0].headers["authorization"] == "Bearer t"
    assert body["questions"]["operation"]["type"] == "choice" and body["state"] == {"step": "search"}
    assert llm.stats()["by_purpose"]["decide"]["in"] == 420


async def test_jev_retries_on_overload(monkeypatch, mock_http):
    monkeypatch.setenv("FISHBOWL_FAST", "jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    replies = iter(
        [
            httpx.Response(529),
            httpx.Response(
                200,
                json={
                    "answers": {
                        "operation": {"choice": "DONE", "confidence": 1},
                        "click_target": {"choice": "7", "confidence": 1},
                    }
                },
            ),
        ]
    )
    calls = mock_http(lambda r: next(replies))  # fmt: skip
    assert (await llm.choose("decide", {}, Q))["operation"]["choice"] == "DONE"
    assert len(calls) == 2


async def test_choice_outside_options_is_rejected(monkeypatch, mock_http):
    monkeypatch.setenv("FISHBOWL_FAST", "jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    mock_http(lambda r: httpx.Response(200, json={"answers": {
        "operation": {"choice": "CLICK", "confidence": 0.9}, "click_target": {"choice": "99", "confidence": 0.9}}}))  # fmt: skip
    with pytest.raises(llm.LLMError, match="not one of the offered options"):
        await llm.choose("decide", {}, Q)


async def test_emulated_choose_uses_enum_schema(monkeypatch, mock_http):
    monkeypatch.setenv("FISHBOWL_FAST", "ollama")
    seen = {}

    def handler(r):
        body = json.loads(r.content)
        seen["format"] = body["format"]
        out = {"operation": "CLICK", "operation__confidence": 0.8, "click_target": "7", "click_target__confidence": 0.6}
        return httpx.Response(200, json={"message": {"content": json.dumps(out)}})

    mock_http(handler)
    a = await llm.choose("decide", {"step": "next page"}, Q)
    assert a["click_target"] == {"choice": "7", "confidence": 0.6}
    assert seen["format"]["properties"]["click_target"]["enum"] == ["3", "7"]
    assert set(seen["format"]["required"]) == {
        "operation",
        "operation__confidence",
        "click_target",
        "click_target__confidence",
    }


async def test_decide_refuses_jev(monkeypatch):
    monkeypatch.setenv("FISHBOWL_FAST", "jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    with pytest.raises(llm.LLMError, match="multiple-choice"):
        await llm.decide("x", "s", "u", SCHEMA)
