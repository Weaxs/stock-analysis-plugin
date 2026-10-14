"""Fixed-data final-answer evaluation; model calls are separate from provider tests."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes.schemas import TOOL_SCHEMAS
from tests.analysis_quality import CASES, check_report, parse_json_response, review_report
from tests.e2e.test_hermes_llm import OPENAI_MODEL, _run_agent_loop
from tests.test_analysis_quality import reference_report

pytest_plugins = ["tests.e2e.test_hermes_llm"]

pytestmark = [
    pytest.mark.integration_llm,
    pytest.mark.skipif(
        not all(os.environ.get(key) for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL")),
        reason="Requires configured model endpoint, key and model",
    ),
]
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_final_report_is_grounded_in_fixed_evidence(client, case):
    schemas = {s["name"]: s for s in TOOL_SCHEMAS}
    ctx = SimpleNamespace(
        schemas=schemas,
        handlers={name: lambda args, payload=payload: json.dumps(payload) for name, payload in case["tools"].items()},
    )
    skill = (ROOT / "skills/stock-analysis/SKILL.md").read_text(encoding="utf-8")
    schema = (ROOT / "schemas/report_schema.json").read_text(encoding="utf-8")
    prompt = (
        "按以下 skill 分析600519；这是固定数据测试，以2026-10-14为分析日。"
        "必须先调用所有提供的工具取得证据。只有这些工具可用，无法覆盖skill其余维度时如实标注缺失。"
        "只输出最终报告 JSON，不加代码围栏。缺失、过期、同口径冲突或样本不足时降低置信度。"
        "evidence 必须保留原始数值，不将百分数与小数混为一谈；path 使用工具名加字段路径，数组下标用数字。"
        "path不含调用参数，例如get_financials.periods.0.roe；来源及时间未返回时必须填null，不用工具名或方法名代替来源。"
        "as_of只引用结果中的data_as_of并保留完整时间及其时区；report_date是财报报告期，要单独引用value，不能替代as_of。"
        "错误证据也保留原始字符串，例如value='provider unavailable'，不能加error:前缀。"
        "可引用已知返回对象中未提供的末级字段，但value必须为null并标注未知，不能编成空数组、字符串或数值。"
        "不能跨越不存在的对象或数组编造路径。未知现价时可以给等待数据的文字计划，不能编造数值价位。"
        "报告需核对所有返回的现价、数据时间、财务报告期及ROE（包括历史periods），有回测时引用total_trades和win_rate。"
        f"\nSkill:\n{skill}\nReport schema:\n{schema}"
    )
    if "run_backtest" in case["tools"]:
        prompt += "回测请求的策略文件路径使用fixture.yaml；这是请求参数，不代表已验证文件内容或数据覆盖期间。"
    result = _run_agent_loop(client, ctx, prompt, list(case["tools"]), max_turns=6)
    assert set(case["tools"]) <= {call["name"] for call in result["tool_calls"]}
    assert all(call["args"].get("symbol") == "600519" for call in result["tool_calls"])
    assert result["final"], "Model did not deliver a final report"
    report = parse_json_response(result["final"])
    review = review_report(client, OPENAI_MODEL, case, report, request_context=prompt, tool_calls=result["tool_calls"])
    assert check_report(case, report, review=review) == [], review


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_semantic_reviewer_accepts_curated_reference_reports(client, case):
    report = reference_report(case)
    review = review_report(client, OPENAI_MODEL, case, report)
    assert check_report(case, report, review=review) == [], review


@pytest.mark.parametrize("index", [0, 4], ids=["invented_price", "unfounded_prediction"])
def test_semantic_reviewer_rejects_correct_citations_with_false_conclusions(client, index):
    case = CASES[index]
    report = reference_report(case)
    report["core_conclusion"]["one_sentence"] = (
        "现价999元，公司ROE为99%，因此确定上涨。" if index == 0 else "样本充足，可保证长期100%胜率。"
    )
    report["data_quality"]["limitations"] = ["无须降级判断"]
    review = review_report(client, OPENAI_MODEL, case, report)
    assert isinstance(review, dict)
    assert any(
        review.get(key) is False for key in ("facts_supported", "limitations_honest", "conclusion_calibrated")
    ), review
    assert isinstance(review.get("issues"), list) and review["issues"], review
    assert check_report(case, report, review=review)
