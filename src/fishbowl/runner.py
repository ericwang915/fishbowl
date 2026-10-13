"""`browser_run`: the fast execution layer.

The big model (Claude / GPT) writes a plan. This engine executes it locally,
step by step, using the structured decision model:

  * one DECIDE call per action: the full element table in, {action, element} out
  * a separate, smaller CHECK call: "is this step done?", given the text that
    appeared since the last action
  * text that gets typed comes ONLY from the plan step (`text`), never from the model
  * hands control back after 3 no-change actions, a login wall, low confidence,
    a pause, a guard refusal, or a timeout
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import audit, control, guard, llm
from .browser import BrowserController, Element, element_table

ACTIONS = ["click", "type", "select", "scroll_down", "scroll_up", "wait", "done", "handback"]

DECIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "element": {"type": "integer", "description": "element id from the table; 0 if none"},
        "submit": {"type": "boolean", "description": "for type: press Enter afterwards"},
        "option": {"type": "string", "description": "for select: visible option text"},
        "confidence": {"type": "number", "description": "0..1"},
        "reason": {"type": "string"},
    },
    "required": ["action", "element", "confidence", "reason"],
}

DECIDE_SYSTEM = """You drive a web browser one action at a time to complete the CURRENT STEP of a plan.
You see the page as a table of interactive elements: [id] role "name" value=… checked=…
Pick exactly one action and one element id:
- click: press a button/link/checkbox/tab
- type: put the step's prepared text into a textbox (you never choose the text; set submit=true to press Enter after)
- select: choose an option in a dropdown (give `option`)
- scroll_down / scroll_up: the target is probably off screen
- wait: the page is still loading or changing
- done: the step's goal is ALREADY satisfied on this page
- handback: you are blocked — login wall, captcha, missing info, or the step is impossible
If a popup or cookie banner blocks the page, dismiss it first. Do not repeat an action that did not change anything.
confidence is how sure you are this action advances the step."""

CHECK_SCHEMA = {
    "type": "object",
    "properties": {"done": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["done", "reason"],
}

CHECK_SYSTEM = """Decide whether a browser automation step is complete. You get the step goal, the action just taken,
the page URL/title, and the text that newly appeared after the action. Answer done=true only if the goal is visibly met."""

LOGIN_WALL = re.compile(
    r"(sign in to continue|log in to continue|please (?:sign|log) in|verify you are human|i'm not a robot|captcha|"
    r"登录后|请先登录|请登录|验证码|silakan masuk|masuk untuk melanjutkan)",
    re.I,
)

Approver = Callable[[str], Awaitable[bool]]


@dataclass
class Step:
    goal: str
    text: str | None = None  # the ONLY text this step may type

    @classmethod
    def parse(cls, raw: Any) -> Step:
        if isinstance(raw, str):
            return cls(goal=raw)
        return cls(goal=str(raw.get("goal") or raw.get("step") or ""), text=raw.get("text"))


@dataclass
class RunResult:
    status: str  # done | handback | needs_approval | paused | timeout | error
    steps_done: int
    total_steps: int
    reason: str = ""
    url: str = ""
    log: list[dict] = field(default_factory=list)
    secs: float = 0.0

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _fingerprint(meta: dict, els: list[Element], text: str) -> str:
    h = hashlib.sha1()
    h.update(meta.get("url", "").encode())
    h.update(str(meta.get("scrollY")).encode())
    for e in els[:200]:
        h.update(f"{e.id}|{e.name}|{e.value}|{e.checked}|{e.expanded}".encode())
    h.update(text[-4000:].encode())
    return h.hexdigest()


def _new_text(before: str, after: str, limit: int = 1500) -> str:
    seen = set(before.splitlines())
    fresh = [l for l in after.splitlines() if l.strip() and l not in seen]
    return "\n".join(fresh)[:limit]


class Runner:
    def __init__(
        self,
        browser: BrowserController,
        approver: Approver | None = None,
        allow: list[str] | None = None,
        max_no_change: int = 3,
        min_confidence: float = 0.45,
    ):
        self.b = browser
        self.approver = approver
        self.allow = allow or []
        self.max_no_change = max_no_change
        self.min_confidence = min_confidence

    async def run(self, plan: list[Any], timeout_s: float = 180, max_actions_per_step: int = 12) -> RunResult:
        steps = [Step.parse(s) for s in plan]
        t0 = time.time()
        res = RunResult("done", 0, len(steps))
        pinned = None
        try:
            await self.b.page()
            pinned = self.b.working_tab
            for i, step in enumerate(steps):
                if step.text and guard.contains_secret(step.text):
                    return self._end(
                        res, t0, "handback", f"step {i + 1} text contains a card number or secret; refusing to type it"
                    )
                outcome = await self._run_step(i, step, res, t0, timeout_s, max_actions_per_step, pinned)
                if outcome is not None:
                    return outcome
                res.steps_done = i + 1
            return self._end(res, t0, "done", "all steps complete")
        except Exception as e:
            return self._end(res, t0, "error", f"{type(e).__name__}: {e}")

    def _end(self, res: RunResult, t0: float, status: str, reason: str) -> RunResult:
        res.status, res.reason, res.secs = status, reason, round(time.time() - t0, 2)
        audit.log("browser_run.end", {"status": status, "reason": reason, "steps_done": res.steps_done})
        return res

    async def _run_step(self, i, step: Step, res: RunResult, t0, timeout_s, max_actions, pinned) -> RunResult | None:
        no_change = 0
        last_action = "none yet"
        history: list[str] = []
        for _ in range(max_actions):
            if control.is_paused():
                return self._end(res, t0, "paused", "paused by the human in the viewer")
            if time.time() - t0 > timeout_s:
                return self._end(res, t0, "timeout", f"timed out after {timeout_s}s on step {i + 1}")
            # Stay on the working tab even if the site opened a new one.
            if pinned and self.b.working_tab != pinned:
                self.b.working_tab = pinned

            meta, els = await self.b.elements()
            text = (await self.b.text(8000))["text"]
            res.url = meta.get("url", "")
            if LOGIN_WALL.search(text[:3000]) and not any(
                w in step.goal.lower() for w in ("log in", "login", "sign in", "登录", "masuk")
            ):
                return self._end(res, t0, "handback", "login wall or captcha — needs a human (use handoff)")

            overlays = await self.b.overlays()
            ov = ""
            if overlays:
                ov = "\nOVERLAYS covering the page:\n" + "\n".join(
                    f"- {o['kind']} {int(o['cover'] * 100)}%: {o['text'][:120]!r} dismiss={o['dismiss']}"
                    for o in overlays
                )

            prompt = (
                f"STEP {i + 1}/{res.total_steps}: {step.goal}\n"
                f"Prepared text for this step: {'(none)' if step.text is None else repr(step.text)}\n"
                f"URL: {meta.get('url')}  TITLE: {meta.get('title')}  scroll {meta.get('scrollY')}/{meta.get('scrollMax')}\n"
                f"Previous actions this step: {history[-5:] or 'none'}{ov}\n\nELEMENTS:\n{element_table(els)}"
            )
            d = await llm.decide("decide", DECIDE_SYSTEM, prompt, DECIDE_SCHEMA)
            action, eid = d.get("action"), int(d.get("element") or 0)
            entry = {
                "step": i + 1,
                "action": action,
                "element": eid,
                "reason": d.get("reason", ""),
                "confidence": d.get("confidence"),
            }
            res.log.append(entry)
            audit.log("browser_run.decide", entry)

            if action == "done":
                return None
            if action == "handback":
                return self._end(res, t0, "handback", d.get("reason") or "model handed back")
            if float(d.get("confidence") or 0) < self.min_confidence:
                return self._end(
                    res, t0, "handback", f"not confident enough ({d.get('confidence')}): {d.get('reason', '')}"
                )

            before = _fingerprint(meta, els, text)
            tabs_before = {t["id"] for t in await self.b.tabs()}
            target = next((e for e in els if e.id == eid), None)
            outcome = await self._act(action, target, d, step, text, res, t0)
            if isinstance(outcome, RunResult):
                return outcome
            last_action = f"{action} {target.line() if target else ''}".strip()
            history.append(last_action + ("" if outcome else " (FAILED: element moved or covered)"))

            await self.b.wait_stable(quiet_ms=400, timeout_ms=6000)
            meta2, els2 = await self.b.elements()
            text2 = (await self.b.text(8000))["text"]
            # A site-opened tab counts as a change, but we stay on the working tab.
            opened = [t for t in await self.b.tabs() if t["id"] not in tabs_before]
            if opened:
                history[-1] += f" (site opened a new tab: {opened[0]['url'][:80]}; staying on the working tab)"
            if not opened and _fingerprint(meta2, els2, text2) == before:
                no_change += 1
                if no_change >= self.max_no_change:
                    return self._end(res, t0, "handback", f"{no_change} actions in a row did not change the page")
                continue
            no_change = 0

            if action in ("scroll_down", "scroll_up", "wait"):
                continue
            check = await llm.decide(
                "check",
                CHECK_SYSTEM,
                f"STEP GOAL: {step.goal}\nACTION TAKEN: {last_action}\nURL: {meta2.get('url')}  TITLE: {meta2.get('title')}\n"
                f"NEW TEXT SINCE ACTION:\n{_new_text(text, text2) or '(none)'}"
                + "".join(f"\n[a new tab opened: {t['title'] or t['url']}]" for t in opened),
                CHECK_SCHEMA,
                max_tokens=150,
            )
            res.log.append({"step": i + 1, "check": check.get("done"), "reason": check.get("reason", "")})
            if check.get("done"):
                return None
        return self._end(res, t0, "handback", f"step {i + 1} not finished after {max_actions} actions")

    async def _act(self, action, target: Element | None, d: dict, step: Step, text: str, res, t0):
        needs_el = action in ("click", "type", "select")
        if needs_el and target is None:
            return False
        if action in ("click",) or (action == "type" and d.get("submit")):
            commit = "click" if action == "click" else "enter"
            v = await guard.assess(commit, target.line(), f"{res.url}\n{text[:1200]}", self.allow)
            if v.needs_approval:
                ok = await self.approver(f"{commit} on {target.line()} — {v.reason}") if self.approver else False
                audit.log("guard", {"action": commit, "target": target.line(), "reason": v.reason, "approved": ok})
                if not ok:
                    return self._end(
                        res, t0, "needs_approval", f"{commit} on {target.name!r} needs your approval: {v.reason}"
                    )
        if action == "click":
            r = await self.b.click(target.id)
            return r["ok"]
        if action == "type":
            if step.text is None:
                return self._end(res, t0, "handback", "step needs typing but the plan gave no `text` for it")
            r = await self.b.type(target.id, step.text, clear=True, submit=bool(d.get("submit")))
            return r["ok"]
        if action == "select":
            r = await self.b.select(target.id, d.get("option") or step.text or "")
            return r.get("ok", False)
        if action == "scroll_down":
            await self.b.scroll("down")
        elif action == "scroll_up":
            await self.b.scroll("up")
        elif action == "wait":
            await asyncio.sleep(1.0)
        return True
