"""Register fishbowl with MCP clients: Claude Code, Codex, Claude Desktop, Cursor.

Each installer is idempotent (re-running replaces the old entry) and backs up any
config file it edits to `<file>.fishbowl.bak`. Secrets are never written to config
files: Claude Code and Codex forward the keys from your shell instead.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

NAME = "fishbowl"

# Env vars the server reads. Codex only forwards variables it is told about.
FORWARD_ENV = [
    "TYPESAFE_API_KEY", "TYPESAFE_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OLLAMA_HOST",
    "FISHBOWL_FAST", "FISHBOWL_FAST_MODEL", "FISHBOWL_SLOW", "FISHBOWL_SLOW_MODEL",
    "FISHBOWL_LLM", "FISHBOWL_MODEL", "FISHBOWL_LLM_BASE_URL",
    "FISHBOWL_PRICE_IN", "FISHBOWL_PRICE_OUT", "FISHBOWL_GUARD", "FISHBOWL_AUTO_OPEN",
]  # fmt: skip

# browser_run can take minutes and handoff waits for a human; Codex defaults to 60 s per tool call.
TOOL_TIMEOUT_S = 900


def server_command() -> list[str]:
    """Absolute command that starts the MCP server, independent of the client's PATH."""
    exe = shutil.which(NAME)
    if exe:
        return [str(Path(exe).resolve()), "mcp"]
    return [sys.executable, "-m", NAME, "mcp"]


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".fishbowl.bak"))


# ---------------------------------------------------------------- JSON clients


def _json_install(path: Path, remove: bool) -> str:
    data = json.loads(path.read_text() or "{}") if path.exists() else {}
    servers = data.setdefault("mcpServers", {})
    if remove:
        if servers.pop(NAME, None) is None:
            return f"not registered in {path}"
    else:
        cmd = server_command()
        servers[NAME] = {"command": cmd[0], "args": cmd[1:]}
    _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return f"{'removed from' if remove else 'registered in'} {path}"


def claude_desktop_config() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def cursor_config() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


# ---------------------------------------------------------------- Codex (TOML)


def codex_config() -> Path:
    return Path.home() / ".codex" / "config.toml"


_SECTION = re.compile(r"^\s*\[\s*mcp_servers\.(\"?)fishbowl\1(\.[^\]]*)?\s*\]\s*$")
_ANY_SECTION = re.compile(r"^\s*\[")


def _strip_codex_entry(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if _ANY_SECTION.match(line):
            skipping = bool(_SECTION.match(line))
        if not skipping:
            out.append(line)
    return "".join(out).rstrip() + ("\n" if out else "")


def codex_block() -> str:
    cmd = server_command()
    return (
        f"\n[mcp_servers.{NAME}]\n"
        f"command = {json.dumps(cmd[0])}\n"
        f"args = {json.dumps(cmd[1:])}\n"
        f"startup_timeout_sec = 30\n"
        f"tool_timeout_sec = {TOOL_TIMEOUT_S}\n"
        f"env_vars = {json.dumps(FORWARD_ENV)}\n"
    )


def _codex_install(path: Path, remove: bool) -> str:
    text = path.read_text() if path.exists() else ""
    had = any(_SECTION.match(l) for l in text.splitlines())
    if remove and not had:
        return f"not registered in {path}"
    new = _strip_codex_entry(text)
    if not remove:
        new = new + codex_block()
    try:  # validate before writing (tomllib is 3.11+)
        import tomllib

        parsed = tomllib.loads(new)
        assert remove or parsed["mcp_servers"][NAME]["tool_timeout_sec"] == TOOL_TIMEOUT_S
    except ImportError:
        pass
    _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    return f"{'removed from' if remove else 'registered in'} {path}"


# ---------------------------------------------------------------- Claude Code


def _claude_code_install(remove: bool) -> str:
    claude = shutil.which("claude")
    if not claude:
        return "skipped: `claude` CLI not found"
    subprocess.run([claude, "mcp", "remove", NAME, "--scope", "user"], capture_output=True)
    if remove:
        return "removed from Claude Code (user scope)"
    r = subprocess.run(
        [claude, "mcp", "add", NAME, "--scope", "user", "--", *server_command()], capture_output=True, text=True
    )
    if r.returncode != 0:
        return f"failed: {r.stderr.strip() or r.stdout.strip()}"
    return "registered with Claude Code (user scope) — run /mcp in Claude Code to check"


# ---------------------------------------------------------------- dispatch

CLIENTS: dict[str, tuple[Callable[[], bool], Callable[[bool], str]]] = {
    "claude-code": (lambda: shutil.which("claude") is not None, _claude_code_install),
    "codex": (
        lambda: shutil.which("codex") is not None or codex_config().parent.exists(),
        lambda remove: _codex_install(codex_config(), remove),
    ),
    "claude-desktop": (
        lambda: claude_desktop_config().parent.exists(),
        lambda remove: _json_install(claude_desktop_config(), remove),
    ),
    "cursor": (lambda: cursor_config().parent.exists(), lambda remove: _json_install(cursor_config(), remove)),
}


def detected() -> list[str]:
    return [name for name, (present, _) in CLIENTS.items() if present()]


def install(client: str, remove: bool = False) -> dict[str, str]:
    names = detected() if client == "all" else [client]
    if not names:
        return {"all": "no MCP clients detected — use `fishbowl install --client print`"}
    return {name: CLIENTS[name][1](remove) for name in names}


def snippet() -> str:
    cmd = server_command()
    return json.dumps({"mcpServers": {NAME: {"command": cmd[0], "args": cmd[1:]}}}, indent=2)
