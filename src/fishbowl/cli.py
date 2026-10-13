"""`fishbowl` command line: up / down / status / view / doctor / mcp / install."""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from . import __version__, audit, control, hostos, llm
from .config import get_config
from .vm import get_backend


def cmd_up(_args) -> int:
    rc = get_backend().up()
    if rc == 0:
        print("\nNext:  fishbowl view   (watch the screen)   ·   fishbowl install   (connect your agent)")
    return rc


def cmd_down(_args) -> int:
    return get_backend().down()


def cmd_destroy(args) -> int:
    if not args.yes:
        print("This deletes the VM disk (your ~/vc/workspace stays). Re-run with --yes to confirm.")
        return 1
    return get_backend().destroy()


def _get(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code < 500
    except httpx.HTTPError:
        return False


def cmd_doctor(_args) -> int:
    cfg = get_config()
    ok = True

    def check(name: str, passed: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  → {hint}" if hint and not passed else ""))

    backend = get_backend()
    print(f"fishbowl {__version__} · {hostos.describe()} · {backend.name}")
    for name, passed, hint in backend.checks():
        check(name, passed, hint)
    check(
        f"Chromium CDP on {cfg.cdp_url}",
        _get(cfg.cdp_url + "/json/version"),
        "fishbowl down && fishbowl up",
    )
    check(
        f"noVNC on 127.0.0.1:{cfg.novnc_port}",
        _get(f"http://127.0.0.1:{cfg.novnc_port}/vnc.html"),
        "systemctl restart fishbowl-novnc",
    )
    check(f"workspace {cfg.workspace}", cfg.workspace.exists(), "fishbowl up")
    for role, what in (("fast", "executes browser_run, guard, judge"), ("slow", "plans browser_run goals")):
        try:
            s = llm.LLMSettings.from_env(role)
            print(f"  · {role} thinking: {s.provider} / {s.model}  ({what})")
            if s.provider == "ollama":
                reachable = _get(s.base_url + "/api/tags")
                check(f"Ollama at {s.base_url}", reachable, "ollama serve && ollama pull " + s.model)
            else:
                check(f"{s.provider} API key set", bool(s.api_key), f"export {llm.KEY_ENV[s.provider]}")
        except llm.NotConfigured as e:
            print(f"  · {role} thinking: off — {str(e).split(': ', 1)[1]}")  # optional, not a failure
        except llm.LLMError as e:
            check(f"{role} model config", False, str(e))
    print("All good." if ok else "Some checks failed.")
    return 0 if ok else 1


def cmd_status(_args) -> int:
    cfg = get_config()
    st = control.read_state()
    print(
        json.dumps(
            {
                "host": hostos.describe(),
                "backend": get_backend().name,
                "vm": get_backend().status(),
                "cdp": _get(cfg.cdp_url + "/json/version"),
                "viewer_open": control.viewer_is_open(),
                "paused": st.get("paused"),
                "handoff": st.get("handoff"),
                "workspace": str(cfg.workspace),
            },
            indent=2,
        )
    )
    return 0


def cmd_view(_args) -> int:
    get_config().ensure_dirs()
    serving = control.ensure_server()
    control.open_viewer(force=True)
    print(f"Viewer: {control.viewer_url()}")
    if serving:
        print("Serving the viewer here (Ctrl-C to stop). The MCP server serves it too while it runs.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_logs(args) -> int:
    for e in audit.tail(args.n):
        print(json.dumps(e, ensure_ascii=False))
    return 0


def cmd_stats(_args) -> int:
    print(json.dumps(llm.stats(), indent=2))
    return 0


def cmd_install(args) -> int:
    from . import install

    if args.client == "print":
        print("Add this to your MCP client's config:")
        print(install.snippet())
        return 0
    results = install.install(args.client, remove=args.remove)
    for name, msg in results.items():
        print(f"  {name:15s} {msg}")
    if not args.remove and any("registered" in m for m in results.values()):
        print('\nRestart the client (or reconnect MCP), then ask: "Use fishbowl to open example.com".')
    return 1 if any(m.startswith("failed") for m in results.values()) else 0


def cmd_mcp(_args) -> int:
    from .server import main

    main()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fishbowl", description="A watchable Linux computer for AI agents.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up", help="create/start the VM").set_defaults(fn=cmd_up)
    sub.add_parser("down", help="stop the VM").set_defaults(fn=cmd_down)
    d = sub.add_parser("destroy", help="delete the VM")
    d.add_argument("--yes", action="store_true")
    d.set_defaults(fn=cmd_destroy)
    sub.add_parser("status", help="VM / viewer / pause state").set_defaults(fn=cmd_status)
    sub.add_parser("doctor", help="health checks").set_defaults(fn=cmd_doctor)
    sub.add_parser("view", help="open the live viewer").set_defaults(fn=cmd_view)
    lg = sub.add_parser("logs", help="tail the audit log")
    lg.add_argument("-n", type=int, default=30)
    lg.set_defaults(fn=cmd_logs)
    sub.add_parser("stats", help="decision-model usage and spend").set_defaults(fn=cmd_stats)
    ins = sub.add_parser("install", help="register with MCP clients (Claude Code, Codex, Claude Desktop, Cursor)")
    ins.add_argument(
        "--client",
        choices=["all", "claude-code", "codex", "claude-desktop", "cursor", "print"],
        default="all",
        help="default: every client found on this machine",
    )
    ins.add_argument("--remove", action="store_true", help="unregister instead")
    ins.set_defaults(fn=cmd_install)
    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(fn=cmd_mcp)
    args = p.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
