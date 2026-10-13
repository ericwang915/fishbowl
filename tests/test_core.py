"""Audit log, control/viewer server, clipboard sync, desktop helpers, MCP server wiring."""

import asyncio
import os
import time

import httpx

from fishbowl import audit, clipboard, control, desktop, server
from fishbowl.config import get_config

# ----------------------------------------------------------------- audit


def test_audit_log_and_redaction():
    audit.log("browser_type", {"element": 3, "text": "sk-ant-api03-" + "x" * 40})
    audit.log("browser_open", {"url": "https://example.com"})
    rows = audit.tail(10)
    assert rows[0]["args"]["text"] == "[REDACTED]"
    assert rows[1]["args"]["url"] == "https://example.com"


def test_screenshot_retention():
    old = audit.save_screenshot(b"\x89PNG old")
    os.utime(old, (time.time() - 8 * 86400,) * 2)
    new = audit.save_screenshot(b"\x89PNG new")
    assert not old.exists() and new.exists()


# ----------------------------------------------------------------- control


def test_pause_and_viewer_server():
    assert control.ensure_server()
    base = control.viewer_url()
    html = httpx.get(base).text
    assert "noVNC" not in html or "vnc.html" in html
    assert f":{get_config().novnc_port}/vnc.html" in html and "Pause" in html
    assert not control.viewer_is_open()
    httpx.post(base + "heartbeat")
    assert control.viewer_is_open()
    httpx.post(base + "pause")
    assert control.is_paused()
    httpx.post(base + "resume")
    assert not control.is_paused()
    # cross-origin writes are refused
    r = httpx.post(base + "pause", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and not control.is_paused()


def test_viewer_not_reopened_when_open(monkeypatch):
    opened = []
    monkeypatch.setattr(control.subprocess, "Popen", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(control.sys, "platform", "darwin")
    control._last_open_attempt = 0
    control.write_state(heartbeat=time.time())
    assert control.open_viewer() is False and not opened
    control.write_state(heartbeat=0)
    assert control.open_viewer() is True and len(opened) == 1
    assert control.open_viewer() is False  # just opened, page still loading


async def test_handoff_waits_for_human(monkeypatch):
    monkeypatch.setattr(control, "open_viewer", lambda force=False: False)
    control.ensure_server()

    async def human():
        await asyncio.sleep(0.5)
        assert control.is_paused()  # agent is held while the human has the screen
        await asyncio.to_thread(httpx.post, control.viewer_url() + "handoff/done", json={"note": "logged in"})

    asyncio.create_task(human())
    r = await control.handoff("please log in", timeout=10)
    assert r == {"status": "returned", "note": "logged in"}
    assert not control.is_paused()


async def test_handoff_timeout(monkeypatch):
    monkeypatch.setattr(control, "open_viewer", lambda force=False: False)
    r = await control.handoff("x", timeout=0.5)
    assert r["status"] == "timeout" and not control.is_paused()


# ----------------------------------------------------------------- clipboard


def test_clipboard_two_way(monkeypatch):
    mac = {"v": "hello from mac"}
    monkeypatch.setattr(clipboard, "_mac_get", lambda: mac["v"])
    monkeypatch.setattr(clipboard, "_mac_set", lambda t: mac.update(v=t))
    state = {}
    assert clipboard.sync_once(state) == "to_vm"
    assert clipboard._file().read_text() == "hello from mac"
    assert clipboard.sync_once(state) is None
    clipboard._file().write_text("copied in vm")  # what the VM loop does
    assert clipboard.sync_once(state) == "to_mac" and mac["v"] == "copied in vm"
    assert clipboard.sync_once(state) is None


# ----------------------------------------------------------------- desktop


def test_key_normalization():
    assert desktop.normalize_key("enter") == "Return"
    assert desktop.normalize_key("cmd+L") == "ctrl+L"
    assert desktop.normalize_key("ctrl + shift + t") == "ctrl+shift+t"


async def test_xdotool_commands(monkeypatch):
    scripts = []

    class Fake:
        async def run(self, script, timeout=60, stdin=None):
            scripts.append(script)
            from fishbowl.executor import Result

            return Result(0, b"X=10\nY=20\n", b"")

    monkeypatch.setattr(desktop, "get_executor", lambda: Fake())
    await desktop.click(5, 6, "right_click")
    await desktop.type_text("hello 'x'")
    await desktop.type_text("你好")
    await desktop.scroll(1, 2, "up", 2)
    assert await desktop.cursor_position() == (10, 20)
    assert scripts[0] == "xdotool mousemove --sync 5 6 click --repeat 1 --delay 80 3"
    assert scripts[1] == "xdotool type --delay 8 -- 'hello '\"'\"'x'\"'\"''"
    assert "xclip -i" in scripts[2] and "ctrl+v" in scripts[2]  # non-ASCII is pasted
    assert "click --repeat 2 --delay 30 4" in scripts[3]


def test_lima_executor_argv(monkeypatch):
    from fishbowl.executor import LimaExecutor

    argv = LimaExecutor().argv("ls")
    assert argv[:2] == ["limactl", "shell"] and "/workspace" in argv and argv[-1].endswith("ls")


def test_lima_template_isolation():
    from importlib import resources

    import yaml

    t = yaml.safe_load(resources.files("fishbowl").joinpath("lima.yaml").read_text())
    assert [m["location"] for m in t["mounts"]] == ["~/vc/workspace"]
    fwd = [p for p in t["portForwards"] if not p.get("ignore")]
    assert all(p["hostIP"] == "127.0.0.1" for p in fwd)
    assert t["portForwards"][-1] == {"guestPortRange": [1, 65535], "ignore": True}
    assert "1280x800" in t["provision"][1]["script"]


# ----------------------------------------------------------------- server


async def test_twenty_five_tools():
    tools = await server.mcp.list_tools()
    assert len(tools) == 25
    for t in tools:
        props = (getattr(t, "input_schema", None) or t.inputSchema)["properties"]
        assert "ctx" not in props


async def test_paused_tools_refuse():
    control.write_state(paused=True)
    r = await server.shell(command="echo hi")
    assert r["error"] == "paused"
    control.write_state(paused=False)
    r = await server.shell(command="echo hi")
    assert r["stdout"].strip() == "hi"


async def test_computer_refuses_secrets():
    r = await server.computer(action="type", text="4111111111111111")
    assert r["error"] == "blocked"


async def test_stale_notice(monkeypatch):
    monkeypatch.setattr(server, "_BOOT_FINGERPRINT", ("old",))
    r = await server.control_status()
    assert "reconnect" in r["notice"].lower()
    monkeypatch.setattr(server, "_BOOT_FINGERPRINT", server._code_fingerprint())
    assert "notice" not in await server.control_status()


async def test_every_call_is_audited():
    await server.shell(command="true")
    assert audit.tail(1)[0]["tool"] == "shell"


async def test_browser_tools_end_to_end(chrome, page_server, monkeypatch):
    from fishbowl.browser import BrowserController

    monkeypatch.setattr(server, "_browser", BrowserController(chrome))
    monkeypatch.setenv("FISHBOWL_GUARD", "heuristic")
    await server.browser_tab(action="new")
    await server.browser_open(url=f"{page_server}/checkout.html")
    els = await server.browser_elements()
    btn = next(int(l[1 : l.index("]")]) for l in els["table"].splitlines() if "Place order" in l)
    r = await server.browser_click(element=btn)
    assert r["status"] == "needs_approval"
    r = await server.browser_click(element=btn, approved=True)
    assert r["ok"]
    assert "Order placed" in (await server.browser_read())["text"]
    await server._browser.close()
