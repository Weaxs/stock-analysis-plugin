#!/usr/bin/env python3
"""Gather technical data for dragon-head analysis."""

import subprocess
import sys
from pathlib import Path

# Windows defaults stdio to a legacy code page (cp1252); force UTF-8 so the
# child's Chinese output both decodes and re-prints here without crashing.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

tools = Path(__file__).resolve().parents[2] / "tools"
result = subprocess.run(
    [sys.executable, str(tools / "gather.py"), "technical", sys.argv[1], "--kline-count", "30", "--with-quote"],
    capture_output=True,
    text=True,
    encoding="utf-8",
)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
