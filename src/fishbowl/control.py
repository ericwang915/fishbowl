"""Human-in-the-loop control: pause/resume, handoff, and the live viewer page.

State lives in a JSON file under ~/vc/state so the viewer server and any MCP
process agree on it. The viewer page (served on 127.0.0.1) embeds noVNC and
posts heartbeats, which is how we know not to open a second window.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any

from . import hostos
from .config import get_config

HEARTBEAT_FRESH_S = 6.0
_last_open_attempt = 0.0
_server: ThreadingHTTPServer | None = None


def _state_path():
    cfg = get_config()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg.state_dir / "control.json"


def read_state() -> dict[str, Any]:
    try:
        return json.loads(_state_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"paused": False, "handoff": None, "heartbeat": 0.0}


def write_state(**changes: Any) -> dict[str, Any]:
    st = read_state()
    st.update(changes)
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(_state_path())
    return st


def is_paused() -> bool:
    st = read_state()
    return bool(st.get("paused") or st.get("handoff"))


def viewer_is_open() -> bool:
    return time.time() - float(read_state().get("heartbeat", 0)) < HEARTBEAT_FRESH_S


def viewer_url() -> str:
    return f"http://127.0.0.1:{get_config().control_port}/"


def open_viewer(force: bool = False) -> bool:
    """Open the viewer in the Mac browser unless it is already open. Returns True if opened."""
    global _last_open_attempt
    if not force and (viewer_is_open() or time.time() - _last_open_attempt < 20):
        return False
    _last_open_attempt = time.time()
    ensure_server()
    return hostos.open_url(viewer_url())


def maybe_auto_open() -> None:
    if get_config().auto_open_viewer:
        open_viewer()


async def handoff(reason: str, timeout: float = 600, on_tick=None) -> dict[str, Any]:
    """Give the screen to the human and wait until they click Done (or timeout).
    `on_tick(waited_s, timeout_s)` is awaited every few seconds while waiting."""
    write_state(handoff={"reason": reason, "since": time.time()})
    open_viewer(force=not viewer_is_open())
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
        st = read_state()
        if not st.get("handoff"):
            return {"status": "returned", "note": st.get("handoff_note", "")}
        waited = time.time() - start
        if on_tick and int(waited) % 5 == 0:
            await on_tick(waited, timeout)
        await asyncio.sleep(1)
    write_state(handoff=None)
    return {"status": "timeout", "waited_s": timeout}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = resources.files("fishbowl").joinpath("viewer.html").read_text()
            html = html.replace("__NOVNC_PORT__", str(get_config().novnc_port))
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif self.path == "/state":
            self._send(200, json.dumps(read_state()).encode())
        else:
            self._send(404, b"{}")

    def do_POST(self):
        # Only accept same-origin requests from the viewer page.
        origin = self.headers.get("Origin")
        if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
            self._send(403, b"{}")
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if self.path == "/heartbeat":
            st = write_state(heartbeat=time.time())
        elif self.path == "/pause":
            st = write_state(paused=True)
        elif self.path == "/resume":
            st = write_state(paused=False)
        elif self.path == "/handoff/done":
            st = write_state(handoff=None, handoff_note=str(body.get("note", ""))[:500])
        else:
            self._send(404, b"{}")
            return
        self._send(200, json.dumps(st).encode())


def ensure_server() -> bool:
    """Start the viewer/control server on 127.0.0.1 if no one else is serving it."""
    global _server
    if _server is not None:
        return True
    try:
        _server = ThreadingHTTPServer(("127.0.0.1", get_config().control_port), _Handler)
    except OSError:
        return False  # another fishbowl process already serves it; state file is shared
    threading.Thread(target=_server.serve_forever, daemon=True, name="fishbowl-viewer").start()
    return True


def stop_server() -> None:
    global _server
    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None
