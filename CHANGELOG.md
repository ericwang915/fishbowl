# Changelog

## Unreleased

- `fishbowl install` now registers with **Codex** (writes `~/.codex/config.toml` with `tool_timeout_sec = 900` and `env_vars` forwarding), **Claude Code**, **Claude Desktop** and **Cursor**, or every client it finds (`--client all`, the default). Supports `--remove`.
- `browser_run` and `handoff` send MCP progress notifications, so clients with per-call timeouts keep waiting.
- `limactl` is found even when a GUI client starts the server with a minimal `PATH`.
- New `docs/clients.md` and `examples/AGENTS.md`; a stdio handshake test that spawns `fishbowl mcp` like a real client.

## 0.1.0 (2026-10-13)

First public release.

- Lima VM template: Debian 12, Xvfb 1280×800, openbox, Chromium, x11vnc + noVNC, xdotool, xclip. Only `~/vc/workspace` is mounted; all ports are bound to 127.0.0.1.
- 25 MCP tools (stdio): desktop, browser over CDP, `browser_run`, chatbot QA, human in the loop, audit.
- `browser_run` fast executor: one structured decision per action, a separate completion check, text only from the plan, auto handback, working-tab pinning.
- Approval guardrail with model judgement and a multilingual fail-closed fallback; card numbers and secrets always blocked.
- Decision model: Anthropic, OpenAI or any OpenAI-compatible API (BYOK), or local Ollama, with usage and spend stats.
- Live viewer with Pause/Resume and `handoff`; two-way clipboard sync; JSONL audit log; 7-day screenshot retention; stale-code reconnect notice.
- Works with MCP SDK 1.x and 2.x.
