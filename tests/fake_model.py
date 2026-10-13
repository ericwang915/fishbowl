"""A deterministic stand-in for the fast and slow models.

It reads the same multiple-choice questions the real fast model gets (operations, per-operation
target heads, visible page text) and answers with simple rules, so the execution engine can be
tested end to end in CI with no API key and no network.
"""

import re

from fishbowl import guard


def _quoted(goal: str) -> str | None:
    m = re.search(r"'([^']+)'", goal)
    return m.group(1).lower() if m else None


def _pick(criteria: dict, needle: str | None):
    if not needle:
        return None
    return next((k for k, v in criteria.items() if needle in v.lower()), None)


def decide(state: dict, q: dict) -> dict:
    goal = state["step"]
    g = goal.lower()
    target = _quoted(goal)
    ops = q["operation"]["criteria"]
    click = q.get("click_target", {}).get("criteria", {})
    typ = q.get("type_target", {}).get("criteria", {})
    sel = q.get("select_target", {}).get("criteria", {})
    history = " ".join(str(h.get("action", "")) for h in state["recent_actions"]).lower()
    dflt = {k: next(iter(v["criteria"])) for k, v in q.items() if k != "operation"}  # every head is answered

    def answer(op, conf=0.9, **chosen):
        out = {"operation": {"choice": op, "confidence": conf}}
        for k, v in {**dflt, **chosen}.items():
            out[k] = {"choice": v, "confidence": conf}
        return out

    overlays = state["overlays"]
    if overlays and overlays[0]["dismiss"] and "accept" not in history and "reject" not in history:
        return answer("CLICK", click_target=str(overlays[0]["dismiss"][0]["id"]))
    if g.startswith("give up"):
        return answer("BLOCKED")
    if "low confidence" in g:
        return answer("CLICK", conf=0.1)
    if "TYPE_TEXT" in ops and g.startswith(("type", "search", "enter", "fill")):
        box = _pick(typ, target) or next(iter(typ))
        submit = "yes" if ("submit" in g or g.startswith("search")) else "no"
        return answer("TYPE_TEXT", type_target=box, submit_after_typing=submit)
    if g.startswith("select") and sel:
        opt = re.search(r"select '([^']+)'", goal).group(1).lower()
        key = next(k for k, v in sel.items() if v.lower().split("→ ", 1)[1].startswith(opt))
        if "(current)" in sel[key]:
            return answer("DONE")
        return answer("SELECT", select_target=key)
    if g.startswith("wait"):
        if _pick(click, target):
            return answer("DONE")
        return answer("SCROLL_DOWN" if "SCROLL_DOWN" in ops else "WAIT")
    if target:
        hit = _pick(click, target)
        if hit:
            if "· checked" in click[hit]:
                return answer("DONE")
            return answer("CLICK", click_target=hit)
        return answer("SCROLL_DOWN" if "SCROLL_DOWN" in ops else "WAIT")
    return answer("BLOCKED", conf=0.5)


def verify(state: dict) -> str:
    goal = state["step"].lower()
    seen = (state["page"]["text"] + " " + " ".join(state.get("elements", []))).lower()
    m = re.search(r"expect '([^']+)'", goal)
    if m:
        return "yes" if m.group(1).lower() in seen else "no"
    t = _quoted(goal)
    return "yes" if not t or t in seen else "no"


def check(state: dict) -> str:
    goal = state["step"]
    new = state["new_text_since_action"]
    m = re.search(r"expect '([^']+)'", goal)
    if m:
        return "yes" if m.group(1).lower() in new.lower() else "no"
    done = new != "(none)" or state["new_tabs"] or goal.lower().startswith(("type", "fill", "select"))
    return "yes" if done else "no"


async def fake_choose(purpose: str, state: dict, questions: dict) -> dict:
    if purpose == "decide":
        return decide(state, questions)
    if purpose == "verify":
        return {"satisfied": verify(state)}
    if purpose == "check":
        return {"step_done": check(state)}
    if purpose == "guard":
        v = guard.heuristic(state["action"], state["target"], state["page"])
        return {"category": "payment" if v.needs_approval else "navigation"}
    if purpose == "judge":
        return {
            "reply_type": "answer",
            "detected_language": "en",
            "language_match": "yes",
            "actionable": "yes",
            "cites_store_data": "yes" if "#" in state["reply"] else "no",
            "score": "4",
        }
    raise AssertionError(purpose)


async def fake_llm(purpose: str, system: str, user: str, schema: dict) -> dict:
    """decide()-style JSON calls: the slow planner."""
    if purpose == "plan":
        goal = re.search(r"GOAL: (.*)", user).group(1)
        if "needs info" in goal:
            return {"steps": [], "missing_info": "the user's shipping address"}
        if goal.startswith("replan:") and "PREVIOUS ATTEMPT FAILED" not in user:
            # First plan is wrong on purpose; the replan uses the real steps.
            return {"steps": [{"goal": "click 'no such button'", "text": None}], "missing_info": None}
        steps = [{"goal": s.strip(), "text": None} for s in goal.removeprefix("replan:").split(" then ")]
        return {"steps": steps, "missing_info": None}
    raise AssertionError(purpose)


def use_fake(llm_module) -> None:
    llm_module.set_override(fake_llm)
    llm_module.set_choice_override(fake_choose)
