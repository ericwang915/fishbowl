# Changelog

## Unreleased

### Security
- **Clipboard bridge rewritten: in memory, never on disk.** Older versions mirrored the clipboard through a plaintext file in `~/vc/workspace/.fishbowl/`, where a password pasted during a login could stay, readable by the agent. Now a long-lived `limactl shell` pipe carries it. Mac → VM only flows while the human controls the screen (handoff or Pause), and the VM clipboard is wiped when control returns. The legacy file and VM service are removed automatically. `clipboard_get` redacts card numbers and API keys.

### Fast and slow thinking
- **Fast mode is optional and turns on with Jev** (`TYPESAFE_API_KEY`), or with an explicit `FISHBOWL_FAST`. Without it, fishbowl runs normally: `browser_run` replies `fast_mode_off` and points to the step-by-step tools, the guard uses keyword rules, `judge_reply` uses the planner model (or replies `no_model`), and the server instructions tell the agent which mode it is in. Anthropic and OpenAI keys alone no longer turn on fast mode; they turn on the planner.
- `browser_run` takes a `plan`, a `goal`, or both. With a goal, a **slow** planner model (Claude Sonnet by default) writes the plan and replans up to `max_replans` times after a handback, starting from the current page. The planner asks for missing info instead of inventing personal data.
- The **fast** executor answers multiple-choice questions in one request: the operation plus a speculative target per operation, each offering only compatible elements. Dropdown options are chosen directly. `TYPE_TEXT` is only offered when the plan has text.
- **Jev (TypeSafe) provider** for the fast role (`TYPESAFE_API_KEY`), using its native multiple-choice API with calibrated confidence. Anthropic, OpenAI and Ollama answer the same questions through enum-constrained JSON schemas. Answers outside the offered options are refused.
- One `Runtime.evaluate` per observation (elements, visible text, overlays, marker, freshness guards). A freshness check runs in the same round trip as the hit test, and stale decisions are re-decided rather than executed.
- `DONE` claims are verified independently. Handbacks carry a compact page `observation` and `timing` (observe, decide and act time; model and CDP calls).
- The guard and `judge_reply` are multiple-choice questions too, so they also run on Jev.
- New config: `FISHBOWL_FAST[_MODEL]` and `FISHBOWL_SLOW[_MODEL]`. `FISHBOWL_LLM` and `FISHBOWL_MODEL` still apply to both roles. `fishbowl doctor` shows both roles.
- 27-task suite: −28% CDP round trips and −22% wall time compared with 0.1.0.

### Clients

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
