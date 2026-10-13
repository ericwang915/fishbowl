"""High-level browser control on top of CDP.

The controller keeps one *working tab*. Tabs the site opens on its own
(popups, target=_blank) never steal it; only an explicit `switch_tab` /
`new_tab` changes which tab we act on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from . import dom_js
from .cdp import Browser, CDPError, Session
from .config import get_config


@dataclass
class Element:
    id: int
    role: str
    name: str
    tag: str
    x: int
    y: int
    value: str | None = None
    checked: bool | None = None
    expanded: bool | None = None
    options: list[str] | None = None
    inViewport: bool = True
    screenX: int = 0
    screenY: int = 0
    w: int = 0
    h: int = 0

    def line(self) -> str:
        s = f'[{self.id}] {self.role} "{self.name}"'
        if self.value is not None:
            s += f' value="{self.value}"'
        if self.checked is not None:
            s += f" checked={str(self.checked).lower()}"
        if self.expanded is not None:
            s += f" expanded={str(self.expanded).lower()}"
        if self.options:
            s += " options=" + json.dumps(self.options[:12], ensure_ascii=False)
        if not self.inViewport:
            s += " (offscreen)"
        return s


_FIELDS = set(Element.__dataclass_fields__)


def element_table(elements: list[Element], limit: int = 150) -> str:
    return "\n".join(e.line() for e in elements[:limit])


class BrowserController:
    def __init__(self, cdp_url: str | None = None):
        self.browser = Browser(cdp_url or get_config().cdp_url)
        self.working_tab: str | None = None

    # ---------- tabs ----------
    async def tabs(self) -> list[dict[str, Any]]:
        pages = await self.browser.pages()
        return [{"id": p["id"], "title": p.get("title", ""), "url": p.get("url", ""),
                 "working": p["id"] == self.working_tab} for p in pages]

    async def page(self) -> Session:
        pages = await self.browser.pages()
        ids = [p["id"] for p in pages]
        if self.working_tab not in ids:
            if not pages:
                pages = [await self.browser.new_page()]
                ids = [pages[0]["id"]]
            # Fall back to the first page Chrome lists (most recently focused).
            self.working_tab = ids[0]
        return await self.browser.session(self.working_tab)

    async def new_tab(self, url: str = "about:blank") -> dict:
        t = await self.browser.new_page(url)
        self.working_tab = t["id"]
        await self.browser.activate(t["id"])
        if url != "about:blank":
            await self.wait_stable()
        return {"id": t["id"], "url": url}

    async def switch_tab(self, tab_id: str) -> dict:
        if tab_id not in [p["id"] for p in await self.browser.pages()]:
            raise CDPError(f"no tab {tab_id}")
        self.working_tab = tab_id
        await self.browser.activate(tab_id)
        return {"working": tab_id}

    async def close_tab(self, tab_id: str | None = None) -> dict:
        tid = tab_id or self.working_tab
        if not tid:
            raise CDPError("no tab to close")
        await self.browser.close_page(tid)
        if tid == self.working_tab:
            self.working_tab = None
        return {"closed": tid}

    # ---------- reading ----------
    async def navigate(self, url: str) -> dict:
        if "://" not in url and not url.startswith(("about:", "data:")):
            url = "https://" + url
        s = await self.page()
        await self.browser.activate(s.target_id)
        r = await s.send("Page.navigate", {"url": url})
        if r.get("errorText"):
            raise CDPError(f"navigation failed: {r['errorText']}")
        st = await self.wait_stable()
        info = await s.evaluate("({url: location.href, title: document.title})")
        return {**info, "stable": st.get("stable")}

    async def back(self) -> dict:
        s = await self.page()
        await s.evaluate("history.back()")
        await self.wait_stable()
        return await s.evaluate("({url: location.href, title: document.title})")

    async def text(self, max_chars: int = 20000) -> dict:
        s = await self.page()
        r = await s.evaluate(dom_js.PAGE_TEXT)
        if len(r["text"]) > max_chars:
            r["text"] = r["text"][:max_chars] + f"\n…[truncated, {len(r['text'])} chars total]"
        return r

    async def elements(self) -> tuple[dict, list[Element]]:
        s = await self.page()
        r = await s.evaluate(dom_js.ELEMENTS)
        els = [Element(**{k: v for k, v in e.items() if k in _FIELDS}) for e in r.pop("elements")]
        return r, els

    async def overlays(self) -> list[dict]:
        s = await self.page()
        return await s.evaluate(dom_js.OVERLAYS)

    async def wait_stable(self, quiet_ms: int = 500, timeout_ms: int = 8000) -> dict:
        s = await self.page()
        try:
            return await s.evaluate(dom_js.WAIT_STABLE % (quiet_ms, timeout_ms), timeout=timeout_ms / 1000 + 5)
        except CDPError:
            # Page navigated mid-wait (context destroyed); wait again on the new document.
            return await s.evaluate(dom_js.WAIT_STABLE % (quiet_ms, timeout_ms), timeout=timeout_ms / 1000 + 5)

    async def eval(self, expr: str) -> Any:
        s = await self.page()
        return await s.evaluate(expr)

    # ---------- acting ----------
    async def locate(self, element_id: int) -> dict:
        s = await self.page()
        return await s.evaluate(dom_js.LOCATE % int(element_id))

    async def click(self, element_id: int) -> dict:
        loc = await self.locate(element_id)
        if not loc["ok"]:
            return {"ok": False, "reason": loc["reason"], "by": loc.get("by", "")}
        s = await self.page()
        x, y = loc["x"], loc["y"]
        await s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        return {"ok": True, "x": x, "y": y}

    async def type(self, element_id: int, text: str, clear: bool = True, submit: bool = False) -> dict:
        loc = await self.locate(element_id)
        if not loc["ok"]:
            return {"ok": False, "reason": loc["reason"], "by": loc.get("by", "")}
        if loc.get("select"):
            return await self.select(element_id, text)
        s = await self.page()
        focused = await s.evaluate(dom_js.FOCUS_CLEAR % (int(element_id), "true" if clear else "false"))
        if not focused:
            # Fall back to a real click to focus custom widgets.
            await self.click(element_id)
        await s.send("Input.insertText", {"text": text})
        if submit:
            await self.press("Enter")
        return {"ok": True}

    async def select(self, element_id: int, option: str) -> dict:
        s = await self.page()
        return await s.evaluate(dom_js.SELECT_OPTION % (int(element_id), json.dumps(option)))

    async def press(self, key: str) -> None:
        s = await self.page()
        codes = {"Enter": (13, "\r"), "Tab": (9, ""), "Escape": (27, ""), "Backspace": (8, "")}
        vk, txt = codes.get(key, (0, ""))
        down = {"type": "keyDown", "key": key, "code": key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
        if txt:
            down["text"] = txt
        await s.send("Input.dispatchKeyEvent", down)
        await s.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})

    async def scroll(self, direction: str = "down", amount: float = 0.8) -> dict:
        s = await self.page()
        sign = -1 if direction == "up" else 1
        return await s.evaluate(
            f"(window.scrollBy(0, {sign} * innerHeight * {float(amount)}), "
            "{scrollY: Math.round(scrollY), scrollMax: Math.max(0, document.documentElement.scrollHeight - innerHeight)})"
        )

    async def close(self) -> None:
        await self.browser.close()
