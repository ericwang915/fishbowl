"""Run the 27-task browser_run suite against your real decision model.

    TYPESAFE_API_KEY=... python examples/live_eval.py                      # Jev
    FISHBOWL_FAST=ollama FISHBOWL_FAST_MODEL=qwen3:8b python examples/live_eval.py

Needs Chrome/Chromium on this machine (no VM required). Prints pass rate, time and model spend.
"""

import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
from fishbowl import llm  # noqa: E402

if not llm.configured("fast"):
    sys.exit(f"{llm.HOW_TO_ENABLE['fast']}\nlive_eval measures the fast executor, so it needs one.")
env = {**os.environ, "FISHBOWL_LIVE": "1"}
rc = subprocess.call(
    [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-k",
        "test_task_suite",
        "--durations=5",
        str(root / "tests" / "test_browser_run.py"),
    ],
    env=env,
    cwd=root,
)
sys.exit(rc)
