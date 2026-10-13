"""End to end on a real X desktop (Xvfb + openbox + Chrome + xdotool), like inside the VM.

Runs only when FISHBOWL_DESKTOP=1 (CI starts the stack with scripts/desktop-services.sh).
"""

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


async def test_shell(live):
    r = await server.shell(command="echo $DISPLAY && xdotool getdisplaygeometry")
    assert r["stdout"].split() == [":1", "1280", "800"]
