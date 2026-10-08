import json

import pandas as pd
import pytest

from backend.reporting.html import generate_html_report


def test_trace_display_counts_ratios_and_missing(app):
    data = pd.DataFrame([
        dict(model="A", trace_statement_count=20, hallucination_statement_count=3,
             trace_fact_error_count=1, trace_fabrication_count=2,
             hallucination_ratio=0.15, pos_trace_hallucination_ratio=0),
        dict(model="B"),
    ])
    original = data.copy(deep=True)
    display = app.build_display_df(data)
    assert display.iloc[0].tolist() == ["A", "20", "3", "1", "2", "15.0%", "0.0%"]
    assert display.iloc[1].tolist() == ["B", "-", "-", "-", "-", "-", "-"]
    pd.testing.assert_frame_equal(data, original)


@pytest.mark.parametrize("scene", ["高分通过", "中等异常", "多模型对比"])
def test_demo_trace_schema_and_totals(app, scene):
    result = app.build_demo_result(scene)
    for row in result["summary"]:
        assert set(app.TRACE_DISPLAY_COLUMNS) <= row.keys()
        assert row["hallucination_statement_count"] == row["trace_fact_error_count"] + row["trace_fabrication_count"]
        assert row["hallucination_ratio"] == row["hallucination_statement_count"] / row["trace_statement_count"]
    assert set(app.TRACE_DISPLAY_COLUMNS.values()) <= set(app.build_display_df(result["summary_df"]).columns)


def test_html_trace_table_and_missing_values(app, tmp_path):
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"offline-image")
    output = tmp_path / "report.html"
    summary = app.build_demo_result("多模型对比")["summary"]
    summary.append({"model": "old<record>"})
    app.generate_html_report(summary, str(chart), str(output))
    html = output.read_text(encoding="utf-8")
    trace = html.split("<h2>幻觉溯源与归因</h2>")[1].split('<div class="chart">')[0]
    for label in app.TRACE_DISPLAY_COLUMNS.values():
        assert label in trace
    assert "10.0%" in trace and "35.0%" in trace and "15.0%" in trace
    assert "old&lt;record&gt;" in trace and "<td>-</td>" in trace
    assert "nan" not in trace and "None" not in trace
    assert 'overflow-x: auto' in html


def test_backend_report_uses_actual_case_counts_and_trace_statuses(tmp_path):
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"offline-image")
    output = tmp_path / "backend-report.html"
    frame = pd.DataFrame({
        "hallucination_trace": [
            json.dumps({"statements": [], "trace_status": "empty_answer", "trace_attempts": 1}),
            json.dumps({"statements": [], "trace_status": "parse_error", "trace_attempts": 1}),
            json.dumps({"statements": [{"text": "a", "verdict": "supported", "evidence": "原文"}], "trace_status": "ok"}),
        ]
    })
    generate_html_report(
        [{"model": "deepseek-chat", "faithfulness": 0.5}],
        chart_path=str(chart), output_path=str(output),
        case_counts={"total": 25, "positive": 20, "negative": 5, "other": 0},
        model_dfs={"deepseek-chat": frame},
    )
    html = output.read_text(encoding="utf-8")
    assert "25 条问题：20 条正向用例 + 5 条负样本" in html
    assert "纯拒答，模型返回空数组" in html
    assert "JSON 解析失败" in html
    assert "逐句结果已保存" in html
