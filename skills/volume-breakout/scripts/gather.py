#!/usr/bin/env python3
"""Gather technical data for volume-breakout analysis."""

import subprocess
import sys
from pathlib import Path

tools = Path(__file__).resolve().parents[2] / "tools"
result = subprocess.run(
    [sys.executable, str(tools / "gather.py"), "technical", sys.argv[1], "--kline-count", "90"],
    capture_output=True,
    text=True,
)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
