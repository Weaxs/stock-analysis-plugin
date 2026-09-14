"""Shared subprocess helper for tools that call sibling CLIs (stock_data.py etc.).

Imported flat (`from _subproc import ...`): each importer puts its own directory
on sys.path first (the `from stock_data import ...` precedent in anomaly_detect.py),
so this resolves both when a tool runs as a script (sys.path[0] = tools/) and when
pytest imports it as tools.*.
"""

import json
import os
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def run_tool(script: str, args: list, timeout: int = 30):
    """Run tools/<script> with an argv list (never a shell). Returns parsed JSON
    stdout, or None on any failure (non-zero exit, empty/invalid output, timeout) —
    callers pick their own sentinel with `or {}` / `or []`."""
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(TOOLS_DIR, script), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None
