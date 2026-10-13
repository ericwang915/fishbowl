"""Real stdio handshake, the way Claude Code and Codex connect: spawn `fishbowl mcp` and talk MCP to it."""

import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_stdio_handshake_and_calls(tmp_path):
    env = {
        **os.environ,
        "FISHBOWL_HOME": str(tmp_path / "vc"),
        "FISHBOWL_EXECUTOR": "local",
        "FISHBOWL_AUTO_OPEN": "0",
        "FISHBOWL_CONTROL_PORT": "0",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "fishbowl", "mcp"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        info = getattr(init, "serverInfo", None) or init.server_info  # mcp 1.x / 2.x
        assert info.name == "fishbowl"
        tools = (await session.list_tools()).tools
        assert len(tools) == 25
        r = await session.call_tool("shell", {"command": "echo from-the-bowl"})
        text = "".join(getattr(c, "text", "") for c in r.content)
        assert "from-the-bowl" in text
        r = await session.call_tool("control_status", {})
        assert "paused" in "".join(getattr(c, "text", "") for c in r.content)
    assert list((tmp_path / "vc" / "audit").glob("*.jsonl"))  # calls were audited
