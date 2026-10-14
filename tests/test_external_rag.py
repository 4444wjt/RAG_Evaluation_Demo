"""Actual client protocol and complete offline replay, without a network/model call."""

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from langchain_core.outputs import Generation, LLMResult
import pytest

from backend.adapters import rag_application as app
from tools.competition import evaluate_external as tool


def item(item_id="Q01"):
    return {"id": item_id, "question": "最高限额？", "ground_truth": "50万元", "case_type": "positive",
            "contexts": ["平台资料不能发出"], "expected": "秘密标签"}


def response(item_id="Q01", **patch):
    return {"schemaVersion": app.SCHEMA_VERSION, "id": item_id, "status": "answered", "answer": "50万元",
            "contexts": [{"text": "最高限额为50万元。", "sourceId": "law", "location": "第五条", "rank": 1, "score": 0.83}],
            "error": None, "meta": {"model": "app-generator"}, **patch}


def call_with(payload, *, status=200, text=None, headers=None, request_item=None):
    calls = []
    def handler(request):
        calls.append(request)
        if text is not None:
            return httpx.Response(status, text=text, headers=headers)
        return httpx.Response(status, json=payload, headers=headers)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = app.call_application("http://127.0.0.1:9000/rag", request_item or item(), client=client)
    return result, calls


def test_request_sends_only_id_and_question_and_preserves_context_order():
    supplied = response(contexts=[{"text": "第二段", "rank": 2}, {"text": "第一段", "rank": 1}])
    result, calls = call_with(supplied)
    assert json.loads(calls[0].content) == {"schemaVersion": app.SCHEMA_VERSION, "id": "Q01", "question": "最高限额？"}
    assert result.usable and result.response == supplied
    assert len(calls) == 1
    assert result.response["contexts"][0]["text"] == "第二段"


@pytest.mark.parametrize("patch,outcome", [
    ({"schemaVersion": "future"}, "version_mismatch"),
    ({"id": "Q02"}, "id_mismatch"),
    ({"status": "unknown"}, "invalid_response"),
    ({"answer": None}, "invalid_response"),
    ({"answer": "  "}, "invalid_response"),
    ({"contexts": None}, "invalid_response"),
    ({"contexts": [{"text": "x"}] * 21}, "invalid_response"),
    ({"contexts": [{"text": "x" * 8001}]}, "invalid_response"),
    ({"contexts": [{"text": "原文", "rank": True}]}, "invalid_response"),
    ({"error": {"code": "X", "message": "bad"}}, "invalid_response"),
])
def test_invalid_responses_are_failures_not_empty_evidence(patch, outcome):
    result, calls = call_with(response(**patch))
    assert result.outcome == outcome and not result.usable
    assert result.response == response(**patch)
    assert len(calls) == 1


def test_empty_contexts_and_refusals_are_preserved_without_inventing_trace():
    for status in ("answered", "refused"):
        result, _ = call_with(response(status=status, contexts=[]))
        assert result.usable
        assert result.response["contexts"] == []
        assert "statements" not in result.response


def test_context_size_boundary_keeps_every_character():
    passages = [{"text": "原" * app.MAX_CONTEXT_CHARS} for _ in range(app.MAX_CONTEXTS)]
    result, _ = call_with(response(contexts=passages))
    assert result.usable and result.response["contexts"] == passages


def test_invalid_utf8_is_not_silently_replaced():
    def handler(request):
        return httpx.Response(200, content=b'{"answer":"\xff"}')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = app.call_application("http://127.0.0.1:9000/rag", item(), client=client)
    assert result.outcome == "encoding_error" and result.response_text is None
    assert result.response_sha256 is not None


def test_duplicate_json_id_is_rejected_instead_of_taking_last_value():
    result, _ = call_with(None, text='{"schemaVersion":"veriscope.e2e.v1","id":"Q02","id":"Q01"}')
    assert result.outcome == "parse_error"
    assert result.response_sha256 is not None


@pytest.mark.parametrize("text,status,outcome", [
    ("not JSON", 200, "parse_error"), ("[]", 200, "invalid_response"),
    ('{"score": NaN}', 200, "parse_error"), ("service down", 503, "http_error"),
    ("redirect", 307, "http_error"), ("x" * (app.MAX_RESPONSE_BYTES + 1), 200, "response_too_large"),
], ids=["invalid-json", "array", "non-finite", "http-error", "redirect", "oversized-response"])
def test_transport_and_json_failures_are_distinct(text, status, outcome):
    result, calls = call_with(None, text=text, status=status, headers={"Location": "https://elsewhere.invalid"})
    assert result.outcome == outcome and len(calls) == 1


def test_application_error_code_is_retained():
    result, _ = call_with(response(status="error", answer=None, contexts=None,
                                   error={"code": "RETRIEVAL_TIMEOUT", "message": "未完成"}))
    assert result.outcome == "application_error" and result.error_code == "RETRIEVAL_TIMEOUT"


def test_auth_is_environment_only_and_timeouts_do_not_retry(monkeypatch):
    monkeypatch.setenv("RAG_APP_TOKEN", "fake-token-for-test")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer fake-token-for-test"
        raise httpx.ReadTimeout("do not echo this", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = app.call_application("http://127.0.0.1:9000/rag", item(), token_env="RAG_APP_TOKEN", client=client)
    assert result.outcome == "timeout" and len(calls) == 1
    assert "fake-token-for-test" not in json.dumps(asdict(result))


@pytest.mark.parametrize("endpoint", ["file:///tmp/a", "https://u:secret@example.invalid", "https://a.invalid/?token=x", "https://a.invalid/#x"])
def test_endpoint_rejects_credentials_and_unsafe_schemes(endpoint):
    with pytest.raises(ValueError):
        app.checked_endpoint(endpoint)


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    cfg = {"name": "offline-judge", "base_url": "https://judge.invalid/v1", "api_key_env": "OFFLINE_JUDGE_KEY"}
    monkeypatch.setattr(tool, "model_config", lambda _name: deepcopy(cfg))
    monkeypatch.setenv("OFFLINE_JUDGE_KEY", "fake-judge-key-for-test")
    source = tmp_path / "input.json"
    source.write_text(json.dumps([item("Q01"), item("Q02")], ensure_ascii=False), encoding="utf-8")
    directory = tmp_path / "archive"
    tool.prepare(source, directory, "http://127.0.0.1:9000/rag", "offline-judge")
    return directory


def fake_scorer(item_value, supplied, _embeddings, llm):
    for handler in llm.callbacks:
        handler.on_llm_end(LLMResult(generations=[[Generation(text="ok")]], llm_output={
            "veriscope_response_model": "actual-judge", "token_usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}}), run_id=item_value["id"])
    return {"id": item_value["id"], "question": item_value["question"], "ground_truth": item_value["ground_truth"],
            "answer": supplied["answer"], "contexts": [value["text"] for value in supplied["contexts"]],
            "case_type": "positive", "application_status": supplied["status"], "refusal_status_agrees": True,
            "hallucination_trace": json.dumps({"statements": [], "trace_status": "model_empty"}),
            "judge_response_models": ["actual-judge"], **{key: 0.9 for key in tool.METRICS}}


def test_complete_batch_is_serial_separate_hashed_and_reproducible(prepared):
    order = []
    def caller(_endpoint, case, **_kwargs):
        order.append(case["id"])
        return app.ApplicationCall(app.request_body(case), response(case["id"]), None, 200, 0.1, "completed")
    result = tool.run(prepared, allow_model_calls=True, application_call=caller,
                      resources_factory=lambda _cfg: ("E", SimpleNamespace(callbacks=None)), scorer=fake_scorer)
    assert order == ["Q01", "Q02"]
    assert result["retrievalParty"] == "application" and result["evaluated"] == 2
    assert result["gate"]["status"] == "PASS"
    assert result["usage"]["total_tokens"] == 20 and result["judgeCalls"] == 2
    assert all(result["validCounts"][key] == 2 for key in tool.METRICS)
    assert tool.analyze(prepared) == result
    tool.verify(prepared)
    assert (prepared / "report.html").is_file()
    assert (prepared / "manifest.json").name in (prepared / "SHA256SUMS.txt").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="不自动整批重跑"):
        tool.run(prepared, allow_model_calls=True)


def test_failure_does_not_turn_into_zero_and_later_items_continue(prepared):
    def caller(_endpoint, case, **_kwargs):
        if case["id"] == "Q01":
            return app.ApplicationCall(app.request_body(case), None, "bad", 500, 0.1, "http_error")
        return app.ApplicationCall(app.request_body(case), response(case["id"]), None, 200, 0.1, "completed")
    result = tool.run(prepared, allow_model_calls=True, application_call=caller,
                      resources_factory=lambda _cfg: ("E", SimpleNamespace(callbacks=None)), scorer=fake_scorer)
    assert result["evaluated"] == 1 and len(result["failed"]) == 1
    assert result["summary"]["faithfulness"] == 0.9
    assert result["validCounts"]["faithfulness"] == 1 and result["total"] == 2


def test_no_authorization_or_tampered_input_cannot_call_anything(prepared):
    caller = Mock()
    with pytest.raises(ValueError, match="未授权"):
        tool.run(prepared, application_call=caller)
    (prepared / "testset.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="校验失败"):
        tool.run(prepared, allow_model_calls=True, application_call=caller)
    caller.assert_not_called()


def test_all_failed_has_no_gate_verdict_or_made_up_metrics(prepared):
    def caller(_endpoint, case, **_kwargs):
        return app.ApplicationCall(app.request_body(case), None, None, None, 0.1, "timeout")
    result = tool.run(prepared, allow_model_calls=True, application_call=caller,
                      resources_factory=lambda _cfg: ("E", SimpleNamespace(callbacks=None)), scorer=Mock())
    assert len(result["failed"]) == 2 and result["summary"] is None
    assert result["gate"]["passed"] is None and result["gate"]["status"] == "not_evaluated"
    assert all(value == 0 for value in result["validCounts"].values())
    assert result["usage"]["total_tokens"] is None


def test_judge_failure_usage_and_credential_redaction_are_retained(prepared):
    def caller(_endpoint, case, **_kwargs):
        supplied = response(case["id"], meta={"token": "unknown-secret-from-app"})
        return app.ApplicationCall(app.request_body(case), supplied, json.dumps(supplied), 200, 0.1, "completed")
    def scorer(case, supplied, embeddings, llm):
        fake_scorer(case, supplied, embeddings, llm)
        raise RuntimeError("fake-judge-key-for-test must not leak")
    result = tool.run(prepared, allow_model_calls=True, application_call=caller,
                      resources_factory=lambda _cfg: ("E", SimpleNamespace(callbacks=None)), scorer=scorer)
    assert result["judgeCalls"] == 2 and result["usage"]["total_tokens"] == 20
    assert len(result["failed"]) == 2
    archived = (prepared / "records.jsonl").read_text(encoding="utf-8")
    assert "fake-judge-key-for-test" not in archived and "unknown-secret-from-app" not in archived
    assert all(json.loads(line)["redactionApplied"] for line in archived.splitlines())


def test_interrupt_keeps_partial_record_and_does_not_restart(prepared):
    def caller(*args, **kwargs):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        tool.run(prepared, allow_model_calls=True, application_call=caller,
                 resources_factory=lambda _cfg: ("E", SimpleNamespace(callbacks=None)))
    result = tool.analyze(prepared)
    assert result["status"] == "interrupted" and result["applicationCalls"] == 1
    assert len(result["failed"]) == 1 and result["notAttempted"] == ["Q02"]


def test_duplicate_ids_and_missing_references_are_rejected(tmp_path):
    for cases in ([item(), item()], [{"id": "Q01", "question": "问题"}]):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(cases), encoding="utf-8")
        with pytest.raises(ValueError):
            tool.load_cases(path)
