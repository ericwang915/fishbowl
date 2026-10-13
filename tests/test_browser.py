"""The browser_* base layer over real CDP."""

from fishbowl.browser import element_table


async def test_navigate_and_read(browser, page_server):
    r = await browser.navigate(f"{page_server}/tabs.html")
    assert r["title"] == "Product" and r["stable"]
    t = await browser.text()
    assert "Great hub." in t["text"] and "Ships" not in t["text"]


async def test_elements_have_roles_names_values(browser, page_server):
    await browser.navigate(f"{page_server}/select.html")
    _, els = await browser.elements()
    sel = next(e for e in els if e.role == "combobox")
    assert sel.name == "Language" and sel.value == "English" and "中文" in sel.options
    assert '[%d] combobox "Language" value="English"' % sel.id in element_table(els)


async def test_checkbox_state(browser, page_server):
    await browser.navigate(f"{page_server}/checkbox.html")
    _, els = await browser.elements()
    cb = next(e for e in els if e.role == "checkbox")
    assert cb.checked is False and cb.name == "Newsletter"
    assert (await browser.click(cb.id))["ok"]
    _, els = await browser.elements()
    assert next(e for e in els if e.id == cb.id).checked is True


async def test_ids_are_stable_across_reads(browser, page_server):
    await browser.navigate(f"{page_server}/form.html")
    _, a = await browser.elements()
    _, b = await browser.elements()
    assert [e.id for e in a] == [e.id for e in b]


async def test_type_directly_and_submit(browser, page_server):
    await browser.navigate(f"{page_server}/search.html")
    _, els = await browser.elements()
    box = next(e for e in els if e.role == "textbox")
    assert (await browser.type(box.id, "usb hub", submit=True))["ok"]
    await browser.wait_stable()
    assert "Results for usb hub" in (await browser.text())["text"]


async def test_type_appends_when_not_clearing(browser, page_server):
    await browser.navigate(f"{page_server}/form.html")
    _, els = await browser.elements()
    box = next(e for e in els if e.name == "Full name")
    await browser.type(box.id, "Ada")
    await browser.type(box.id, " L", clear=False)
    assert await browser.eval("document.getElementById('name').value") == "Ada L"


async def test_covered_element_is_not_clicked(browser, page_server):
    await browser.navigate(f"{page_server}/covered.html")
    _, els = await browser.elements()
    btn = next(e for e in els if e.name == "Target")
    r = await browser.click(btn.id)
    assert r["ok"] is False and r["reason"] == "covered"
    assert "clicked" not in (await browser.text())["text"]


async def test_gone_element(browser, page_server):
    await browser.navigate(f"{page_server}/form.html")
    r = await browser.click(99999)
    assert r == {"ok": False, "reason": "gone", "by": ""}


async def test_overlay_detection(browser, page_server):
    await browser.navigate(f"{page_server}/overlay.html")
    ov = await browser.overlays()
    assert ov and ov[0]["cover"] > 0.9
    assert ov[0]["dismiss"][0]["name"] == "Accept all"
    assert (await browser.click(ov[0]["dismiss"][0]["id"]))["ok"]
    assert await browser.overlays() == []


async def test_wait_stable_waits_for_late_content(browser, page_server):
    await browser.navigate(f"{page_server}/delayed.html")
    r = await browser.wait_stable(quiet_ms=1500, timeout_ms=6000)
    assert r["stable"]
    assert "Ready" in (await browser.text())["text"]


async def test_select_option(browser, page_server):
    await browser.navigate(f"{page_server}/select.html")
    _, els = await browser.elements()
    sel = next(e for e in els if e.role == "combobox")
    assert (await browser.select(sel.id, "bahasa"))["selected"] == "Bahasa Indonesia"
    assert (await browser.select(sel.id, "klingon"))["ok"] is False


async def test_scroll(browser, page_server):
    await browser.navigate(f"{page_server}/scroll.html")
    r = await browser.scroll("down")
    assert r["scrollY"] > 0
    r2 = await browser.scroll("up")
    assert r2["scrollY"] < r["scrollY"]


async def test_tabs_new_switch_close_and_pinning(browser, page_server):
    await browser.navigate(f"{page_server}/popup.html")
    home = browser.working_tab
    _, els = await browser.elements()
    await browser.click(next(e for e in els if e.name == "Partner site").id)
    await browser.wait_stable()
    tabs = await browser.tabs()
    assert len(tabs) >= 2
    assert browser.working_tab == home  # popup did not steal the working tab
    other = next(t for t in tabs if t["id"] != home)
    await browser.switch_tab(other["id"])
    assert browser.working_tab == other["id"]
    await browser.close_tab(other["id"])
    assert other["id"] not in [t["id"] for t in await browser.tabs()]


async def test_back(browser, page_server):
    await browser.navigate(f"{page_server}/form.html")
    await browser.navigate(f"{page_server}/select.html")
    r = await browser.back()
    assert r["title"] == "Signup"


async def test_screen_offset_is_measured_not_guessed(browser, page_server):
    """Simulate a window manager that puts the viewport at (7, 93) on screen: the measurement must find it."""
    await browser.navigate(f"{page_server}/form.html")
    true_offset = (7, 93)
    moves = []

    async def move(x, y):  # what xdotool would do: the page sees the pointer at screen − offset
        moves.append((x, y))
        s = await browser.page()
        await s.send(
            "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x - true_offset[0], "y": y - true_offset[1]}
        )

    assert await browser.screen_offset(move) == true_offset
    n = len(moves)
    assert await browser.screen_offset(move) == true_offset and len(moves) == n  # cached until the window changes


async def test_screen_offset_without_a_desktop(browser, page_server):
    await browser.navigate(f"{page_server}/form.html")

    async def no_desktop(x, y):
        raise RuntimeError("xdotool: command not found")

    assert await browser.screen_offset(no_desktop) is None
