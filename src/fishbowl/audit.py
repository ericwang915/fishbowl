"""Append-only JSONL audit log and screenshot retention."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_config

_REDACT_KEYS = {"text", "value"}


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    from .guard import contains_secret

    out = {}
    for k, v in args.items():
        if isinstance(v, str) and k in _REDACT_KEYS and contains_secret(v):
            out[k] = "[REDACTED]"
        elif isinstance(v, str) and len(v) > 2000:
            out[k] = v[:2000] + "…"
        else:
            out[k] = v
    return out


def log(tool: str, args: dict[str, Any] | None = None, result: Any = None, **extra: Any) -> None:
    cfg = get_config()
    cfg.audit_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    entry = {"ts": now.isoformat(timespec="milliseconds"), "tool": tool, "args": _redact(args or {})}
    if result is not None:
        entry["result"] = result if isinstance(result, (dict, list, int, float, bool)) else str(result)[:2000]
    entry.update(extra)
    path = cfg.audit_dir / f"{now:%Y-%m-%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def tail(n: int = 50) -> list[dict[str, Any]]:
    cfg = get_config()
    files = sorted(cfg.audit_dir.glob("*.jsonl"))
    lines: list[str] = []
    for p in reversed(files):
        lines = p.read_text(encoding="utf-8").splitlines() + lines
        if len(lines) >= n:
            break
    return [json.loads(l) for l in lines[-n:] if l.strip()]


def save_screenshot(png: bytes) -> Path:
    cfg = get_config()
    cfg.screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.screenshot_dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}.png"
    path.write_bytes(png)
    prune_screenshots()
    return path


def prune_screenshots(now: float | None = None) -> int:
    cfg = get_config()
    cutoff = (now or time.time()) - cfg.screenshot_retention_days * 86400
    removed = 0
    for p in Path(cfg.screenshot_dir).glob("*.png"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            removed += 1
    return removed
