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
import threading
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


COMMAND_BUDGETS = json.loads((Path(TOOLS_DIR) / "cli_budgets.json").read_text(encoding="utf-8"))


def command_timeout(script: str, args: list) -> int:
    key = f"{Path(script).name}:{args[0] if args else ''}"
    budget = COMMAND_BUDGETS.get(key, COMMAND_BUDGETS["default"])

    def option(name, default):
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default

    def positive_int(value, default):
        return int(value) if str(value).isdigit() and int(value) > 0 else default

    if key == "screener.py:screen":
        top = positive_int(option("--top", 20), 20)
        workers = COMMAND_BUDGETS["l2_workers"]
        budget += math.ceil(top / workers) * COMMAND_BUDGETS["l2_batch"]
    elif key in ("watchlist.py:analyze", "watchlist_context.py:build"):
        count = len([s for s in str(args[1] if len(args) > 1 else "").split(",") if s.strip()])
        workers = positive_int(option("--workers", 3), 3)
        budget += math.ceil(count / workers) * COMMAND_BUDGETS["watchlist_item"]
        if key == "watchlist_context.py:build":
            budget += math.ceil(count / min(workers * 2, 6)) * COMMAND_BUDGETS["anomaly_detect.py:detect"]
            if "--include-market-review" in args:
                budget += 3 * COMMAND_BUDGETS["market_review.py:review"] + 30
    elif key == "market_review.py:review" and option("--market", "A") == "all":
        budget = 3 * budget + 30
    elif key == "technical.py:analyze" and "--periods" in args:
        periods = {p.strip() for p in str(option("--periods", "daily")).split(",") if p.strip()}
        budget *= len(periods | {option("--period", "daily")})
    return budget


def find_python() -> str:
    """Prefer the repo .venv interpreter; fall back to the current one.

    Windows venvs ship .venv/Scripts/python.exe (there is no python3);
    POSIX venvs ship .venv/bin/python3 — same rule as hermes' _find_python.
    """
    parts = ("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python3")
    venv = os.path.join(os.path.dirname(TOOLS_DIR), ".venv", *parts)
    return venv if os.path.exists(venv) else sys.executable


def run_tool(
    script: str, args: list, timeout: int | None = None, parse_json: bool = True, preserve_errors: bool = False
):
    """Run a sibling CLI; opt in to safe error data, otherwise keep the None sentinel."""

    if timeout is None:
        timeout = command_timeout(script, args)

    def failure(message, code):
        if not preserve_errors:
            return None
        payload = {"error": scrub_secrets(message), "error_code": code}
        return payload if parse_json else json.dumps(payload, ensure_ascii=False)

    try:
        r = subprocess.run(
            [find_python(), os.path.join(TOOLS_DIR, script), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        raw = r.stdout.strip()
        if not raw:
            return failure("Python command returned no JSON", "empty_output")
        if not preserve_errors:
            return (json.loads(raw) if parse_json else raw) if r.returncode == 0 else None
        try:
            payload = json.loads(raw)
        except ValueError:
            return failure("Python command returned invalid JSON", "invalid_json")
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            return failure(payload["error"], payload.get("error_code", "tool_error"))
        if r.returncode != 0:
            return failure(f"Python command failed (exit {r.returncode})", "process_failed")
        return payload if parse_json else raw
    except subprocess.TimeoutExpired:
        return failure(f"Command timed out after {timeout}s", "timeout")
    except Exception as exc:
        return failure(f"Python command failed ({type(exc).__name__})", "process_failed")


def utf8_stdio() -> None:
    """Force UTF-8 stdio. Windows defaults stdio to a legacy code page (cp1252)
    that cannot encode the Chinese text these tools emit — call at CLI entry."""
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")


_timeout_lock = threading.RLock()
_active_timeouts = {}
_original_socket_timeout = None
_original_send = None


@contextlib.contextmanager
def socket_timeout(seconds: float):
    """Default socket/requests timeouts while guards are active (not a total deadline).

    Like the socket default, this is process-wide: overlapping guards use the shortest
    budget. Explicit requests timeouts still win. Restore only after the last guard exits.
    """
    try:
        import requests
    except ImportError:
        requests = None

    global _original_socket_timeout, _original_send
    token = object()
    with _timeout_lock:
        if not _active_timeouts:
            _original_socket_timeout = socket.getdefaulttimeout()
            _original_send = requests.sessions.Session.send if requests is not None else None
            if requests is not None:
                original_send = _original_send

                def bounded_send(session, request, **kwargs):
                    with _timeout_lock:
                        if kwargs.get("timeout") is None and _active_timeouts:
                            kwargs["timeout"] = min(_active_timeouts.values())
                    return original_send(session, request, **kwargs)

                requests.sessions.Session.send = bounded_send
        _active_timeouts[token] = seconds
        socket.setdefaulttimeout(min(_active_timeouts.values()))
    try:
        yield
    finally:
        with _timeout_lock:
            del _active_timeouts[token]
            if _active_timeouts:
                socket.setdefaulttimeout(min(_active_timeouts.values()))
            else:
                if requests is not None and _original_send is not None:
                    requests.sessions.Session.send = _original_send
                socket.setdefaulttimeout(_original_socket_timeout)


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
