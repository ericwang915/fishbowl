"""End to end against a REAL fishbowl VM created by `fishbowl up` (Lima + QEMU/KVM in CI).

Runs only with FISHBOWL_VM_LIVE=1. Pages are data: URLs because the VM cannot reach the host's 127.0.0.1.
"""

import asyncio
import os
import socket
import struct
import urllib.parse
import uuid
from pathlib import Path

import pytest

from fishbowl import config, llm, server
from fishbowl.browser import BrowserController
from fishbowl.runner import Runner

from .fake_model import use_fake

pytestmark = pytest.mark.skipif(os.environ.get("FISHBOWL_VM_LIVE") != "1", reason="needs a real fishbowl VM")

CHECKBOX = """<!doctype html><meta charset="utf-8"><title>Prefs</title>
<label><input type="checkbox" id="n" onchange="document.getElementById('o').textContent=this.checked?'Newsletter enabled':''"> Newsletter</label>
<p id="o"></p><input id="q" placeholder="Search"><p id="r"></p>
<script>document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('r').textContent='Results for '+e.target.value})</script>"""


def data_url(html: str) -> str:
    return "data:text/html;charset=utf-8," + urllib.parse.quote(html)


@pytest.fixture
async def live_vm(monkeypatch):
    cfg = config.Config(home=Path.home() / "vc", executor="lima", auto_open_viewer=False)
    config.set_config(cfg)
    b = BrowserController(cfg.cdp_url)
    monkeypatch.setattr(server, "_browser", b)
    monkeypatch.setenv("FISHBOWL_GUARD", "heuristic")
    yield cfg
    await b.close()


async def test_screenshot_is_1280x800(live_vm):
    img = await server.computer(action="screenshot")
    assert struct.unpack(">II", img.data[16:24]) == (1280, 800)


async def test_pixel_click_and_unicode_typing(live_vm):
    await server.browser_open(url=data_url(CHECKBOX))
    els = await server.browser_elements()
    assert els["calibrated"], "screen coordinates must be measured with a real mouse move"
    rows = {l.split('"')[1] if '"' in l else l: int(l[1 : l.index("]")]) for l in els["table"].splitlines()}
    x, y = els["screen_xy"][rows["Newsletter"]]
    await server.computer(action="left_click", x=x, y=y, screenshot_after=False)
    await server.browser_wait(quiet_ms=300)
    assert "Newsletter enabled" in (await server.browser_read())["text"]
    x, y = els["screen_xy"][rows["Search"]]
    await server.computer(action="left_click", x=x, y=y, screenshot_after=False)
    await asyncio.sleep(0.5)  # QEMU guests are slower to move focus
    focused = await server._browser.eval("document.activeElement && document.activeElement.id")
    assert focused == "q", f"pixel click did not focus the search box (focused: {focused!r})"
    await server.computer(action="type", text="héllo 你好", screenshot_after=False)
    await asyncio.sleep(0.3)
    value = await server._browser.eval("document.getElementById('q').value")
    assert value == "héllo 你好", f"typed value was {value!r}"
    await server.computer(action="key", key="enter", screenshot_after=False)
    await server.browser_wait(quiet_ms=300)
    assert "Results for héllo 你好" in (await server.browser_read())["text"]


async def test_clipboard(live_vm):
    assert (await server.clipboard_set(text="from the agent ✓"))["ok"]
    assert (await server.clipboard_get())["text"] == "from the agent ✓"


async def test_runs_as_unprivileged_user(live_vm):
    r = await server.shell(command="id -u; uname -m; echo $DISPLAY; xdotool getdisplaygeometry")
    uid, machine, display, *geom = r["stdout"].split()
    assert uid != "0" and machine in ("x86_64", "aarch64") and display == ":1" and geom == ["1280", "800"]


async def test_only_the_workspace_is_shared(live_vm):
    token = uuid.uuid4().hex
    await server.shell(command=f"echo {token} > /workspace/from-vm.txt")
    assert (live_vm.workspace / "from-vm.txt").read_text().strip() == token  # VM → host
    (live_vm.workspace / "from-host.txt").write_text(token)
    r = await server.shell(command="cat /workspace/from-host.txt")
    assert r["stdout"].strip() == token  # host → VM
    home = str(Path.home())
    r = await server.shell(command=f"test -e {home}/vc && echo LEAK || echo ok; findmnt -rn -o TARGET")
    assert r["stdout"].split()[0] == "ok", "the host home directory must not be visible in the VM"
    host_mounts = [m for m in r["stdout"].split()[1:] if m == home or m.startswith((home + "/", "/Users/"))]
    assert host_mounts == []


def test_only_cdp_and_novnc_are_forwarded(live_vm):
    def open_port(port):
        with socket.socket() as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0

    assert open_port(9222) and open_port(6080)
    assert not open_port(5900), "raw VNC must stay inside the VM"


async def test_browser_run_on_the_real_vm(live_vm):
    use_fake(llm)
    b = server._browser
    await b.navigate(data_url(CHECKBOX))
    res = await Runner(b).run([{"goal": "tick 'newsletter' expect 'enabled'"}], timeout_s=60)
    assert res.status == "done", (res.reason, res.log)
    llm.set_override(None)
    llm.set_choice_override(None)
