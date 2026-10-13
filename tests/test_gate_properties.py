"""质量门禁的性质测试：用 Hypothesis 自动生成大量输入，检查门禁在任意输入下都应成立的性质。

与 test_backend_gate.py 的固定样例互补：固定样例锁定已知结论，这里检查「对所有输入都成立」的规则。
判定规则在本文件中独立写成 expected()，不调用被测代码。
derandomize=True 使每次运行生成相同的输入序列，结果可复现；database=None 不在仓库里留下 .hypothesis/ 目录。
"""
import contextlib
import io
import math
import re

import pandas as pd
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from backend.config import SINGLE_CASE_WARN_THRESHOLD
from backend.core.gate import check_quality_gate
from backend.core.pipeline import aggregate_model_metrics

GATE_SETTINGS = settings(
    derandomize=True, database=None, deadline=None, max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)

NAN = float("nan")
UNIT = st.floats(min_value=0.0, max_value=1.0)
THRESHOLDS = st.floats(min_value=0.5, max_value=1.0)
SCORE_OR_NAN = st.one_of(UNIT, st.just(NAN))
REFUSAL = "根据提供的文档，无法回答该问题。"


def score_near(threshold):
    """分数：任意值、恰等于阈值、阈值上下相邻的浮点数、None、NaN。"""
    return st.one_of(
        UNIT, st.just(threshold),
        st.just(math.nextafter(threshold, -math.inf)), st.just(math.nextafter(threshold, math.inf)),
        st.none(), st.just(NAN),
    )


def expected(pos_faith, pos_ctx, faith_threshold, ctx_threshold):
    """门禁规则：两个正向均值都存在且不低于各自阈值才放行。"""
    def reaches(value, threshold):
        return value is not None and not math.isnan(value) and value >= threshold
    return reaches(pos_faith, faith_threshold) and reaches(pos_ctx, ctx_threshold)


def summary_row(pos_faith, pos_ctx, model="m", faith=0.5, ctx=0.5):
    return {"model": model, "faithfulness": faith, "context_precision": ctx,
            "pos_faithfulness": pos_faith, "pos_context_precision": pos_ctx}


def run_gate(summary, faith_threshold=0.8, ctx_threshold=0.8, model_dfs=None):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = check_quality_gate(summary, model_dfs or {}, faith_threshold, ctx_threshold)
    return result, out.getvalue()


def passed(*args, **kwargs):
    return run_gate(*args, **kwargs)[0]["gate_passed"]


def summarize(rows):
    """把逐题分数交给正式的 aggregate_model_metrics，得到门禁读取的 summary 行。"""
    df = pd.DataFrame(rows, columns=["case_type", "faithfulness", "context_precision"])
    df["answer"] = REFUSAL
    raw = {"df": df, "trace_total": 0, "trace_fact_error": 0, "trace_fabrication": 0,
           "pos_trace_total": 0, "pos_trace_halluc": 0}
    return aggregate_model_metrics(raw, "m", 0.0)


def question(case_type):
    return st.fixed_dictionaries({
        "case_type": st.just(case_type), "faithfulness": SCORE_OR_NAN, "context_precision": SCORE_OR_NAN,
    })


QUESTIONS = st.lists(st.one_of(question("positive"), question("negative")), min_size=1, max_size=25)


# ① 阈值边界：裁决与规则一致，包括恰等于阈值、差一个浮点步长、缺失与 NaN。
@GATE_SETTINGS
@given(data=st.data(), faith_threshold=THRESHOLDS, ctx_threshold=THRESHOLDS)
def test_verdict_follows_threshold_rule_at_boundaries(data, faith_threshold, ctx_threshold):
    """阈值边界：恰等于阈值放行，低一个浮点步长、缺失或 NaN 拦截。"""
    pos_faith = data.draw(score_near(faith_threshold))
    pos_ctx = data.draw(score_near(ctx_threshold))
    result, _ = run_gate([summary_row(pos_faith, pos_ctx)], faith_threshold, ctx_threshold)
    want = expected(pos_faith, pos_ctx, faith_threshold, ctx_threshold)
    assert result["gate_passed"] is want
    assert ("通过" in result["gate_message"]) is want


# ② 分数只升不降时，放行不会变成拦截；阈值只升不降时，拦截不会变成放行。
@GATE_SETTINGS
@given(pos_faith=UNIT, pos_ctx=UNIT, raise_faith=UNIT, raise_ctx=UNIT,
       faith_threshold=THRESHOLDS, ctx_threshold=THRESHOLDS)
def test_higher_scores_never_turn_pass_into_block(pos_faith, pos_ctx, raise_faith, raise_ctx, faith_threshold, ctx_threshold):
    """分数升高，放行不会变成拦截。"""
    before = passed([summary_row(pos_faith, pos_ctx)], faith_threshold, ctx_threshold)
    after = passed([summary_row(min(1.0, pos_faith + raise_faith), min(1.0, pos_ctx + raise_ctx))], faith_threshold, ctx_threshold)
    assert after or not before


@GATE_SETTINGS
@given(pos_faith=UNIT, pos_ctx=UNIT, raise_faith=UNIT, raise_ctx=UNIT,
       faith_threshold=THRESHOLDS, ctx_threshold=THRESHOLDS)
def test_stricter_thresholds_never_turn_block_into_pass(pos_faith, pos_ctx, raise_faith, raise_ctx, faith_threshold, ctx_threshold):
    """阈值调高，拦截不会变成放行。"""
    before = passed([summary_row(pos_faith, pos_ctx)], faith_threshold, ctx_threshold)
    after = passed([summary_row(pos_faith, pos_ctx)], faith_threshold + raise_faith, ctx_threshold + raise_ctx)
    assert before or not after


# ③ 题目顺序不影响结论：打乱逐题顺序，正向均值与裁决都不变。
@GATE_SETTINGS
@given(rows=QUESTIONS, data=st.data(), faith_threshold=THRESHOLDS, ctx_threshold=THRESHOLDS)
def test_question_order_does_not_change_verdict(rows, data, faith_threshold, ctx_threshold):
    """打乱题目顺序，正向均值与裁决都不变。"""
    original = summarize(rows)
    shuffled = summarize(data.draw(st.permutations(rows)))
    for key in ("pos_faithfulness", "pos_context_precision"):
        assert original[key] == shuffled[key]
    assume(original["faithfulness"] is not None)
    assert passed([original], faith_threshold, ctx_threshold) is passed([shuffled], faith_threshold, ctx_threshold)


# ④ 负向样本不影响门禁：任意增加负向题、任意给分，正向均值与裁决都不变。
@GATE_SETTINGS
@given(positives=st.lists(question("positive"), min_size=1, max_size=15),
       negatives=st.lists(question("negative"), min_size=1, max_size=15),
       data=st.data(), faith_threshold=THRESHOLDS, ctx_threshold=THRESHOLDS)
def test_negative_cases_do_not_change_verdict(positives, negatives, data, faith_threshold, ctx_threshold):
    """任意增加负向题、任意给分，正向均值与裁决都不变。"""
    alone = summarize(positives)
    mixed = summarize(data.draw(st.permutations(positives + negatives)))
    for key in ("pos_faithfulness", "pos_context_precision"):
        assert alone[key] == mixed[key]
    assume(alone["faithfulness"] is not None)
    want = expected(alone["pos_faithfulness"], alone["pos_context_precision"], faith_threshold, ctx_threshold)
    assert passed([alone], faith_threshold, ctx_threshold) is want
    assert passed([mixed], faith_threshold, ctx_threshold) is want


@GATE_SETTINGS
@given(pos_faith=UNIT, pos_ctx=UNIT, faith=UNIT, ctx=st.one_of(UNIT, st.none()),
       faith_threshold=THRESHOLDS, ctx_threshold=THRESHOLDS)
def test_overall_means_including_negatives_do_not_change_verdict(pos_faith, pos_ctx, faith, ctx, faith_threshold, ctx_threshold):
    """含负向题的整体均值不影响裁决。"""
    row = summary_row(pos_faith, pos_ctx, faith=faith, ctx=ctx)
    assert passed([row], faith_threshold, ctx_threshold) is expected(pos_faith, pos_ctx, faith_threshold, ctx_threshold)


# 附加性质：只看第一个有效模型；单用例告警只打印、不改变裁决；日志如实打印均值；不修改输入。
@GATE_SETTINGS
@given(first=st.tuples(UNIT, UNIT), others=st.lists(st.tuples(UNIT, UNIT), max_size=3),
       invalid=st.lists(st.tuples(UNIT, UNIT), max_size=3), faith_threshold=THRESHOLDS, ctx_threshold=THRESHOLDS)
def test_only_first_valid_model_is_judged(first, others, invalid, faith_threshold, ctx_threshold):
    """只裁决第一个有效模型，其前后的模型不影响结论。"""
    summary = ([summary_row(*scores, model=f"invalid-{i}", faith=None) for i, scores in enumerate(invalid)]
               + [summary_row(*first, model="first")]
               + [summary_row(*scores, model=f"other-{i}") for i, scores in enumerate(others)])
    assert passed(summary, faith_threshold, ctx_threshold) is expected(*first, faith_threshold, ctx_threshold)


@GATE_SETTINGS
@given(pos_faith=UNIT, pos_ctx=UNIT,
       cases=st.lists(st.one_of(UNIT, st.just(SINGLE_CASE_WARN_THRESHOLD), st.just(NAN)), max_size=12),
       questions=st.lists(st.text(alphabet="abc 问答条例存款保险0123456789", max_size=30), min_size=12, max_size=12))
def test_single_case_warnings_list_low_cases_without_changing_verdict(pos_faith, pos_ctx, cases, questions):
    """单题告警如实列出低分题，且不改变裁决。"""
    frame = pd.DataFrame({"faithfulness": cases, "question": questions[:len(cases)]})
    result, out = run_gate([summary_row(pos_faith, pos_ctx)], model_dfs={"m": frame})
    assert result["gate_passed"] is expected(pos_faith, pos_ctx, 0.8, 0.8)
    low = [i for i, value in enumerate(cases) if not math.isnan(value) and value < SINGLE_CASE_WARN_THRESHOLD]
    assert [int(i) for i in re.findall(r"用例\[(\d+)\]", out)] == low
    for i in low:
        assert f"{questions[i][:20]}..." in out
    assert ("无单用例异常" in out) is (not low)


@GATE_SETTINGS
@given(pos_faith=UNIT, pos_ctx=UNIT, faith=UNIT, ctx=UNIT)
def test_gate_log_reports_the_judged_model_and_means(pos_faith, pos_ctx, faith, ctx):
    """门禁日志如实打印被裁决的模型与各项均值。"""
    _, out = run_gate([summary_row(pos_faith, pos_ctx, model="judged", faith=faith, ctx=ctx)])

    def printed(label):
        return re.search(rf"{label}:\s*(\S+)", out).group(1)

    assert printed("主评测模型") == "judged"
    assert printed("平均忠实度 faithfulness") == str(faith)
    assert printed("平均上下文精度 context_precision") == str(ctx)
    assert printed("正向忠实度 faithfulness") == str(pos_faith)
    assert printed("正向上下文精度 context_precision") == str(pos_ctx)


@GATE_SETTINGS
@given(pos_faith=score_near(0.8), pos_ctx=score_near(0.8), cases=st.lists(SCORE_OR_NAN, max_size=6))
def test_gate_does_not_modify_inputs(pos_faith, pos_ctx, cases):
    """门禁不修改传入的汇总与逐题数据。"""
    summary = [summary_row(pos_faith, pos_ctx), summary_row(0.1, 0.1, model="other")]
    frame = pd.DataFrame({"faithfulness": cases, "question": [f"q{i}" for i in range(len(cases))]})
    summary_before, frame_before = repr(summary), frame.copy()
    run_gate(summary, model_dfs={"m": frame})
    assert repr(summary) == summary_before
    pd.testing.assert_frame_equal(frame, frame_before)
