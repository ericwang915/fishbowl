# Changelog

## 0.1.0 (2026-10-13)

First public release.

- Lima VM template: Debian 12, Xvfb 1280×800, openbox, Chromium, x11vnc + noVNC, xdotool, xclip. Only `~/vc/workspace` is mounted; all ports are bound to 127.0.0.1.
- 25 MCP tools (stdio): desktop, browser over CDP, `browser_run`, chatbot QA, human in the loop, audit.
- `browser_run` fast executor: one structured decision per action, a separate completion check, text only from the plan, auto handback, working-tab pinning.
- Approval guardrail with model judgement and a multilingual fail-closed fallback; card numbers and secrets always blocked.
- Decision model: Anthropic, OpenAI or any OpenAI-compatible API (BYOK), or local Ollama, with usage and spend stats.
- Live viewer with Pause/Resume and `handoff`; two-way clipboard sync; JSONL audit log; 7-day screenshot retention; stale-code reconnect notice.
- Works with MCP SDK 1.x and 2.x.
