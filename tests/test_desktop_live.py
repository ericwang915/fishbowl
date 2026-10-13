"""End to end on a real X desktop (Xvfb + openbox + Chrome + xdotool), like inside the VM.

Runs only when FISHBOWL_DESKTOP=1 (CI starts the stack with scripts/desktop-services.sh).
"""

import contextlib
import os
import struct

import pytest

from fishbowl import server
from fishbowl.browser import BrowserController
from fishbowl.config import get_config

pytestmark = pytest.mark.skipif(os.environ.get("FISHBOWL_DESKTOP") != "1", reason="needs a live X desktop")


@pytest.fixture
async def live(monkeypatch, page_server):
    get_config().executor = "local"
    b = BrowserController("http://127.0.0.1:9222")
    monkeypatch.setattr(server, "_browser", b)
    monkeypatch.setenv("FISHBOWL_GUARD", "heuristic")
    yield page_server
    await b.close()


async def test_screenshot_is_1280x800(live):
    img = await server.computer(action="screenshot")
    png = img.data
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (1280, 800)
    assert list(get_config().screenshot_dir.glob("*.png"))


async def test_pixel_click_lands_on_element(live):
    await server.browser_open(url=f"{live}/checkbox.html")
    els = await server.browser_elements()
    line = next(l for l in els["table"].splitlines() if "Newsletter" in l)
    eid = int(line[1 : line.index("]")])
    x, y = els["screen_xy"][eid]
    await server.computer(action="left_click", x=x, y=y, screenshot_after=False)
    await server.browser_wait(quiet_ms=300)
    assert "Newsletter enabled" in (await server.browser_read())["text"]


async def test_xdotool_typing_and_keys(live):
    await server.browser_open(url=f"{live}/search.html")
    els = await server.browser_elements()
    line = next(l for l in els["table"].splitlines() if "textbox" in l)
    x, y = els["screen_xy"][int(line[1 : line.index("]")])]
    await server.computer(action="left_click", x=x, y=y, screenshot_after=False)
    await server.computer(action="type", text="héllo hub", screenshot_after=False)
    await server.computer(action="key", key="enter", screenshot_after=False)
    await server.browser_wait(quiet_ms=300)
    assert "Results for héllo hub" in (await server.browser_read())["text"]


async def test_clipboard_roundtrip(live):
    assert (await server.clipboard_set(text="copied 你好"))["ok"]
    assert (await server.clipboard_get())["text"] == "copied 你好"


async def test_clipboard_get_redacts_secrets(live):
    from fishbowl.executor import get_executor

    await get_executor().run("xclip -i -selection clipboard >/dev/null 2>&1", stdin=b"4111 1111 1111 1111")
    r = await server.clipboard_get()
    assert r["redacted"] and "4111" not in r["text"]


async def test_real_bridge_with_xclip(live):
    """The VM loop with real xclip: VM → Mac always; Mac → VM only during handoff; wiped afterwards."""
    import asyncio

    from fishbowl import clipboard
    from fishbowl.executor import get_executor

    ex = get_executor()
    xget = lambda: ex.run("xclip -o -selection clipboard 2>/dev/null || true")  # noqa: E731
    mac = {"v": "mac secret"}
    ctl = {"v": False}
    b = clipboard.Bridge(mac_get=lambda: mac["v"], mac_set=lambda t: mac.update(v=t), in_control=lambda: ctl["v"])
    task = asyncio.create_task(b.run_once())
    await asyncio.sleep(1.5)
    assert "mac secret" not in (await xget()).text  # agent driving: Mac clipboard stays out

    await server.clipboard_set(text="agent result 你好")
    for _ in range(20):
        await asyncio.sleep(0.25)
        if mac["v"] == "agent result 你好":
            break
    assert mac["v"] == "agent result 你好"  # VM → Mac

    ctl["v"] = True
    mac["v"] = "pasted password"
    await asyncio.sleep(2)
    assert (await xget()).text == "pasted password"  # human in control: Mac → VM
    ctl["v"] = False
    await asyncio.sleep(2)
    assert (await xget()).text == ""  # control returned: wiped
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_shell(live):
    r = await server.shell(command="echo $DISPLAY && xdotool getdisplaygeometry")
    assert r["stdout"].split() == [":1", "1280", "800"]
