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


def find_python() -> str:
    """Prefer the repo .venv interpreter; fall back to the current one.

    Windows venvs ship .venv/Scripts/python.exe (there is no python3);
    POSIX venvs ship .venv/bin/python3 — same rule as hermes' _find_python.
    """
    if sys.platform == "win32":
        venv = os.path.join(os.path.dirname(TOOLS_DIR), ".venv", "Scripts", "python.exe")
    else:
        venv = os.path.join(os.path.dirname(TOOLS_DIR), ".venv", "bin", "python3")
    return venv if os.path.exists(venv) else sys.executable


def run_tool(script: str, args: list, timeout: int = 30, parse_json: bool = True):
    """Run tools/<script> with an argv list (never a shell). Returns parsed JSON
    stdout (raw stripped stdout when parse_json=False), or None on any failure
    (non-zero exit, empty/invalid output, timeout) — callers pick their own
    sentinel with `or {}` / `or []`.

    解释器从 sys.executable 统一为 find_python()（venv 优先，子进程需要 .venv 里的
    依赖），影响第一家族四个调用方（anomaly_detect/risk_screening/market_regime/
    market_review），与 hermes 的 _find_python 解析逻辑对齐。
    """
    try:
        r = subprocess.run(
            [find_python(), os.path.join(TOOLS_DIR, script), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout) if parse_json else r.stdout.strip()
    except Exception:
        pass
    return None
