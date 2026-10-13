# Security model

fishbowl assumes the agent driving it can be wrong, confused, or manipulated by the pages it reads
(prompt injection). The design goal: **when that happens, the damage stays inside the bowl, and a human sees it.**

## Boundaries

| Layer | What stops what |
|---|---|
| VM | The agent runs in a Lima VM (macOS, Linux) or a WSL2 distro (Windows), not on your machine. A broken or hijacked session can trash the VM; `fishbowl destroy --yes && fishbowl up` resets it. |
| Files | The only host folder mounted into the VM is `~/vc/workspace` (at `/workspace`). Your home folder, SSH keys and browser profiles are not visible. |
| Windows (WSL2) | WSL can normally see every Windows drive and start Windows programs. fishbowl writes `/etc/wsl.conf` with `[automount] enabled=false` and `[interop] enabled=false`, mounts only `~\vc\workspace` (via `/etc/fstab`), and runs the desktop and every command as the unprivileged `fishbowl` user. WSL2 forwards ports to `localhost` only. |
| Network | CDP (9222) and noVNC (6080) are forwarded to `127.0.0.1` only. Every other guest port is explicitly ignored, so nothing in the VM is reachable from your LAN. The viewer/control server binds `127.0.0.1` and rejects cross-origin writes. |
| Typing | Card numbers (Luhn-validated) and API keys/tokens (Anthropic, OpenAI, AWS, GitHub, Slack, Google, GitLab, private keys, JWTs) are refused by `computer`, `browser_type`, `clipboard_set`, `chat_ask` and `browser_run`, regardless of what the plan says. |
| Clipboard | Bridged in memory over `limactl shell`; nothing is written to disk. VM → Mac always flows. Mac → VM only flows while the human controls the screen (a `handoff` or Pause), so passwords copied on your Mac never reach the VM while the agent drives. When control returns, the VM clipboard is cleared if it still holds Mac content. `clipboard_get` redacts card numbers and API keys. |
| Commit actions | Clicks and Enter go through the approval guard. Irreversible or public actions (pay, send, delete, submit, publish, account changes) need a human: via MCP elicitation if the client supports it, otherwise the tool returns `needs_approval`. If the decision model is unreachable, a multilingual keyword check takes over: the guard **fails closed**. |
| `browser_run` | The local model chooses *where* to act, never *what to type*: text comes only from the plan. It hands control back after 3 no-op actions, a login wall, low confidence, a pause or a timeout. |
| Oversight | Live noVNC view, one-click Pause (all screen-touching tools refuse while paused), `handoff` for logins and captchas, and an append-only JSONL audit log of every call with secrets redacted. Screenshots are kept 7 days. |

## Logging into sites (Gmail, X, …)

Use `handoff`: you type the credentials in the live view yourself, and the agent never sees them. Sessions persist
in the VM's Chromium profile until `fishbowl destroy`. Prefer dedicated accounts for agent work. Google may refuse
sign-in from browsers it considers automated; that is outside fishbowl's control.

## Not in scope

- The VM has normal outbound internet access. If you need egress filtering, add it in the VM (e.g. `nftables`).
- Sessions you log into inside the VM (cookies in its Chromium profile) are usable by the agent. Log into only what the task needs.
- The guard is a safety net, not a guarantee. Keep the viewer open for anything that matters.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
