#!/usr/bin/env python3
"""MCP stdio server for the stock-analysis tools: newline-delimited JSON-RPC 2.0, stdlib only.

Reuses the Hermes adapter (tools/tool_schemas.json via hermes/schemas.py + hermes/tools.py argv mapping), so the
tools stay identical to the other hosts. Also exposes bundled skills/*/SKILL.md as MCP Prompts and
references as MCP Resources, plus optional --discovery / --profile=core modes to reduce context footprint.
stdout carries only JSON-RPC messages; anything else goes to stderr.
"""

import argparse
import contextlib
import json
import os
import re
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
CORE_TOOL_NAMES = frozenset(
    {
        "get_quote",
        "get_kline",
        "get_technical_analysis",
        "get_financials",
        "get_fundamental_context",
        "get_valuation_history",
        "get_macro_anchors",
        "screen_stocks",
        "screen_risk",
        "run_backtest",
        "get_market_review",
        "detect_market_regime",
        "search_stock_news",
        "resolve_stock_name",
        "get_watchlist",
        "build_watchlist_context",
        "diagnose_data_sources",
        "get_market_capabilities",
    }
)
DISCOVERY_TOOL_DEF = {
    "name": "stock_tool_search",
    "description": "按中文关键词、英文关键词或工具名搜索 76 个股票分析工具，返回匹配工具的完整名称与参数 schema，并在支持动态刷新的 MCP 客户端中将其加载到当前会话工具列表。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或工具名，例如 财务、get_quote、DCF、回测"},
            "limit": {"type": "number", "description": "最多返回的工具数量（1..20，默认 5）"},
        },
        "required": ["query"],
    },
}

_current = threading.local()
# Each call is a Python process that loads pandas; parallel model calls must not fan out unbounded.
_SLOTS = threading.BoundedSemaphore(4)
_FRONTMATTER_RE = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?")
_MCP_SKILL_PREAMBLE = (
    "[MCP 执行说明]\n"
    "你当前通过 MCP 协议接入 stock-analysis 工具集，无需也不要依赖本地 shell 脚本（如 `python3 scripts/gather.py`）或本地文件路径：\n"
    "1. 请直接调用已注册的 MCP 工具完成数据采集与分析（如 `get_quote`、`get_kline`、`get_technical_analysis`、`get_financials`、"
    "`get_fundamental_context`、`get_valuation_history`、`get_macro_anchors`、`screen_risk`、`detect_market_regime`、"
    "`search_stock_news`、`render_stock_report` 等；若开启了 `--discovery`，先调用 `stock_tool_search` 加载所需工具）。\n"
    "2. 若正文引用了 `references/...` 或 `schemas/...` 文件，请通过 MCP `resources/read` 读取对应的 "
    "`stock-skill://<skill>/references/<file>` 或 `stock-schema://<file>` 资源（例如 `stock-schema://report_schema.json`）。\n\n"
)


def _skills_dir() -> Path:
    return tools.TOOLS_DIR.parent / "skills"


def _schemas_dir() -> Path:
    return tools.TOOLS_DIR.parent / "schemas"


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, raw[m.end() :].lstrip()


def _discover_skills() -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (prompts_by_name, resources_by_uri) from skills/*/SKILL.md, references/, and schemas/."""
    sdir = _skills_dir()
    prompts: dict[str, dict] = {}
    resources: dict[str, dict] = {}
    if sdir.is_dir():
        for child in sorted(sdir.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            raw = skill_md.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)
            desc = meta.get("description") or child.name
            prompts[child.name] = {
                "name": child.name,
                "description": desc,
                "body": body,
                "arguments": [
                    {
                        "name": "request",
                        "description": "可选：股票代码、市场或具体分析问题",
                        "required": False,
                    }
                ],
            }
            uri = f"stock-skill://{child.name}/SKILL.md"
            resources[uri] = {
                "uri": uri,
                "name": f"{child.name}/SKILL.md",
                "description": desc,
                "mimeType": "text/markdown",
                "path": skill_md,
            }
            refs_dir = child / "references"
            if refs_dir.is_dir():
                for ref in sorted(refs_dir.iterdir(), key=lambda p: p.name):
                    if ref.is_file() and ref.suffix.lower() in (".md", ".txt", ".yaml", ".yml", ".json"):
                        ref_uri = f"stock-skill://{child.name}/references/{ref.name}"
                        suffix = ref.suffix.lower()
                        mime = (
                            "text/markdown"
                            if suffix == ".md"
                            else ("application/json" if suffix == ".json" else "text/plain")
                        )
                        resources[ref_uri] = {
                            "uri": ref_uri,
                            "name": f"{child.name}/references/{ref.name}",
                            "description": f"Reference material for {child.name}",
                            "mimeType": mime,
                            "path": ref,
                        }
    schemas_dir = _schemas_dir()
    if schemas_dir.is_dir():
        for schema_file in sorted(schemas_dir.iterdir(), key=lambda p: p.name):
            if schema_file.is_file() and schema_file.suffix.lower() == ".json":
                schema_uri = f"stock-schema://{schema_file.name}"
                resources[schema_uri] = {
                    "uri": schema_uri,
                    "name": f"schemas/{schema_file.name}",
                    "description": f"Shared JSON schema: {schema_file.name}",
                    "mimeType": "application/json",
                    "path": schema_file,
                }
    return prompts, resources


def _search_tools(query: str, limit: int = 5) -> list[dict]:
    limit = max(1, min(int(limit) if isinstance(limit, (int, float)) else 5, 20))
    terms = [t for t in re.split(r"[\s,/，、]+", str(query or "").strip().casefold()) if t]
    if not terms:
        return []
    scored = []
    for entry in TOOL_LIST:
        name_low = entry["name"].casefold()
        desc_low = entry["description"].casefold()
        score = 0
        for term in terms:
            if name_low == term:
                score += 100
            elif term in name_low:
                score += 30
            if term in desc_low:
                score += 10
        if score > 0:
            scored.append((score, entry["name"], entry))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _, _, entry in scored[:limit]]


def _version() -> str:
    # Wheels carry no package.json; a source checkout is not pip-installed.
    with contextlib.suppress(metadata.PackageNotFoundError):
        return metadata.version("stock-analysis-plugin")
    try:
        return json.loads((tools.PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    except Exception:
        return "unknown"


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
                tools.terminate_tree(self.proc)


def _run(script: str, argv: list) -> str:
    # hermes.tools._run, but the child must not inherit the JSON-RPC stdin, and cancellation kills it.
    call = getattr(_current, "call", None) or _Call()
    with _SLOTS:
        return _run_slot(call, script, argv)


def _run_slot(call, script: str, argv: list) -> str:
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
                **tools.TREE_POPEN,
            )
    except Exception:
        return json.dumps({"error": "Python command failed; check interpreter/dependencies"})
    try:
        stdout, _ = proc.communicate(timeout=tools.command_timeout(script, argv) + 30)
    except subprocess.TimeoutExpired:
        tools.terminate_tree(proc)
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


def call_tool(params: dict, *, session_state: dict | None = None, on_tools_changed=None) -> dict:
    name = params.get("name")
    args = params.get("arguments", {})
    if not isinstance(args, dict):
        raise RpcError(-32602, "Tool arguments must be an object")

    if name == "stock_tool_search" and session_state and session_state.get("discovery"):
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {
                "content": [{"type": "text", "text": json.dumps({"error": "missing required argument: query"})}],
                "isError": True,
            }
        matches = _search_tools(query, args.get("limit", 5))
        added = False
        with session_state["lock"]:
            for entry in matches:
                if entry["name"] not in session_state["loaded"]:
                    session_state["loaded"].add(entry["name"])
                    added = True
        if added and on_tools_changed is not None:
            on_tools_changed()
        payload = {"query": query, "matched": len(matches), "tools": matches}
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "isError": False}

    schema = SCHEMAS.get(name) if isinstance(name, str) else None
    if schema is None:
        raise RpcError(-32602, f"Unknown tool: {name}")
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


def handle(method: str, params: dict, *, session_state: dict | None = None) -> dict:
    discovery = bool(session_state and session_state.get("discovery"))
    profile = (session_state or {}).get("profile", "full")
    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {
                "tools": {"listChanged": discovery},
                "prompts": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {"name": "stock-analysis", "version": _version()},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        if discovery:
            with session_state["lock"]:
                loaded = set(session_state["loaded"])
            exposed = [DISCOVERY_TOOL_DEF] + [t for t in TOOL_LIST if t["name"] in loaded]
            return {"tools": exposed}
        if profile == "core":
            return {"tools": [t for t in TOOL_LIST if t["name"] in CORE_TOOL_NAMES]}
        return {"tools": TOOL_LIST}
    if method == "prompts/list":
        prompts, _ = _discover_skills()
        return {
            "prompts": [
                {"name": p["name"], "description": p["description"], "arguments": p["arguments"]}
                for p in prompts.values()
            ]
        }
    if method == "prompts/get":
        name = params.get("name")
        prompts, _ = _discover_skills()
        prompt = prompts.get(name) if isinstance(name, str) else None
        if prompt is None:
            raise RpcError(-32602, f"Unknown prompt: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "Prompt arguments must be an object")
        req = arguments.get("request")
        text = f"{_MCP_SKILL_PREAMBLE}{prompt['body']}"
        if isinstance(req, str) and req.strip():
            text = f"{text}\n\n---\n用户请求：{req.strip()}"
        return {
            "description": prompt["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
    if method == "resources/list":
        _, resources = _discover_skills()
        return {
            "resources": [
                {
                    "uri": r["uri"],
                    "name": r["name"],
                    "description": r["description"],
                    "mimeType": r["mimeType"],
                }
                for r in resources.values()
            ]
        }
    if method == "resources/read":
        uri = params.get("uri")
        _, resources = _discover_skills()
        res = resources.get(uri) if isinstance(uri, str) else None
        if res is None:
            raise RpcError(-32602, f"Unknown resource: {uri}")
        content = res["path"].read_text(encoding="utf-8")
        return {"contents": [{"uri": res["uri"], "mimeType": res["mimeType"], "text": content}]}
    raise RpcError(-32601, f"Method not found: {method}")


def serve(stdin, stdout, *, discovery: bool = False, profile: str = "full") -> None:
    write_lock = threading.Lock()
    calls: dict = {}
    workers: list = []
    session_state = {"discovery": discovery, "profile": profile, "loaded": set(), "lock": threading.Lock()}

    def send(message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with write_lock:
            stdout.write(data)
            stdout.flush()

    def notify_tools_changed() -> None:
        send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    def reply(request_id, work, *args, **kwargs) -> dict:
        try:
            return {"jsonrpc": "2.0", "id": request_id, "result": work(*args, **kwargs)}
        except RpcError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": exc.message}}

    def run_call(request_id, params, call):
        _current.call = call
        if discovery:
            message = reply(
                request_id,
                call_tool,
                params,
                session_state=session_state,
                on_tools_changed=notify_tools_changed,
            )
        else:
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
        send(reply(request_id, handle, method, params, session_state=session_state))

    for call in list(calls.values()):  # client closed stdin: stop outstanding work
        call.cancel()
    for worker in workers:
        worker.join()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Stock Analysis MCP stdio server")
    parser.add_argument(
        "--discovery",
        action="store_true",
        default=os.environ.get("STOCK_MCP_DISCOVERY", "").lower() in ("1", "true", "yes"),
        help="Start with stock_tool_search only and load matched tools on demand",
    )
    parser.add_argument(
        "--profile",
        choices=("full", "core"),
        default=os.environ.get("STOCK_MCP_PROFILE", "full").lower() or "full",
        help="Static tool set profile when --discovery is off (full=all tools, core=18 core tools)",
    )
    args = parser.parse_args(argv)
    stdout = sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints must never corrupt the JSON-RPC stream
    with contextlib.suppress(KeyboardInterrupt):
        serve(sys.stdin.buffer, stdout, discovery=args.discovery, profile=args.profile)


if __name__ == "__main__":
    main()
