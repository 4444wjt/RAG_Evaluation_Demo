"""质量门禁 backend.core.gate.check_quality_gate 的离线测试。

迁移原 test_pipeline_gate.py 的门禁语义到 backend 实现，直接调用纯函数
check_quality_gate（真实 pandas + 真实函数体），不 mock gate 本身、不调模型、不写文件。
"""
import pandas as pd
import pytest

from backend import config
from backend.core.gate import check_quality_gate


def row(model="m1", faith=0.9, ctx=0.88, pos_faith=0.9, pos_ctx=0.88):
    """构造一条 summary 行：门禁用 pos_faithfulness/pos_context_precision 判定。"""
    return {
        "model": model, "faithfulness": faith, "context_precision": ctx,
        "pos_faithfulness": pos_faith, "pos_context_precision": pos_ctx,
    }


def df(faith_values):
    return pd.DataFrame({"faithfulness": faith_values,
                         "question": [f"q{i}" for i in range(len(faith_values))]})


# ---- 阈值边界（与旧 test_gate_threshold_boundaries 语义一致，改直接喂门禁） ----
@pytest.mark.parametrize("pos_faith, pos_ctx, passed", [
    (0.8, 0.8, True),
    (0.7999, 0.8, False),
    (0.8, 0.7999, False),
    (1.0, 1.0, True),
    (0.0, 1.0, False),
])
def test_gate_threshold_boundaries(pos_faith, pos_ctx, passed):
    result = check_quality_gate([row(pos_faith=pos_faith, pos_ctx=pos_ctx)], {}, 0.8, 0.8)
    assert result["gate_passed"] is passed


def test_gate_none_and_nan_block():
    assert check_quality_gate([row(pos_faith=None)], {}, 0.8, 0.8)["gate_passed"] is False
    assert check_quality_gate([row(pos_faith=float("nan"))], {}, 0.8, 0.8)["gate_passed"] is False
    assert check_quality_gate([row(pos_ctx=None)], {}, 0.8, 0.8)["gate_passed"] is False


def test_gate_zero_threshold_is_honored():
    # 阈值 0.0 保留：正向忠实度 0.0 等于阈值 → 通过
    assert check_quality_gate([row(pos_faith=0.0, pos_ctx=0.0)], {}, 0.0, 0.0)["gate_passed"] is True


def test_gate_uses_first_valid_model_not_best():
    # 第一个有效模型决定门禁，禁止选最高分/最佳
    summary = [
        {"model": "m-bad", "faithfulness": None},  # 失败行 → 不计入 valid_summary
        row(model="m-first", pos_faith=0.9, pos_ctx=0.9),
        row(model="m-better", pos_faith=1.0, pos_ctx=1.0),
    ]
    assert check_quality_gate(summary, {}, 0.8, 0.8)["gate_passed"] is True

    summary2 = [
        row(model="m-first", pos_faith=0.1, pos_ctx=0.1),
        row(model="m-better", pos_faith=1.0, pos_ctx=1.0),
    ]
    assert check_quality_gate(summary2, {}, 0.8, 0.8)["gate_passed"] is False


def test_gate_messages():
    assert check_quality_gate([row(pos_faith=0.9, pos_ctx=0.9)], {}, 0.8, 0.8)["gate_message"] == "✅ 质量门禁通过，允许发布"
    assert check_quality_gate([row(pos_faith=0.1, pos_ctx=0.9)], {}, 0.8, 0.8)["gate_message"] == "⛔ 质量门禁拦截，终止发布"


def test_gate_single_case_warning_does_not_change_result(capsys):
    # model_dfs 中存在低于 SINGLE_CASE_WARN_THRESHOLD 的用例 → 仅告警，不影响门禁
    model_dfs = {"m1": df([0.9, 0.4, 0.9])}
    result = check_quality_gate([row(pos_faith=0.9, pos_ctx=0.9)], model_dfs, 0.8, 0.8)
    assert result["gate_passed"] is True
    assert "单用例异常告警" in capsys.readouterr().out


def test_gate_is_pure_function():
    summary = [row(pos_faith=0.9, pos_ctx=0.9)]
    model_dfs = {"m1": df([0.9, 0.4])}
    summary_snapshot = [dict(s) for s in summary]
    model_dfs_snapshot = model_dfs["m1"].copy(deep=True)
    check_quality_gate(summary, model_dfs, 0.8, 0.8)
    assert summary == summary_snapshot
    pd.testing.assert_frame_equal(model_dfs["m1"], model_dfs_snapshot)
