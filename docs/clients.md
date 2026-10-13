# Using fishbowl from your agent

fishbowl is a standard MCP server over stdio, so any MCP client can drive it. `fishbowl install` registers it
with every client it finds on your machine:

```bash
fishbowl install                    # all detected clients
fishbowl install --client codex     # or: claude-code | claude-desktop | cursor
fishbowl install --client print     # just print the JSON snippet
fishbowl install --remove           # unregister everywhere
```

The installer is idempotent, backs up any file it edits (`*.fishbowl.bak`), and uses the absolute path of the
server, so GUI apps with a minimal `PATH` still find it. **API keys are never written to config files.**

| Client | What `fishbowl install` does | Model keys come from |
|---|---|---|
| **Claude Code** | `claude mcp add fishbowl --scope user -- <fishbowl> mcp` | your shell (inherited) |
| **Codex CLI / IDE** | adds `[mcp_servers.fishbowl]` to `~/.codex/config.toml` with `tool_timeout_sec = 900` and `env_vars` forwarding | your shell, via `env_vars` |
| **Claude Desktop** | merges into `~/Library/Application Support/Claude/claude_desktop_config.json` | add an `"env"` block yourself, or use Ollama |
| **Cursor** | merges into `~/.cursor/mcp.json` | add an `"env"` block yourself, or use Ollama |

## Claude Code

```bash
fishbowl install --client claude-code
claude          # then: /mcp  → fishbowl should be "connected"
```

Try: *"Use fishbowl to open news.ycombinator.com and summarise the top 3 posts."*

Long `browser_run` calls and `handoff` (which waits for you) send MCP progress notifications. If your setup still
times out, raise `MCP_TOOL_TIMEOUT` (milliseconds) before starting `claude`.

## Codex

```bash
fishbowl install --client codex
codex           # then: /mcp  → fishbowl is listed with 25 tools
```

What gets written:

```toml
[mcp_servers.fishbowl]
command = "/path/to/fishbowl"
args = ["mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 900        # Codex defaults to 60 s; browser_run and handoff can run longer
env_vars = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OLLAMA_HOST", "FISHBOWL_LLM", …]
```

Codex only passes the environment variables listed in `env_vars` to MCP servers, so your keys stay in your
shell. To skip approval prompts for read-only tools, add for example:

```toml
[mcp_servers.fishbowl.tools.browser_read]
approval_mode = "approve"
```

## Teach the agent the house rules

Drop [`examples/AGENTS.md`](../examples/AGENTS.md) into your project (Codex reads `AGENTS.md`; for Claude Code,
copy it into `CLAUDE.md`). It tells the agent to prefer `browser_*` over pixel clicks, plan with `browser_run`,
use `handoff` for logins, and never retry an action the guard refused.
