"""Parsing and presentation contracts, including malformed/partial inputs."""

import json

import pandas as pd
import pytest


@pytest.mark.parametrize("text, expected", [
    ('  {"answer": "中文"}  ', {"answer": "中文"}),
    ('```json\n{"answer": {"items": [1, 2]}}\n```', {"answer": {"items": [1, 2]}}),
    ('```\n{"ok": true}\n```', {"ok": True}),
    ('模型说明：{"answer": 42}\n补充说明', {"answer": 42}),
    ('[{"question": "q"}]', [{"question": "q"}]),
])
def test_json_response_recovery(app, text, expected):
    assert app.safe_json_loads(text) == expected


@pytest.mark.parametrize("text", ["", "not json", "{'single': 'quotes'}", '```json\n{"x":}\n```'])
def test_invalid_json_has_actionable_error(app, text):
    with pytest.raises(ValueError, match="无法解析JSON格式"):
        app.safe_json_loads(text)


@pytest.mark.parametrize("values, expected", [
    ([0.8, None, float("nan"), 1.0], 0.9),
    ([1 / 3, 1 / 3], 0.3333),
    ([0.0, 0.0], 0.0),
    ([], None), ([None, float("nan")], None), (None, None), (["invalid"], None),
])
def test_safe_mean_handles_missing_invalid_and_zero(app, values, expected):
    assert app._safe_mean(values) == expected


def test_display_formats_missing_values_without_mutating_input(app):
    source = pd.DataFrame({
        "model": ["DeepSeek", "GLM"], "faithfulness": [0.876, float("nan")],
        "hallucination_rate": [0.0, None], "score": [0.825, None],
        "elapsed_seconds": [12.34, None], "internal": ["hide", "hide"],
    })
    original = source.copy(deep=True)
    display = app.build_display_df(source)
    assert display.columns.tolist() == ["模型名称", "幻觉率", "忠实度", "综合得分", "耗时(秒)"]
    assert display.iloc[0].tolist() == ["DeepSeek", "0.0%", "87.6%", "82.5%", "12.3s"]
    assert display.iloc[1].tolist() == ["GLM", "-", "-", "-", "-"]
    pd.testing.assert_frame_equal(source, original)


def test_display_accepts_partial_metrics(app):
    display = app.build_display_df(pd.DataFrame([{"model": "model-a", "score": 0.0}]))
    assert display.to_dict("records") == [{"模型名称": "model-a", "综合得分": "0.0%"}]


def test_empty_display_has_stable_columns(app):
    display = app.empty_display_df()
    assert display.empty
    assert display.columns.tolist() == [
        "模型名称", "幻觉率", "忠实度", "上下文精度", "答案相关性", "事实一致性", "回答完整性", "综合得分",
        "溯源语句总数", "幻觉语句数", "事实错误数", "无依据编造数", "语句幻觉占比", "正向语句幻觉占比",
    ]


@pytest.mark.parametrize("error, fragment", [
    (RuntimeError("HTTP 401"), "API 调用失败"),
    (RuntimeError("429 quota"), "API 调用失败"),
    (TimeoutError("timed out"), "请求超时"),
    (RuntimeError("所有模型评测全部失败"), "所有模型评测均失败"),
    (FileNotFoundError("not found"), "文件错误"),
    (ValueError("JSON decode error"), "格式错误"),
    (RuntimeError(), "RuntimeError"),
])
def test_friendly_errors(app, error, fragment):
    assert fragment in app.friendly_error_message(error)


def test_unknown_error_is_bounded(app):
    assert app.friendly_error_message(RuntimeError("x" * 300)) == "评测过程中出现异常：" + "x" * 140


@pytest.mark.parametrize("passed, style, message", [
    (True, "ok", "质量门禁通过"), (False, "no", "质量门禁拦截"),
])
def test_gate_card_keeps_status_and_uses_first_valid_model(app, passed, style, message):
    result = {
        "gate_passed": passed, "total_elapsed_seconds": 12.34,
        "summary": [
            {"model": "failed-model", "faithfulness": None},
            {"model": "GLM", "faithfulness": 0.9, "pos_faithfulness": 0.9,
             "pos_context_precision": 0.8, "score": 0.85, "answer_relevancy": float("nan")},
        ],
    }
    html = app.gate_card_html(result, 0.8, 0.8)
    assert f"gate-card {style}" in html and message in html
    assert "主评测模型：GLM" in html and "failed-model" not in html
    assert "90.0%" in html and "85.0%" in html and "12.3 秒" in html
    assert "<b>-</b>" in html and "nan%" not in html


def test_empty_and_error_cards_are_safe_fallbacks(app):
    assert "无有效评测结果" in app.gate_card_html({"summary": []}, 0.8, 0.8)
    html = app.error_gate_html("文件格式错误")
    assert "gate-card no" in html and "文件格式错误" in html
