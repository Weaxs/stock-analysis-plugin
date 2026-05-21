#!/usr/bin/env python3
"""Run backtest and return results."""

import subprocess
import sys
from pathlib import Path

tools = Path(__file__).resolve().parents[2] / "tools"
result = subprocess.run(
    [sys.executable, str(tools / "backtest.py"), "run"] + sys.argv[1:],
    capture_output=True,
    text=True,
    timeout=120,
)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
