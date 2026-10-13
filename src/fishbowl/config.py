"""Paths, ports and model settings. Everything is overridable with FISHBOWL_* env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import hostos


def _env(name: str, default: str) -> str:
    return os.environ.get(f"FISHBOWL_{name}", default)


@dataclass
class Config:
    home: Path = field(default_factory=lambda: Path(_env("HOME", str(Path.home() / "vc"))).expanduser())
    vm_name: str = field(default_factory=lambda: _env("VM", "fishbowl"))
    # All ports are forwarded to 127.0.0.1 on the Mac only.
    cdp_port: int = field(default_factory=lambda: int(_env("CDP_PORT", "9222")))
    novnc_port: int = field(default_factory=lambda: int(_env("NOVNC_PORT", "6080")))
    control_port: int = field(default_factory=lambda: int(_env("CONTROL_PORT", "6090")))
    screen_w: int = 1280
    screen_h: int = 800
    screenshot_retention_days: int = field(default_factory=lambda: int(_env("SCREENSHOT_DAYS", "7")))
    # "lima" (macOS, Linux) or "wsl" (Windows) run commands inside the VM; "local" runs them on this host (CI, tests).
    executor: str = field(default_factory=lambda: _env("EXECUTOR", hostos.default_backend()))
    auto_open_viewer: bool = field(default_factory=lambda: _env("AUTO_OPEN", "1") == "1")

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

    @property
    def audit_dir(self) -> Path:
        return self.home / "audit"

    @property
    def screenshot_dir(self) -> Path:
        return self.home / "screenshots"

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def cdp_url(self) -> str:
        return _env("CDP_URL", f"http://127.0.0.1:{self.cdp_port}")

    def ensure_dirs(self) -> None:
        for d in (self.workspace, self.audit_dir, self.screenshot_dir, self.state_dir):
            d.mkdir(parents=True, exist_ok=True)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
