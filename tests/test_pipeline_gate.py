"""Exercise real run_pipeline score/gate branches with mocked external systems.

Expected numbers are fixed examples, not a second copy of the gate algorithm.
"""

from unittest.mock import Mock

import pandas as pd
import pytest


def metrics(faith, context):
    return pd.DataFrame({"faithfulness": faith, "context_precision": context})


@pytest.mark.parametrize("faith, context, passed", [
    (0.8, 0.8, True), (0.7999, 0.8, False), (0.8, 0.7999, False),
    (1.0, 1.0, True), (0.0, 1.0, False),
])
def test_gate_threshold_boundaries(run_fake_pipeline, faith, context, passed):
    result = run_fake_pipeline([metrics([faith], [context])])
    assert result["gate_passed"] is passed


def test_score_and_gate_exclude_negative_examples(app, run_fake_pipeline):
    result = run_fake_pipeline(
        [metrics([0.9, 0.8, 0.0], [0.8, 1.0, 0.0])],
        case_types=["positive", "positive", "negative"], version_label="regression",
    )
    row = result["summary"][0]
    assert row["faithfulness"] == 0.5667  # Overall mean would fail the gate.
    assert row["context_precision"] == 0.6
    assert row["pos_faithfulness"] == 0.85
    assert row["pos_context_precision"] == 0.9
    assert row["score"] == 0.875
    assert row["hallucination_rate"] == 0.15
    assert row["neg_faithfulness"] == 0.0
    assert result["gate_passed"] is True
    record = app.load_history()[0]
    assert record["version"] == "regression" and record["gate_passed"] is True
    assert record["models"][0]["score"] == 0.875
    assert app.evaluate.call_count == 1
    assert app.rag_answer.call_count == 3
    assert app.HuggingFaceEmbeddings.call_args.kwargs["model_kwargs"] == {"local_files_only": True}


@pytest.mark.parametrize("faith, context", [(0.0, 0.0), (0.6, 0.7)])
def test_explicit_thresholds_including_zero_are_honored(run_fake_pipeline, faith, context):
    result = run_fake_pipeline([metrics([faith], [context])],
                               faith_threshold=faith, ctx_prec_threshold=context)
    assert result["gate_passed"] is True


def test_score_rounds_to_four_decimal_places(run_fake_pipeline):
    result = run_fake_pipeline([metrics([0.8123], [0.8456])])
    assert result["summary"][0]["score"] == 0.829


def test_nan_is_ignored_when_other_positive_samples_exist(run_fake_pipeline):
    result = run_fake_pipeline([metrics([float("nan"), 0.9], [0.8, float("nan")])])
    assert result["summary"][0]["score"] == 0.85
    assert result["gate_passed"] is True


@pytest.mark.parametrize("frame, case_types", [
    (metrics([float("nan"), 0.1], [0.9, 0.1]), ["positive", "negative"]),
    (metrics([0.9, 0.1], [float("nan"), 0.1]), ["positive", "negative"]),
    (pd.DataFrame({"faithfulness": [0.9]}), ["positive"]),
    (metrics([0.1], [0.2]), ["negative"]),
])
def test_missing_positive_metrics_block_gate_and_score(run_fake_pipeline, frame, case_types):
    result = run_fake_pipeline([frame], case_types=case_types)
    assert result["gate_passed"] is False
    assert result["summary"][0]["score"] is None


@pytest.mark.parametrize("first_score, second_score, passed", [
    (0.9, 0.1, True), (0.1, 0.9, False),
])
def test_gate_uses_first_valid_model_not_best_or_all(run_fake_pipeline, first_score, second_score, passed):
    result = run_fake_pipeline([
        metrics([first_score], [first_score]), metrics([second_score], [second_score]),
    ])
    assert result["gate_passed"] is passed


def test_partial_model_failure_retains_row_and_gates_next_model(app, run_fake_pipeline):
    result = run_fake_pipeline([RuntimeError("fake provider failure"), metrics([0.9], [0.8])])
    failed, successful = result["summary"]
    assert failed["model"] == "model-0" and failed["score"] is None
    assert failed["faithfulness"] is None
    assert successful["model"] == "model-1" and successful["score"] == 0.85
    assert result["gate_passed"] is True
    assert len(app.load_history()[0]["models"]) == 2


def test_all_model_failures_raise_without_saving_success(app, run_fake_pipeline):
    with pytest.raises(RuntimeError, match="所有模型评测全部失败"):
        run_fake_pipeline([RuntimeError("fake failure"), TimeoutError("fake timeout")])
    assert app.load_history() == []
    app.generate_html_report.assert_not_called()


def test_nan_only_evaluation_cannot_pass(app, run_fake_pipeline):
    with pytest.raises(RuntimeError, match="所有模型评测全部失败"):
        run_fake_pipeline([metrics([float("nan")], [float("nan")])])
    assert app.load_history() == []


def test_progress_callback_failure_does_not_change_evaluation(run_fake_pipeline):
    progress = Mock(side_effect=RuntimeError("UI disconnected"))
    result = run_fake_pipeline([metrics([0.9], [0.8])], progress_cb=progress)
    assert result["gate_passed"] is True
    assert [call.args[0] for call in progress.call_args_list] == [1, 2, 3, 4, 5, 6]


def test_three_model_summary_and_history_are_preserved(app, run_fake_pipeline):
    result = run_fake_pipeline([
        metrics([0.9], [0.9]), metrics([0.7], [0.8]), metrics([0.88], [0.9]),
    ])
    assert len(result["summary_df"]) == 3
    assert [row["score"] for row in result["summary"]] == [0.9, 0.75, 0.89]
    assert len(app.load_history()[0]["models"]) == 3
