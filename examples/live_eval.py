"""Run the 27-task browser_run suite against your real decision model.

    FISHBOWL_LLM=ollama FISHBOWL_MODEL=qwen3:8b python examples/live_eval.py
    ANTHROPIC_API_KEY=... python examples/live_eval.py

Needs Chrome/Chromium on this machine (no VM required). Prints pass rate, time and model spend.
"""

import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
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
