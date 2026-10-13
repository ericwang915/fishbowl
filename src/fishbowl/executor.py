"""Run commands inside the VM (via `limactl shell`) or locally (Linux hosts, CI)."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass

from .config import get_config


@dataclass
class Result:
    code: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    @property
    def err(self) -> str:
        return self.stderr.decode("utf-8", "replace")


class Executor:
    """Runs `bash -lc <script>` with DISPLAY=:1 in the guest."""

    def argv(self, script: str) -> list[str]:
        raise NotImplementedError

    async def run(self, script: str, timeout: float = 60, stdin: bytes | None = None) -> Result:
        proc = await asyncio.create_subprocess_exec(
            *self.argv(script),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return Result(124, b"", f"timed out after {timeout}s".encode())
        return Result(proc.returncode or 0, out, err)


def limactl_path() -> str:
    """GUI MCP clients (Claude Desktop, Cursor) start servers with a minimal PATH, so look in the usual places."""
    import shutil
    from pathlib import Path

    found = shutil.which("limactl")
    if found:
        return found
    for p in ("/opt/homebrew/bin/limactl", "/usr/local/bin/limactl", str(Path.home() / ".local/bin/limactl")):
        if Path(p).exists():
            return p
    return "limactl"


class LimaExecutor(Executor):
    def argv(self, script: str) -> list[str]:
        cfg = get_config()
        return [
            limactl_path(),
            "shell",
            "--workdir",
            "/workspace",
            cfg.vm_name,
            "--",
            "bash",
            "-lc",
            f"export DISPLAY=:1; {script}",
        ]


class WSLExecutor(Executor):
    """Windows: commands run in the fishbowl WSL2 distro as its unprivileged `fishbowl` user."""

    def argv(self, script: str) -> list[str]:
        cfg = get_config()
        return [
            "wsl.exe",
            "--distribution",
            cfg.vm_name,
            "--user",
            "fishbowl",
            "--cd",
            "/workspace",
            "--exec",
            "bash",
            "-lc",
            f"export DISPLAY=:1; {script}",
        ]


def bash_exe() -> str:
    """A real bash. On Windows, PATH's bash.exe is often the WSL launcher in System32; prefer Git Bash."""
    import os
    import shutil
    import sys

    if sys.platform != "win32":
        return "bash"
    for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if os.path.exists(p):
            return p
    found = shutil.which("bash")
    return found if found and "system32" not in found.lower() else "bash"


class LocalExecutor(Executor):
    def argv(self, script: str) -> list[str]:
        return [bash_exe(), "-lc", f"export DISPLAY=${{DISPLAY:-:1}}; {script}"]


def get_executor() -> Executor:
    return {"local": LocalExecutor, "wsl": WSLExecutor}.get(get_config().executor, LimaExecutor)()


def q(s: str) -> str:
    return shlex.quote(s)
