"""Offline proofs that generation, metrics and Trace use their configured roles."""

import json
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
from langchain_core.outputs import Generation, LLMResult
from langchain_openai import ChatOpenAI
import httpx
import pandas as pd
import pytest

from backend.adapters.models.judging import ModelCallRecorder, build_judge, observe_judge
from backend.api.app import create_app
from backend.api import service
from backend.api.schemas import EvaluateRequest
from backend.core import pipeline


def frame():
    return pd.DataFrame({"question": ["问题"], "answer": ["已有回答"], "contexts": [["原文"]],
                         "ground_truth": ["参考"], "case_type": ["positive"],
                         **{key: [0.9] for key in ("faithfulness", "context_precision", "answer_relevancy", "answer_correctness", "context_recall")}})


def test_generation_metrics_and_trace_use_distinct_models(monkeypatch):
    generator, judge = SimpleNamespace(callbacks=None), SimpleNamespace(callbacks=None)
    answering = Mock(return_value=("已有回答", ["原文"]))
    tracing = Mock(return_value={"statements": [], "trace_status": "model_empty"})
    metrics = []

    def evaluate(**kwargs):
        assert kwargs["llm"] is judge
        assert kwargs["dataset"]["answer"] == ["已有回答"]
        assert kwargs["dataset"]["contexts"] == [["原文"]]
        metrics.extend(kwargs["metrics"])
        for callback in judge.callbacks:
            callback.on_llm_end(LLMResult(generations=[[Generation(text="ok")]],
                                         llm_output={"veriscope_response_model": "actual-judge", "token_usage": {"total_tokens": 7}}), run_id="offline")
        return SimpleNamespace(to_pandas=frame)

    monkeypatch.setattr(pipeline, "rag_answer", answering)
    monkeypatch.setattr(pipeline, "ragas_evaluate", evaluate)
    monkeypatch.setattr(pipeline, "trace_hallucinations", tracing)
    result = pipeline.run_single_model_raw([{"question": "问题", "ground_truth": "参考", "case_type": "positive"}],
                                           "retriever", "embedding", generator, True, judge_llm=judge)
    answering.assert_called_once_with("问题", "retriever", generator)
    assert tracing.call_args.args[-1] is judge
    assert len(metrics) == 5
    assert metrics[2].strictness == 1
    assert metrics[3].weights == [0.75, 0.25]
    assert result["judge_recorder"].response_models == ["actual-judge"]
    assert generator.callbacks is None and judge.callbacks is None


def test_metric_instances_do_not_carry_previous_judge(monkeypatch):
    seen = []
    def fake(**kwargs):
        seen.append(kwargs["metrics"])
        return SimpleNamespace(to_pandas=frame)
    monkeypatch.setattr(pipeline, "ragas_evaluate", fake)
    data = {"question": ["问题"], "answer": ["已有回答"], "contexts": [["原文"]], "ground_truth": ["参考"]}
    items = [{"question": "问题", "ground_truth": "参考", "case_type": "positive"}]
    for judge in ("judge-a", "judge-b"):
        pipeline.score_answer_rows(items, data, "E", judge, False)
    assert all(left is not right for left, right in zip(*seen))
    assert all(metric.llm is None for group in seen for metric in group if hasattr(metric, "llm"))


def test_run_models_persists_judge_in_rows_csv_and_summary(monkeypatch, tmp_path):
    judge, generator = object(), object()
    factory = Mock(side_effect=[judge, generator])
    recorder = ModelCallRecorder()
    recorder.on_llm_end(LLMResult(generations=[[Generation(text="ok")]], llm_output={"veriscope_response_model": "actual-judge"}), run_id="one")
    raw = {"df": frame(), "trace_total": 0, "trace_fact_error": 0, "trace_fabrication": 0,
           "pos_trace_total": 0, "pos_trace_halluc": 0, "judge_recorder": recorder}
    scoring = Mock(return_value=raw)
    monkeypatch.setattr(pipeline, "run_single_model_raw", scoring)
    cfg, judge_cfg = {"name": "small-generator"}, {"name": "larger-judge"}
    result = pipeline.run_models([cfg], {"testset": [], "retriever": "R", "embeddings": "E"}, factory,
                                 base_dir=str(tmp_path), trace_enabled=True, judge_cfg=judge_cfg)
    assert factory.call_args_list[0].args == (judge_cfg,)
    assert factory.call_args_list[1].args == (cfg,)
    assert scoring.call_args.args[3] is generator
    assert scoring.call_args.kwargs == {"judge_llm": judge}
    assert result["summary"][0]["judge_model"] == "larger-judge"
    assert result["summary"][0]["judge_response_models"] == ["actual-judge"]
    assert result["model_dfs"][cfg["name"]].iloc[0]["judge_mode"] == "configured"
    assert "larger-judge" in (tmp_path / "ragas_report_small-generator.csv").read_text(encoding="utf-8-sig")


def test_invalid_judge_does_not_generate_or_fallback(monkeypatch, tmp_path):
    scoring = Mock()
    monkeypatch.setattr(pipeline, "run_single_model_raw", scoring)
    factory = Mock(side_effect=ValueError("judge unavailable"))
    with pytest.raises(ValueError):
        pipeline.run_models([{"name": "generator"}], {}, factory, base_dir=str(tmp_path), trace_enabled=True,
                            judge_cfg={"name": "judge"})
    assert factory.call_count == 1
    scoring.assert_not_called()


def test_recorder_restores_callbacks_after_error_and_keeps_unknown():
    existing = Mock()
    llm = SimpleNamespace(callbacks=[existing])
    observer = ModelCallRecorder()
    with pytest.raises(ValueError):
        with observer.capture(llm):
            assert llm.callbacks == [existing, observer]
            observer.on_llm_error(ValueError("must not be logged"), run_id="bad")
            raise ValueError
    assert llm.callbacks == [existing]
    assert observer.response_models == []
    assert "must not be logged" not in json.dumps(observer.events)


@pytest.mark.parametrize("returned", ["requested-judge", "actual-judge", None])
def test_actual_sdk_never_invents_response_model_identity(returned):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        payload = {"id": "offline", "object": "chat.completion", "created": 1,
                   "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
        if returned is not None:
            payload["model"] = returned
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        original = ChatOpenAI(model="requested-judge", api_key="offline-placeholder", base_url="https://judge.invalid/v1",
                              http_client=client, max_retries=0, temperature=0)
        observed = observe_judge(original)
        assert observed.client is original.client and observed.http_client is client
        assert observed.temperature == original.temperature
        recorder = ModelCallRecorder()
        with recorder.capture(observed):
            answer = observed.invoke("固定问题与材料")
        assert calls == [{"messages": [{"content": "固定问题与材料", "role": "user"}],
                          "model": "requested-judge", "n": 1, "stream": False, "temperature": 0.0}]
        assert answer.response_metadata["veriscope_response_model"] == returned
        assert answer.response_metadata.get("model_name") == returned
        assert recorder.response_models == ([] if returned is None else [returned])
        assert recorder.events[0]["usage"]["total_tokens"] == 4


def test_sdk_unverified_model_name_is_left_unknown():
    observer = ModelCallRecorder()
    observer.on_llm_end(LLMResult(generations=[[Generation(text="ok")]],
                                 llm_output={"model_name": "possibly-requested-name"}), run_id="offline")
    assert observer.response_models == []


def test_shared_factory_is_used_by_comparison(monkeypatch):
    from backend.adapters.models import registry
    from tools.competition import judge_compare
    fake = Mock(return_value=object())
    monkeypatch.setattr(registry, "build_llm", fake)
    assert judge_compare.build_judge("deepseek") is fake.return_value
    assert fake.call_args.args[0]["name"] == judge_compare.JUDGES["deepseek"]["name"]
    fake.reset_mock()
    assert build_judge({"name": "custom"}, fake) is fake.return_value


def test_cli_wrapper_uses_shared_pipeline_and_keeps_old_defaults(monkeypatch):
    source = Path(pipeline.__file__).resolve().parents[2] / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    wrapper = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_pipeline")
    factory = Mock()
    namespace = {"get_llm": factory}
    exec(compile(ast.Module(body=[wrapper], type_ignores=[]), str(source), "exec"), namespace)
    evaluate = Mock(return_value={"gate_passed": False})
    monkeypatch.setattr(pipeline, "evaluate", evaluate)
    namespace["run_pipeline"](doc_path="demo.txt", judge_model_name="judge")
    assert evaluate.call_args.args[0]["judge_model_name"] == "judge"
    assert evaluate.call_args.kwargs["model_factory"] is factory
    namespace["run_pipeline"](doc_path="demo.txt")
    assert "judge_model_name" not in evaluate.call_args.args[0]


def test_html_report_records_request_and_actual_response(monkeypatch, tmp_path):
    from backend.reporting.html import generate_html_report
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"offline-image")
    output = tmp_path / "report.html"
    generate_html_report([{"model": "generator", "judge_model": "judge", "judge_mode": "configured",
                           "judge_response_models": ["actual-judge"]}], chart_path=str(chart), output_path=str(output))
    text = output.read_text(encoding="utf-8")
    assert "裁判请求模型" in text and "actual-judge" in text and "单独配置" in text
    assert "独立认证" in text


def test_api_accepts_judge_outside_tested_models_and_preserves_envelope(tmp_path):
    # Existing helper bootstraps Windows' event-loop wakeup sockets before its
    # network/dotenv/import guards. All actual ASGI requests stay under the guard.
    from test_api import _run_evaluation_http
    _run_evaluation_http(tmp_path, r'''
    fake = Mock(return_value=sample_result())
    with client_for(fake) as client:
        response = client.post("/evaluate", json={"model_names": ["deepseek-chat"], "judge_model_name": "glm-5.2-fast-preview"})
        assert response.status_code == 200
        assert len(response.json()) == 8
        assert fake.call_args.args[0]["judge_model_name"] == "glm-5.2-fast-preview"
        fake.reset_mock()
        assert client.post("/evaluate", json={"judge_model_name": "missing"}).status_code == 422
        fake.assert_not_called()
    ''')


@pytest.mark.parametrize("name", ["", " api-key ", "https://model", "key?secret"])
def test_judge_name_rejects_unregistered_transport_input(name):
    with pytest.raises(ValueError):
        EvaluateRequest(judge_model_name=name)
