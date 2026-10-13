"""Clipboard bridge between the Mac and the VM: in memory, never on disk.

One long-lived `limactl shell` process runs a tiny loop inside the VM. Lines on its
stdin/stdout carry base64 clipboard contents:

    Mac → VM   "S <base64>"  set the VM clipboard
               "C"           clear it
    VM → Mac   "V <base64>"  the VM clipboard changed

Policy (the agent can read the VM clipboard with `clipboard_get`, so what reaches the
VM matters):

  * VM → Mac always flows: things the agent copies show up on your Mac.
  * Mac → VM only flows while YOU control the screen (a handoff, or Pause). Whatever
    you copy on your Mac at other times, such as passwords, never enters the VM.
  * When control returns to the agent, the VM clipboard is cleared if it still holds
    something that came from the Mac, so a password you pasted during a login is gone.
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
from collections.abc import Awaitable, Callable

from . import control
from .config import get_config
from .executor import get_executor

POLL_S = 0.7

# Runs inside the VM. `read -t` doubles as the poll interval; EOF (parent gone) ends the loop.
VM_SCRIPT = r"""
last=""
while true; do
  if IFS= read -r -t 0.7 line; then
    case "$line" in
      "S "*) printf %s "${line#S }" | base64 -d | xclip -i -selection clipboard >/dev/null 2>&1 ;;
      C) printf '' | xclip -i -selection clipboard >/dev/null 2>&1 ;;
    esac
    last=$(xclip -o -selection clipboard 2>/dev/null | base64 -w0)
    continue
  elif [ $? -le 128 ]; then exit 0; fi
  cur=$(xclip -o -selection clipboard 2>/dev/null | base64 -w0)
  if [ "$cur" != "$last" ]; then last="$cur"; [ -n "$cur" ] && echo "V $cur"; fi
done
"""


def _mac_get() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def _mac_set(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True)


def human_in_control() -> bool:
    st = control.read_state()
    return bool(st.get("paused") or st.get("handoff"))


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


Spawn = Callable[[], Awaitable[asyncio.subprocess.Process]]


async def _spawn_vm() -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *get_executor().argv(VM_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


class Bridge:
    def __init__(
        self,
        spawn: Spawn = _spawn_vm,
        mac_get: Callable[[], str] = _mac_get,
        mac_set: Callable[[str], None] = _mac_set,
        in_control: Callable[[], bool] = human_in_control,
        poll_s: float = POLL_S,
    ):
        self.spawn, self.mac_get, self.mac_set = spawn, mac_get, mac_set
        self.in_control, self.poll_s = in_control, poll_s
        self.last_mac: str | None = None  # Mac clipboard as last seen or set by us
        self.vm_holds_mac = False  # the VM clipboard currently holds something from the Mac
        self.was_in_control = False

    # ---- pure decision logic (unit-tested) ----------------------------------
    def tick(self, mac: str) -> list[str]:
        """Lines to send to the VM, given the current Mac clipboard."""
        out: list[str] = []
        now = self.in_control()
        if now and mac and (mac != self.last_mac or not self.was_in_control):
            out.append("S " + _b64(mac))
            self.vm_holds_mac = True
        if not now and self.vm_holds_mac:
            out.append("C")
            self.vm_holds_mac = False
        self.last_mac, self.was_in_control = mac, now
        return out

    def from_vm(self, line: str) -> str | None:
        """Handle a line from the VM; returns text to put on the Mac clipboard, if any."""
        if not line.startswith("V "):
            return None
        try:
            text = base64.b64decode(line[2:].strip()).decode("utf-8")
        except ValueError:
            return None
        self.vm_holds_mac = False  # the VM now holds something it copied itself
        if text == self.last_mac:
            return None
        self.last_mac = text
        return text

    # ---- process plumbing -----------------------------------------------------
    async def _reader(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        while line := await proc.stdout.readline():
            text = self.from_vm(line.decode("ascii", "replace"))
            if text is not None:
                await asyncio.to_thread(self.mac_set, text)

    async def _writer(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdin
        while proc.returncode is None:
            mac = await asyncio.to_thread(self.mac_get)
            for line in self.tick(mac):
                proc.stdin.write((line + "\n").encode("ascii"))
            await proc.stdin.drain()
            await asyncio.sleep(self.poll_s)

    async def run_once(self) -> None:
        proc = await self.spawn()
        tasks = [asyncio.create_task(self._reader(proc)), asyncio.create_task(self._writer(proc))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                pass
            await asyncio.sleep(3)  # VM stopped or restarting: try again


def remove_legacy_file() -> bool:
    """Older versions mirrored the clipboard through a plaintext file in the shared workspace."""
    p = get_config().workspace / ".fishbowl" / "clipboard"
    if p.exists():
        p.unlink()
        try:
            p.parent.rmdir()
        except OSError:
            pass
        return True
    return False


async def disable_legacy_vm_loop() -> None:
    """VMs created by older templates run a file-based loop; switch it off."""
    await get_executor().run(
        "sudo -n systemctl disable --now fishbowl-clipboard >/dev/null 2>&1; rm -f /workspace/.fishbowl/clipboard",
        timeout=20,
    )


async def run_forever() -> None:
    if sys.platform != "darwin":
        return
    remove_legacy_file()
    try:
        await disable_legacy_vm_loop()
    except Exception:
        pass
    await Bridge().run_forever()
