"""LLM test budgets and timing logs remain independent of private payloads."""

import json
import sys
from types import SimpleNamespace

import pytest

from tests.e2e import test_hermes_llm as harness


def test_llm_client_is_bounded_and_stages_do_not_log_payloads(monkeypatch, capsys):
    call = SimpleNamespace(
        id="call", function=SimpleNamespace(name="parse_stock_list", arguments='{"text":"private input"}')
    )
    messages = iter(
        [
            SimpleNamespace(tool_calls=[call], content=None, model_dump=lambda **kw: {"role": "assistant"}),
            SimpleNamespace(tool_calls=[], content="private answer", model_dump=lambda **kw: {"role": "assistant"}),
        ]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=next(messages))])
            )
        )
    )
    options = {}

    def create_client(**kwargs):
        options.update(kwargs)
        return fake_client

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=create_client))
    monkeypatch.setenv("OPENAI_API_KEY", "private key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")
    monkeypatch.setattr(harness, "OPENAI_MODEL", "test-model")
    client = harness.client.__wrapped__()
    assert options["timeout"] == 120
    assert options["max_retries"] == 1
    ctx = SimpleNamespace(
        schemas={"parse_stock_list": {"name": "parse_stock_list", "description": "parse", "parameters": {}}},
        handlers={"parse_stock_list": lambda args: json.dumps({"private": "private result"})},
    )
    assert harness._run_agent_loop(client, ctx, "private request", ["parse_stock_list"])["final"] == "private answer"
    logs = capsys.readouterr().err
    assert "model:1" in logs and "model:2" in logs and "tool:parse_stock_list" in logs
    assert "private" not in logs and "example.invalid" not in logs


def test_timing_is_recorded_when_an_operation_fails(capsys):
    from tests.analysis_quality import timed_stage

    with pytest.raises(RuntimeError, match="private error"), timed_stage("model:1"):
        raise RuntimeError("private error")
    logs = capsys.readouterr().err
    assert "model:1" in logs and "seconds=" in logs
    assert "private error" not in logs
