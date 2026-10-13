"""A deterministic stand-in for the decision model.

It reads the same prompts the real model gets (element table, overlays, step goal)
and answers with simple rules, so the execution engine can be tested end to end
in CI with no API key and no network.
"""

import re

from fishbowl import guard


def _parse(prompt: str):
    goal = re.search(r"STEP \d+/\d+: (.*)", prompt).group(1)
    text_m = re.search(r"Prepared text for this step: (.*)", prompt)
    has_text = text_m and text_m.group(1) != "(none)"
    els = []
    for line in prompt.split("ELEMENTS:\n", 1)[1].splitlines():
        m = re.match(r'\[(\d+)\] (\S+) "(.*?)"(.*)', line)
        if m:
            els.append({"id": int(m.group(1)), "role": m.group(2), "name": m.group(3), "rest": m.group(4)})
    ov = re.findall(r"dismiss=\[\{'id': (\d+)", prompt)
    hist = re.search(r"Previous actions this step: (.*)", prompt).group(1)
    return goal, has_text, els, ov, hist


def _quoted(goal: str) -> str | None:
    m = re.search(r"'([^']+)'", goal)
    return m.group(1).lower() if m else None


def decide(prompt: str) -> dict:
    goal, has_text, els, overlays, hist = _parse(prompt)
    g = goal.lower()
    target = _quoted(goal)
    if overlays and "accept" not in hist.lower():
        return {"action": "click", "element": int(overlays[0]), "confidence": 0.9, "reason": "dismiss overlay"}
    if g.startswith("give up"):
        return {"action": "handback", "element": 0, "confidence": 0.9, "reason": "asked to give up"}
    if g.startswith(("type", "search", "enter")) and has_text:
        box = next(
            (e for e in els if e["role"] in ("textbox", "searchbox") and (not target or target in e["name"].lower())),
            None,
        )
        if box:
            return {
                "action": "type",
                "element": box["id"],
                "submit": "submit" in g or g.startswith("search"),
                "confidence": 0.9,
                "reason": "type prepared text",
            }
    if g.startswith("select"):
        opt = re.search(r"select '([^']+)'", goal).group(1)
        box = next((e for e in els if e["role"] == "combobox"), None)
        if box and opt.lower() in box["rest"].lower().split("options=")[0]:
            return {"action": "done", "element": 0, "confidence": 0.9, "reason": "already selected"}
        if box:
            return {"action": "select", "element": box["id"], "option": opt, "confidence": 0.9, "reason": "select"}
    if g.startswith("wait"):
        if target and any(target in e["name"].lower() for e in els):
            return {"action": "done", "element": 0, "confidence": 0.9, "reason": "appeared"}
        sc = re.search(r"scroll (\d+)/(\d+)", prompt)
        if sc and int(sc.group(1)) < int(sc.group(2)):
            return {"action": "scroll_down", "element": 0, "confidence": 0.8, "reason": "maybe below"}
        return {"action": "wait", "element": 0, "confidence": 0.9, "reason": "loading"}
    if target:
        hit = next((e for e in els if target in e["name"].lower()), None)
        if hit:
            if "checked=true" in hit["rest"]:
                return {"action": "done", "element": 0, "confidence": 0.9, "reason": "already checked"}
            return {"action": "click", "element": hit["id"], "confidence": 0.9, "reason": f"click {target}"}
        sc = re.search(r"scroll (\d+)/(\d+)", prompt)
        if sc and int(sc.group(1)) < int(sc.group(2)):
            return {"action": "scroll_down", "element": 0, "confidence": 0.8, "reason": "look further down"}
        return {"action": "wait", "element": 0, "confidence": 0.8, "reason": "maybe still loading"}
    if "low confidence" in g:
        return {"action": "click", "element": els[0]["id"] if els else 0, "confidence": 0.1, "reason": "unsure"}
    return {"action": "handback", "element": 0, "confidence": 0.5, "reason": "no matching element"}


def check(prompt: str) -> dict:
    goal = re.search(r"STEP GOAL: (.*)", prompt).group(1)
    new = prompt.split("NEW TEXT SINCE ACTION:\n", 1)[1]
    m = re.search(r"expect '([^']+)'", goal)
    if m:
        return {"done": m.group(1).lower() in new.lower(), "reason": "expectation"}
    return {"done": "(none)" not in new or "new tab opened" in new or "type" in goal.lower(), "reason": "page changed"}


async def fake_llm(purpose: str, system: str, user: str, schema: dict) -> dict:
    if purpose == "decide":
        return decide(user)
    if purpose == "check":
        return check(user)
    if purpose == "guard":
        m = re.search(r"Action: (\S+)\nTarget element: (.*)\nPage context: (.*)", user, re.S)
        v = guard.heuristic(m.group(1), m.group(2), m.group(3))
        return {"needs_approval": v.needs_approval, "category": "other_safe", "reason": v.reason}
    if purpose == "judge":
        return {
            "reply_type": "answer",
            "detected_language": "en",
            "language_match": True,
            "actionable": True,
            "cites_store_data": "#" in user,
            "score": 4,
            "issues": [],
        }
    raise AssertionError(purpose)
