"""History stays in tmp_path; demo tests never generate live evaluation data."""

import json

import pandas as pd
import pytest


@pytest.mark.parametrize("text, expected", [
    ("", []), ("{broken", []), ("null", []), ("42", []),
    ('{"version": "old"}', [{"version": "old"}]),
    ('[{"version": "valid"}, null, 42, "invalid"]', [{"version": "valid"}]),
])
def test_history_legacy_and_corrupt_formats(app, tmp_path, text, expected):
    path = tmp_path / "history.json"
    path.write_text(text, encoding="utf-8-sig")
    assert app.load_history(str(path)) == expected


def test_history_missing_file_and_directory_are_safe(app, tmp_path):
    assert app.load_history(str(tmp_path / "missing.json")) == []
    assert app.load_history(str(tmp_path)) == []


def test_append_preserves_existing_history_and_three_demo_models(app):
    previous = {"version": "old", "models": [{"model": "older-model", "score": 0.5}]}
    app.save_eval_record(previous)
    demo = app.build_demo_result("多模型对比")
    record = {"version": "演示-多模型对比", "models": demo["summary"], "gate_passed": True}
    app.save_eval_record(record)
    history = app.load_history()
    assert history == [previous, record]
    assert {m["model"] for m in history[-1]["models"]} == {
        "deepseek-chat", "Pro/Qwen/Qwen2.5-7B-Instruct", "glm-5.2-fast-preview",
    }
    display = app.history_to_dataframe()
    assert len(display) == 4
    assert "glm-5.2-fast-preview" in display["模型"].tolist()
    assert display.iloc[-1]["门禁"] == "通过"
    assert display.iloc[-1]["综合得分"] == 0.89


def test_history_skips_bad_model_entries_and_keeps_missing_metrics(app, tmp_path):
    history = [
        {"models": None}, {"models": "invalid"},
        {"version": "v2", "gate_passed": False,
         "models": [None, "invalid", {"model": "GLM", "faithfulness": 0.9}]},
    ]
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps(history), encoding="utf-8")
    result = app.history_to_dataframe(str(path))
    assert len(result) == 1
    assert result.iloc[0]["模型"] == "GLM"
    assert result.iloc[0]["门禁"] == "拦截"
    assert pd.isna(result.iloc[0]["综合得分"])


def test_history_write_failure_warns_without_crashing(app, tmp_path, capsys):
    app.save_eval_record({"version": "test"}, str(tmp_path / "missing-dir" / "history.json"))
    assert "历史记录保存失败" in capsys.readouterr().out


@pytest.mark.parametrize("scene, count, passed", [
    ("高分通过", 1, True), ("中等异常", 1, False), ("多模型对比", 3, True),
])
def test_demo_scenarios_and_case_partitions(app, scene, count, passed):
    result = app.build_demo_result(scene)
    assert len(result["summary"]) == count
    assert result["gate_passed"] is passed
    assert len(app.build_display_df(result["summary_df"])) == count
    pd.testing.assert_frame_equal(result["summary_df"], pd.DataFrame(result["summary"]))
    for frame in result["model_dfs"].values():
        assert frame["case_type"].tolist() == ["positive", "positive", "negative"]


def test_demo_scores_and_hallucination_contract(app):
    rows = app.build_demo_result("多模型对比")["summary"]
    assert [row["score"] for row in rows] == [0.935, 0.765, 0.89]
    assert [row["hallucination_rate"] for row in rows] == [0.05, 0.32, 0.12]


def test_demo_results_do_not_share_mutable_data(app):
    first = app.build_demo_result("多模型对比")
    first["summary"][0]["score"] = -1
    first["model_dfs"]["deepseek-chat"].loc[0, "faithfulness"] = -1
    second = app.build_demo_result("多模型对比")
    assert second["summary"][0]["score"] == 0.935
    assert second["model_dfs"]["deepseek-chat"].loc[0, "faithfulness"] == 0.96
