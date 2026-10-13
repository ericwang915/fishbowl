"""browser_run end to end against local pages: 9 task types x 3 cases, plus handback paths."""

import os

import pytest

from fishbowl import control, llm
from fishbowl.runner import Runner

from .fake_model import fake_llm

LIVE = os.environ.get("FISHBOWL_LIVE") == "1"  # run the suite against your real FISHBOWL_LLM model


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
    llm.set_override(None if LIVE else fake_llm)
    await browser.navigate(f"{page_server}/{page}")
    home = browser.working_tab
    res = await Runner(browser).run(plan, timeout_s=60)
    assert res.status == "done", (res.reason, res.log)
    assert browser.working_tab == home, "working tab must not follow site-opened tabs"
    assert expect in (await browser.text())["text"]


async def test_three_no_change_actions_hand_back(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/nochange.html")
    res = await Runner(browser).run([T("click 'does nothing' expect 'never'")], timeout_s=60)
    assert res.status == "handback"
    assert "did not change" in res.reason


async def test_login_wall_hands_back(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/login.html")
    res = await Runner(browser).run([T("click 'orders'")])
    assert res.status == "handback" and "login" in res.reason


async def test_guard_requires_approval(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/checkout.html")
    res = await Runner(browser).run([T("click 'place order' expect 'placed'")])
    assert res.status == "needs_approval"
    assert "Order placed" not in (await browser.text())["text"]


async def test_guard_approved_by_human(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/checkout.html")
    asked = []

    async def approver(msg):
        asked.append(msg)
        return True

    res = await Runner(browser, approver=approver).run([T("click 'place order' expect 'placed'")])
    assert res.status == "done" and asked
    assert "Order placed" in (await browser.text())["text"]


async def test_secret_in_plan_is_refused(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/form.html")
    res = await Runner(browser).run([T("type into 'full name'", "4111 1111 1111 1111")])
    assert res.status == "handback" and "secret" in res.reason


async def test_pause_stops_run(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/form.html")
    control.write_state(paused=True)
    res = await Runner(browser).run([T("type into 'full name'", "x")])
    assert res.status == "paused"
    control.write_state(paused=False)


async def test_low_confidence_hands_back(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/form.html")
    res = await Runner(browser).run([T("low confidence step")])
    assert res.status == "handback" and "confident" in res.reason


async def test_model_handback(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/form.html")
    res = await Runner(browser).run([T("give up please")])
    assert res.status == "handback"


async def test_timeout(browser, page_server):
    llm.set_override(fake_llm)
    await browser.navigate(f"{page_server}/delayed.html")
    res = await Runner(browser).run([T("wait for 'never appears'")], timeout_s=0.5)
    assert res.status in ("timeout", "handback")


async def test_typed_text_only_comes_from_plan(browser, page_server):
    async def sneaky(purpose, system, user, schema):
        if purpose == "decide":
            # Model tries to type although the step has no prepared text.
            return {"action": "type", "element": 1, "confidence": 0.9, "reason": "x"}
        return {"done": True, "reason": ""}

    llm.set_override(sneaky)
    await browser.navigate(f"{page_server}/form.html")
    await browser.elements()
    res = await Runner(browser).run([T("fill the name")])
    assert res.status == "handback" and "no `text`" in res.reason
