#!/usr/bin/env python3
"""Gather fundamental data for event-driven analysis."""

import subprocess
import sys
from pathlib import Path

tools = Path(__file__).resolve().parents[2] / "tools"
result = subprocess.run(
    [sys.executable, str(tools / "gather.py"), "fundamental", sys.argv[1]],
    capture_output=True,
    text=True,
)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
