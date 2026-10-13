# Contributing

Thanks for helping! fishbowl is small on purpose; the bar for new code is "makes agents safer, faster or easier to watch".

## Dev setup

```bash
git clone https://github.com/ericwang915/fishbowl && cd fishbowl
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest            # needs Chrome or Chromium; no VM or API key required
ruff check . && ruff format --check .
```

- `tests/test_browser_run.py` runs the execution engine end to end on local pages with a deterministic
  decision model (`tests/fake_model.py`). Run it against a real model with `python examples/live_eval.py`.
- `tests/test_desktop_live.py` needs a live X desktop: `scripts/desktop-services.sh` starts one on Linux.
- After changing a tool's signature or docstring, run `python scripts/gen_tool_docs.py`.
- After changing the demo page or the runner's output, `python scripts/make_demo.py` re-records `docs/demo.gif`.

## Good first contributions

- New `browser_run` task types: add a page under `tests/pages/` and cases to `CASES`.
- More placeholder / survey phrases for `chat_ask` in your language (`chat.py`).
- More risky-action words for the guard fallback in your language (`guard.py`), with labelled test rows.

## Pull requests

Keep PRs focused, include a test, and describe what you verified. CI must be green.
