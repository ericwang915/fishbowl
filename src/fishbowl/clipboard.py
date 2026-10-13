"""Two-way clipboard sync between the Mac and the VM.

Both sides meet at a hidden file in the shared workspace. Inside the VM a tiny
loop (installed by the Lima template) mirrors the X clipboard to/from that file;
here we mirror the macOS pasteboard to/from the same file.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

from .config import get_config

POLL_S = 1.0


def _file():
    p = get_config().workspace / ".fishbowl" / "clipboard"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _mac_get() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def _mac_set(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True)


def sync_once(state: dict) -> str | None:
    """One sync step. Returns 'to_vm', 'to_mac' or None."""
    f = _file()
    mac = _mac_get()
    shared = f.read_text(encoding="utf-8") if f.exists() else ""
    if mac != state.get("mac") and mac != shared:
        f.write_text(mac, encoding="utf-8")
        state["mac"] = state["shared"] = mac
        return "to_vm"
    if shared != state.get("shared") and shared != mac:
        _mac_set(shared)
        state["mac"] = state["shared"] = shared
        return "to_mac"
    state["mac"], state["shared"] = mac, shared
    return None


async def run_forever() -> None:
    if sys.platform != "darwin":
        return
    state: dict = {}
    while True:
        try:
            await asyncio.to_thread(sync_once, state)
        except Exception:
            pass
        await asyncio.sleep(POLL_S)
