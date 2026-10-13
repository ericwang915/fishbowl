"""browser_run end to end against local pages: 9 task types x 3 cases, plus handback paths."""

import os

import pytest

from fishbowl import control, llm
from fishbowl.runner import Runner

from .fake_model import use_fake

LIVE = os.environ.get("FISHBOWL_LIVE") == "1"  # run the suite against your real fast model


def T(goal, text=None):
    return {"goal": goal, "text": text} if text is not None else {"goal": goal}


CASES = [
    # 1 search
    ("search", "search.html", [T("search for it", "usb c hub")], "Results for usb c hub"),
    (
        "search",
        "search.html",
        [T("search for it", "usb c hub"), T("click 'usb-c hub pro' expect 'Item page'")],
        "Item page: USB-C Hub Pro",
    ),
    ("search", "search.html", [T("search for it", "hdmi cable")], "Results for hdmi cable"),
    # 2 form filling
    (
        "form",
        "form.html",
        [T("type into 'full name'", "Ada Lovelace"), T("click 'save draft' expect 'Saved draft'")],
        "Saved draft for Ada Lovelace",
    ),
    (
        "form",
        "form.html",
        [
            T("type into 'full name'", "Ada"),
            T("type into 'email'", "ada@example.com"),
            T("click 'save draft' expect 'Saved'"),
        ],
        "Ada / ada@example.com",
    ),
    ("form", "form.html", [T("type into 'email'", "x@y.io"), T("click 'save draft' expect 'Saved'")], "/ x@y.io"),
    # 3 dropdowns
    ("select", "select.html", [T("select 'Bahasa Indonesia' as language")], "Language set to Bahasa Indonesia"),
    ("select", "select.html", [T("select '中文' as language")], "Language set to 中文"),
    ("select", "select.html", [T("select 'Bahasa Indonesia'"), T("select 'English'")], "Language set to English"),
    # 4 checkbox / radio
    ("checkbox", "checkbox.html", [T("tick 'newsletter' expect 'enabled'")], "Newsletter enabled"),
    ("checkbox", "checkbox.html", [T("choose 'large' expect 'large'")], "Size large chosen"),
    (
        "checkbox",
        "checkbox.html",
        [T("tick 'newsletter' expect 'enabled'"), T("choose 'large' expect 'large'")],
        "Size large chosen",
    ),
    # 5 overlays blocking the page
    ("overlay", "overlay.html", [T("click 'read article' expect 'Article opened'")], "Article opened"),
    ("overlay", "overlay.html", [T("open the 'read article' button expect 'opened'")], "Article opened"),
    ("overlay", "overlay.html", [T("click 'read article' expect 'Article'")], "Article opened"),
    # 6 scrolling / lazy loading
    ("scroll", "scroll.html", [T("click 'contact us' expect 'Footer'")], "Footer link clicked"),
    ("scroll", "lazy.html", [T("click 'load more results' expect 'All results loaded'")], "All results loaded"),
    (
        "scroll",
        "lazy.html",
        [T("wait for 'load more results'"), T("click 'load more' expect 'loaded'")],
        "All results loaded",
    ),
    # 7 in-page tabs
    ("tabs", "tabs.html", [T("click 'shipping' expect 'Ships in 2 days'")], "Ships in 2 days"),
    (
        "tabs",
        "tabs.html",
        [T("click 'shipping' expect 'Jakarta'"), T("click 'description' expect 'Great hub'")],
        "Great hub",
    ),
    ("tabs", "tabs.html", [T("click 'shipping' expect 'Jakarta'")], "Jakarta"),
    # 8 slow pages
    ("delayed", "delayed.html", [T("wait for 'generate report'")], "Ready"),
    (
        "delayed",
        "delayed.html",
        [T("wait for 'generate report'"), T("click 'generate report' expect 'generated'")],
        "Report generated",
    ),
    ("delayed", "delayed.html", [T("click 'generate report' expect 'generated'")], "Report generated"),
    # 9 sites opening their own tabs
    (
        "popup",
        "popup.html",
        [T("click 'partner site'"), T("click 'continue here' expect 'Stayed'")],
        "Stayed on home tab",
    ),
    (
        "popup",
        "popup.html",
        [T("click 'show promo' expect 'Promo shown'"), T("click 'continue here' expect 'Stayed'")],
        "Stayed on home tab",
    ),
    ("popup", "popup.html", [T("click 'show promo' expect 'Promo'")], "Promo shown"),
]


@pytest.mark.parametrize("kind,page,plan,expect", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
async def test_task_suite(browser, page_server, kind, page, plan, expect):
    if not LIVE:
        use_fake(llm)
    await browser.navigate(f"{page_server}/{page}")
    home = browser.working_tab
    res = await Runner(browser).run(plan, timeout_s=60)
    assert res.status == "done", (res.reason, res.log)
    assert browser.working_tab == home, "working tab must not follow site-opened tabs"
    assert expect in (await browser.text())["text"]


async def test_three_no_change_actions_hand_back(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/nochange.html")
    res = await Runner(browser).run([T("click 'does nothing' expect 'never'")], timeout_s=60)
    assert res.status == "handback"
    assert "did not change" in res.reason


async def test_login_wall_hands_back(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/login.html")
    res = await Runner(browser).run([T("click 'orders'")])
    assert res.status == "handback" and "login" in res.reason


async def test_guard_requires_approval(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/checkout.html")
    res = await Runner(browser).run([T("click 'place order' expect 'placed'")])
    assert res.status == "needs_approval"
    assert "Order placed" not in (await browser.text())["text"]


async def test_guard_approved_by_human(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/checkout.html")
    asked = []

    async def approver(msg):
        asked.append(msg)
        return True

    res = await Runner(browser, approver=approver).run([T("click 'place order' expect 'placed'")])
    assert res.status == "done" and asked
    assert "Order placed" in (await browser.text())["text"]


async def test_secret_in_plan_is_refused(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/form.html")
    res = await Runner(browser).run([T("type into 'full name'", "4111 1111 1111 1111")])
    assert res.status == "handback" and "secret" in res.reason


async def test_pause_stops_run(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/form.html")
    control.write_state(paused=True)
    res = await Runner(browser).run([T("type into 'full name'", "x")])
    assert res.status == "paused"
    control.write_state(paused=False)


async def test_low_confidence_hands_back(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/form.html")
    res = await Runner(browser).run([T("low confidence step")])
    assert res.status == "handback" and "confident" in res.reason


async def test_model_handback(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/form.html")
    res = await Runner(browser).run([T("give up please")])
    assert res.status == "handback"


async def test_timeout(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/delayed.html")
    res = await Runner(browser).run([T("wait for 'never appears'")], timeout_s=0.5)
    assert res.status in ("timeout", "handback")


async def test_typed_text_only_comes_from_plan(browser, page_server):
    async def sneaky(purpose, state, questions):
        # The model tries to type although the step has no prepared text: TYPE_TEXT is not even offered.
        assert "TYPE_TEXT" not in questions["operation"]["criteria"]
        return {
            "operation": "TYPE_TEXT",
            **{k: next(iter(v["criteria"])) for k, v in questions.items() if k != "operation"},
        }

    llm.set_choice_override(sneaky)
    await browser.navigate(f"{page_server}/form.html")
    res = await Runner(browser).run([T("fill the name")])
    assert res.status == "handback" and "not one of the offered options" in res.reason


async def test_progress_is_reported(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/form.html")
    seen = []

    async def progress(done, total, msg):
        seen.append((done, total, msg))

    res = await Runner(browser, progress=progress).run(
        [T("type into 'full name'", "Ada"), T("click 'save draft' expect 'Saved'")]
    )
    assert res.status == "done"
    assert [s[:2] for s in seen] == [(0, 2), (1, 2)] and "save draft" in seen[1][2]


# ------------------------------------------------------------ fast + slow thinking


async def test_goal_only_is_planned_by_the_slow_model(browser, page_server):
    from fishbowl.runner import run_task

    use_fake(llm)
    await browser.navigate(f"{page_server}/checkbox.html")
    res = await run_task(Runner(browser), goal="tick 'newsletter' expect 'enabled' then choose 'large' expect 'large'")
    assert res.status == "done" and res.replans == 0
    assert [s["goal"] for s in res.plans[0]][0].startswith("tick 'newsletter'")
    assert llm.stats()["by_purpose"]["plan"]["calls"] == 1


async def test_slow_model_replans_after_handback(browser, page_server):
    from fishbowl.runner import run_task

    use_fake(llm)
    await browser.navigate(f"{page_server}/tabs.html")
    res = await run_task(Runner(browser), goal="replan:click 'shipping' expect 'Jakarta'")
    assert res.status == "done" and res.replans == 1 and len(res.plans) == 2
    assert "Jakarta" in (await browser.text())["text"]


async def test_planner_asks_for_missing_info(browser, page_server):
    from fishbowl.runner import run_task

    use_fake(llm)
    await browser.navigate(f"{page_server}/form.html")
    res = await run_task(Runner(browser), goal="checkout, needs info")
    assert res.status == "handback" and "shipping address" in res.reason
    assert res.observation["title"] == "Signup"


async def test_done_must_be_verified(browser, page_server):
    async def liar(purpose, state, questions):
        if purpose == "decide":
            return {
                "operation": "DONE",
                **{k: next(iter(v["criteria"])) for k, v in questions.items() if k != "operation"},
            }
        return {"satisfied": "no"}

    llm.set_choice_override(liar)
    await browser.navigate(f"{page_server}/form.html")
    res = await Runner(browser).run([T("click 'save draft' expect 'Saved'")])
    assert res.status == "handback" and "verification disagrees" in res.reason


async def test_stale_decision_is_not_executed(browser, page_server, monkeypatch):
    use_fake(llm)
    await browser.navigate(f"{page_server}/checkbox.html")
    real = browser.click
    calls = {"n": 0}

    async def flaky(eid, fresh=False):
        calls["n"] += 1
        if calls["n"] == 1:
            assert fresh  # the runner always asks for a freshness check before input
            return {"ok": False, "reason": "stale", "by": "page changed"}
        return await real(eid, fresh)

    monkeypatch.setattr(browser, "click", flaky)
    res = await Runner(browser).run([T("tick 'newsletter' expect 'enabled'")])
    assert res.status == "done" and calls["n"] == 2


async def test_stale_check_in_same_round_trip(browser, page_server):
    await browser.navigate(f"{page_server}/form.html")
    obs = await browser.observe()
    btn = next(e for e in obs.elements if e.name == "Save draft")
    await browser.eval("document.getElementById('email').value = 'typed by someone else'")
    n = browser.cdp_calls()
    r = await browser.click(btn.id, fresh=True)
    assert r == {"ok": False, "reason": "stale", "by": "page changed"} and browser.cdp_calls() - n == 1
    await browser.observe()
    assert (await browser.click(btn.id, fresh=True))["ok"]


async def test_fresh_detects_changes(browser, page_server):
    await browser.navigate(f"{page_server}/form.html")
    obs = await browser.observe()
    name = next(e for e in obs.elements if e.name == "Full name")
    assert (await browser.fresh(name.id))["fresh"]
    await browser.eval("document.getElementById('name').value = 'changed'")
    assert (await browser.fresh(name.id)) == {"fresh": False, "why": "page changed"}


async def test_one_evaluate_per_observation(browser, page_server):
    await browser.navigate(f"{page_server}/overlay.html")
    before = browser.cdp_calls()
    obs = await browser.observe()
    assert browser.cdp_calls() - before == 1
    assert obs.overlays and "cookies" in obs.visible_text and obs.marker
    assert "Read article" in [e.name for e in obs.elements]


async def test_handback_carries_observation_and_timing(browser, page_server):
    use_fake(llm)
    await browser.navigate(f"{page_server}/nochange.html")
    res = (await Runner(browser).run([T("click 'does nothing' expect 'never'")])).as_dict()
    assert res["status"] == "handback"
    assert res["observation"]["title"] == "Broken" and "Does nothing" in res["observation"]["elements"]
    t = res["timing"]
    assert t["model_calls"] >= 3 and t["cdp_calls"] > 0 and t["decide_s"] >= 0
