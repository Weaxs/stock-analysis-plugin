#!/usr/bin/env python3
"""Gather market review data."""

import subprocess
import sys
from pathlib import Path

market = sys.argv[1] if len(sys.argv) > 1 else "A"
tools = Path(__file__).resolve().parents[2] / "tools"
result = subprocess.run(
    [sys.executable, str(tools / "market_review.py"), "review", "--market", market],
    capture_output=True,
    text=True,
)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
