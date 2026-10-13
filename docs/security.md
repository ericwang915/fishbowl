# Security model

fishbowl assumes the agent driving it can be wrong, confused, or manipulated by the pages it reads
(prompt injection). The design goal: **when that happens, the damage stays inside the bowl, and a human sees it.**

## Boundaries

| Layer | What stops what |
|---|---|
| VM | The agent runs in a Lima VM, not on your Mac. A broken or hijacked session can trash the VM; `fishbowl destroy --yes && fishbowl up` resets it. |
| Files | The only host folder mounted into the VM is `~/vc/workspace` (at `/workspace`). Your home folder, SSH keys and browser profiles are not visible. |
| Network | CDP (9222) and noVNC (6080) are forwarded to `127.0.0.1` only. Every other guest port is explicitly ignored, so nothing in the VM is reachable from your LAN. The viewer/control server binds `127.0.0.1` and rejects cross-origin writes. |
| Typing | Card numbers (Luhn-validated) and API keys/tokens (Anthropic, OpenAI, AWS, GitHub, Slack, Google, GitLab, private keys, JWTs) are refused by `computer`, `browser_type`, `clipboard_set`, `chat_ask` and `browser_run`, regardless of what the plan says. |
| Commit actions | Clicks and Enter go through the approval guard. Irreversible or public actions (pay, send, delete, submit, publish, account changes) need a human: via MCP elicitation if the client supports it, otherwise the tool returns `needs_approval`. If the decision model is unreachable, a multilingual keyword check takes over: the guard **fails closed**. |
| `browser_run` | The local model chooses *where* to act, never *what to type*: text comes only from the plan. It hands control back after 3 no-op actions, a login wall, low confidence, a pause or a timeout. |
| Oversight | Live noVNC view, one-click Pause (all screen-touching tools refuse while paused), `handoff` for logins and captchas, and an append-only JSONL audit log of every call with secrets redacted. Screenshots are kept 7 days. |

## Not in scope

- The VM has normal outbound internet access. If you need egress filtering, add it in the VM (e.g. `nftables`).
- Sessions you log into inside the VM (cookies in its Chromium profile) are usable by the agent. Log into only what the task needs.
- The guard is a safety net, not a guarantee. Keep the viewer open for anything that matters.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
