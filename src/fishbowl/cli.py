"""`fishbowl` command line: up / down / status / view / doctor / mcp / install."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from importlib import resources

import httpx

from . import __version__, audit, control, llm
from .config import get_config


def _lima_vm() -> dict | None:
    if not shutil.which("limactl"):
        return None
    out = subprocess.run(["limactl", "list", "--json"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        try:
            vm = json.loads(line)
        except json.JSONDecodeError:
            continue
        if vm.get("name") == get_config().vm_name:
            return vm
    return None


def _intel_mac() -> bool:
    import platform

    return sys.platform == "darwin" and platform.machine() == "x86_64"


def cmd_up(_args) -> int:
    cfg = get_config()
    cfg.ensure_dirs()
    if not shutil.which("limactl"):
        print("Lima is not installed. Run:  brew install lima", file=sys.stderr)
        return 1
    vm = _lima_vm()
    if vm is None:
        template = resources.files("fishbowl").joinpath("lima.yaml")
        with resources.as_file(template) as path:
            print(f"Creating VM '{cfg.vm_name}' (first boot downloads Debian and installs a desktop, ~3-5 min)…")
            cmd = ["limactl", "start", "--name", cfg.vm_name, "--tty=false"]
            if _intel_mac():
                # Apple's Virtualization.framework is unreliable on Intel Macs; QEMU works everywhere.
                if not shutil.which("qemu-system-x86_64"):
                    print("Intel Mac detected: fishbowl uses QEMU here. Run:  brew install qemu", file=sys.stderr)
                    return 1
                cmd.append("--vm-type=qemu")
            rc = subprocess.call(cmd + [str(path)])
    elif vm.get("status") != "Running":
        rc = subprocess.call(["limactl", "start", cfg.vm_name])
    else:
        print(f"VM '{cfg.vm_name}' is already running.")
        rc = 0
    if rc == 0:
        print("\nNext:  fishbowl view   (watch the screen)   ·   fishbowl install   (add to Claude)")
    return rc


def cmd_down(_args) -> int:
    return subprocess.call(["limactl", "stop", get_config().vm_name])


def cmd_destroy(args) -> int:
    if not args.yes:
        print("This deletes the VM disk (your ~/vc/workspace stays). Re-run with --yes to confirm.")
        return 1
    return subprocess.call(["limactl", "delete", "--force", get_config().vm_name])


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

    print(f"fishbowl {__version__}")
    check("limactl installed", bool(shutil.which("limactl")), "brew install lima")
    if _intel_mac():
        check("QEMU installed (needed on Intel Macs)", bool(shutil.which("qemu-system-x86_64")), "brew install qemu")
    vm = _lima_vm()
    check(f"VM '{cfg.vm_name}' running", bool(vm and vm.get("status") == "Running"), "fishbowl up")
    check(f"Chromium CDP on {cfg.cdp_url}", _get(cfg.cdp_url + "/json/version"), "limactl shell fishbowl -- systemctl restart fishbowl-chromium")
    check(f"noVNC on 127.0.0.1:{cfg.novnc_port}", _get(f"http://127.0.0.1:{cfg.novnc_port}/vnc.html"), "systemctl restart fishbowl-novnc")
    check(f"workspace {cfg.workspace}", cfg.workspace.exists(), "fishbowl up")
    try:
        s = llm.LLMSettings.from_env()
        print(f"  · decision model: {s.provider} / {s.model}")
        if s.provider == "ollama":
            reachable = _get(s.base_url + "/api/tags")
            check(f"Ollama at {s.base_url}", reachable, "ollama serve && ollama pull " + s.model)
        else:
            check(f"{s.provider} API key set", bool(s.api_key), "export the API key")
    except llm.LLMError as e:
        check("decision model config", False, str(e))
    print("All good." if ok else "Some checks failed.")
    return 0 if ok else 1


def cmd_status(_args) -> int:
    cfg = get_config()
    vm = _lima_vm()
    st = control.read_state()
    print(json.dumps({
        "vm": vm.get("status") if vm else "absent",
        "cdp": _get(cfg.cdp_url + "/json/version"),
        "viewer_open": control.viewer_is_open(),
        "paused": st.get("paused"), "handoff": st.get("handoff"),
        "workspace": str(cfg.workspace),
    }, indent=2))
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
    exe = shutil.which("fishbowl") or f"{sys.executable} -m fishbowl"
    snippet = {"mcpServers": {"fishbowl": {"command": exe.split()[0], "args": exe.split()[1:] + ["mcp"]}}}
    if args.client == "claude-code" and shutil.which("claude"):
        return subprocess.call(["claude", "mcp", "add", "fishbowl", "--scope", "user", "--", *exe.split(), "mcp"])
    print("Add this to your MCP client config (Claude Desktop: ~/Library/Application Support/Claude/claude_desktop_config.json):")
    print(json.dumps(snippet, indent=2))
    return 0


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
    ins = sub.add_parser("install", help="register with an MCP client")
    ins.add_argument("--client", choices=["claude-code", "print"], default="claude-code")
    ins.set_defaults(fn=cmd_install)
    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(fn=cmd_mcp)
    args = p.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
