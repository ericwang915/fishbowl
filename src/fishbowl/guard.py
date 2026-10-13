"""Approval guardrail.

1. Card numbers and secrets are *always* blocked from being typed, whatever the plan says.
2. Before a click or Enter, decide whether the action is irreversible or visible to
   others (pay, send, delete, submit, publish…). If so, a human must approve it.

The decision model makes the call; a keyword heuristic backs it up when the model
is unavailable (fail closed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import llm

_SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9_\-]{20,}",
    r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bASIA[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{40,}\b",
    r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",
    r"\bAIza[0-9A-Za-z_\-]{35}\b",
    r"\bglpat-[A-Za-z0-9_\-]{20,}\b",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
]
_SECRET_RE = re.compile("|".join(_SECRET_PATTERNS))
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def has_card_number(text: str) -> bool:
    for m in _CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits) and len(set(digits)) > 1:
            return True
    return False


def contains_secret(text: str) -> bool:
    return bool(_SECRET_RE.search(text)) or has_card_number(text)


# ---------------------------------------------------------------------------
# Irreversible / externally visible actions

_RISKY_WORDS = [
    # en
    r"pay", r"payment", r"purchase", r"buy now", r"buy", r"checkout", r"check out", r"place order", r"order now",
    r"subscribe", r"donate", r"transfer", r"withdraw", r"send", r"submit", r"post", r"publish", r"tweet", r"reply",
    r"delete", r"remove", r"erase", r"destroy", r"cancel (?:my )?(?:subscription|account|order)", r"close account",
    r"confirm", r"approve", r"sign", r"book now", r"reserve", r"invite", r"share", r"merge", r"deploy", r"unsubscribe",
    # zh
    r"付款", r"支付", r"购买", r"下单", r"结算", r"发送", r"提交", r"发布", r"删除", r"移除", r"确认", r"转账", r"订阅", r"注销",
    # id / ms
    r"bayar", r"beli", r"kirim", r"hapus", r"pesan sekarang", r"konfirmasi",
]
_RISKY_RE = re.compile(r"(?<![a-z])(?:" + "|".join(_RISKY_WORDS) + r")(?![a-z])", re.I)
_SAFE_HINTS = re.compile(r"(?<![a-z])(search|搜索|cari|filter|sort|next|previous|back|more|show|view|open|menu|close|cancel|dismiss|learn more)(?![a-z])", re.I)


@dataclass
class Verdict:
    needs_approval: bool
    reason: str
    source: str  # model | heuristic | secret


def heuristic(action: str, target: str, context: str = "") -> Verdict:
    text = f"{target} {context}".strip()
    if action not in ("click", "enter", "submit"):
        return Verdict(False, "not a commit action", "heuristic")
    m = _RISKY_RE.search(target) or (action in ("enter", "submit") and _RISKY_RE.search(context))
    if m and not (_SAFE_HINTS.search(target) and not _RISKY_RE.search(target)):
        return Verdict(True, f"looks irreversible or public: '{m.group()}' in {text[:80]!r}", "heuristic")
    return Verdict(False, "no risky signal", "heuristic")


GUARD_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_approval": {"type": "boolean"},
        "category": {"type": "string", "enum": ["payment", "send_message", "delete", "submit_form", "publish",
                                                 "account_change", "navigation", "input", "other_safe"]},
        "reason": {"type": "string"},
    },
    "required": ["needs_approval", "category", "reason"],
}

GUARD_SYSTEM = """You are a safety reviewer for a browser agent. Decide if the NEXT action needs human approval.
Needs approval: anything irreversible or visible to other people — paying or purchasing, sending a message/email/comment,
posting or publishing, deleting, submitting a form that creates/changes something, changing account settings, inviting/sharing.
Does NOT need approval: searching, navigating, opening menus/tabs, filtering, typing into a field without submitting,
dismissing popups/cookie banners, logging out, pagination, reading. When the user has explicitly allowed the category, it is fine.
Be decisive."""


async def assess(action: str, target: str, context: str = "", allowed: list[str] | None = None) -> Verdict:
    """Decide whether `action` on `target` needs a human. Never raises."""
    if action not in ("click", "enter", "submit"):
        return Verdict(False, "not a commit action", "heuristic")
    allowed = allowed or []
    prompt = (f"Action: {action}\nTarget element: {target}\nPage context: {context[:1500]}\n"
              f"Categories the user pre-approved: {allowed or 'none'}")
    try:
        r = await llm.decide("guard", GUARD_SYSTEM, prompt, GUARD_SCHEMA, max_tokens=200)
        needs = bool(r.get("needs_approval")) and r.get("category") not in allowed
        return Verdict(needs, f"{r.get('category')}: {r.get('reason', '')}", "model")
    except Exception as e:  # model down → fail closed on the heuristic
        v = heuristic(action, target, context)
        v.reason += f" (model unavailable: {str(e)[:80]})"
        return v
