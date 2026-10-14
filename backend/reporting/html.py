"""HTML 报告生成（与 main.generate_html_report 一致）。"""
import base64
import json
from collections import Counter
from datetime import datetime
from html import escape

import pandas as pd

from backend.config import (
    VERSION, EMBEDDING_MODEL, TEST_QA_NUM, TOP_K, CHUNK_SIZE, CHUNK_OVERLAP,
    CHART_PATH, HTML_OUTPUT, FAITH_THRESHOLD, CTX_PREC_THRESHOLD,
)
from . import build_display_df, TRACE_DISPLAY_COLUMNS


def generate_html_report(summary_data, chart_path=CHART_PATH, output_path=HTML_OUTPUT,
                         faith_threshold=FAITH_THRESHOLD, ctx_prec_threshold=CTX_PREC_THRESHOLD,
                         total_elapsed=None, *, case_counts=None, model_dfs=None):
    """生成带配置说明的HTML报告"""
    with open(chart_path, "rb") as f:
        chart_base64 = base64.b64encode(f.read()).decode("utf-8")

    def pct(v):
        return f"{v*100:.1f}%" if isinstance(v, (int, float)) and pd.notna(v) else "-"

    def fmt_sec(v):
        return f"{v:.1f}s" if isinstance(v, (int, float)) and pd.notna(v) else "-"

    rows_html = ""
    for item in summary_data:
        rows_html += (
            f"<tr>"
            f"<td>{item['model']}</td>"
            f"<td>{pct(item.get('hallucination_rate'))}</td>"
            f"<td>{pct(item.get('faithfulness'))}</td>"
            f"<td>{pct(item.get('context_precision'))}</td>"
            f"<td>{pct(item.get('answer_relevancy'))}</td>"
            f"<td>{pct(item.get('answer_correctness'))}</td>"
            f"<td>{pct(item.get('context_recall'))}</td>"
            f"<td>{pct(item.get('refusal_accuracy'))}</td>"
            f"<td>{pct(item.get('score'))}</td>"
            f"<td>{fmt_sec(item.get('elapsed_seconds'))}</td>"
            f"</tr>"
        )

    trace_df = build_display_df(pd.DataFrame(summary_data)).reindex(
        columns=["模型名称", *TRACE_DISPLAY_COLUMNS.values()], fill_value="-"
    )
    trace_table = trace_df.to_html(index=False, border=0, escape=True)
    if case_counts is None:
        case_description = "本次实际题集数量未提供（不使用默认配置推断）"
    else:
        case_description = (
            f"{case_counts.get('total', 0)} 条问题："
            f"{case_counts.get('positive', 0)} 条正向用例 + "
            f"{case_counts.get('negative', 0)} 条负样本（对抗测试）"
        )
        if case_counts.get("other", 0):
            case_description += f" + {case_counts['other']} 条其他类型"

    status_labels = {
        "ok": "逐句结果已保存", "partial": "部分陈述未通过校验",
        "empty_answer": "纯拒答，模型返回空数组", "model_empty": "模型返回空数组",
        "filtered_all": "候选陈述全部未通过校验", "parse_error": "JSON 解析失败",
        "invalid_structure": "输出结构异常", "call_error": "调用失败",
        "disabled": "Trace 未开启", "legacy_nonempty": "有逐句结果，未记录处理状态",
        "legacy_empty": "空数组，历史原因未记录", "missing": "缺少 Trace 字段",
    }
    status_rows = []
    for model, frame in (model_dfs or {}).items():
        records = frame.to_dict(orient="records") if isinstance(frame, pd.DataFrame) else frame
        counts = Counter()
        for record in records:
            value = record.get("hallucination_trace")
            if value is None:
                counts["missing"] += 1
                continue
            try:
                trace = json.loads(value) if isinstance(value, str) else value
            except (ValueError, TypeError):
                counts["parse_error"] += 1
                continue
            if not isinstance(trace, dict) or not isinstance(trace.get("statements"), list):
                counts["invalid_structure"] += 1
                continue
            status = trace.get("trace_status")
            counts[status if isinstance(status, str) else "legacy_nonempty" if trace["statements"] else "legacy_empty"] += 1
        for status, count in counts.items():
            label = status_labels.get(status, "未知处理状态")
            status_rows.append(f"<tr><td>{escape(str(model))}</td><td>{label}</td><td>{count}</td></tr>")
    trace_status_table = (
        '<h3>Trace 处理状态</h3><table><thead><tr><th>模型</th><th>处理结果</th><th>回答数</th></tr></thead>'
        f"<tbody>{''.join(status_rows)}</tbody></table>"
        if status_rows else ""
    )
    judge_rows = []
    for item in summary_data:
        if not isinstance(item.get("judge_model"), str):
            continue
        returned = item.get("judge_response_models")
        returned_label = "、".join(str(name) for name in returned) if isinstance(returned, list) and returned else "未知（响应未记录）"
        mode = "单独配置" if item.get("judge_mode") == "configured" else "沿用被测模型"
        judge_rows.append(
            f"<tr><td>{escape(str(item['model']))}</td><td>{mode}</td>"
            f"<td>{escape(item['judge_model'])}</td><td>{escape(returned_label)}</td></tr>"
        )
    judge_table = (
        '<h2>生成模型与裁判配置</h2><table><thead><tr><th>被测模型</th><th>裁判方式</th>'
        '<th>裁判请求模型</th><th>实际响应模型</th></tr></thead>'
        f"<tbody>{''.join(judge_rows)}</tbody></table>"
        '<p class="trace-note">五项指标和 Trace 使用此处裁判；回答仍由被测模型生成。'
        '单独配置不代表裁判已经通过独立认证，响应模型未知时不以请求名称代填。</p>'
        if judge_rows else ""
    )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>鉴真全域 VeriScope AI · 评测报告 {VERSION}</title>
<style>
    body {{ font-family: 'Microsoft YaHei', 'WenQuanYi Micro Hei', sans-serif; background: #f5f5f5; margin: 0; padding: 40px; }}
    .container {{ max-width: 1000px; margin: 0 auto; background: white; border-radius: 12px; padding: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }}
    h1 {{ color: #2c3e50; border-bottom: 3px solid #4CAF50; padding-bottom: 12px; }}
    h2 {{ color: #34495e; margin-top: 30px; }}
    .meta {{ color: #888; font-size: 14px; line-height: 1.8; }}
    .config-box {{ background: #f8f9fa; padding: 16px; border-radius: 8px; margin: 20px 0; font-size: 14px; color: #555; }}
    table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
    th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #eee; }}
    th {{ background: #f8f9fa; color: #555; font-weight: 600; }}
    tr:hover {{ background: #fafafa; }}
    .table-scroll {{ overflow-x: auto; }}
    .table-scroll th, .table-scroll td {{ white-space: nowrap; }}
    .trace-table th, .trace-table td {{ padding: 10px 8px; font-size: 14px; }}
    .trace-note {{ color: #777; font-size: 13px; line-height: 1.7; }}
    .chart {{ text-align: center; margin: 30px 0; }}
    .chart img {{ max-width: 100%; border-radius: 8px; }}
    .robustness {{ background: #e8f5e9; padding: 12px 16px; border-left: 4px solid #4CAF50; border-radius: 4px; margin: 20px 0; color: #2e7d32; font-size: 14px; }}
    .footer {{ color: #999; font-size: 13px; margin-top: 30px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
    <h1>鉴真全域 VeriScope AI · 评测报告</h1>
    <p class="meta">
        <b>版本号：</b>{VERSION}<br>
        <b>生成时间：</b>{now}<br>
        <b>评测框架：</b>Ragas + LangChain + Chroma<br>
        <b>Embedding：</b>{EMBEDDING_MODEL}<br>
        <b>评测总耗时：</b>{f'{total_elapsed:.1f} 秒' if total_elapsed is not None else '-'}
    </p>

    <div class="config-box">
        <b>测试配置：</b><br>
        · 质量门禁阈值：忠实度 ≥ {faith_threshold}，上下文精度 ≥ {ctx_prec_threshold}<br>
        · 测试集构成：{case_description}<br>
        · 检索 Top-K：{TOP_K} &nbsp;|&nbsp; 文本块大小：{CHUNK_SIZE} 字符 &nbsp;|&nbsp; 重叠：{CHUNK_OVERLAP} 字符
    </div>

    <div class="robustness">
        ✔ 已内置空值(nan)过滤机制 &nbsp;|&nbsp; ✔ 异常重试 &nbsp;|&nbsp; ✔ 超时保护 &nbsp;|&nbsp; ✔ JSON格式容错
    </div>

    {judge_table}
    <h2>多模型对比结果</h2>
    <div class="table-scroll"><table>
        <thead>
            <tr>
                <th>模型</th>
                <th>幻觉率</th>
                <th>忠实度</th>
                <th>上下文精度</th>
                <th>答案相关性</th>
                <th>事实一致性</th>
                <th>回答完整性</th>
                <th>拒答准确率</th>
                <th>综合得分</th>
                <th>耗时</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table></div>
    <p style="color:#888; font-size:13px;">
        * 幻觉率为派生指标（1 − 正向忠实度）；门禁仅以正向忠实度与上下文精度判定，负样本用于对抗测试不参与门禁。
    </p>

    <h2>幻觉溯源与归因</h2>
    <div class="table-scroll trace-table">{trace_table}</div>
    {trace_status_table}
    <p class="trace-note">语句幻觉占比 =（事实错误数 + 无依据编造数）/ 有效溯源语句总数；
        正向语句幻觉占比仅统计正向用例。缺失或无有效陈述的占比显示“−”。
        语句数为已保存记录的计数，0 不证明回答无幻觉；空数组、失败和未开启分别呈现。
        此处为 LLM 归因结果，不等同于上表“幻觉率”，不参与综合得分和门禁。
        原文引用存在只说明能够定位，语义支持仍是裁判判断。</p>

    <div class="chart">
        <h2>可视化图表</h2>
        <img src="data:image/png;base64,{chart_base64}" alt="评测图表">
    </div>

    <div class="footer">由 鉴真全域 VeriScope AI 生成 · {VERSION}</div>
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML报告已保存: {output_path}")
