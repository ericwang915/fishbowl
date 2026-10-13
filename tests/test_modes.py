"""Fast mode is ON with Jev and OFF without it; fishbowl keeps working either way."""

import pytest

from fishbowl import chat, guard, llm, server


@pytest.fixture
def no_models(monkeypatch):
    for k in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "TYPESAFE_API_KEY",
        "FISHBOWL_LLM",
        "FISHBOWL_FAST",
        "FISHBOWL_SLOW",
    ):
        monkeypatch.delenv(k, raising=False)


async def test_browser_run_explains_when_fast_mode_is_off(no_models):
    r = await server.browser_run(plan=[{"goal": "open the first result"}])
    assert r["status"] == "fast_mode_off"
    assert "TYPESAFE_API_KEY" in r["reason"] and "browser_click" in r["instead"]


async def test_goal_without_planner(no_models, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    r = await server.browser_run(goal="buy a hub")
    assert r["status"] == "no_planner" and "ANTHROPIC_API_KEY" in r["reason"]


async def test_guard_uses_keyword_rules_when_off(no_models):
    v = await guard.assess("click", "[9] button Place order")
    assert v.needs_approval and v.source == "heuristic" and "fast mode is off" in v.reason
    v = await guard.assess("click", "[3] button Search")
    assert not v.needs_approval


async def test_judge_falls_back_to_the_planner(no_models, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    seen = {}

    async def slow_only(purpose, state, questions):
        seen["called"] = True
        return {"reply_type": "answer", "detected_language": "en", "language_match": "yes", "actionable": "yes",
                "cites_store_data": "no", "score": "4"}  # fmt: skip

    async def fake_from_env_choose(purpose, state, questions, role="fast"):
        if role == "fast":
            raise llm.NotConfigured("off")
        return {k: {"choice": v, "confidence": 0.9} for k, v in (await slow_only(purpose, state, questions)).items()}

    monkeypatch.setattr(chat.llm, "choose", fake_from_env_choose)
    r = await chat.judge_reply("Where is my order?", "It ships Friday.", label="answered")
    assert seen["called"] and r["score"] == 4


async def test_judge_without_any_model(no_models):
    r = await chat.judge_reply("q", "some reply", label="answered")
    assert r["error"] == "no_model"


def test_instructions_follow_the_mode(no_models, monkeypatch):
    assert "browser_run is unavailable" in server.instructions()
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    assert "FAST MODE IS ON" in server.instructions()


def test_stats_report_off(no_models):
    cur = llm.stats()["current"]
    assert "off" in cur["fast"] and "off" in cur["slow"]
