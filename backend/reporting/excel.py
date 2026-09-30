"""Excel 报告导出（与 main.export_report_xlsx 一致）。"""
import pandas as pd


def export_report_xlsx(result, path):
    """导出完整评测结果为 Excel（summary 汇总 + 各模型逐题结果）"""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        result["summary_df"].to_excel(writer, sheet_name="多模型汇总", index=False)
        for name, df in (result.get("model_dfs") or {}).items():
            safe = name.replace("/", "_")[:31]
            df.to_excel(writer, sheet_name=safe, index=False)
    return path
