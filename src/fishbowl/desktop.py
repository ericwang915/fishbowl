"""Pixel-level desktop control with xdotool (the `computer` tool)."""

from __future__ import annotations

import asyncio

from .executor import get_executor, q

BUTTONS = {"left_click": 1, "middle_click": 2, "right_click": 3}
SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}

# Friendly key names -> xdotool keysyms.
KEY_ALIASES = {
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "space": "space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "pageup": "Prior",
    "pagedown": "Next",
    "home": "Home",
    "end": "End",
    "cmd": "ctrl",
    "command": "ctrl",
    "control": "ctrl",
    "option": "alt",
}


def normalize_key(combo: str) -> str:
    parts = [p.strip() for p in combo.replace(" ", "").split("+") if p.strip()]
    return "+".join(KEY_ALIASES.get(p.lower(), p) for p in parts)


class DesktopError(RuntimeError):
    pass


async def _xdo(script: str, timeout: float = 30) -> str:
    r = await get_executor().run(script, timeout=timeout)
    if r.code != 0:
        raise DesktopError(r.err.strip() or f"exit {r.code}")
    return r.text


async def screenshot() -> bytes:
    r = await get_executor().run("import -window root png:-", timeout=20)
    if r.code != 0 or not r.stdout.startswith(b"\x89PNG"):
        raise DesktopError(f"screenshot failed: {r.err.strip()}")
    return r.stdout


async def cursor_position() -> tuple[int, int]:
    out = await _xdo("xdotool getmouselocation --shell")
    vals = dict(line.split("=", 1) for line in out.split() if "=" in line)
    return int(vals["X"]), int(vals["Y"])


async def click(x: int, y: int, button: str = "left_click", repeat: int = 1) -> None:
    b = BUTTONS[button]
    await _xdo(f"xdotool mousemove --sync {int(x)} {int(y)} click --repeat {repeat} --delay 80 {b}")


async def move(x: int, y: int) -> None:
    await _xdo(f"xdotool mousemove --sync {int(x)} {int(y)}")


async def drag(x1: int, y1: int, x2: int, y2: int) -> None:
    await _xdo(
        f"xdotool mousemove --sync {x1} {y1} mousedown 1 sleep 0.1 mousemove --sync {x2} {y2} sleep 0.1 mouseup 1"
    )


async def type_text(text: str) -> None:
    if not text.isascii():
        # xdotool drops characters missing from the X keymap (é, 中文…); paste them instead.
        r = await get_executor().run(
            "xclip -i -selection clipboard >/dev/null 2>&1 && sleep 0.1 && xdotool key --clearmodifiers ctrl+v",
            timeout=30,
            stdin=text.encode(),
        )
        if r.code != 0:
            raise DesktopError(r.err.strip() or f"paste failed ({r.code})")
        return
    # Chunk so very long strings don't hit argv limits.
    for i in range(0, len(text), 200):
        await _xdo(f"xdotool type --delay 8 -- {q(text[i : i + 200])}", timeout=120)


async def key(combo: str) -> None:
    await _xdo(f"xdotool key -- {q(normalize_key(combo))}")


async def scroll(x: int, y: int, direction: str = "down", amount: int = 3) -> None:
    b = SCROLL_BUTTONS[direction]
    await _xdo(f"xdotool mousemove --sync {int(x)} {int(y)} click --repeat {int(amount)} --delay 30 {b}")


async def wait(seconds: float) -> None:
    await asyncio.sleep(min(max(seconds, 0), 30))
