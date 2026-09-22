"""Shared subprocess helper for tools that call sibling CLIs (stock_data.py etc.).

Imported flat (`from _subproc import ...`): each importer puts its own directory
on sys.path first (the `from stock_data import ...` precedent in anomaly_detect.py),
so this resolves both when a tool runs as a script (sys.path[0] = tools/) and when
pytest imports it as tools.*.
"""

import contextlib
import json
import math
import os
import re
import socket
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def find_python() -> str:
    """Prefer the repo .venv interpreter; fall back to the current one.

    Windows venvs ship .venv/Scripts/python.exe (there is no python3);
    POSIX venvs ship .venv/bin/python3 — same rule as hermes' _find_python.
    """
    parts = ("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python3")
    venv = os.path.join(os.path.dirname(TOOLS_DIR), ".venv", *parts)
    return venv if os.path.exists(venv) else sys.executable


def run_tool(script: str, args: list, timeout: int = 30, parse_json: bool = True):
    """Run tools/<script> with an argv list (never a shell). Returns parsed JSON
    stdout (raw stripped stdout when parse_json=False), or None on any failure
    (non-zero exit, empty/invalid output, timeout) — callers pick their own
    sentinel with `or {}` / `or []`. Interpreter: find_python() (venv-first,
    so child processes get the .venv deps)."""
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


def utf8_stdio() -> None:
    """Force UTF-8 stdio. Windows defaults stdio to a legacy code page (cp1252)
    that cannot encode the Chinese text these tools emit — call at CLI entry."""
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")


@contextlib.contextmanager
def socket_timeout(seconds: float):
    """Bound every blocking socket op for the wrapped chain's duration: akshare/efinance
    leave requests timeout-less, so on a blackholed network a leg hangs until the kernel
    TCP timeout (~2min) and the chain would overrun the caller's subprocess budget before
    ever reaching the plain-HTTPS legs. try/finally always restores the previous default
    (no global-state leak); explicit per-request timeouts (tencent/sina legs) still win."""
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(old_timeout)


_SECRET_PARAM_RE = re.compile(r"(?i)((?:api[_-]?key|token|secret|access[_-]?token)=)[^&\s]+")


def scrub_secrets(msg: str) -> str:
    """Error text may embed request URLs (requests connection errors) — never leak keys
    into stdout JSON."""
    return _SECRET_PARAM_RE.sub(r"\1***", msg)


def json_safe(obj):
    """Replace non-finite floats (inf/-inf/nan, e.g. an all-flat P&L profit_loss_ratio)
    with None so json.dumps never emits the non-standard Infinity/NaN tokens that
    break strict JSON.parse hosts."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj
