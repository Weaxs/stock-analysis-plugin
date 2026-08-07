#!/usr/bin/env python3
"""Gather market review data."""

import subprocess
import sys
from pathlib import Path

# Windows defaults stdio to a legacy code page (cp1252); force UTF-8 so the
# child's Chinese output both decodes and re-prints here without crashing.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

market = sys.argv[1] if len(sys.argv) > 1 else "A"
tools = Path(__file__).resolve().parents[2] / "tools"
result = subprocess.run(
    [sys.executable, str(tools / "market_review.py"), "review", "--market", market],
    capture_output=True,
    text=True,
    encoding="utf-8",
)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
