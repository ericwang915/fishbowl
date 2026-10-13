import functools
import http.server
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

from fishbowl import config, control, llm

PAGES = Path(__file__).parent / "pages"

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    shutil.which("google-chrome") or "",
    shutil.which("google-chrome-stable") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def fishbowl_home(tmp_path, monkeypatch):
    for k in (
        "FISHBOWL_LLM",
        "FISHBOWL_MODEL",
        "FISHBOWL_FAST",
        "FISHBOWL_SLOW",
        "FISHBOWL_GUARD",
        "FISHBOWL_PRICE_IN",
        "FISHBOWL_PRICE_OUT",
        "TYPESAFE_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = config.Config(home=tmp_path / "vc", executor="local", auto_open_viewer=False, control_port=_free_port())
    config.set_config(cfg)
    cfg.ensure_dirs()
    yield cfg
    control.stop_server()
    llm.set_override(None)
    llm.set_choice_override(None)


@pytest.fixture(scope="session")
def page_server():
    port = _free_port()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(PAGES))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture(scope="session")
def chrome():
    exe = next((c for c in CHROME_CANDIDATES if c and Path(c).exists()), None)
    if not exe:
        pytest.skip("Chrome/Chromium not found (set CHROME_BIN)")
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="fishbowl-chrome-")
    proc = subprocess.Popen(
        [
            exe,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--window-size=1280,800",
            "--disable-popup-blocking",
            "--no-sandbox",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url + "/json/version", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.skip("Chrome did not start")
    yield url
    proc.kill()
    shutil.rmtree(profile, ignore_errors=True)


@pytest.fixture
async def browser(chrome):
    from fishbowl.browser import BrowserController

    b = BrowserController(chrome)
    # fresh working tab per test
    await b.new_tab("about:blank")
    yield b
    for t in await b.tabs():
        if len(await b.tabs()) > 1:
            try:
                await b.close_tab(t["id"])
            except Exception:
                pass
    await b.close()
