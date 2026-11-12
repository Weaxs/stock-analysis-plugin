#!/usr/bin/env python3
"""MCP stdio server for the stock-analysis tools: newline-delimited JSON-RPC 2.0, stdlib only.

Reuses the Hermes adapter (hermes/schemas.py JSON Schemas + hermes/tools.py argv mapping), so the
tools stay identical to the other hosts. stdout carries only JSON-RPC messages; anything else
goes to stderr.
"""

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes import _without_nulls, schemas, tools  # noqa: E402

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SCHEMAS = {schema["name"]: schema for schema in schemas.TOOL_SCHEMAS}
TOOL_LIST = [
    {"name": s["name"], "description": s["description"], "inputSchema": s["parameters"]} for s in schemas.TOOL_SCHEMAS
]
_current = threading.local()


def _version() -> str:
    # Wheels carry no package.json; a source checkout is not pip-installed.
    with contextlib.suppress(metadata.PackageNotFoundError):
        return metadata.version("stock-analysis-plugin")
    try:
        return json.loads((tools.PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    except Exception:
        return "unknown"


def _terminate(proc):
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)


class _Call:
    """One in-flight tools/call; cancel() kills its running CLI child."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cancelled = False
        self.proc = None

    def cancel(self):
        with self.lock:
            self.cancelled = True
            if self.proc is not None:
                _terminate(self.proc)


def _run(script: str, argv: list) -> str:
    # hermes.tools._run, but the child must not inherit the JSON-RPC stdin, and cancellation kills it.
    call = getattr(_current, "call", None) or _Call()
    try:
        with call.lock:
            if call.cancelled:
                return json.dumps({"error": "Cancelled"})
            proc = call.proc = subprocess.Popen(
                [tools._find_python(), str(tools.TOOLS_DIR / script), *map(str, argv)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0,
                start_new_session=sys.platform != "win32",
            )
    except Exception:
        return json.dumps({"error": "Python command failed; check interpreter/dependencies"})
    try:
        stdout, _ = proc.communicate(timeout=tools.command_timeout(script, argv) + 30)
    except subprocess.TimeoutExpired:
        _terminate(proc)
        proc.communicate()
        return json.dumps({"error": "Python command timed out"})
    failure = None
    if proc.returncode != 0:
        failure = f"Python command failed (exit {proc.returncode}); check interpreter/dependencies"
    return tools._cli_json(stdout or "", failure)


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def call_tool(params: dict) -> dict:
    name = params.get("name")
    schema = SCHEMAS.get(name) if isinstance(name, str) else None
    if schema is None:
        raise RpcError(-32602, f"Unknown tool: {name}")
    args = params.get("arguments", {})
    if not isinstance(args, dict):
        raise RpcError(-32602, "Tool arguments must be an object")
    missing = [key for key in schema["parameters"].get("required", []) if args.get(key) is None]
    if missing:
        text = json.dumps({"error": f"missing required argument: {', '.join(missing)}"})
    else:
        # Binds the cancellable runner to this tools/call only; Hermes itself keeps its default runner.
        token = tools._RUN_OVERRIDE.set(_run)
        try:
            text = _without_nulls(getattr(tools, name))(args)
        except Exception:
            text = json.dumps({"error": "Tool handler failed; check arguments"})
        finally:
            tools._RUN_OVERRIDE.reset(token)
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    is_error = isinstance(payload, dict) and isinstance(payload.get("error"), str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(method: str, params: dict) -> dict:
    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "stock-analysis", "version": _version()},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOL_LIST}
    raise RpcError(-32601, f"Method not found: {method}")


def serve(stdin, stdout) -> None:
    write_lock = threading.Lock()
    calls: dict = {}
    workers: list = []

    def send(message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with write_lock:
            stdout.write(data)
            stdout.flush()

    def reply(request_id, work, *args) -> dict:
        try:
            return {"jsonrpc": "2.0", "id": request_id, "result": work(*args)}
        except RpcError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": exc.message}}

    def run_call(request_id, params, call):
        _current.call = call
        message = reply(request_id, call_tool, params)
        with call.lock:
            if calls.get(request_id) is call:
                del calls[request_id]
            if not call.cancelled:  # cancellation and response are ordered by the same lock
                send(message)

    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
            continue  # responses to server requests; this server sends none
        params = msg.get("params") if isinstance(msg, dict) else None
        if (
            not isinstance(msg, dict)
            or msg.get("jsonrpc") != "2.0"
            or not isinstance(msg.get("method"), str)
            or not isinstance(params if params is not None else {}, dict)
            or type(msg.get("id")) not in (str, int, type(None))  # type(), not isinstance: JSON true is not an id
        ):
            request_id = msg.get("id") if isinstance(msg, dict) and type(msg.get("id")) in (str, int) else None
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid Request"}})
            continue
        method, params = msg["method"], params or {}
        if method == "notifications/cancelled":
            target = params.get("requestId")
            call = calls.get(target) if isinstance(target, (str, int)) else None
            if call is not None:
                call.cancel()
            continue
        if "id" not in msg:
            continue  # other notifications (initialized, ...) need no reply
        request_id = msg["id"]
        if method == "tools/call":
            call = calls[request_id] = _Call()
            worker = threading.Thread(target=run_call, args=(request_id, params, call), daemon=True)
            worker.start()
            workers = [w for w in workers if w.is_alive()] + [worker]
            continue
        send(reply(request_id, handle, method, params))

    for call in list(calls.values()):  # client closed stdin: stop outstanding work
        call.cancel()
    for worker in workers:
        worker.join()


def main() -> None:
    stdout = sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints must never corrupt the JSON-RPC stream
    with contextlib.suppress(KeyboardInterrupt):
        serve(sys.stdin.buffer, stdout)


if __name__ == "__main__":
    main()
