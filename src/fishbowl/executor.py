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


class LimaExecutor(Executor):
    def argv(self, script: str) -> list[str]:
        cfg = get_config()
        return [
            "limactl", "shell", "--workdir", "/workspace", cfg.vm_name,
            "--", "bash", "-lc", f"export DISPLAY=:1; {script}",
        ]


class LocalExecutor(Executor):
    def argv(self, script: str) -> list[str]:
        return ["bash", "-lc", f"export DISPLAY=${{DISPLAY:-:1}}; {script}"]


def get_executor() -> Executor:
    return LocalExecutor() if get_config().executor == "local" else LimaExecutor()


def q(s: str) -> str:
    return shlex.quote(s)
