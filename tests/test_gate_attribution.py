"""门禁失败归因：区分检索与生成问题，不改变门禁裁决。"""
from pathlib import Path
import json

import pytest

from backend.core import attribution as attr
from scripts import gate_replay

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "docs/competition/evidence/20261005T144504Z-full-39511424/api-response.json"
PILOT = ROOT / "docs/competition/evidence/20261005T131353Z-pilot-35f623cb/api-response.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def row(faith, ctx=1.0, recall=1.0, answer="依据条例作答。"):
    return {"case_type": "positive", "question": "q", "answer": answer,
            "faithfulness": faith, "context_precision": ctx, "context_recall": recall}


@pytest.mark.parametrize("item, expected", [
    (row(0.0, recall=0.0), attr.RETRIEVAL_MISS),
    (row(1.0, ctx=0.2), attr.RETRIEVAL_RANK),
    (row(0.0, answer="根据提供的文档，无法回答该问题。"), attr.REFUSAL),
    (row(0.25), attr.UNGROUNDED),
    (row(0.9), attr.OK),
    (row(None), attr.UNKNOWN),
    (row(float("nan")), attr.UNKNOWN),
])
def test_classify(item, expected):
    assert attr.classify(item) == expected


def test_formal_run_fail_is_caused_by_refusals_despite_full_recall():
    report = attr.attribute(load(FULL))
    assert report["counts"][attr.REFUSAL] == 4 and report["counts"][attr.OK] == 16
    assert [q["index"] for q in report["questions"] if q["category"] == attr.REFUSAL] == [20, 22, 23, 24]
    assert report["retrievalLoss"] == 0.0 and report["generationLoss"] == 0.2
    assert report["primaryStage"] == "生成"
    # 损失之和等于正向忠实度离满分的差距
    assert round(sum(report["faithfulnessLoss"].values()), 4) == round(1 - 0.7375, 4)


def test_pilot_run_splits_refusal_and_ungrounded():
    report = attr.attribute(load(PILOT))
    assert report["counts"][attr.REFUSAL] == 1 and report["counts"][attr.UNGROUNDED] == 1


def test_attribution_does_not_change_inputs_or_exit_code(tmp_path, capsys):
    data = load(FULL)
    before = json.dumps(data, sort_keys=True)
    attr.attribute(data)
    assert json.dumps(data, sort_keys=True) == before
    summary = tmp_path / "summary.md"
    assert gate_replay.main([str(FULL), "--summary", str(summary)]) == gate_replay.EXIT_BLOCK
    text = summary.read_text(encoding="utf-8")
    assert "失败归因" in text and "| 生成拒答 | 4 | 0.2000 |" in text
    assert "失败归因" in capsys.readouterr().out


def test_passing_gate_has_no_attribution(tmp_path):
    summary = tmp_path / "summary.md"
    assert gate_replay.main([str(ROOT / "frontend/src/fixtures/demo.json"), "--summary", str(summary)]) == gate_replay.EXIT_PASS
    assert "失败归因" not in summary.read_text(encoding="utf-8")


def test_missing_rows_is_reported_not_guessed():
    with pytest.raises(ValueError, match="model_dfs"):
        attr.attribute({"summary": [{"model": "m", "faithfulness": 0.5}]})
    assert gate_replay.attribution_lines({"summary": [{"model": "m", "faithfulness": 0.5}]}) == []
