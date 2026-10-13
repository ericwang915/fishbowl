"""Tiny Chrome DevTools Protocol client (one websocket per page target)."""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

import httpx
import websockets


class CDPError(RuntimeError):
    pass


class Session:
    def __init__(self, ws, target_id: str):
        self.ws = ws
        self.target_id = target_id
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: list[tuple[str, asyncio.Queue]] = []
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def connect(cls, ws_url: str, target_id: str) -> Session:
        ws = await websockets.connect(ws_url, max_size=None, ping_interval=None, open_timeout=10)
        return cls(ws, target_id)

    @property
    def closed(self) -> bool:
        return self._reader.done()

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(CDPError(msg["error"].get("message", str(msg["error"]))))
                        else:
                            fut.set_result(msg.get("result", {}))
                else:
                    for method, q in self._listeners:
                        if method == msg.get("method"):
                            q.put_nowait(msg.get("params", {}))
        except Exception:
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CDPError("connection closed"))

    async def send(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        if self.closed:
            raise CDPError("connection closed")
        i = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        await self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(i, None)

    def listen(self, method: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._listeners.append((method, q))
        return q

    async def evaluate(self, expr: str, timeout: float = 30) -> Any:
        r = await self.send(
            "Runtime.evaluate",
            {
                "expression": expr,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise CDPError(d.get("exception", {}).get("description") or d.get("text", "JS error"))
        return r.get("result", {}).get("value")

    async def close(self) -> None:
        await self.ws.close()
        self._reader.cancel()


class Browser:
    """HTTP endpoints for target management + cached page sessions."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self._sessions: dict[str, Session] = {}

    async def _http(self, method: str, path: str) -> Any:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.request(method, self.base + path)
            r.raise_for_status()
            try:
                return r.json()
            except json.JSONDecodeError:
                return r.text

    async def version(self) -> dict:
        return await self._http("GET", "/json/version")

    async def pages(self) -> list[dict]:
        targets = await self._http("GET", "/json/list")
        return [t for t in targets if t.get("type") == "page" and not t.get("url", "").startswith("devtools://")]

    async def new_page(self, url: str = "about:blank") -> dict:
        from urllib.parse import quote

        return await self._http("PUT", "/json/new?" + quote(url, safe=":/?&=#%"))

    async def activate(self, target_id: str) -> None:
        await self._http("GET", f"/json/activate/{target_id}")

    async def close_page(self, target_id: str) -> None:
        s = self._sessions.pop(target_id, None)
        if s:
            await s.close()
        await self._http("GET", f"/json/close/{target_id}")

    async def session(self, target_id: str) -> Session:
        s = self._sessions.get(target_id)
        if s and not s.closed:
            return s
        page = next((p for p in await self.pages() if p["id"] == target_id), None)
        if not page:
            raise CDPError(f"tab {target_id} is gone")
        s = await Session.connect(page["webSocketDebuggerUrl"], target_id)
        await s.send("Page.enable")
        await s.send("Runtime.enable")
        self._sessions[target_id] = s
        return s

    async def close(self) -> None:
        for s in list(self._sessions.values()):
            await s.close()
        self._sessions.clear()
