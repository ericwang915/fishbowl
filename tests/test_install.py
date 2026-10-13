"""`fishbowl install` for Codex, Claude Desktop, Cursor and Claude Code — against a temp HOME."""

import json

import pytest

from fishbowl import install

tomllib = pytest.importorskip("tomllib")  # Python 3.11+


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path


EXISTING_CODEX = """model = "gpt-5"

[mcp_servers.other]
command = "npx"
args = ["other"]

[mcp_servers.fishbowl]
command = "old"

[mcp_servers.fishbowl.env]
STALE = "1"

[profiles.fast]
model = "mini"
"""


def test_codex_install_preserves_other_entries_and_is_idempotent(home):
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(EXISTING_CODEX)
    for _ in range(2):
        assert "registered" in install.install("codex")["codex"]
    data = tomllib.loads(cfg.read_text())
    fb = data["mcp_servers"]["fishbowl"]
    assert fb["args"][-1] == "mcp" and fb["tool_timeout_sec"] == 900
    assert "ANTHROPIC_API_KEY" in fb["env_vars"] and "env" not in fb  # keys forwarded, never written
    assert data["mcp_servers"]["other"]["command"] == "npx"
    assert data["profiles"]["fast"]["model"] == "mini" and data["model"] == "gpt-5"
    assert cfg.read_text().count("[mcp_servers.fishbowl]") == 1
    assert (home / ".codex" / "config.toml.fishbowl.bak").read_text() != ""


def test_codex_remove(home):
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(EXISTING_CODEX)
    assert "removed" in install.install("codex", remove=True)["codex"]
    data = tomllib.loads(cfg.read_text())
    assert "fishbowl" not in data["mcp_servers"] and "other" in data["mcp_servers"]
    assert "not registered" in install.install("codex", remove=True)["codex"]


def test_codex_fresh_config(home):
    install.install("codex")
    data = tomllib.loads((home / ".codex" / "config.toml").read_text())
    assert data["mcp_servers"]["fishbowl"]["startup_timeout_sec"] == 30


@pytest.mark.parametrize("client", ["claude-desktop", "cursor"])
def test_json_clients_merge(home, client):
    cfg = install.claude_desktop_config() if client == "claude-desktop" else install.cursor_config()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    install.install(client)
    install.install(client)
    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["fishbowl"]["args"][-1] == "mcp"
    install.install(client, remove=True)
    assert "fishbowl" not in json.loads(cfg.read_text())["mcpServers"]


def test_detect_all(home, monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    assert install.detected() == []
    assert "no MCP clients" in install.install("all")["all"]
    (home / ".cursor").mkdir()
    (home / ".codex").mkdir()
    assert install.detected() == ["codex", "cursor"]
    res = install.install("all")
    assert set(res) == {"codex", "cursor"}


def test_claude_code_uses_cli(home, monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(install.shutil, "which", lambda name: "/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(install.subprocess, "run", lambda argv, **k: calls.append(argv) or R())
    assert "registered" in install.install("claude-code")["claude-code"]
    assert calls[0][:3] == ["/bin/claude", "mcp", "remove"]
    assert calls[1][:6] == ["/bin/claude", "mcp", "add", "fishbowl", "--scope", "user"]
    assert calls[1][-1] == "mcp"


def test_server_command_is_absolute():
    cmd = install.server_command()
    assert install.Path(cmd[0]).is_absolute() and cmd[-1] == "mcp"
