"""Layer 3 e2e: LLM → tool_use → real Hermes handler → answer.

Verifies the full agentic loop works: DeepSeek gets a natural-language task,
decides to call our tools, we execute them via Hermes handlers, and DeepSeek
produces a final answer.

Requires DEEPSEEK_API_KEY. Marked integration_llm — skipped by default.

Uses OpenAI SDK against DeepSeek's OpenAI-compatible endpoint.
Model: DEEPSEEK_MODEL env var (default: deepseek-v4-flash).
"""

import json
import os

import pytest

pytestmark = pytest.mark.integration_llm


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")


@pytest.fixture(scope="module")
def client():
    """OpenAI-compatible client for DeepSeek."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.fail("DEEPSEEK_API_KEY not set — layer 3 e2e requires a real API key")
    try:
        from openai import OpenAI
    except ImportError:
        pytest.fail("openai SDK not installed. add to requirements-dev.txt")
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


@pytest.fixture(scope="module")
def hermes_handlers():
    """Build the same handler map Hermes exposes to a live agent."""
    from hermes import register

    class Ctx:
        def __init__(self):
            self.handlers = {}
            self.schemas = {}

        def register_tool(self, name, toolset, schema, handler):
            self.handlers[name] = handler
            self.schemas[name] = schema

        def register_skill(self, name, path):
            pass

    ctx = Ctx()
    register(ctx)
    return ctx


def _to_openai_tools(schemas: dict, names: list[str]) -> list[dict]:
    """Convert Hermes schema dicts → OpenAI tools[] format."""
    return [
        {
            "type": "function",
            "function": {
                "name": schemas[n]["name"],
                "description": schemas[n]["description"],
                "parameters": schemas[n]["parameters"],
            },
        }
        for n in names
    ]


def _run_agent_loop(client, hermes_ctx, user_msg: str, tool_names: list[str], max_turns: int = 3) -> dict:
    """One-shot agent loop: LLM → tool_calls → handler → LLM → final answer."""
    tools = _to_openai_tools(hermes_ctx.schemas, tool_names)
    messages = [{"role": "user", "content": user_msg}]

    tool_calls_seen: list[dict] = []

    for _ in range(max_turns):
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        # Append the assistant message (must include tool_calls if present)
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            return {
                "final": msg.content or "",
                "tool_calls": tool_calls_seen,
                "turns": len(messages),
            }

        for call in msg.tool_calls:
            name = call.function.name
            args = json.loads(call.function.arguments or "{}")
            tool_calls_seen.append({"name": name, "args": args})

            if name not in hermes_ctx.handlers:
                result = json.dumps({"error": f"unknown tool: {name}"})
            else:
                result = hermes_ctx.handlers[name](args)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )

    return {
        "final": "",
        "tool_calls": tool_calls_seen,
        "turns": max_turns,
        "note": "hit max_turns without final answer",
    }


class TestSingleToolFlow:
    """LLM must call parse_stock_list — the user only gives natural language,
    never the ticker/code. The only way to get the answer is via the tool."""

    def test_parse_stock_list_english(self, client, hermes_handlers):
        """English: company names → US tickers. Doesn't hit akshare, fully stable."""
        result = _run_agent_loop(
            client,
            hermes_handlers,
            user_msg=(
                "I want to check on Nvidia and Apple. What are their exact stock tickers? "
                "Use a tool to extract them — don't guess from memory. "
                "Reply with just the tickers, comma-separated."
            ),
            tool_names=["parse_stock_list"],
        )

        names_called = [tc["name"] for tc in result["tool_calls"]]
        assert "parse_stock_list" in names_called, (
            f"LLM did not call parse_stock_list. tool_calls={result['tool_calls']}"
        )

        final = result["final"]
        assert "NVDA" in final, f"final answer missing NVDA: {final}"
        assert "AAPL" in final, f"final answer missing AAPL: {final}"

    def test_parse_stock_list_chinese(self, client, hermes_handlers):
        """Chinese: full names → A-share codes. parse_stock_list uses akshare
        for name resolution — if akshare is unreachable, tool returns unresolved.
        We still assert the LLM called the tool (main e2e contract) but only
        soft-assert on final codes to avoid coupling to upstream data health."""
        result = _run_agent_loop(
            client,
            hermes_handlers,
            user_msg=("我想查贵州茅台和宁德时代的股票代码。用工具从文本里提取，不要凭记忆猜。只回答代码，逗号分隔。"),
            tool_names=["parse_stock_list"],
        )

        names_called = [tc["name"] for tc in result["tool_calls"]]
        assert "parse_stock_list" in names_called, (
            f"LLM did not call parse_stock_list. tool_calls={result['tool_calls']}"
        )

        # Soft-check: if akshare worked, tool should have surfaced codes.
        # If akshare was flaky, tool returned {unresolved: [...]} and the LLM
        # will honestly say so — either way the e2e contract (LLM → tool → answer) held.
        tool_output = json.loads(hermes_handlers.handlers["parse_stock_list"]({"text": "贵州茅台和宁德时代"}))
        resolved_codes = {i["symbol"] for i in tool_output.get("items", []) if i.get("market") == "A"}
        if resolved_codes:
            final = result["final"]
            for expected in ("600519", "300750"):
                if expected in resolved_codes:
                    assert expected in final, f"tool resolved {expected} but final answer missing it: {final}"


class TestMultiToolFlow:
    """LLM picks the right tool from a set. Static-data tools only to avoid network flake."""

    def test_capabilities_flow(self, client, hermes_handlers):
        result = _run_agent_loop(
            client,
            hermes_handlers,
            user_msg="港股支持获取资金流数据（capital_flow）吗？用工具查一下再回答，答案只需 是/否。",
            tool_names=["get_market_capabilities"],
        )

        names_called = [tc["name"] for tc in result["tool_calls"]]
        assert "get_market_capabilities" in names_called, f"LLM did not query capabilities: {result['tool_calls']}"

        # capital_flow is A-share only → answer should be no/否
        final_lower = result["final"].lower()
        assert "否" in result["final"] or "不支持" in result["final"] or "no" in final_lower, (
            f"LLM should answer negative but said: {result['final']}"
        )
