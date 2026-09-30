import json
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest


def judge(payload):
    return Mock(invoke=Mock(return_value=SimpleNamespace(content=json.dumps(payload))))


def test_trace_single_call_and_original_evidence(app):
    rows = [
        {"text": "额度50万", "verdict": "supported", "evidence": "额度50万"},
        {"text": "额度100万", "verdict": "fact_error", "evidence": "额度50万"},
        {"text": "明天提高额度", "verdict": "fabrication", "evidence": ""},
    ]
    llm = judge({"statements": rows})
    assert app.trace_hallucinations("额度？", "回答", ["额度50万"], "参考答案", llm) == {"statements": rows}
    llm.invoke.assert_called_once()
    prompt = llm.invoke.call_args.args[0]
    assert "参考答案" in prompt and "禁止 Markdown 代码块" in prompt


@pytest.mark.parametrize("payload", [None, [], {"statements": None}, {"statements": "bad"}])
def test_bad_structure_is_empty(app, payload):
    assert app.trace_hallucinations("q", "a", [], "g", judge(payload)) == {"statements": []}


@pytest.mark.parametrize("failure", [False, True])
def test_parse_or_provider_failure_is_empty(app, failure):
    llm = Mock()
    if failure:
        llm.invoke.side_effect = RuntimeError("offline failure")
    else:
        llm.invoke.return_value.content = "invalid JSON"
    assert app.trace_hallucinations("q", "a", [], "g", llm) == {"statements": []}
    llm.invoke.assert_called_once()


def test_invalid_verdict_and_forged_evidence_are_not_supported(app):
    llm = judge({"statements": [
        {"text": "a", "verdict": "unknown", "evidence": "原文"},
        {"text": "b", "verdict": "supported", "evidence": "伪造引用"},
        {"text": "c", "verdict": "fact_error", "evidence": ""},
        {"text": None, "verdict": "fabrication", "evidence": ""},
    ]})
    assert app.trace_hallucinations("q", "a", ["原文"], "g", llm) == {"statements": []}


def test_empty_context_and_refusal_with_claim_are_still_reviewed(app):
    rows = [{"text": "明天涨价", "verdict": "fabrication", "evidence": ""}]
    llm = judge({"statements": rows})
    assert app.trace_hallucinations("q", "文档未提及，但明天涨价", [], "g", llm)["statements"] == rows
    llm.invoke.assert_called_once()


def test_pipeline_trace_csv_counts_and_unchanged_gate(app, run_fake_pipeline, monkeypatch):
    traces = [
        {"statements": [{"text": "a", "verdict": "supported", "evidence": "a"},
                        {"text": "b", "verdict": "fact_error", "evidence": "a"}]},
        {"statements": [{"text": "c", "verdict": "fabrication", "evidence": ""}]},
    ]
    mock = Mock(side_effect=traces)
    monkeypatch.setattr(app, "trace_hallucinations", mock)
    result = run_fake_pipeline([pd.DataFrame({
        "faithfulness": [0.9, 0.0], "context_precision": [0.8, 0.0],
        "answer": ["answer", "answer"],
    })], case_types=["positive", "negative"])
    row = result["summary"][0]
    assert row["hallucination_statement_count"] == 2
    assert row["hallucination_ratio"] == 0.6667
    assert row["score"] == 0.85 and row["hallucination_rate"] == 0.1
    assert result["gate_passed"] is True
    assert mock.call_count == 2
    assert mock.call_args_list[1].args[:4] == ("q1", "offline answer", ["offline context"], "a1")
    df = pd.read_csv(app.BASE_DIR + "/ragas_report_model-0.csv")
    assert [json.loads(x) for x in df["hallucination_trace"]] == traces
    assert app.rag_answer.call_count == 2
