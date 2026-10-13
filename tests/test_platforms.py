"""macOS (Apple Silicon + Intel), Windows (x64 + ARM, via WSL2) and Linux: detection, backends, isolation."""

import shutil
import subprocess
import sys

import pytest
import yaml

from fishbowl import config, hostos, install, vm
from fishbowl.executor import LimaExecutor, WSLExecutor, get_executor


@pytest.fixture
def host(monkeypatch):
    def set_host(plat, machine):
        monkeypatch.setattr(hostos.sys, "platform", plat)
        monkeypatch.setattr(hostos.platform, "machine", lambda: machine)

    return set_host


@pytest.mark.parametrize(
    "plat,machine,kind,arch,desc,backend",
    [
        ("darwin", "arm64", "macos", "arm64", "macOS · Apple Silicon", "lima"),
        ("darwin", "x86_64", "macos", "x86_64", "macOS · Intel", "lima"),
        ("win32", "AMD64", "windows", "x86_64", "Windows · x86_64", "wsl"),
        ("win32", "ARM64", "windows", "arm64", "Windows · arm64", "wsl"),
        ("linux", "x86_64", "linux", "x86_64", "Linux · x86_64", "lima"),
        ("linux", "aarch64", "linux", "arm64", "Linux · arm64", "lima"),
    ],
)
def test_host_detection(host, plat, machine, kind, arch, desc, backend):
    host(plat, machine)
    assert (hostos.kind(), hostos.arch(), hostos.describe(), hostos.default_backend()) == (kind, arch, desc, backend)


def test_executor_follows_the_host(host, monkeypatch):
    monkeypatch.delenv("FISHBOWL_EXECUTOR", raising=False)
    host("win32", "AMD64")
    assert config.Config().executor == "wsl"
    host("darwin", "arm64")
    assert config.Config().executor == "lima"


def test_executor_argv(fishbowl_home):
    fishbowl_home.executor = "wsl"
    argv = get_executor().argv("echo hi")
    assert isinstance(get_executor(), WSLExecutor)
    assert argv[:7] == ["wsl.exe", "--distribution", "fishbowl", "--user", "fishbowl", "--cd", "/workspace"]
    assert argv[-1].endswith("echo hi") and "DISPLAY=:1" in argv[-1]
    assert LimaExecutor().argv("ls")[1] == "shell"


# ------------------------------------------------------------------------- Lima (macOS / Linux)


@pytest.mark.parametrize(
    "plat,machine,expected",
    [("darwin", "arm64", None), ("darwin", "x86_64", "qemu"), ("linux", "x86_64", None)],
)
def test_lima_vm_type(host, monkeypatch, plat, machine, expected):
    monkeypatch.delenv("FISHBOWL_VM_TYPE", raising=False)
    host(plat, machine)
    assert vm.LimaBackend().vm_type() == expected
    monkeypatch.setenv("FISHBOWL_VM_TYPE", "vz")
    assert vm.LimaBackend().vm_type() == "vz"


def test_lima_up_on_intel_mac_uses_qemu(host, monkeypatch, fishbowl_home):
    monkeypatch.delenv("FISHBOWL_VM_TYPE", raising=False)
    host("darwin", "x86_64")
    b = vm.LimaBackend()
    monkeypatch.setattr(b, "_limactl", lambda: "/usr/local/bin/limactl")
    monkeypatch.setattr(b, "status", lambda: "absent")
    calls = []
    monkeypatch.setattr(vm.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(vm.shutil, "which", lambda name: None)
    assert b.up() == 1 and not calls  # no QEMU: clear error, nothing started
    monkeypatch.setattr(vm.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    assert b.up() == 0
    cmd = calls[0]
    assert cmd[:4] == ["/usr/local/bin/limactl", "start", "--name", "fishbowl"] and "--vm-type=qemu" in cmd
    rendered = yaml.safe_load(open(cmd[-1]).read())
    assert "xvfb" in rendered["provision"][0]["script"] and "fishbowl-chromium" in rendered["provision"][1]["script"]


def test_lima_up_on_apple_silicon_uses_default_vz(host, monkeypatch, fishbowl_home):
    monkeypatch.delenv("FISHBOWL_VM_TYPE", raising=False)
    host("darwin", "arm64")
    b = vm.LimaBackend()
    monkeypatch.setattr(b, "_limactl", lambda: "/opt/homebrew/bin/limactl")
    monkeypatch.setattr(b, "status", lambda: "absent")
    calls = []
    monkeypatch.setattr(vm.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert b.up() == 0 and not any(c.startswith("--vm-type") for c in calls[0])


def test_template_has_both_cpu_images():
    t = yaml.safe_load(vm.lima_template())
    assert {i["arch"] for i in t["images"]} == {"aarch64", "x86_64"}


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("which", ["lima-system", "lima-desktop", "wsl-setup", "wsl-provision"])
def test_provisioning_scripts_parse(which, tmp_path):
    t = yaml.safe_load(vm.lima_template())
    script = {
        "lima-system": t["provision"][0]["script"],
        "lima-desktop": t["provision"][1]["script"],
        "wsl-setup": vm.wsl_setup_script(r"C:\Users\Ada Lovelace\vc\workspace"),
        "wsl-provision": vm.wsl_provision_script(),
    }[which]
    f = tmp_path / "s.sh"
    f.write_text(script)
    r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------------- WSL (Windows)


def test_wsl_isolation_config():
    conf = vm.wsl_conf()
    assert "[automount]\nenabled=false" in conf  # no C:\ inside the VM
    assert "[interop]\nenabled=false" in conf and "appendWindowsPath=false" in conf  # no Windows programs
    assert "systemd=true" in conf and "default=fishbowl" in conf


def test_drvfs_path_handles_spaces():
    assert vm.drvfs_path(r"C:\Users\Ada Lovelace\vc\workspace") == "C:/Users/Ada\\040Lovelace/vc/workspace"
    line = vm.wsl_fstab(r"D:\vc\workspace")
    assert line.startswith("D:/vc/workspace /workspace drvfs ") and "uid=1000" in line


def test_wsl_provision_reuses_the_lima_scripts():
    s = vm.wsl_provision_script()
    assert 'sudo() { "$@"; }' in s and "export FB_USER=fishbowl FB_HOME=/home/fishbowl" in s
    assert vm._resource("provision_system.sh").strip() in s and vm._resource("provision_desktop.sh").strip() in s


def test_wsl_list_parsing_utf16():
    raw = "  NAME        STATE           VERSION\r\n* Ubuntu      Running         2\r\n  fishbowl    Stopped         2\r\n"
    text = vm._decode("\ufeff".encode("utf-16-le") + raw.encode("utf-16-le"))
    assert vm.parse_wsl_list(text) == {"Ubuntu": "Running", "fishbowl": "Stopped"}
    assert vm._decode(raw.encode()) == raw


class FakeWSL:
    def __init__(self, fail_install=False):
        self.calls, self.distros, self.fail_install = [], {}, fail_install

    def __call__(self, args, stdin=None, timeout=0):
        self.calls.append((args, stdin))
        if args[:2] == ["--list", "--verbose"]:
            body = "".join(f"  {n}  {s}  2\n" for n, s in self.distros.items())
            return 0, "  NAME  STATE  VERSION\n" + body
        if args[0] == "--install":
            if self.fail_install:
                return 1, "Invalid command line argument: --name"
            self.distros["fishbowl"] = "Stopped"
        return 0, ""


def test_wsl_up_sequence(fishbowl_home):
    fake = FakeWSL()
    assert vm.WSLBackend(runner=fake).up() == 0
    steps = [c[0] for c in fake.calls]
    assert steps[1] == ["--install", "Debian", "--name", "fishbowl", "--no-launch"]
    setup, provision = [c for c in fake.calls if c[0][-2:] == ["bash", "-s"]]
    assert "[automount]" in setup[1] and str(fishbowl_home.workspace.name) in setup[1]
    assert steps.index(["--terminate", "fishbowl"]) < fake.calls.index(provision)  # wsl.conf applied first
    assert "fishbowl-chromium" in provision[1]


def test_wsl_up_explains_old_wsl(fishbowl_home, capsys):
    assert vm.WSLBackend(runner=FakeWSL(fail_install=True)).up() == 1
    assert "wsl --update" in capsys.readouterr().err


def test_wsl_down_and_destroy(fishbowl_home):
    fake = FakeWSL()
    b = vm.WSLBackend(runner=fake)
    b.down()
    b.destroy()
    assert [c[0] for c in fake.calls] == [["--terminate", "fishbowl"], ["--unregister", "fishbowl"]]


# ------------------------------------------------------------------------- per-OS client config paths


@pytest.mark.parametrize(
    "plat,expected_tail",
    [
        ("darwin", "Library/Application Support/Claude/claude_desktop_config.json"),
        ("win32", "Roaming/Claude/claude_desktop_config.json"),
        ("linux", ".config/Claude/claude_desktop_config.json"),
    ],
)
def test_claude_desktop_config_path(host, monkeypatch, tmp_path, plat, expected_tail):
    host(plat, "x86_64")
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert install.claude_desktop_config().as_posix().endswith(expected_tail)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard")
def test_windows_clipboard_roundtrip():
    hostos.clipboard_set("fishbowl 你好 ✓")
    assert hostos.clipboard_get() == "fishbowl 你好 ✓"
