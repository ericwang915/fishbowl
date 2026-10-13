# Fast and slow thinking

`browser_run` splits a web task between two kinds of model:

| | slow thinking | fast thinking |
|---|---|---|
| who | Claude / GPT, or the agent driving fishbowl (Claude Code, Codex) | Jev, or any small model (Haiku, gpt-4.1-mini, a local Ollama model) |
| job | understand the goal, write a short plan, write every piece of text to type, replan after a failure | pick the next operation and its target, check whether a step is done, approve or refuse commit actions |
| calls | a few per task | one per action, plus one small check |
| output | free-form JSON plan | answers to multiple-choice questions |

```
goal ──► slow: plan [{goal, text}, …] ──► fast: execute step by step ──► done
                     ▲                                  │
                     └──── replan from current page ◄───┘ handback (stuck · blocked · unsure · no progress)
```

## Why multiple choice

A fast model that writes free text can invent selectors, coordinates or values. fishbowl never asks it to.
Every fast decision is a set of **multiple-choice questions** over things that exist on the page right now:

```
operation       CLICK · TYPE_TEXT · SELECT · SCROLL_DOWN · SCROLL_UP · WAIT · DONE · BLOCKED
click_target    [3] button Search · [5] link USB-C Hub Pro · [9] button Accept all · …
type_target     [1] searchbox Search products · …            (only if the step has prepared text)
select_target   [4] Language → English (current) · [4] Language → 中文 · …
submit_after_typing   yes · no
```

All questions go in **one request**. The target questions are *speculative*: each one is asked as if its operation
won, and only the head that matches the chosen operation is executed. That turns "what to do" plus "where" into
a single round trip. Each operation only offers compatible elements: you can't type into a link, and you can't
choose a dropdown option that doesn't exist. `TYPE_TEXT` isn't offered at all when the plan gave no text.

[Jev](https://github.com/browser-use/jev-ultrafast) (TypeSafe's `systemone` API) answers these natively, with
probabilities and a calibrated confidence per question. For Anthropic, OpenAI and Ollama, fishbowl turns the same
questions into a JSON schema with an `enum` per question. Every answer is validated, and anything outside the
offered options is refused before any input happens.

## One action, end to end

1. **Observe**: one `Runtime.evaluate` returns the element table (role, name, value, checked, expanded), the
   visible text in the viewport only, overlays and their dismiss buttons, a page marker for change detection,
   and freshness guards.
2. **Decide**: one request answers the operation and every target question. Confidence below 0.45 hands back.
3. **Guard**: clicks and Enter go through the approval guard, itself a multiple-choice question
   (payment · send_message · delete · submit_form · publish · account_change · navigation · …).
4. **Act, if still fresh**: the freshness check runs in the *same* round trip as the hit test. If the page or
   the target changed since the decision (URL, scroll, form values, the element's role, name, value or
   surrounding text), the input is not sent, and the runner observes and decides again.
5. **Settle and re-observe**: wait for the DOM to be quiet for 200 ms, then observe again. If nothing changed
   and no tab opened, that counts toward the 3-strikes handback.
6. **Check**: if the page changed, a small question decides whether the step is done, given only the text that
   *newly appeared* and any new tabs.

When the fast model answers `DONE` without acting, that claim is **verified** by a separate question against the
visible page. Two rejected claims hand back.

## Handback is a feature

The fast layer is allowed to give up. It returns `handback` when:

- it is blocked or unsure (low confidence, or `BLOCKED`),
- three actions in a row change nothing,
- the page keeps changing under its decisions,
- its `DONE` claims keep failing verification,
- it hits a login wall or captcha (this one always goes to the human, through `handoff`).

Each handback carries the reason, the action log and a compact `observation` (URL, title, visible elements,
visible text). With `goal` set, fishbowl's slow model replans from that observation, up to `max_replans` times.
Otherwise the driving agent gets it and replans itself, without another tool call.

## What the slow model must not do

The planner prompt forbids inventing personal data (names, emails, addresses, passwords, cards). If the goal
needs any, the planner returns `missing_info`, and `browser_run` hands back asking for it. Logins and captchas
are never planned: they are a `handoff`.

## Measured

On the local 27-task suite, run with the scripted test model so that only the engine differs:

| | first release | fast path |
|---|---|---|
| tasks passed | 27/27 | 27/27 |
| CDP round trips | 581 | **419** (−28%) |
| wall time | 45.5 s | **≈35.6 s** (−22%) |
| model calls | 121 | 124 |

This measures the engine, not model quality. To measure your models: `python examples/live_eval.py`.

## Credits

The fast path adapts ideas from [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (MIT):
the indexed per-operation action space with speculative target heads, the single observation call with visible
text only, freshness checks before input, and independent DONE verification. fishbowl adds the slow planner and
replanning, the plan-only text rule, the approval guard, and the human-in-the-loop VM around it.
