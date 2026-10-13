"""VM backends: Lima on macOS and Linux, WSL2 on Windows. Both run the same desktop.

The desktop itself (packages + systemd units) lives in provision_system.sh and
provision_desktop.sh, so the two backends cannot drift apart.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import PureWindowsPath

import httpx

from . import hostos
from .config import get_config


def _resource(name: str) -> str:
    return resources.files("fishbowl").joinpath(name).read_text()


def _indent(text: str, spaces: int = 6) -> str:
    pad = " " * spaces
    return "\n".join((pad + line) if line.strip() else "" for line in text.rstrip().splitlines()).lstrip()


def lima_template() -> str:
    """The Lima YAML with the shared provisioning scripts filled in."""
    t = _resource("lima.yaml")
    return t.replace("__PROVISION_SYSTEM__", _indent(_resource("provision_system.sh"))).replace(
        "__PROVISION_DESKTOP__", _indent(_resource("provision_desktop.sh"))
    )


def cdp_ready(timeout: float = 0) -> bool:
    url = get_config().cdp_url + "/json/version"
    deadline = time.time() + timeout
    while True:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(2)


class Backend:
    name = "?"

    def status(self) -> str:  # "Running" | "Stopped" | "absent"
        raise NotImplementedError

    def up(self) -> int:
        raise NotImplementedError

    def down(self) -> int:
        raise NotImplementedError

    def destroy(self) -> int:
        raise NotImplementedError

    def checks(self) -> list[tuple[str, bool, str]]:
        """(name, passed, hint) for `fishbowl doctor`."""
        return []

    def ensure_running(self) -> None:
        """Called when the MCP server starts."""


# ============================================================================ Lima


class LimaBackend(Backend):
    name = "lima"

    def _limactl(self) -> str | None:
        from .executor import limactl_path

        p = limactl_path()
        return p if shutil.which(p) or os.path.exists(p) else None

    def _vm(self) -> dict | None:
        exe = self._limactl()
        if not exe:
            return None
        out = subprocess.run([exe, "list", "--json"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            try:
                vm = json.loads(line)
            except json.JSONDecodeError:
                continue
            if vm.get("name") == get_config().vm_name:
                return vm
        return None

    def status(self) -> str:
        vm = self._vm()
        return vm.get("status", "Stopped") if vm else "absent"

    def vm_type(self) -> str | None:
        forced = os.environ.get("FISHBOWL_VM_TYPE")
        if forced:
            return forced
        # Apple's Virtualization.framework is unreliable on Intel Macs; QEMU works everywhere.
        if hostos.kind() == "macos" and hostos.arch() == "x86_64":
            return "qemu"
        return None  # Lima's default: vz on Apple Silicon, qemu (+KVM) on Linux

    def mount_type(self, vm_type: str | None) -> str | None:
        forced = os.environ.get("FISHBOWL_MOUNT_TYPE")
        if forced:
            return forced
        # QEMU's default (9p) needs kernel modules Debian's cloud kernel lacks; sshfs works everywhere.
        if vm_type == "qemu" or (vm_type is None and hostos.kind() == "linux"):
            return "reverse-sshfs"
        return None  # vz: virtiofs

    def up(self) -> int:
        cfg = get_config()
        cfg.ensure_dirs()
        exe = self._limactl()
        if not exe:
            hint = "brew install lima" if hostos.kind() == "macos" else "see https://lima-vm.io/docs/installation/"
            print(f"Lima is not installed: {hint}", file=sys.stderr)
            return 1
        state = self.status()
        if state == "Running":
            print(f"VM '{cfg.vm_name}' is already running.")
            return 0
        if state != "absent":
            return subprocess.call([exe, "start", cfg.vm_name])
        vm_type = self.vm_type()
        if vm_type == "qemu" and not shutil.which(f"qemu-system-{'aarch64' if hostos.arch() == 'arm64' else 'x86_64'}"):
            hint = "brew install qemu" if hostos.kind() == "macos" else "install qemu-system for your distro"
            print(f"This machine needs QEMU for the VM: {hint}", file=sys.stderr)
            return 1
        path = cfg.state_dir / "lima-fishbowl.yaml"
        path.write_text(lima_template())
        print(f"Creating VM '{cfg.vm_name}' on {hostos.describe()} (first boot downloads Debian, ~3-5 min)…")
        cmd = [exe, "start", "--name", cfg.vm_name, "--tty=false"]
        if vm_type:
            cmd.append(f"--vm-type={vm_type}")
        if mount := self.mount_type(vm_type):
            cmd.append(f"--mount-type={mount}")
        return subprocess.call(cmd + [str(path)])

    def down(self) -> int:
        return subprocess.call([self._limactl() or "limactl", "stop", get_config().vm_name])

    def destroy(self) -> int:
        return subprocess.call([self._limactl() or "limactl", "delete", "--force", get_config().vm_name])

    def checks(self):
        out = [("limactl installed", bool(self._limactl()), "brew install lima")]
        if self.vm_type() == "qemu":
            q = f"qemu-system-{'aarch64' if hostos.arch() == 'arm64' else 'x86_64'}"
            out.append((f"QEMU installed ({q})", bool(shutil.which(q)), "brew install qemu"))
        out.append((f"VM '{get_config().vm_name}' running", self.status() == "Running", "fishbowl up"))
        return out


# ============================================================================= WSL

WSL_USER = "fishbowl"


def wsl_conf() -> str:
    """Isolation for the WSL distro: no Windows drives, no Windows programs, systemd on."""
    return f"""# Written by fishbowl. The VM must not see your Windows drives or start Windows programs.
[boot]
systemd=true

[automount]
enabled=false
mountFsTab=true

[interop]
enabled=false
appendWindowsPath=false

[user]
default={WSL_USER}
"""


def drvfs_path(path) -> str:
    """C:\\Users\\Ada Lovelace\\vc\\workspace -> C:/Users/Ada\\040Lovelace/vc/workspace (fstab-safe)."""
    p = str(PureWindowsPath(str(path))).replace("\\", "/")
    return p.replace(" ", "\\040")


def wsl_fstab(workspace) -> str:
    return f"{drvfs_path(workspace)} /workspace drvfs defaults,uid=1000,gid=1000,metadata 0 0\n"


def wsl_setup_script(workspace) -> str:
    return f"""set -eux
id {WSL_USER} >/dev/null 2>&1 || useradd -m -s /bin/bash -u 1000 {WSL_USER}
mkdir -p /workspace
cat > /etc/wsl.conf <<'FISHBOWL_EOF'
{wsl_conf()}FISHBOWL_EOF
cat > /etc/fstab <<'FISHBOWL_EOF'
{wsl_fstab(workspace)}FISHBOWL_EOF
"""


def wsl_provision_script() -> str:
    """Run as root inside the distro after systemd is up: same scripts as the Lima template."""
    return (
        'sudo() { "$@"; }  # we are root already\n'
        f"(\n{_resource('provision_system.sh')}\n)\n"
        f"export FB_USER={WSL_USER} FB_HOME=/home/{WSL_USER}\n"
        f"(\n{_resource('provision_desktop.sh')}\n)\n"
    )


def _decode(raw: bytes) -> str:
    # wsl.exe writes UTF-16LE unless WSL_UTF8=1 is honoured.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:64]:
        return raw.decode("utf-16-le", "replace").lstrip("\ufeff")
    return raw.decode("utf-8", "replace")


def parse_wsl_list(text: str) -> dict[str, str]:
    """Parse `wsl -l -v` into {name: state}."""
    out = {}
    for line in text.replace("\x00", "").splitlines()[1:]:
        parts = line.replace("*", " ").split()
        if len(parts) >= 3:
            out[parts[0]] = parts[1]
    return out


class WSLBackend(Backend):
    name = "wsl"

    def __init__(self, runner=None):
        self.runner = runner or self._run

    @staticmethod
    def _run(args: list[str], stdin: str | None = None, timeout: float = 1800) -> tuple[int, str]:
        env = {**os.environ, "WSL_UTF8": "1"}
        p = subprocess.run(
            ["wsl.exe", *args], input=stdin.encode() if stdin else None, capture_output=True, env=env, timeout=timeout
        )
        return p.returncode, _decode(p.stdout) + _decode(p.stderr)

    def _distros(self) -> dict[str, str]:
        code, out = self.runner(["--list", "--verbose"])
        return parse_wsl_list(out) if code == 0 else {}

    def status(self) -> str:
        return self._distros().get(get_config().vm_name, "absent")

    def _keepalive(self) -> None:
        # WSL stops idle distros; a sleeping process keeps the desktop up while fishbowl is in use.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            ["wsl.exe", "--distribution", get_config().vm_name, "--user", "root", "--exec", "sleep", "infinity"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )

    def up(self) -> int:
        cfg = get_config()
        cfg.ensure_dirs()
        name = cfg.vm_name
        if not shutil.which("wsl.exe") and self.runner is self._run:
            print("WSL is not installed. In an admin PowerShell run:  wsl --install   then reboot.", file=sys.stderr)
            return 1
        if self.status() == "absent":
            print(f"Creating WSL distro '{name}' on {hostos.describe()} (downloads Debian, ~3-5 min)…")
            code, out = self.runner(["--install", "Debian", "--name", name, "--no-launch"])
            if code != 0:
                print(out.strip(), file=sys.stderr)
                print("Needs WSL 2.4.4 or newer: run  wsl --update", file=sys.stderr)
                return 1
        code, out = self.runner(
            ["--distribution", name, "--user", "root", "--exec", "bash", "-s"], stdin=wsl_setup_script(cfg.workspace)
        )
        if code != 0:
            print(out, file=sys.stderr)
            return code
        self.runner(["--terminate", name])  # apply wsl.conf: systemd on, drives and interop off
        print("Installing the desktop (Xvfb, Chromium, noVNC)…")
        code, out = self.runner(
            ["--distribution", name, "--user", "root", "--exec", "bash", "-s"], stdin=wsl_provision_script()
        )
        if code != 0:
            print(out[-4000:], file=sys.stderr)
            return code
        if self.runner is self._run:
            self._keepalive()
            if not cdp_ready(timeout=120):
                print("The desktop did not come up. Try: fishbowl doctor", file=sys.stderr)
                return 1
        print("fishbowl is up.  Watch it:  fishbowl view")
        return 0

    def down(self) -> int:
        return self.runner(["--terminate", get_config().vm_name])[0]

    def destroy(self) -> int:
        return self.runner(["--unregister", get_config().vm_name])[0]

    def ensure_running(self) -> None:
        if self.status() != "absent" and not cdp_ready():
            self._keepalive()

    def checks(self):
        return [
            ("WSL installed", bool(shutil.which("wsl.exe")), "wsl --install (admin PowerShell), then reboot"),
            (f"WSL distro '{get_config().vm_name}' running", self.status() == "Running", "fishbowl up"),
        ]


def get_backend() -> Backend:
    return WSLBackend() if get_config().executor == "wsl" else LimaBackend()
