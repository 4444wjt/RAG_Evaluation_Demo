"""Trace 服务的离线解析、过滤和空结果分类回归。"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.services.trace import (
    TRACE_PROCESSING_VERSION,
    _parse_trace_payload,
    trace_hallucinations,
)


def response(content, **metadata):
    return SimpleNamespace(content=content, response_metadata=metadata)


def llm_for(payload, **metadata):
    return Mock(invoke=Mock(return_value=response(payload, **metadata)))


def supported_row(text="星语产品具备三项核心特性", evidence="核心特性包括安全、稳定和易用"):
    return {"text": text, "verdict": "supported", "evidence": evidence}


def test_qwen_style_multi_statement_answer_is_preserved_without_synthesis():
    rows = [
        supported_row("星语产品具备安全性", "核心特性包括安全、稳定和易用"),
        supported_row("星语产品具备稳定性", "核心特性包括安全、稳定和易用"),
        supported_row("星语产品具备易用性", "核心特性包括安全、稳定和易用"),
    ]
    result = trace_hallucinations(
        "星语产品具备哪三大核心特性？",
        "星语产品具备安全、稳定和易用三项核心特性。",
        ["核心特性包括安全、稳定和易用"],
        "安全、稳定和易用",
        llm_for(json.dumps({"statements": rows}, ensure_ascii=False), model_name="trace-model"),
    )

    assert result["statements"] == rows
    assert result["trace_status"] == "ok"
    assert result["trace_attempts"] == 1
    assert result["trace_diagnostics"]["input_statement_count"] == 3
    assert result["trace_diagnostics"]["returned_model"] == "trace-model"


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({"statements": [supported_row()]}),
        json.dumps([supported_row()]),
        "```json\n" + json.dumps({"statements": [supported_row()]}) + "\n```",
        [{"type": "text", "text": json.dumps({"statements": [supported_row()]})}],
    ],
)
def test_trace_accepts_provider_json_shapes_without_changing_contract(payload):
    result = trace_hallucinations("q", "a", ["核心特性包括安全、稳定和易用"], "g", llm_for(payload))
    assert len(result["statements"]) == 1
    assert result["trace_status"] == "ok"
    assert result["trace_attempts"] == 1


def test_single_fact_normal_answer_generates_trace_with_evidence():
    """正常单事实回答：应生成 1 条 supported 陈述并关联原文证据。"""
    result = trace_hallucinations(
        "星云科技最新一轮B轮融资金额是多少？",
        "星云科技最新一轮B轮融资金额达5亿元人民币。",
        ["星云科技最新一轮B轮融资金额达5亿元人民币。"],
        "5亿元",
        llm_for(json.dumps({"statements": [{
            "text": "星云科技最新一轮B轮融资金额达5亿元人民币",
            "verdict": "supported",
            "evidence": "星云科技最新一轮B轮融资金额达5亿元人民币",
        }]}, ensure_ascii=False)),
    )
    assert result["trace_status"] == "ok"
    assert len(result["statements"]) == 1
    assert result["statements"][0]["verdict"] == "supported"
    assert result["statements"][0]["evidence"] == "星云科技最新一轮B轮融资金额达5亿元人民币"
    assert result["trace_diagnostics"]["answer_mode"] == "normal"
    assert result["trace_diagnostics"]["answer_kind"] == "substantive_or_unknown"
    assert result["trace_diagnostics"]["input_statement_count"] == 1


def test_pure_refusal_empty_trace_is_distinguished_from_substantive_empty_trace():
    refusal = trace_hallucinations(
        "材料没有回答什么？",
        "根据提供的文档，无法回答该问题。",
        ["文档内容"],
        "",
        llm_for('{"statements": []}'),
    )
    substantive = trace_hallucinations(
        "星语产品具备哪三大核心特性？",
        "星语产品具备安全、稳定和易用三项核心特性。",
        ["核心特性包括安全、稳定和易用"],
        "安全、稳定和易用",
        llm_for('{"statements": []}'),
    )

    assert refusal["trace_status"] == "empty_answer"
    assert refusal["trace_diagnostics"]["answer_kind"] == "pure_refusal"
    assert refusal["trace_diagnostics"]["answer_mode"] == "refusal"
    assert substantive["trace_status"] == "model_empty"
    assert substantive["trace_diagnostics"]["answer_kind"] == "substantive_or_unknown"
    assert substantive["trace_diagnostics"]["answer_mode"] == "normal"
    assert refusal["trace_attempts"] == substantive["trace_attempts"] == 1


@pytest.mark.parametrize(
    "answer",
    [
        "根据材料无法回答该问题，因为没有对应条目。",
        "该问题的前提不成立，原文显示产品仅有两项特性。",
    ],
)
def test_refusal_explanation_and_wrong_premise_are_not_rewritten_as_trace(answer):
    result = trace_hallucinations("q", answer, ["原文"], "g", llm_for('{"statements": []}'))
    assert result["statements"] == []
    assert result["trace_status"] == "model_empty"
    assert result["trace_diagnostics"]["answer_kind"] == "substantive_or_unknown"


def test_answer_mode_keeps_refusal_explanation_and_premise_correction_separate():
    refusal = trace_hallucinations(
        "材料没有回答什么？",
        "根据材料无法回答该问题，因为没有对应条目。",
        ["文档内容"],
        "",
        llm_for('{"statements": []}'),
    )
    correction = trace_hallucinations(
        "产品有三项特性吗？",
        "该问题的前提不成立，原文显示产品有两项特性。",
        ["原文显示产品有两项特性"],
        "",
        llm_for('{"statements": []}'),
    )

    assert refusal["trace_diagnostics"]["answer_mode"] == "refusal_explanation"
    assert correction["trace_diagnostics"]["answer_mode"] == "premise_correction"


def test_wrong_premise_correction_keeps_the_correction_evidence():
    payload = {
        "statements": [{
            "text": "该问题的前提不成立，原文显示产品有两项特性。",
            "verdict": "supported",
            "evidence": "原文显示产品有两项特性",
        }]
    }
    result = trace_hallucinations(
        "产品有三项特性吗？",
        "该问题的前提不成立，原文显示产品有两项特性。",
        ["原文显示产品有两项特性"],
        "",
        llm_for(json.dumps(payload, ensure_ascii=False)),
    )

    assert result["trace_status"] == "ok"
    assert result["statements"] == payload["statements"]


def test_parse_error_is_recorded_without_retry_or_fabricated_statement():
    llm = llm_for("这不是 JSON")
    result = trace_hallucinations("q", "回答", ["原文"], "g", llm)
    assert result["statements"] == []
    assert result["trace_status"] == "parse_error"
    assert result["trace_attempts"] == 1
    assert result["trace_diagnostics"]["raw_output"] == "这不是 JSON"
    llm.invoke.assert_called_once()


def test_partial_filter_keeps_valid_evidence_and_explains_rejected_items():
    payload = {
        "statements": [
            supported_row(text="有效陈述"),
            {"text": "伪造引用", "verdict": "supported", "evidence": "不在上下文"},
        ]
    }
    result = trace_hallucinations("q", "回答", ["核心特性包括安全、稳定和易用"], "g", llm_for(json.dumps(payload)))
    assert [item["text"] for item in result["statements"]] == ["有效陈述"]
    assert result["trace_status"] == "partial"
    assert result["trace_diagnostics"]["rejected_statements"] == [
        {"index": 1, "reason": "evidence_not_in_context"}
    ]


def test_all_invalid_candidates_are_not_presented_as_empty_model_response():
    payload = {"statements": [{"text": "断言", "verdict": "supported", "evidence": "不存在"}]}
    result = trace_hallucinations("q", "回答", ["原文"], "g", llm_for(json.dumps(payload)))
    assert result["statements"] == []
    assert result["trace_status"] == "filtered_all"
    assert result["trace_diagnostics"]["input_statement_count"] == 1


def test_call_error_is_explicit_and_does_not_retry():
    llm = Mock(invoke=Mock(side_effect=RuntimeError("offline")))
    result = trace_hallucinations("q", "回答", ["原文"], "g", llm)
    assert result["statements"] == []
    assert result["trace_status"] == "call_error"
    assert result["trace_diagnostics"]["error_type"] == "RuntimeError"
    assert result["trace_attempts"] == 1
    llm.invoke.assert_called_once()


def test_top_level_json_with_explanatory_suffix_is_still_parsed():
    content = json.dumps({"statements": [supported_row()]}) + "\n处理完成。"
    payload = _parse_trace_payload(content)
    assert payload["statements"][0]["text"] == "星语产品具备三项核心特性"
    assert TRACE_PROCESSING_VERSION == "trace-json-v2"
