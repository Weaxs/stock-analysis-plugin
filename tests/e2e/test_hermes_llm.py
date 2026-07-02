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
    """One-shot agent loop: LLM → tool_calls → handler → LLM → final answer.

    Returns:
      tool_calls: [{name, args, result_json}] — every tool invocation the LLM made,
                  with our tool's return value already parsed
      final: the LLM's final text answer (may be empty — some models return
             `content: null` after tool_use, that's model-side variability, not
             a contract violation)
    """
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

            if name not in hermes_ctx.handlers:
                result = json.dumps({"error": f"unknown tool: {name}"})
            else:
                result = hermes_ctx.handlers[name](args)

            try:
                result_parsed = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                result_parsed = None
            tool_calls_seen.append({"name": name, "args": args, "result": result_parsed})

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
    never the ticker/code. The only way to get the answer is via the tool.

    Assertions target the tool_use contract, not the LLM's final text:
      - LLM picked parse_stock_list
      - Args carried the natural-language input (contains the company names)
      - Our tool successfully extracted the expected symbols

    We deliberately do NOT assert on the LLM's final response — deepseek-v4-flash
    (like many models) sometimes returns content=null after tool_use. That's model
    variability, not a broken plugin contract.
    """

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

        # 1. LLM chose the right tool
        parse_calls = [tc for tc in result["tool_calls"] if tc["name"] == "parse_stock_list"]
        assert parse_calls, f"LLM did not call parse_stock_list. tool_calls={result['tool_calls']}"

        # 2. Tool successfully extracted both US tickers (across all its parse_stock_list calls)
        symbols_found = set()
        for call in parse_calls:
            for item in (call["result"] or {}).get("items", []):
                symbols_found.add(item["symbol"])
        assert "NVDA" in symbols_found, f"tool didn't extract NVDA. items across calls: {symbols_found}"
        assert "AAPL" in symbols_found, f"tool didn't extract AAPL. items across calls: {symbols_found}"

    def test_parse_stock_list_chinese(self, client, hermes_handlers):
        """Chinese: full names → A-share codes. Requires akshare for name resolution.

        Hard-assert LLM picked the tool. Soft-assert specific codes only when
        akshare actually resolved them — decouples from upstream data outages.
        """
        result = _run_agent_loop(
            client,
            hermes_handlers,
            user_msg=("我想查贵州茅台和宁德时代的股票代码。用工具从文本里提取，不要凭记忆猜。只回答代码，逗号分隔。"),
            tool_names=["parse_stock_list"],
        )

        parse_calls = [tc for tc in result["tool_calls"] if tc["name"] == "parse_stock_list"]
        assert parse_calls, f"LLM did not call parse_stock_list. tool_calls={result['tool_calls']}"

        # akshare probe outside the loop — did the resolver work at all this run?
        probe = json.loads(hermes_handlers.handlers["parse_stock_list"]({"text": "贵州茅台和宁德时代"}))
        probe_codes = {i["symbol"] for i in probe.get("items", []) if i.get("market") == "A"}

        if probe_codes:
            # akshare works — the LLM's tool calls should have yielded the same codes
            symbols_found = set()
            for call in parse_calls:
                for item in (call["result"] or {}).get("items", []):
                    if item.get("market") == "A":
                        symbols_found.add(item["symbol"])
            for expected in ("600519", "300750"):
                if expected in probe_codes:
                    assert expected in symbols_found, (
                        f"probe resolved {expected} but LLM's tool calls only yielded {symbols_found}. "
                        f"Args LLM sent: {[tc['args'] for tc in parse_calls]}"
                    )


class TestMultiToolFlow:
    """LLM picks the right tool from a set. Static-data tools only to avoid network flake."""

    def test_capabilities_flow(self, client, hermes_handlers):
        result = _run_agent_loop(
            client,
            hermes_handlers,
            user_msg="港股支持获取资金流数据（capital_flow）吗？用工具查一下再回答。",
            tool_names=["get_market_capabilities"],
        )

        cap_calls = [tc for tc in result["tool_calls"] if tc["name"] == "get_market_capabilities"]
        assert cap_calls, f"LLM did not query capabilities: {result['tool_calls']}"

        # The tool result itself must say capital_flow is unsupported for HK.
        # (Verifies the LLM asked about HK, not that its final answer was correct English/Chinese.)
        hk_calls = [c for c in cap_calls if (c["result"] or {}).get("market") == "HK"]
        assert hk_calls, f"LLM did not query HK market. args: {[c['args'] for c in cap_calls]}"

        unsupported = {u["tool"] for c in hk_calls for u in (c["result"] or {}).get("unsupported", [])}
        assert "get_capital_flow" in unsupported, (
            f"HK capabilities should mark get_capital_flow unsupported, got: {unsupported}"
        )
