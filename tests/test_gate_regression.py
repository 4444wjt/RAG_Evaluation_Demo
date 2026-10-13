"""回归对比（backend/core/regression.py 与 gate_replay.py --baseline）的离线测试。

真实样例：两次 5 题预跑是同一组题目（中文兼容修正前 / 后），门禁从 PASS 变为 FAIL；
逐题配对后，正向忠实度只在可配对的 3 题上平均下降 0.0333，主要差异来自基线缺分的一题。
"""
from pathlib import Path
import importlib.util
import json
import xml.etree.ElementTree as ET

from hypothesis import given, settings, strategies as st

from backend.core import regression as reg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = PROJECT_ROOT / "docs" / "competition" / "evidence"
PILOT_BEFORE = EVIDENCE / "20261005T101847Z-pilot-c72cb679" / "api-response.json"
PILOT_AFTER = EVIDENCE / "20261005T131353Z-pilot-35f623cb" / "api-response.json"
FULL_RUN = EVIDENCE / "20261005T144504Z-full-39511424" / "api-response.json"
JUDGE_SUMMARY = PROJECT_ROOT / "docs" / "competition" / "judge-comparison" / "20261011-qwen72b" / "summary.json"

spec = importlib.util.spec_from_file_location("gate_replay", PROJECT_ROOT / "scripts" / "gate_replay.py")
gate_replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate_replay)


def load(path):
    return gate_replay.load_result(path)


def synthetic(faith_scores, pos_faith=0.9, pos_ctx=0.9, ctx_scores=None, model="m"):
    ctx_scores = ctx_scores or [1.0] * len(faith_scores)
    rows = [{"question": f"q{i}", "case_type": "positive", "faithfulness": f, "context_precision": c}
            for i, (f, c) in enumerate(zip(faith_scores, ctx_scores))]
    rows.append({"question": "neg", "case_type": "negative", "faithfulness": 0.0, "context_precision": 0.0})
    return {"summary": [{"model": model, "faithfulness": 0.5, "context_precision": 0.5,
                         "pos_faithfulness": pos_faith, "pos_context_precision": pos_ctx}],
            "model_dfs": {model: rows}}


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def metric(comparison, key):
    return next(m for m in comparison["metrics"] if m["key"] == key)


def test_noise_floor_matches_archived_same_judge_rerun():
    rerun = json.loads(JUDGE_SUMMARY.read_text(encoding="utf-8"))["fullRun"]["vsOriginal"]["self-rerun"]
    assert reg.NOISE_FLOOR == {"pos_faithfulness": rerun["faithfulness"]["meanAbsDiff"],
                               "pos_context_precision": rerun["context_precision"]["meanAbsDiff"]}


def test_identical_runs_show_no_regression():
    comparison = reg.compare(load(FULL_RUN), load(FULL_RUN))
    assert comparison["shared"] == 20 and not comparison["confirmed"]
    for m in comparison["metrics"]:
        assert m["mean_delta"] == 0 and m["status"] == reg.NONE and not m["drops"]


def test_real_pilot_pair_separates_paired_drop_from_newly_scored_question():
    comparison = reg.compare(load(PILOT_BEFORE), load(PILOT_AFTER))
    faith = metric(comparison, "pos_faithfulness")
    assert (faith["baseline_mean"], faith["candidate_mean"]) == (0.8333, 0.6)
    assert faith["paired"] == 3 and faith["mean_delta"] == -0.0333
    assert faith["status"] == reg.POSSIBLE and faith["interval"][1] == 0.0
    assert [(d["baseline"], d["candidate"]) for d in faith["drops"]] == [(0.5, 0.4)]
    assert [(o["baseline"], o["candidate"]) for o in faith["one_sided"]] == [(None, 0.0)]
    assert metric(comparison, "pos_context_precision")["status"] == reg.NONE
    assert not comparison["confirmed"]


def test_cli_reports_verdict_change_against_real_baseline(tmp_path, capsys):
    summary = tmp_path / "summary.md"
    code = gate_replay.main([str(PILOT_AFTER), "--baseline", str(PILOT_BEFORE), "--summary", str(summary)])
    assert code == gate_replay.EXIT_BLOCK  # 本次正向忠实度 0.6 低于 0.8，绝对门禁拦截
    assert "门禁结论：基线 PASS → 本次 FAIL" in capsys.readouterr().out
    text = summary.read_text(encoding="utf-8")
    assert "（结论变化）" in text and "⚠️ 可能退步" in text and "缺失 → 0.0000" in text


def test_confirmed_regression_blocks_even_when_absolute_gate_passes(tmp_path):
    baseline = write(tmp_path, "base.json", synthetic([1.0] * 20))
    candidate = write(tmp_path, "cand.json", synthetic([0.9] * 20))
    report = tmp_path / "gate.junit.xml"
    assert gate_replay.main([str(candidate), "--junit", str(report)]) == gate_replay.EXIT_PASS
    assert gate_replay.main([str(candidate), "--baseline", str(baseline), "--junit", str(report)]) == gate_replay.EXIT_BLOCK
    failures = [f for f in ET.parse(report).getroot().iter("failure") if f.get("type") == "GateRegression"]
    assert len(failures) == 1 and "下降 0.1000" in failures[0].get("message")


def test_drop_within_judge_noise_only_warns(tmp_path):
    baseline = write(tmp_path, "base.json", synthetic([1.0] * 20))
    candidate = write(tmp_path, "cand.json", synthetic([0.99] * 20))
    comparison = reg.compare(load(baseline), load(candidate))
    faith = metric(comparison, "pos_faithfulness")
    assert faith["interval"][1] < 0 and faith["status"] == reg.POSSIBLE
    assert gate_replay.main([str(candidate), "--baseline", str(baseline)]) == gate_replay.EXIT_PASS
    assert gate_replay.main([str(candidate), "--baseline", str(baseline), "--noise-faith", "0.005"]) == gate_replay.EXIT_BLOCK


def test_noisy_drop_whose_interval_crosses_zero_only_warns():
    baseline = synthetic([0.5] * 10)
    candidate = synthetic([0.0, 1.0] * 5)
    candidate["model_dfs"]["m"][1]["faithfulness"] = 0.6  # 均值 0.46，比基线低 0.04（超过裁判波动），但逐题差异很分散
    faith = metric(reg.compare(baseline, candidate), "pos_faithfulness")
    assert faith["mean_delta"] == -0.04 and faith["interval"][0] < 0 < faith["interval"][1]
    assert faith["status"] == reg.POSSIBLE


def test_questions_are_matched_by_text_and_unmatched_ones_counted():
    baseline = synthetic([1.0, 1.0, 1.0])
    candidate = synthetic([1.0, 1.0, 1.0])
    candidate["model_dfs"]["m"][2]["question"] = "new question"
    comparison = reg.compare(baseline, candidate)
    assert (comparison["shared"], comparison["only_baseline"], comparison["only_candidate"]) == (2, 1, 1)


def test_missing_per_question_rows_is_invalid_input(tmp_path):
    baseline = synthetic([1.0])
    del baseline["model_dfs"]
    base_path = write(tmp_path, "base.json", baseline)
    assert gate_replay.main([str(FULL_RUN), "--baseline", str(base_path)]) == gate_replay.EXIT_INVALID


def test_bootstrap_interval_is_reproducible():
    deltas = [-0.2, 0.1, 0.0, -0.05, 0.3]
    assert reg.bootstrap_interval(deltas) == reg.bootstrap_interval(deltas)


@settings(derandomize=True, database=None, deadline=None, max_examples=100)
@given(base=st.lists(st.floats(0, 1), min_size=1, max_size=20), gains=st.data())
def test_never_flags_regression_when_no_question_got_worse(base, gains):
    after = [min(1.0, b + gains.draw(st.floats(0, 1))) for b in base]
    comparison = reg.compare(synthetic(base), synthetic(after))
    assert not comparison["confirmed"]
    assert all(m["status"] == reg.NONE for m in comparison["metrics"])
