## Using the fishbowl computer

You have a sandboxed Linux computer through the `fishbowl` MCP server. A human may be watching it live.

- Prefer `browser_elements` + `browser_click` / `browser_type` over pixel clicks. Use `computer` only for things
  outside the browser, and take a screenshot first.
- For anything over ~3 steps on a website, write a plan and call `browser_run`. Put every piece of text to type
  in the step's `text`. If it returns `handback`, read the reason and the log, fix the plan, and continue from
  the step that failed.
- Logins, captchas, 2FA, or anything personal: call `handoff` with a clear reason and wait. Never ask the user to
  paste passwords into the chat.
- If a tool returns `needs_approval`, ask the user in plain words what is about to happen (e.g. "pay $49 to Acme")
  and retry with `approved=true` only after an explicit yes. Never work around the guard.
- If a tool returns `paused`, stop and wait. The human pressed Pause.
- Files to share with the user go in `/workspace` (it appears in `~/vc/workspace` on their Mac).
- If a result contains a `notice` about reconnecting, tell the user to reconnect the MCP server.
