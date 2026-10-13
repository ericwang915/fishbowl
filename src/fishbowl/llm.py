"""The structured decision model: bring your own key, or run fully local with Ollama.

Every call returns a JSON object that matches a schema. Providers:

  anthropic  ANTHROPIC_API_KEY             (forced tool call)
  openai     OPENAI_API_KEY [+ base URL]   (json_schema; works with OpenRouter, Groq,
                                            DeepSeek, vLLM, LM Studio, … any OpenAI-compatible API)
  ollama     no key, local                 (native `format: <schema>`)

Choose with FISHBOWL_LLM / FISHBOWL_MODEL / FISHBOWL_LLM_BASE_URL. Without
FISHBOWL_LLM we pick anthropic, then openai, then ollama based on which keys exist.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .config import get_config

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-4.1-mini",
    "ollama": "qwen3:8b",
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMSettings:
    provider: str
    model: str
    base_url: str
    api_key: str | None
    price_in: float | None  # USD per 1M input tokens
    price_out: float | None  # USD per 1M output tokens

    @classmethod
    def from_env(cls) -> LLMSettings:
        provider = os.environ.get("FISHBOWL_LLM", "").lower()
        if not provider:
            if os.environ.get("ANTHROPIC_API_KEY"):
                provider = "anthropic"
            elif os.environ.get("OPENAI_API_KEY"):
                provider = "openai"
            else:
                provider = "ollama"
        if provider not in DEFAULT_MODELS:
            raise LLMError(f"unknown FISHBOWL_LLM={provider!r} (use anthropic, openai or ollama)")
        base = (
            os.environ.get("FISHBOWL_LLM_BASE_URL")
            or {
                "anthropic": "https://api.anthropic.com",
                "openai": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "ollama": os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
            }[provider]
        )
        if provider == "ollama" and "://" not in base:
            base = "http://" + base
        key = {
            "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
            "openai": os.environ.get("OPENAI_API_KEY"),
            "ollama": None,
        }[provider]
        p_in, p_out = os.environ.get("FISHBOWL_PRICE_IN"), os.environ.get("FISHBOWL_PRICE_OUT")
        return cls(
            provider=provider,
            model=os.environ.get("FISHBOWL_MODEL") or DEFAULT_MODELS[provider],
            base_url=base.rstrip("/"),
            api_key=key,
            price_in=float(p_in) if p_in else (0.0 if provider == "ollama" else None),
            price_out=float(p_out) if p_out else (0.0 if provider == "ollama" else None),
        )


# A test hook: when set, replaces real network calls. fn(purpose, system, user, schema) -> dict
Override = Callable[[str, str, str, dict], Awaitable[dict]]
_override: Override | None = None


def set_override(fn: Override | None) -> None:
    global _override
    _override = fn


async def _anthropic(s: LLMSettings, system: str, user: str, schema: dict, max_tokens: int):
    if not s.api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    body = {
        "model": s.model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [{"name": "answer", "description": "Return the decision.", "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": "answer"},
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{s.base_url}/v1/messages", json=body, headers={"x-api-key": s.api_key, "anthropic-version": "2023-06-01"}
        )
    if r.status_code >= 400:
        raise LLMError(f"anthropic {r.status_code}: {r.text[:300]}")
    d = r.json()
    block = next((b for b in d.get("content", []) if b.get("type") == "tool_use"), None)
    if not block:
        raise LLMError("anthropic returned no tool call")
    u = d.get("usage", {})
    return block["input"], u.get("input_tokens", 0), u.get("output_tokens", 0)


async def _openai(s: LLMSettings, system: str, user: str, schema: dict, max_tokens: int):
    headers = {"Authorization": f"Bearer {s.api_key}"} if s.api_key else {}
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    body = {
        "model": s.model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{s.base_url}/chat/completions", json=body, headers=headers)
        if r.status_code == 400 and "response_format" in r.text:
            # Older compatible servers: fall back to JSON mode with the schema in the prompt.
            body["response_format"] = {"type": "json_object"}
            msgs[0]["content"] = system + "\nReply with JSON matching this schema:\n" + json.dumps(schema)
            r = await c.post(f"{s.base_url}/chat/completions", json=body, headers=headers)
    if r.status_code >= 400:
        raise LLMError(f"openai {r.status_code}: {r.text[:300]}")
    d = r.json()
    u = d.get("usage", {})
    return _parse_json(d["choices"][0]["message"]["content"]), u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


async def _ollama(s: LLMSettings, system: str, user: str, schema: dict, max_tokens: int):
    body = {
        "model": s.model,
        "stream": False,
        "format": schema,
        "think": False,
        "options": {"temperature": 0, "num_predict": max_tokens},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{s.base_url}/api/chat", json=body)
    except httpx.ConnectError as e:
        raise LLMError(f"cannot reach Ollama at {s.base_url} — is `ollama serve` running?") from e
    if r.status_code >= 400:
        raise LLMError(f"ollama {r.status_code}: {r.text[:300]}")
    d = r.json()
    return _parse_json(d["message"]["content"]), d.get("prompt_eval_count", 0), d.get("eval_count", 0)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise LLMError(f"model did not return JSON: {text[:200]}")
    return json.loads(text[start : end + 1])


_PROVIDERS = {"anthropic": _anthropic, "openai": _openai, "ollama": _ollama}


async def decide(purpose: str, system: str, user: str, schema: dict, max_tokens: int = 400) -> dict:
    """One structured call. `purpose` tags the call in stats (decide, check, guard, judge…)."""
    t0 = time.time()
    if _override is not None:
        out = await _override(purpose, system, user, schema)
        _record(purpose, "override", "test", 0, 0, time.time() - t0, None)
        return out
    s = LLMSettings.from_env()
    try:
        out, tin, tout = await _PROVIDERS[s.provider](s, system, user, schema, max_tokens)
    except LLMError:
        _record(purpose, s.provider, s.model, 0, 0, time.time() - t0, s, error=True)
        raise
    _record(purpose, s.provider, s.model, tin, tout, time.time() - t0, s)
    return out


def _stats_path():
    cfg = get_config()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg.state_dir / "llm_calls.jsonl"


def _record(purpose, provider, model, tin, tout, secs, s: LLMSettings | None, error: bool = False) -> None:
    cost = None
    if s and s.price_in is not None and s.price_out is not None:
        cost = (tin * s.price_in + tout * s.price_out) / 1e6
    row = {
        "ts": time.time(),
        "purpose": purpose,
        "provider": provider,
        "model": model,
        "in": tin,
        "out": tout,
        "secs": round(secs, 3),
        "cost": cost,
        "error": error,
    }
    with _stats_path().open("a") as f:
        f.write(json.dumps(row) + "\n")


def stats(since_hours: float | None = None) -> dict:
    p = _stats_path()
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
    if since_hours:
        cutoff = time.time() - since_hours * 3600
        rows = [r for r in rows if r["ts"] >= cutoff]
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = by.setdefault(r["purpose"], {"calls": 0, "errors": 0, "in": 0, "out": 0, "secs": 0.0, "cost": 0.0})
        b["calls"] += 1
        b["errors"] += int(r.get("error", False))
        b["in"] += r["in"]
        b["out"] += r["out"]
        b["secs"] += r["secs"]
        b["cost"] += r["cost"] or 0.0
    total_cost = sum(b["cost"] for b in by.values())
    priced = all(r.get("cost") is not None for r in rows if not r.get("error"))
    try:
        cur = LLMSettings.from_env()
        current = {"provider": cur.provider, "model": cur.model}
    except LLMError as e:
        current = {"error": str(e)}
    return {
        "current": current,
        "calls": len(rows),
        "input_tokens": sum(r["in"] for r in rows),
        "output_tokens": sum(r["out"] for r in rows),
        "avg_latency_s": round(sum(r["secs"] for r in rows) / len(rows), 2) if rows else 0,
        "cost_usd": round(total_cost, 4) if priced else None,
        "cost_note": None
        if priced
        else "set FISHBOWL_PRICE_IN / FISHBOWL_PRICE_OUT (USD per 1M tokens) to track spend",
        "by_purpose": {k: {**v, "secs": round(v["secs"], 2), "cost": round(v["cost"], 4)} for k, v in by.items()},
    }
