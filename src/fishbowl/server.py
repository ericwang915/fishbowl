"""MCP server (stdio) exposing 25 tools for driving the fishbowl VM."""

import asyncio
import contextlib
import functools
import inspect
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

try:  # mcp >= 2
    from mcp.server.mcpserver import Context, Image
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context, Image  # type: ignore
    from mcp.server.fastmcp import FastMCP as _Server

from . import audit, chat, clipboard, control, desktop, guard, llm
from .browser import BrowserController, element_table
from .config import get_config
from .executor import get_executor
from .runner import Runner, run_task

BASE_INSTRUCTIONS = """fishbowl is a Linux desktop (Debian, 1280x800, Chromium) in a local VM that a human can watch live.
Prefer the browser_* tools (fast, exact) over pixel clicks; use `computer` for anything outside the browser.
If you hit a login, captcha or anything needing a person, call handoff. Only /workspace is shared with the Mac."""


def instructions() -> str:
    if llm.configured("fast"):
        mode = (
            "FAST MODE IS ON: for multi-step web tasks write a plan and call browser_run; it executes locally "
            "and hands back with an observation when stuck."
        )
    else:
        mode = (
            "Fast mode is off (no Jev key): drive web pages yourself with browser_elements, browser_click, "
            "browser_type, browser_select, browser_wait and browser_read. browser_run is unavailable."
        )
    return BASE_INSTRUCTIONS + "\n" + mode


# ---------------------------------------------------------------------------
# Stale-code detection: if the package on disk changes while this process runs
# (e.g. after `pip install -U` or a skill update), tell the client to reconnect.

_PKG = Path(__file__).parent


def _code_fingerprint() -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in _PKG.glob("*.py")))


_BOOT_FINGERPRINT = _code_fingerprint()
STALE_NOTICE = (
    "fishbowl was updated on disk but this MCP process is still running the old code. "
    "Reconnect the MCP server (e.g. /mcp → reconnect) to load the new version."
)


def stale_notice() -> str | None:
    try:
        return STALE_NOTICE if _code_fingerprint() != _BOOT_FINGERPRINT else None
    except OSError:
        return None


# ---------------------------------------------------------------------------

_browser: BrowserController | None = None


def get_browser() -> BrowserController:
    global _browser
    if _browser is None:
        _browser = BrowserController()
    return _browser


@contextlib.asynccontextmanager
async def lifespan(_server):
    cfg = get_config()
    cfg.ensure_dirs()
    audit.prune_screenshots()
    control.ensure_server()
    if cfg.executor == "wsl":
        from .vm import WSLBackend

        await asyncio.to_thread(WSLBackend().ensure_running)
    sync = asyncio.create_task(clipboard.run_forever()) if cfg.executor in ("lima", "wsl") else None
    try:
        yield {}
    finally:
        if sync:
            sync.cancel()
        if _browser:
            await _browser.close()


mcp = _Server("fishbowl", instructions=instructions(), lifespan=lifespan)

SCREEN_TOOLS = {"computer", "browser_open", "browser_click", "browser_type", "browser_run", "chat_ask", "shell"}


def tool(fn):
    """Register a tool with auditing, pause checks, auto-opening the viewer and the stale notice."""
    name = fn.__name__

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        logged = {k: v for k, v in kwargs.items() if k != "ctx"}
        if name in SCREEN_TOOLS:
            if control.is_paused() and name != "handoff":
                audit.log(name, logged, {"refused": "paused"})
                return {
                    "error": "paused",
                    "detail": "The human paused the agent in the viewer. Wait, or ask them to press Resume.",
                }
            control.maybe_auto_open()
        try:
            result = await fn(*args, **kwargs)
        except Exception as e:
            audit.log(name, logged, {"error": str(e)})
            result = {"error": type(e).__name__, "detail": str(e)}
        else:
            audit.log(name, logged, result if not isinstance(result, (Image, list)) else "<image>")
        notice = stale_notice()
        if notice:
            if isinstance(result, dict):
                result = {**result, "notice": notice}
            elif isinstance(result, list):
                result = [*result, notice]
            else:
                result = [result, notice]
        return result

    wrapper.__signature__ = inspect.signature(fn)
    return mcp.tool()(wrapper)


class Approval(BaseModel):
    approve: bool


async def _ask_human(ctx: Context | None, message: str) -> bool:
    if ctx is None:
        return False
    try:
        r = await ctx.elicit(f"fishbowl wants to: {message}\nAllow it?", Approval)
        return getattr(r, "action", "") == "accept" and bool(getattr(r.data, "approve", False))
    except Exception:
        return False  # client cannot elicit → caller gets needs_approval


async def _guard_commit(ctx, action: str, target: str, approved: bool) -> dict | None:
    if approved or os.environ.get("FISHBOWL_GUARD", "model") == "off":
        return None
    b = get_browser()
    page = await b.text(1500)
    if os.environ.get("FISHBOWL_GUARD") == "heuristic":
        v = guard.heuristic(action, target, page["text"])
    else:
        v = await guard.assess(action, target, f"{page['url']}\n{page['text']}")
    if not v.needs_approval:
        return None
    if await _ask_human(ctx, f"{action} on {target} ({v.reason})"):
        return None
    return {
        "status": "needs_approval",
        "reason": v.reason,
        "detail": "Ask the user. If they agree, call again with approved=true.",
    }


def _progress(ctx: Context | None):
    """A progress reporter bound to this call, or None. Never raises."""
    if ctx is None:
        return None

    async def report(done: float, total: float, message: str) -> None:
        try:
            await ctx.report_progress(done, total, message)
        except Exception:
            pass

    return report


async def _screenshot_image() -> Image:
    png = await desktop.screenshot()
    audit.save_screenshot(png)
    return Image(data=png, format="png")


# ============================== desktop ======================================


@tool
async def computer(
    action: Literal[
        "screenshot",
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "mouse_move",
        "left_click_drag",
        "type",
        "key",
        "scroll",
        "cursor_position",
        "wait",
    ],
    x: int | None = None,
    y: int | None = None,
    x2: int | None = None,
    y2: int | None = None,
    text: str | None = None,
    key: str | None = None,
    direction: Literal["up", "down", "left", "right"] = "down",
    amount: int = 3,
    seconds: float = 1.0,
    screenshot_after: bool = True,
) -> Any:
    """Pixel-level control of the 1280x800 desktop via xdotool. Coordinates are screen pixels.
    `key` uses xdotool names, e.g. "Return", "ctrl+l", "ctrl+shift+t". Returns a screenshot after acting."""
    if action == "screenshot":
        return await _screenshot_image()
    if action == "cursor_position":
        cx, cy = await desktop.cursor_position()
        return {"x": cx, "y": cy}
    if action in (
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "mouse_move",
        "scroll",
        "left_click_drag",
    ):
        if x is None or y is None:
            return {"error": "x and y are required"}
    if action in ("left_click", "right_click", "middle_click"):
        await desktop.click(x, y, action)
    elif action == "double_click":
        await desktop.click(x, y, "left_click", repeat=2)
    elif action == "mouse_move":
        await desktop.move(x, y)
    elif action == "left_click_drag":
        if x2 is None or y2 is None:
            return {"error": "x2 and y2 are required"}
        await desktop.drag(x, y, x2, y2)
    elif action == "type":
        if not text:
            return {"error": "text is required"}
        if guard.contains_secret(text):
            return {"error": "blocked", "detail": "text looks like a card number or secret; fishbowl never types those"}
        await desktop.type_text(text)
    elif action == "key":
        if not key:
            return {"error": "key is required"}
        await desktop.key(key)
    elif action == "scroll":
        await desktop.scroll(x, y, direction, amount)
    elif action == "wait":
        await desktop.wait(seconds)
    if screenshot_after:
        await asyncio.sleep(0.3)
        return [f"{action} ok", await _screenshot_image()]
    return {"ok": True}


@tool
async def shell(command: str, timeout_s: float = 60) -> dict:
    """Run a bash command inside the VM (cwd /workspace, DISPLAY=:1). Only /workspace is shared with the Mac."""
    r = await get_executor().run(command, timeout=min(timeout_s, 600))
    return {"exit_code": r.code, "stdout": r.text[-20000:], "stderr": r.err[-5000:]}


@tool
async def view_screen() -> dict:
    """Open the live viewer (noVNC + Pause/Resume) in the Mac browser. Does nothing if it is already open."""
    opened = control.open_viewer()
    return {"url": control.viewer_url(), "opened": opened, "already_open": control.viewer_is_open() and not opened}


@tool
async def handoff(reason: str, timeout_s: float = 600, ctx: Context | None = None) -> dict:
    """Give the screen to the human (to log in, solve a captcha, decide something). Blocks until they click
    "Done" in the viewer or the timeout passes; returns any note they typed."""
    report = _progress(ctx)
    tick = (lambda waited, total: report(waited, total, f"waiting for the human: {reason}")) if report else None
    return await control.handoff(reason, timeout=timeout_s, on_tick=tick)


@tool
async def control_status() -> dict:
    """Is the agent paused, is a handoff in progress, is the viewer open?"""
    st = control.read_state()
    return {
        "paused": bool(st.get("paused")),
        "handoff": st.get("handoff"),
        "viewer_open": control.viewer_is_open(),
        "viewer_url": control.viewer_url(),
    }


@tool
async def clipboard_get() -> dict:
    """Read the VM clipboard. What the agent copies reaches the Mac; the Mac clipboard only reaches the VM
    while the human controls the screen (handoff or Pause), and is cleared when control returns."""
    r = await get_executor().run("xclip -o -selection clipboard 2>/dev/null || true", timeout=10)
    if guard.contains_secret(r.text):
        return {"text": "[REDACTED: looks like a card number or secret]", "redacted": True}
    return {"text": r.text}


@tool
async def clipboard_set(text: str) -> dict:
    """Set the VM clipboard (it also appears on the Mac clipboard)."""
    if guard.contains_secret(text):
        return {"error": "blocked", "detail": "looks like a card number or secret"}
    # xclip stays alive to serve the selection; detach its output so we don't wait on it.
    r = await get_executor().run("xclip -i -selection clipboard >/dev/null 2>&1", timeout=10, stdin=text.encode())
    return {"ok": r.code == 0}


# ============================== browser ======================================


@tool
async def browser_open(url: str) -> dict:
    """Navigate the working tab to a URL and wait for the page to settle."""
    return await get_browser().navigate(url)


@tool
async def browser_read(max_chars: int = 20000) -> dict:
    """The visible text of the working tab (url, title, text)."""
    return await get_browser().text(max_chars)


@tool
async def browser_elements(only_in_viewport: bool = False) -> dict:
    """Interactive elements as a table: [id] role "name" value=… checked=…, plus screen coordinates for `computer`.
    Use the ids with browser_click / browser_type / browser_select."""
    b = get_browser()
    meta, els = await b.elements()
    if only_in_viewport:
        els = [e for e in els if e.inViewport]
    offset = await b.screen_offset(desktop.move)  # measured, not estimated; None without a desktop
    if offset:
        coords = {e.id: [e.x + offset[0], e.y + offset[1]] for e in els}
    else:
        coords = {e.id: [e.screenX, e.screenY] for e in els}
    return {**meta, "table": element_table(els, 300), "screen_xy": coords, "calibrated": bool(offset)}


@tool
async def browser_click(element: int, approved: bool = False, ctx: Context | None = None) -> dict:
    """Click an element by id. Checks it is still there and not covered first. Irreversible or public actions
    (pay, send, delete, submit…) need human approval — pass approved=true only after the user said yes."""
    b = get_browser()
    loc = await b.locate(element)
    if not loc["ok"]:
        return {"ok": False, "reason": loc["reason"], "covered_by": loc.get("by", "")}
    blocked = await _guard_commit(ctx, "click", f'{loc["tag"]} "{loc["name"]}"', approved)
    if blocked:
        return blocked
    r = await b.click(element)
    if r["ok"]:
        await b.wait_stable(quiet_ms=300, timeout_ms=5000)
    return r


@tool
async def browser_type(
    element: int,
    text: str,
    clear: bool = True,
    submit: bool = False,
    approved: bool = False,
    ctx: Context | None = None,
) -> dict:
    """Type text straight into an element (no keystroke jitter). submit=true presses Enter afterwards.
    Card numbers and API keys are always refused."""
    if guard.contains_secret(text):
        return {"error": "blocked", "detail": "text looks like a card number or secret; fishbowl never types those"}
    b = get_browser()
    if submit:
        loc = await b.locate(element)
        blocked = await _guard_commit(ctx, "enter", f'{loc.get("tag")} "{loc.get("name")}"', approved)
        if blocked:
            return blocked
    r = await b.type(element, text, clear=clear, submit=submit)
    if submit and r.get("ok"):
        await b.wait_stable(quiet_ms=300, timeout_ms=5000)
    return r


@tool
async def browser_select(element: int, option: str) -> dict:
    """Choose an option (by visible text or value) in a <select> dropdown."""
    return await get_browser().select(element, option)


@tool
async def browser_scroll(direction: Literal["up", "down"] = "down", amount: float = 0.8) -> dict:
    """Scroll the working tab by `amount` screen heights."""
    return await get_browser().scroll(direction, amount)


@tool
async def browser_press(
    key: Literal["Enter", "Tab", "Escape", "Backspace"], approved: bool = False, ctx: Context | None = None
) -> dict:
    """Press a key in the working tab. Enter goes through the approval guard."""
    if key == "Enter":
        focus = await get_browser().eval(
            "(() => { const a = document.activeElement; return a ? (a.tagName + ' ' + (a.getAttribute('aria-label') || a.name || a.placeholder || '')) : 'body'; })()"
        )
        blocked = await _guard_commit(ctx, "enter", str(focus), approved)
        if blocked:
            return blocked
    await get_browser().press(key)
    return {"ok": True}


@tool
async def browser_overlays() -> dict:
    """Find popups, modals, cookie banners or chat widgets covering the page, with ids of their dismiss buttons."""
    return {"overlays": await get_browser().overlays()}


@tool
async def browser_wait(quiet_ms: int = 600, timeout_ms: int = 10000) -> dict:
    """Wait until the page stops changing (DOM quiet for quiet_ms) or timeout."""
    return await get_browser().wait_stable(quiet_ms, timeout_ms)


@tool
async def browser_tabs() -> dict:
    """List tabs. `working` marks the tab every browser_* tool acts on."""
    return {"tabs": await get_browser().tabs()}


@tool
async def browser_tab(
    action: Literal["new", "switch", "close"], tab_id: str | None = None, url: str = "about:blank"
) -> dict:
    """Open a new working tab, switch the working tab, or close a tab. Tabs a site opens by itself never
    become the working tab unless you switch to them."""
    b = get_browser()
    if action == "new":
        return await b.new_tab(url)
    if action == "switch":
        if not tab_id:
            return {"error": "tab_id is required"}
        return await b.switch_tab(tab_id)
    return await b.close_tab(tab_id)


@tool
async def browser_back() -> dict:
    """Go back in the working tab's history."""
    return await get_browser().back()


@tool
async def browser_run(
    plan: list[dict[str, Any] | str] | None = None,
    goal: str | None = None,
    timeout_s: float = 180,
    max_replans: int = 2,
    allow: list[str] | None = None,
    ctx: Context | None = None,
) -> dict:
    """Fast + slow thinking for web tasks. The fast model (Jev, or any small model) executes one step at a time
    on the working tab; the slow model plans.
    - plan only: you are the slow thinker. plan = [{"goal": "search for the product", "text": "usb c hub"},
      {"goal": "open the first result"}, …]. Only `text` from the plan is ever typed.
    - goal only: fishbowl's planner model writes the plan, and replans up to `max_replans` times on handback.
    - both: start from your plan, and let the planner replan from `goal` if it gets stuck.
    Returns status done | handback | needs_approval | paused | timeout | error, with the reason, a step log,
    timing, and on handback a compact `observation` of the page so you can replan without another call.
    `allow` pre-approves guard categories such as ["send_message"] when the user already agreed."""
    if not plan and not goal:
        return {"error": "give a plan, a goal, or both"}
    if not llm.enabled("fast"):
        return {
            "status": "fast_mode_off",
            "reason": llm.HOW_TO_ENABLE["fast"],
            "instead": "Drive the page step by step: browser_elements → browser_click / browser_type / browser_select, "
            "browser_wait, browser_read.",
        }
    if not plan and not llm.enabled("slow"):
        return {"status": "no_planner", "reason": llm.HOW_TO_ENABLE["slow"], "instead": "pass a `plan`"}
    runner = Runner(get_browser(), approver=lambda m: _ask_human(ctx, m), allow=allow, progress=_progress(ctx))
    res = await run_task(runner, goal=goal, plan=plan or None, max_replans=max_replans, timeout_s=timeout_s)
    return res.as_dict()


# ============================== chatbot testing ==============================


@tool
async def chat_ask(
    message: str, input_selector: str | None = None, send_selector: str | None = None, timeout_s: float = 60
) -> dict:
    """Ask a web chatbot one question on the current page: types it, confirms it shows up in the conversation,
    waits for the reply to settle (ignoring "Thinking…" style placeholders) and labels the outcome:
    answered | no_reply | survey_only | placeholder_stuck | send_failed | blocked."""
    return await chat.chat_ask(get_browser(), message, input_selector, send_selector, timeout_s)


@tool
async def judge_reply(
    question: str,
    reply: str,
    label: str | None = None,
    expected_language: str | None = None,
    store_context: str | None = None,
) -> dict:
    """Grade a chatbot reply: reply type, language match, actionable, cites store data, score 1-5, issues.
    Pass chat_ask's label; no_reply/survey_only are graded by rule without a model call."""
    return await chat.judge_reply(question, reply, label, expected_language, store_context)


@tool
async def model_stats(since_hours: float | None = None) -> dict:
    """Decision-model usage: calls, tokens, latency and spend, split by purpose (decide, check, guard, judge)."""
    return llm.stats(since_hours)


@tool
async def audit_log(last: int = 30) -> dict:
    """The most recent entries of the audit log (every action fishbowl took)."""
    return {"entries": audit.tail(min(last, 500)), "dir": str(get_config().audit_dir)}


def main() -> None:
    mcp.run("stdio")
