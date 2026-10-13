"""Record docs/demo.gif: a real browser_run against docs/demo/shop.html.

Every frame is a CDP screenshot of headless Chrome; the side panel shows the
engine's own decisions as they happen. The decision model is the scripted policy
from tests/fake_model.py, so the recording is reproducible and needs no API key.

    pip install pillow && python scripts/make_demo.py
"""

import asyncio
import base64
import functools
import http.server
import io
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fishbowl import audit, config, llm  # noqa: E402
from fishbowl.browser import BrowserController  # noqa: E402
from fishbowl.runner import Runner  # noqa: E402
from tests.fake_model import use_fake  # noqa: E402

W, H = 1000, 640
SHOT_W, SHOT_H = 880, 563
PANEL_W = 400
OUT = ROOT / "docs" / "demo.gif"

PLAN = [
    {"goal": "search for it", "text": "usb c hub"},
    {"goal": "click 'usb-c hub pro' expect 'HDMI'"},
    {"goal": "click 'add to cart' expect 'Added'"},
    {"goal": "click 'place order' expect 'placed'"},
]


def font(size, mono=False):
    for p in (
        ["/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc"]
        if mono
        else ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"]
    ) + ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_chrome():
    exe = next(
        p
        for p in [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            shutil.which("google-chrome") or "",
            shutil.which("chromium") or "",
        ]
        if p and Path(p).exists()
    )
    port, profile = free_port(), tempfile.mkdtemp()
    proc = subprocess.Popen(
        [exe, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         f"--window-size={W},{H}", "--hide-scrollbars", "--no-first-run", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url + "/json/version", timeout=1)
            return proc, url
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError("chrome did not start")


def serve(directory):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    port = free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Quiet, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"


def describe(event, args):
    if event == "browser_run.decide":
        op, t = args["op"], args["target"]
        if op == "DONE":
            return (f"step {args['step']}", "DONE · verified", "#3fb950")
        target = f" [{t}]" if t else ""
        return (f"step {args['step']}", f"{op}{target}  p={args['confidence']:.2f}", "#e6edf3")
    if event == "guard":
        name = args["target"].split("] ", 1)[1].split(" ", 1)[1].split(" —")[0]
        return ("guard", f"'{name}' is irreversible", "#d29922")
    if event == "browser_run.end":
        color = "#3fb950" if args["status"] == "done" else "#d29922"
        if args["status"] == "needs_approval":
            return ("result", "stopped · asks you first", color)
        return ("result", f"{args['status']} · {args['steps_done']}/4 steps", color)
    return None


CLICKS: list = []  # (x, y, t) in page pixels


def compose(shot: Image.Image, lines, status):
    canvas = Image.new("RGB", (SHOT_W + PANEL_W, SHOT_H + 56), "#0d1117")
    d = ImageDraw.Draw(canvas)
    # title bar like the fishbowl viewer
    d.ellipse((18, 18, 38, 38), fill="#f78166")  # a little fish-orange dot
    d.text((48, 18), "fishbowl", font=font(19), fill="#e6edf3")
    d.text((140, 21), "live view · 127.0.0.1", font=font(14), fill="#8b949e")
    d.text((SHOT_W + 18, 21), "browser_run", font=font(15, mono=True), fill="#79c0ff")
    k = SHOT_W / shot.width
    canvas.paste(shot.resize((SHOT_W, SHOT_H), Image.LANCZOS), (0, 56))
    for cx, cy, t in CLICKS:
        age = time.time() - t
        if age < 1.0:
            r = 14 + 22 * age
            x0, y0 = cx * k, 56 + cy * k
            d.ellipse((x0 - r, y0 - r, x0 + r, y0 + r), outline="#f78166", width=4)
    # Pause pill, bottom-left (as in the real viewer)
    x, y = 14, 56 + SHOT_H - 46
    d.rounded_rectangle((x, y, x + 196, y + 34), 9, fill="#161b22")
    d.text((x + 12, y + 8), status, font=font(14), fill="#8b949e")
    d.rounded_rectangle((x + 124, y + 5, x + 188, y + 29), 6, fill="#d29922")
    d.text((x + 134, y + 8), "Pause", font=font(13), fill="#111")
    # decision log
    px, py = SHOT_W + 18, 70
    d.text((px, py), "slow thinking · plan by Claude", font=font(13), fill="#8b949e")
    py += 22
    for i, s in enumerate(PLAN, 1):
        goal = s["goal"].split(" expect")[0]
        d.text(
            (px, py),
            f"{i}. {goal}" + (f"  “{s['text']}”" if s.get("text") else ""),
            font=font(13, True),
            fill="#c9d1d9",
        )
        py += 19
    py += 14
    d.line((px, py, SHOT_W + PANEL_W - 18, py), fill="#30363d")
    py += 12
    d.text((px, py), "fast thinking · one choice per action", font=font(13), fill="#8b949e")
    py += 24
    for tag, text, color in lines[-12:]:
        d.text((px, py), tag, font=font(13, True), fill="#79c0ff")
        wrapped = text if len(text) <= 34 else text[:33] + "…"
        d.text((px + 74, py), wrapped, font=font(13, True), fill=color)
        py += 22
    return canvas


async def main():
    tmp = Path(tempfile.mkdtemp())
    config.set_config(config.Config(home=tmp / "vc", executor="local", auto_open_viewer=False))
    use_fake(llm)
    proc, cdp = start_chrome()
    base = serve(ROOT / "docs" / "demo")
    lines: list = []
    real_log = audit.log

    def tap(tool, args=None, result=None, **extra):
        real_log(tool, args, result, **extra)
        line = describe(tool, args or {})
        if line:
            lines.append(line)

    audit.log = tap
    import fishbowl.runner as runner_mod

    runner_mod.audit.log = tap

    b = BrowserController(cdp)
    await b.new_tab("about:blank")
    await b.navigate(f"{base}/shop.html")
    frames, status = [], ["Agent driving"]
    done = asyncio.Event()

    async def recorder():
        s = await b.page()
        while not done.is_set():
            r = await s.send("Page.captureScreenshot", {"format": "png"})
            shot = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
            frames.append(compose(shot, list(lines), status[0]))
            await asyncio.sleep(0.12)

    def slow(fn):
        # Give each action a beat so the viewer can follow it, and remember where clicks land.
        @functools.wraps(fn)
        async def w(element_id, *a, **k):
            loc = await b.locate(element_id)
            if loc.get("ok"):
                CLICKS.append((loc["x"], loc["y"], time.time()))
            await asyncio.sleep(0.7)
            return await fn(element_id, *a, **k)

        return w

    for name in ("click", "type"):
        setattr(b, name, slow(getattr(b, name)))

    rec = asyncio.create_task(recorder())
    await asyncio.sleep(1.2)
    res = await Runner(b).run(PLAN, timeout_s=60)
    status[0] = "Needs you  " if res.status == "needs_approval" else "Done"
    await asyncio.sleep(0.6)
    done.set()
    await rec
    print(res.status, res.reason)
    last = frames[-1]
    frames += [last] * 30  # hold the final frame
    pal = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f in frames]
    pal[0].save(OUT, save_all=True, append_images=pal[1:], duration=140, loop=0, optimize=True)
    print(f"{OUT} · {len(frames)} frames · {OUT.stat().st_size / 1e6:.1f} MB")
    await b.close()
    proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
