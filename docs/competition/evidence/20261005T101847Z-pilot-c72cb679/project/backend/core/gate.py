"""质量门禁（从 main.py L1489~1536 原样提取）。

check_quality_gate 依据第一个有效模型的正向用例均值判定是否允许发布，
并保留单用例异常告警打印。纯函数：不修改 summary / dataframe、不调用模型、
不写文件、不 import pipeline / reporting / storage。

依赖仅限：backend.config / backend.core.types / pandas / 标准库。
"""
import pandas as pd

from backend.config import SINGLE_CASE_WARN_THRESHOLD

from .types import GateResult


def check_quality_gate(summary, model_dfs, faith_threshold, ctx_prec_threshold):
    """质量门禁校验（与 main.py L1489~1536 行为一致）。

    参数：
        summary: List[SummaryEntry] — 多模型 summary（含成功与失败行）
        model_dfs: Dict[str, Any] — model name -> Ragas df（单用例告警用）
        faith_threshold: float — 正向忠实度阈值（调用方已解析默认值）
        ctx_prec_threshold: float — 正向上下文精度阈值（调用方已解析默认值）

    返回：
        GateResult — {"gate_passed": bool, "gate_message": str}
    """
    print("\n" + "=" * 50)
    print("🔒 CI/CD 质量门禁校验")
    print("=" * 50)

    # 有效模型选择：只按 faithfulness is not None，取第一个（禁止排序/选最高分/选最佳）
    valid_summary = [s for s in summary if s["faithfulness"] is not None]
    first_model = valid_summary[0]
    faith_score = first_model["faithfulness"]
    ctx_score = first_model["context_precision"]
    pos_faith = first_model.get("pos_faithfulness")
    pos_ctx = first_model.get("pos_context_precision")

    print(f"\n主评测模型: {first_model['model']}")
    print(f"--- 整体均值 ---")
    print(f"  平均忠实度 faithfulness:       {faith_score}")
    print(f"  平均上下文精度 context_precision: {ctx_score}")
    print(f"--- 正向用例均值（不含负样本）---")
    print(f"  正向忠实度 faithfulness:       {pos_faith}")
    print(f"  正向上下文精度 context_precision: {pos_ctx}")

    # 单用例异常告警（只打印，不影响 gate_passed）
    print("\n[单用例异常告警]")
    warning_flag = False
    if first_model["model"] in model_dfs:
        for idx, row in model_dfs[first_model["model"]].iterrows():
            faith_val = row.get("faithfulness")
            if faith_val is not None and not pd.isna(faith_val) and faith_val < SINGLE_CASE_WARN_THRESHOLD:
                q_preview = str(row.get("question", ""))[:20]
                print(f"  ⚠️ 用例[{idx}] 忠实度低于{SINGLE_CASE_WARN_THRESHOLD}: {q_preview}...")
                warning_flag = True
    if not warning_flag:
        print("  ✅ 无单用例异常")

    # 质量门禁：以正向用例均值判定（负样本为对抗测试，低分代表正确识别幻觉，不参与门禁）
    print("\n[正向用例质量门禁]")
    pass_flag = True
    if pos_faith is None or pd.isna(pos_faith) or pos_faith < faith_threshold:
        print(f"  ❌ 不通过：正向忠实度低于阈值 {faith_threshold}")
        pass_flag = False
    if pos_ctx is None or pd.isna(pos_ctx) or pos_ctx < ctx_prec_threshold:
        print(f"  ❌ 不通过：正向上下文精度低于阈值 {ctx_prec_threshold}")
        pass_flag = False

    if pass_flag:
        gate_msg = "✅ 质量门禁通过，允许发布"
        print("\n" + gate_msg)
    else:
        gate_msg = "⛔ 质量门禁拦截，终止发布"
        print("\n" + gate_msg)

    return {
        "gate_passed": pass_flag,
        "gate_message": gate_msg,
    }
