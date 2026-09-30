"""backend.core.pipeline 的离线测试：evaluate 总编排调用链 + run_models 流程。

覆盖 Phase 3-B-7-B 迁移的 evaluate/run_models，mock 全部外部依赖（资源准备/测试集/
模型循环/绘图/HTML/门禁/历史），不调真实 API、不写真实产物、不读 .env。
"""
import contextlib
import functools
import io

import pandas as pd
import pytest
from unittest.mock import Mock

from backend import config
from backend.core import pipeline
from backend.storage.json_history import save_eval_record as real_save_eval_record


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t


SUMMARY_SAMPLE = [{
    "model": "m1", "faithfulness": 0.9, "context_precision": 0.88,
    "pos_faithfulness": 0.9, "pos_context_precision": 0.88,
}]


@pytest.fixture
def eval_env(monkeypatch, tmp_path):
    """安装 evaluate 的依赖 mock，返回可断言的上下文（calls/captured/clock）。"""
    calls = []
    clock = FakeClock()
    captured = {}

    def fake_select(model_names=None):
        calls.append("select_model_configs")
        return [{"name": "m1"}]

    def fake_prepare_resources(doc_path, progress_cb=None):
        calls.append("prepare_resources")
        clock.t += 10
        return {"embeddings": "E", "chunks": "C", "retriever": "R"}

    def fake_prepare_testset(request, resources, model_factory=None, progress_cb=None):
        calls.append("prepare_testset")
        clock.t += 10
        return [{"question": "q", "ground_truth": "g", "case_type": "positive"}]

    def fake_run_models(model_cfgs, resources, model_factory, *, base_dir, trace_enabled):
        calls.append("run_models")
        clock.t += 30
        captured["run_models_resources"] = resources
        captured["run_models_cfgs"] = model_cfgs
        captured["run_models_base_dir"] = base_dir
        captured["run_models_trace_enabled"] = trace_enabled
        return {"summary": SUMMARY_SAMPLE, "model_dfs": {"m1": pd.DataFrame({"faithfulness": [0.9]})}}

    def fake_chart(summary, faith_threshold, chart_path):
        calls.append("generate_evaluation_chart")
        clock.t += 20
        captured["chart_threshold"] = faith_threshold
        return chart_path

    def fake_html(summary, **kw):
        calls.append("generate_html_report")
        clock.t += 50
        captured["html_total_elapsed"] = kw.get("total_elapsed")
        captured["html_chart_path"] = kw.get("chart_path")
        captured["html_output_path"] = kw.get("output_path")
        return None

    def fake_gate(summary, model_dfs, faith_threshold, ctx_prec_threshold):
        calls.append("check_quality_gate")
        clock.t += 5
        captured["gate_thresholds"] = (faith_threshold, ctx_prec_threshold)
        return {"gate_passed": True, "gate_message": "✅ 质量门禁通过，允许发布"}

    def fake_save(record):
        calls.append("save_eval_record")
        clock.t += 5
        captured["record"] = record
        return None

    monkeypatch.setattr(pipeline, "select_model_configs", fake_select)
    monkeypatch.setattr(pipeline, "prepare_resources", fake_prepare_resources)
    monkeypatch.setattr(pipeline, "prepare_testset", fake_prepare_testset)
    monkeypatch.setattr(pipeline, "run_models", fake_run_models)
    monkeypatch.setattr(pipeline, "generate_evaluation_chart", fake_chart)
    monkeypatch.setattr(pipeline, "generate_html_report", fake_html)
    monkeypatch.setattr(pipeline, "check_quality_gate", fake_gate)
    monkeypatch.setattr(pipeline, "save_eval_record", fake_save)
    monkeypatch.setattr(pipeline, "time", clock)
    # 只重定向 summary CSV（真实 DataFrame 写入）；chart/html 已 mock 不写文件
    monkeypatch.setattr(pipeline, "SUMMARY_OUTPUT", str(tmp_path / "summary.csv"))

    return {"calls": calls, "clock": clock, "captured": captured, "tmp_path": tmp_path}


# ---- evaluate 调用链 ----
def test_evaluate_full_call_chain(eval_env):
    result = pipeline.evaluate(
        {"doc_path": "doc.txt", "faith_threshold": 0.75, "ctx_prec_threshold": 0.7},
        resources=None, model_factory=lambda c: "LLM", progress_cb=None,
    )
    assert eval_env["calls"] == [
        "select_model_configs", "prepare_resources", "prepare_testset", "run_models",
        "generate_evaluation_chart", "generate_html_report", "check_quality_gate", "save_eval_record",
    ]
    assert set(result.keys()) == {
        "summary", "summary_df", "gate_passed", "gate_message",
        "chart_path", "html_path", "model_dfs", "total_elapsed_seconds",
    }


def test_evaluate_chart_before_html_and_total_elapsed(eval_env):
    result = pipeline.evaluate({}, resources=None, model_factory=lambda c: "LLM")
    calls = eval_env["calls"]
    assert calls.index("generate_evaluation_chart") < calls.index("generate_html_report")
    # t_start=1000; resources+10→1010; testset+10→1020; run_models+30→1050; chart+20→1070
    # total_elapsed=70.0（含 chart，不含 HTML/gate/history 的 +50/+5/+5）
    assert result["total_elapsed_seconds"] == 70.0
    assert eval_env["captured"]["html_total_elapsed"] == 70.0


def test_evaluate_paths_follow_backend_config(eval_env):
    result = pipeline.evaluate({}, resources=None, model_factory=lambda c: "LLM")
    assert result["chart_path"] == config.CHART_PATH
    assert result["html_path"] == config.HTML_OUTPUT
    assert eval_env["captured"]["html_chart_path"] == config.CHART_PATH
    assert eval_env["captured"]["html_output_path"] == config.HTML_OUTPUT


def test_evaluate_writes_summary_csv_to_summary_output(eval_env):
    pipeline.evaluate({}, resources=None, model_factory=lambda c: "LLM")
    assert (eval_env["tmp_path"] / "summary.csv").exists()
    written = pd.read_csv(eval_env["tmp_path"] / "summary.csv")
    assert written.iloc[0]["model"] == "m1"


def test_evaluate_resources_preset_skips_prepare(eval_env):
    preset = {"embeddings": "E0", "chunks": "C0", "retriever": "R0",
              "testset": [{"question": "q0", "ground_truth": "g0", "case_type": "positive"}]}
    result = pipeline.evaluate({"doc_path": "doc.txt"}, resources=preset, model_factory=lambda c: "LLM")
    assert "prepare_resources" not in eval_env["calls"]
    assert "prepare_testset" not in eval_env["calls"]
    assert eval_env["captured"]["run_models_resources"] is preset
    assert len(result.keys()) == 8


def test_evaluate_threshold_default_and_zero(eval_env):
    pipeline.evaluate({}, resources=None, model_factory=lambda c: "LLM")  # 未传 → 默认
    assert eval_env["captured"]["chart_threshold"] == config.FAITH_THRESHOLD
    pipeline.evaluate({"faith_threshold": 0.0}, resources=None, model_factory=lambda c: "LLM")  # 0.0 保留
    assert eval_env["captured"]["chart_threshold"] == 0.0


def test_evaluate_run_models_receives_base_dir_and_trace(eval_env):
    pipeline.evaluate({}, resources=None, model_factory=lambda c: "LLM")
    assert eval_env["captured"]["run_models_base_dir"] == config.BASE_DIR
    assert eval_env["captured"]["run_models_trace_enabled"] == config.ENABLE_TRACE


def test_evaluate_history_failure_still_returns(monkeypatch, eval_env):
    # 真实 save_eval_record 写入不存在父目录 → 内部吞掉并打 warning，evaluate 仍返回
    bad_path = eval_env["tmp_path"] / "no_such_dir" / "history.json"
    monkeypatch.setattr(pipeline, "save_eval_record", functools.partial(real_save_eval_record, path=str(bad_path)))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = pipeline.evaluate({}, resources=None, model_factory=lambda c: "LLM")
    assert len(result.keys()) == 8
    assert "历史记录保存失败" in buf.getvalue()


@pytest.mark.parametrize("fail_step", ["csv", "chart", "html"])
def test_evaluate_failure_stops_downstream(monkeypatch, eval_env, fail_step):
    if fail_step == "csv":
        monkeypatch.setattr(pipeline, "SUMMARY_OUTPUT", str(eval_env["tmp_path"] / "no_dir" / "s.csv"))
    elif fail_step == "chart":
        monkeypatch.setattr(pipeline, "generate_evaluation_chart",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chart boom")))
    else:  # html
        monkeypatch.setattr(pipeline, "generate_html_report",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("html boom")))
    with pytest.raises((RuntimeError, OSError)):
        pipeline.evaluate({}, resources=None, model_factory=lambda c: "LLM")
    calls = eval_env["calls"]
    if fail_step == "csv":
        assert "generate_evaluation_chart" not in calls and "check_quality_gate" not in calls and "save_eval_record" not in calls
    elif fail_step == "chart":
        assert "generate_html_report" not in calls and "check_quality_gate" not in calls and "save_eval_record" not in calls
    else:
        assert "check_quality_gate" not in calls and "save_eval_record" not in calls


# ---- run_models 流程 ----
def _raw_df(faith=0.9, ctx=0.8):
    return pd.DataFrame({
        "faithfulness": [faith], "context_precision": [ctx], "answer_relevancy": [0.85],
        "answer_correctness": [0.8], "context_recall": [0.82], "answer": ["offline answer"],
        "case_type": ["positive"],
    })


def _raw_result(df):
    return {"df": df, "trace_total": 0, "trace_fact_error": 0, "trace_fabrication": 0,
            "pos_trace_total": 0, "pos_trace_halluc": 0}


def _resources():
    return {"testset": [{"question": "q", "ground_truth": "g", "case_type": "positive"}],
            "retriever": "R", "embeddings": "E"}


def test_run_models_writes_csv_and_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "run_single_model_raw",
                        lambda testset, retriever, embeddings, llm, trace_enabled: _raw_result(_raw_df()))
    result = pipeline.run_models(
        [{"name": "deepseek-chat"}], _resources(), Mock(return_value="LLM"),
        base_dir=str(tmp_path), trace_enabled=True,
    )
    assert len(result["summary"]) == 1
    assert result["summary"][0]["model"] == "deepseek-chat"
    assert result["summary"][0]["score"] == 0.85  # round((0.9+0.8)/2, 4)
    assert "deepseek-chat" in result["model_dfs"]
    assert (tmp_path / "ragas_report_deepseek-chat.csv").exists()


def test_run_models_partial_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "run_single_model_raw",
                        lambda t, r, e, llm, te: _raw_result(_raw_df()))
    factory = Mock(side_effect=["LLM-ok", RuntimeError("fake provider failure")])
    result = pipeline.run_models(
        [{"name": "m0"}, {"name": "m1"}], _resources(), factory,
        base_dir=str(tmp_path), trace_enabled=True,
    )
    ok, failed = result["summary"]
    assert ok["model"] == "m0" and ok["score"] == 0.85
    assert failed["model"] == "m1" and failed["faithfulness"] is None
    assert "m0" in result["model_dfs"] and "m1" not in result["model_dfs"]


def test_run_models_all_failure_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "run_single_model_raw",
                        lambda t, r, e, llm, te: _raw_result(_raw_df()))
    factory = Mock(side_effect=[RuntimeError("a"), RuntimeError("b")])
    with pytest.raises(RuntimeError, match="所有模型评测全部失败"):
        pipeline.run_models(
            [{"name": "m0"}, {"name": "m1"}], _resources(), factory,
            base_dir=str(tmp_path), trace_enabled=True,
        )
