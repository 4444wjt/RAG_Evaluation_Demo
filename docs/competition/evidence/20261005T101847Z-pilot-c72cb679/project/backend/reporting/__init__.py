"""报告与可视化层。

TRACE_DISPLAY_COLUMNS 与 build_display_df 是 generate_html_report / export_report_pdf 的
共享依赖，与 main.py 原定义一致，放在本包 __init__ 中供各子模块复用。
"""
import pandas as pd

# 溯源展示列（与 main.TRACE_DISPLAY_COLUMNS 一致）
TRACE_DISPLAY_COLUMNS = {
    "trace_statement_count": "溯源语句总数",
    "hallucination_statement_count": "幻觉语句数",
    "trace_fact_error_count": "事实错误数",
    "trace_fabrication_count": "无依据编造数",
    "hallucination_ratio": "语句幻觉占比",
    "pos_trace_hallucination_ratio": "正向语句幻觉占比",
}


def build_display_df(summary_df):
    """格式化原指标与现有溯源字段：计数显示整数，占比显示百分数。"""
    mapping = {
        "model": "模型名称",
        "hallucination_rate": "幻觉率",
        "faithfulness": "忠实度",
        "context_precision": "上下文精度",
        "answer_relevancy": "答案相关性",
        "answer_correctness": "事实一致性",
        "context_recall": "回答完整性",
        "refusal_accuracy": "拒答准确率",
        "score": "综合得分",
        "elapsed_seconds": "耗时(秒)",
        **TRACE_DISPLAY_COLUMNS,
    }
    cols = [c for c in mapping if c in summary_df.columns]
    df = summary_df[cols].rename(columns=mapping).copy()
    pct_cols = ["幻觉率", "忠实度", "上下文精度", "答案相关性", "事实一致性", "回答完整性", "拒答准确率", "综合得分", "语句幻觉占比", "正向语句幻觉占比"]
    for col in pct_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-")
    if "耗时(秒)" in df.columns:
        df["耗时(秒)"] = df["耗时(秒)"].apply(lambda v: f"{v:.1f}s" if pd.notna(v) else "-")
    for key in ("trace_statement_count", "hallucination_statement_count",
                "trace_fact_error_count", "trace_fabrication_count"):
        col = TRACE_DISPLAY_COLUMNS[key]
        if col in df.columns:
            df[col] = df[col].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "-")
    return df


from .html import generate_html_report  # noqa: E402
from .excel import export_report_xlsx  # noqa: E402
from .pdf import export_report_pdf  # noqa: E402
from .charts import draw_summary_chart, generate_trend_chart, placeholder_chart  # noqa: E402
from .evaluation_chart import generate_evaluation_chart  # noqa: E402

__all__ = [
    "TRACE_DISPLAY_COLUMNS",
    "build_display_df",
    "generate_html_report",
    "export_report_xlsx",
    "export_report_pdf",
    "draw_summary_chart",
    "generate_trend_chart",
    "placeholder_chart",
    "generate_evaluation_chart",
]
