"""MCP stdio host: protocol round-trips over the real server process, plus in-process cancellation."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from hermes.schemas import TOOL_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_host" / "server.py"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _session(*messages, raw=""):
    # Keep stdin open until every request is answered: closing it is the MCP shutdown signal.
    body = raw + "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    expected = raw.count("\n") + sum("id" in m for m in messages)
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )
    try:
        proc.stdin.write(body)
        proc.stdin.flush()
        responses = [json.loads(proc.stdout.readline()) for _ in range(expected)]  # stdout lines are JSON-RPC only
        proc.stdin.close()
        assert proc.stdout.read() == ""
        assert proc.wait(30) == 0
    finally:
        proc.kill()
    assert all(r["jsonrpc"] == "2.0" for r in responses)
    return responses


def _by_id(*messages):
    return {r["id"]: r for r in _session(*messages)}


def test_initialize_and_tools_list_match_hermes_schemas():
    got = _by_id(INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    init = got[1]["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["capabilities"] == {"tools": {"listChanged": False}}
    assert init["serverInfo"]["name"] == "stock-analysis"
    listed = got[2]["result"]["tools"]
    assert len(listed) == len(TOOL_SCHEMAS)
    by_name = {t["name"]: t for t in listed}
    for schema in TOOL_SCHEMAS:
        assert by_name[schema["name"]] == {
            "name": schema["name"],
            "description": schema["description"],
            "inputSchema": schema["parameters"],
        }


def test_unsupported_protocol_version_gets_server_latest():
    init = dict(INIT, params=dict(INIT["params"], protocolVersion="1999-01-01"))
    assert _by_id(init)[1]["result"]["protocolVersion"] == "2025-11-25"


def test_tools_call_runs_python_cli():
    args = {
        "equity": 100000,
        "cash": 100000,
        "entry_price": 10,
        "stop_price": 9,
        "risk_fraction": 0.01,
        "lot_size": 100,
    }
    params = {"name": "calculate_position_size", "arguments": args}
    result = _by_id(INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params})[3]
    assert result["result"]["isError"] is False
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload and "error" not in payload


def test_errors_are_json_rpc_errors_or_tool_error_results():
    got = _by_id(
        INIT,
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "get_quote", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "nope"},
        {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/unknown"},
    )
    assert got[4]["error"]["code"] == -32602
    assert got[5]["result"]["isError"] is True
    assert "symbol" in json.loads(got[5]["result"]["content"][0]["text"])["error"]
    assert got[6]["error"]["code"] == -32601
    assert got[7]["result"] == {}
    assert len(got) == 5  # notifications get no response


def test_falsy_non_object_arguments_are_rejected():
    got = _by_id(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "calculate_position_size", "arguments": 0},
        }
    )
    assert got[8]["error"]["code"] == -32602


def test_malformed_input_returns_parse_error_and_keeps_serving():

    responses = _session({"jsonrpc": "2.0", "id": 9, "method": "ping"}, raw="{not json\n[]\n")
    assert responses[0] == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    assert responses[1]["id"] is None and responses[1]["error"]["code"] == -32600
    assert responses[2] == {"jsonrpc": "2.0", "id": 9, "result": {}}


@pytest.mark.parametrize("bad_id", [{"a": 1}, True])
def test_invalid_request_id_is_rejected_and_keeps_serving(bad_id):
    bad = {"jsonrpc": "2.0", "id": bad_id, "method": "tools/call", "params": {"name": "get_quote"}}
    responses = _session(bad, {"jsonrpc": "2.0", "id": 10, "method": "ping"})
    assert responses[0] == {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    assert responses[1] == {"jsonrpc": "2.0", "id": 10, "result": {}}


def test_mcp_leaves_hermes_runner_unchanged(tmp_path, monkeypatch):
    import hermes.tools as hermes_tools

    original = hermes_tools._run
    monkeypatch.syspath_prepend(str(ROOT / "mcp_host"))
    import server

    monkeypatch.setenv("STOCK_WATCHLIST_FILE", str(tmp_path / "missing.json"))
    result = server.call_tool({"name": "get_watchlist", "arguments": {}})
    assert result["isError"] is False
    assert hermes_tools._run is original
    assert hermes_tools._RUN_OVERRIDE.get() is None


def test_cancel_send_race_never_emits_late_response(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "mcp_host"))
    import server

    started = threading.Event()

    class SlowCancelCall(server._Call):
        def cancel(self):
            with self.lock:
                started.set()
                time.sleep(0.1)
                self.cancelled = True

    monkeypatch.setattr(server, "_Call", SlowCancelCall)
    monkeypatch.setattr(server, "call_tool", lambda params: started.wait(5) or {"content": []})

    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    stdout = os.fdopen(out_w, "wb")
    worker = threading.Thread(target=server.serve, args=(os.fdopen(in_r, "rb"), stdout), daemon=True)
    worker.start()
    feed, read = os.fdopen(in_w, "wb"), os.fdopen(out_r, "rb")

    def send(message):
        feed.write((json.dumps(message) + "\n").encode())
        feed.flush()

    send({"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "x"}})
    send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 20}})
    send({"jsonrpc": "2.0", "id": 21, "method": "ping"})
    first = json.loads(read.readline())
    feed.close()
    worker.join(10)
    stdout.close()
    remaining = read.read()
    assert not worker.is_alive()
    assert first["id"] == 21
    assert remaining == b""


def test_cancel_kills_nested_cli_process_tree(tmp_path, monkeypatch):
    import hermes.tools as hermes_tools

    monkeypatch.syspath_prepend(str(ROOT / "mcp_host"))
    import server

    pid_file = tmp_path / "nested-pids"
    (tmp_path / "stock_data.py").write_text(
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hermes_tools, "TOOLS_DIR", tmp_path)

    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    stdout = os.fdopen(out_w, "wb")
    worker = threading.Thread(target=server.serve, args=(os.fdopen(in_r, "rb"), stdout), daemon=True)
    worker.start()
    feed, read = os.fdopen(in_w, "wb"), os.fdopen(out_r, "rb")
    pids = []

    def send(message):
        feed.write((json.dumps(message) + "\n").encode())
        feed.flush()

    try:
        call = {"name": "get_quote", "arguments": {"symbol": "X"}}
        send({"jsonrpc": "2.0", "id": 30, "method": "tools/call", "params": call})
        deadline = time.time() + 30
        while not pid_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        pids = [int(value) for value in pid_file.read_text().split()]
        send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 30}})
        send({"jsonrpc": "2.0", "id": 31, "method": "ping"})
        assert json.loads(read.readline())["id"] == 31
        deadline = time.time() + 10
        while any(_alive(pid) for pid in pids) and time.time() < deadline:
            time.sleep(0.05)
        assert all(not _alive(pid) for pid in pids)
        feed.close()
        worker.join(10)
        assert not worker.is_alive()
    finally:
        for pid in pids:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                else:
                    os.kill(pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
        feed.close()
        stdout.close()
        worker.join(10)
        read.close()


def _alive(pid):

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            status = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(status))) and status.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_cancelled_notification_kills_child_and_suppresses_response(tmp_path, monkeypatch):
    import hermes.tools as hermes_tools

    monkeypatch.syspath_prepend(str(ROOT / "mcp_host"))
    import server

    pid_file = tmp_path / "pid"
    (tmp_path / "stock_data.py").write_text(
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n", encoding="utf-8"
    )
    monkeypatch.setattr(hermes_tools, "TOOLS_DIR", tmp_path)

    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    stdout = os.fdopen(out_w, "wb")
    worker = threading.Thread(target=server.serve, args=(os.fdopen(in_r, "rb"), stdout), daemon=True)
    worker.start()
    feed, read = os.fdopen(in_w, "wb"), os.fdopen(out_r, "rb")

    def send(message):
        feed.write((json.dumps(message) + "\n").encode())
        feed.flush()

    call = {"name": "get_quote", "arguments": {"symbol": "X"}}
    send({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": call})
    deadline = time.time() + 30
    while not (pid_file.exists() and pid_file.read_text()) and time.time() < deadline:
        time.sleep(0.05)
    pid = int(pid_file.read_text())
    send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 10}})
    send({"jsonrpc": "2.0", "id": 11, "method": "ping"})
    assert json.loads(read.readline()) == {"jsonrpc": "2.0", "id": 11, "result": {}}
    deadline = time.time() + 10
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)
    feed.close()
    worker.join(10)
    assert not worker.is_alive()
    stdout.close()
    assert read.read() == b""  # no late response for the cancelled id
