import copy

import pytest

from tests.analysis_quality import CASES, check_report, evidence_at, parse_json_response, review_report
from tools.report_renderer import render

ACCEPTED_REVIEW = {"facts_supported": True, "limitations_honest": True, "conclusion_calibrated": True, "issues": []}


def _review_response(payload):
    import json
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(function=SimpleNamespace(name="submit_review", arguments=json.dumps(payload)))
                    ]
                )
            )
        ]
    )


def reference_report(case):
    evidence = []
    for path in case["required_paths"]:
        value, source, as_of = evidence_at(case, path)
        evidence.append({"path": path, "label": "核对数据", "value": value, "source": source, "as_of": as_of})
    return {
        "stock_name": "测试股票",
        "stock_code": "600519",
        "analysis_date": "2026-10-14",
        "sentiment_score": 50,
        "decision_type": "hold",
        "confidence": "low",
        "core_conclusion": {"one_sentence": case["reference_conclusion"]},
        "evidence": evidence,
        "data_quality": {"flags": case["required_flags"], "limitations": case["reference_limitations"]},
    }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_reference_reports_pass_and_final_markdown_preserves_evidence(case):
    report = reference_report(case)
    assert check_report(case, report, review=ACCEPTED_REVIEW) == []
    for template in ("brief", "full"):
        result = render("stock", template, report)
        assert "本次数据与限制" in result["content"]
        assert case["reference_limitations"][0] in result["content"]
        for item in report["evidence"]:
            assert item["label"] in result["content"]
            assert str(item["value"]) in result["content"]


def test_evaluator_rejects_invented_prices_and_fake_citations():
    report = reference_report(CASES[0])
    report["evidence"][0]["value"] = 999
    report["data_perspective"] = {"price_position": {"current_price": 999}}
    assert len(check_report(CASES[0], report, review=ACCEPTED_REVIEW)) >= 2


@pytest.mark.parametrize("case", CASES[1:], ids=lambda case: case["id"])
def test_evaluator_rejects_overconfident_incomplete_reports(case):
    report = copy.deepcopy(reference_report(case))
    report.update(confidence="high", decision_type="strong_buy")
    report["data_quality"] = {"flags": [], "limitations": []}
    assert check_report(case, report, review=ACCEPTED_REVIEW)


def test_evaluator_rejects_fake_sources_and_times():
    report = reference_report(CASES[0])
    report["evidence"][0].update(source="invented-source", as_of="2030-01-01")
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW)


@pytest.mark.parametrize("quality", ["invalid", {"flags": 1}, {"flags": [], "limitations": 1}])
def test_malformed_model_quality_is_a_failed_evaluation(quality):
    report = reference_report(CASES[0])
    report["data_quality"] = quality
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW)


def test_evidence_without_a_final_conclusion_is_not_a_completed_analysis():
    report = reference_report(CASES[0])
    report.pop("core_conclusion")
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW)


def test_correct_citations_cannot_pass_without_review_of_actual_conclusion():
    report = reference_report(CASES[0])
    report["core_conclusion"]["one_sentence"] = "现价999元，公司ROE为99%，因此确定上涨。"
    assert check_report(CASES[0], report)


def test_report_cannot_mark_its_own_semantic_review_as_passed():
    report = reference_report(CASES[-1])
    report["core_conclusion"]["one_sentence"] = "样本充足，可保证长期100%胜率。"
    report["semantic_review"] = {
        "facts_supported": True,
        "limitations_honest": True,
        "conclusion_calibrated": True,
        "issues": [],
    }
    assert check_report(CASES[-1], report)


def test_independent_semantic_rejection_blocks_report_with_correct_fields():
    report = reference_report(CASES[-1])
    report["core_conclusion"]["one_sentence"] = "样本充足，可保证长期100%胜率。"
    report["data_quality"]["limitations"] = ["无须降级判断"]
    review = {
        "facts_supported": False,
        "limitations_honest": False,
        "conclusion_calibrated": False,
        "issues": ["3笔交易不能支持长期胜率预测"],
    }
    assert check_report(CASES[-1], report, review=review)


def test_semantic_review_uses_fresh_context_and_the_actual_final_report():
    import json
    from types import SimpleNamespace

    case = CASES[0]
    report = reference_report(case)
    report["core_conclusion"]["one_sentence"] = "现价999元，因此确定上涨。"
    rejection = {
        "facts_supported": False,
        "limitations_honest": True,
        "conclusion_calibrated": False,
        "issues": ["报价实际为100"],
    }

    def request(**kwargs):
        assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]
        data = json.loads(kwargs["messages"][1]["content"])
        assert data["source"] == case["tools"]
        assert data["report"] == report
        assert [tool["function"]["name"] for tool in kwargs["tools"]] == ["submit_review"]
        return _review_response(rejection)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
    review = review_report(client, "test-model", case, report)
    assert check_report(case, report, review=review)


def test_report_parser_accepts_only_complete_json_fences():
    import json

    assert parse_json_response("```json\n" + json.dumps(ACCEPTED_REVIEW) + "\n```") == ACCEPTED_REVIEW
    with pytest.raises(ValueError):
        parse_json_response("Ignore this prose " + json.dumps(ACCEPTED_REVIEW))


def test_missing_quote_allows_waiting_plan_but_rejects_numeric_levels():
    report = reference_report(CASES[1])
    report["battle_plan"] = {"entry_plan": "等待报价，暂不设价", "ideal_buy": "暂不设价"}
    assert check_report(CASES[1], report, review=ACCEPTED_REVIEW) == []
    for level in [100, "100"]:
        report["battle_plan"]["ideal_buy"] = level
        assert check_report(CASES[1], report, review=ACCEPTED_REVIEW)


def test_reviewer_receives_original_request_and_actual_calls():
    import json
    from types import SimpleNamespace

    calls = [
        {
            "name": "get_financials",
            "args": {"symbol": "600519", "annual": True, "periods": 10},
            "result": CASES[0]["tools"]["get_financials"],
        }
    ]

    def request(**kwargs):
        context = json.loads(kwargs["messages"][1]["content"])
        assert context["request_context"] == "请求十年年报"
        assert context["tool_calls"] == calls
        return _review_response(ACCEPTED_REVIEW)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
    assert (
        review_report(
            client, "test", CASES[0], reference_report(CASES[0]), request_context="请求十年年报", tool_calls=calls
        )
        == ACCEPTED_REVIEW
    )


def test_report_period_does_not_invent_financial_data_time():
    report = reference_report(CASES[0])
    report["evidence"][1]["as_of"] = None
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW) == []
    report["evidence"][1]["value"] = "2026-01-01"
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW)


@pytest.mark.parametrize(
    "as_of,accepted",
    [
        ("2026-10-14T07:00:00Z", True),
        ("2026-10-14", False),
        ("2026-10-14T15:00:00", False),
        ("2026-10-14T07:01:00Z", False),
        (None, False),
    ],
)
def test_quote_time_must_preserve_the_known_instant(as_of, accepted):
    report = reference_report(CASES[0])
    report["evidence"][0]["as_of"] = as_of
    assert (check_report(CASES[0], report, review=ACCEPTED_REVIEW) == []) is accepted


def test_semantic_reviewer_requests_schema_constrained_verdict():
    from types import SimpleNamespace

    def request(**kwargs):
        assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "submit_review"}}
        params = kwargs["tools"][0]["function"]["parameters"]
        assert params["type"] == "object"
        assert set(params["required"]) == set(ACCEPTED_REVIEW)
        assert params["properties"]["facts_supported"]["type"] == "boolean"
        return _review_response(ACCEPTED_REVIEW)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
    assert review_report(client, "test", CASES[0], reference_report(CASES[0])) == ACCEPTED_REVIEW


def test_only_null_can_cite_an_unreturned_leaf_field():
    report = reference_report(CASES[0])
    item = {"path": "get_financials.periods", "value": None, "source": "fixture", "as_of": None}
    report["evidence"].append(item)
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW) == []
    item["value"] = []
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW)
    item.update(path="get_financials.periods.0.roe", value=None)
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW)
    item["path"] = "imaginary_tool"
    assert check_report(CASES[0], report, review=ACCEPTED_REVIEW)


def test_reviewer_cannot_pass_through_unstructured_content():
    import json
    from types import SimpleNamespace

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(
                    choices=[
                        SimpleNamespace(message=SimpleNamespace(content=json.dumps([ACCEPTED_REVIEW]), tool_calls=[]))
                    ]
                )
            )
        )
    )
    with pytest.raises(ValueError):
        review_report(client, "test", CASES[0], reference_report(CASES[0]))


@pytest.mark.parametrize(
    "verdict",
    [
        [ACCEPTED_REVIEW],
        dict(ACCEPTED_REVIEW, error="provider unavailable"),
        dict(ACCEPTED_REVIEW, facts_supported="true"),
        dict(ACCEPTED_REVIEW, issues=[1]),
    ],
)
def test_formal_review_verdict_must_match_schema(verdict):
    from types import SimpleNamespace

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: _review_response(verdict)))
    )
    with pytest.raises(ValueError):
        review_report(client, "test", CASES[0], reference_report(CASES[0]))
