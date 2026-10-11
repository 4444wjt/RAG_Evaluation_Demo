"""Judge comparison analysis is offline and must be reproducible from the archived raw outputs."""

from pathlib import Path
import json

import pytest

from tools.competition import judge_compare as jc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = sorted((PROJECT_ROOT / "docs" / "competition" / "judge-comparison").glob("*/raw.jsonl"))


def _trace(*verdicts, status="ok"):
    return {"statements": [{"text": "s", "verdict": v, "evidence": "e"} for v in verdicts], "trace_status": status}


@pytest.mark.parametrize(("trace", "label"), [
    (_trace("supported", "supported"), "supported"),
    (_trace("supported", "fabrication"), "hallucination"),
    (_trace(status="model_empty"), "empty"),
    (_trace(status="parse_error"), "error"),
    (None, "error"),
])
def test_trace_label(trace, label):
    assert jc.trace_label(trace) == label


def test_original_full_run_gate_is_reproduced():
    rows = jc.load_rows("full")
    scores = {(r["model"], r["rowIndex"]): r["original"] for r in rows}
    assert jc.gate(rows, scores) == {"posFaithfulness": 0.7375, "posContextPrecision": 0.9833, "passed": False}


def test_metric_diff_counts_threshold_crossings_and_missing():
    left = {("m", 0): {"faithfulness": 0.9}, ("m", 1): {"faithfulness": 0.4}, ("m", 2): {"faithfulness": None}}
    right = {("m", 0): {"faithfulness": 0.7}, ("m", 1): {"faithfulness": 0.4}, ("m", 2): {"faithfulness": 1.0}}
    diff = jc.metric_diff(left, right, "faithfulness")
    assert diff["compared"] == 2 and diff["missingEitherSide"] == 1 and diff["identical"] == 1
    assert diff["crossing0.8"] == 1 and diff["crossing0.5"] == 0 and diff["maxAbsDiff"] == 0.2


def test_label_agreement_matrix():
    left = {("m", i): {"label": label} for i, label in enumerate(["supported", "supported", "empty"])}
    right = {("m", i): {"label": label} for i, label in enumerate(["supported", "hallucination", "empty"])}
    result = jc.label_agreement(left, right)
    assert result["agree"] == 2 and result["agreementRate"] == pytest.approx(0.6667)
    assert result["matrix"] == {"supported": {"supported": 1, "hallucination": 1}, "empty": {"empty": 1}}


def test_analyze_synthetic_archive(tmp_path, monkeypatch):
    rows = jc.load_rows("full")
    with (tmp_path / "raw.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:  # 独立裁判给出与原判完全相同的分数和 Trace
            stream.write(json.dumps({"group": "independent", "judge": "qwen72b", "source": "full", "model": row["model"], "rowIndex": row["rowIndex"],
                                     "faithfulness": row["original"]["faithfulness"], "context_precision": row["original"]["context_precision"],
                                     "trace": row["original"]["trace"]}, ensure_ascii=False) + "\n")
    summary = jc.analyze(tmp_path)
    independent = summary["fullRun"]["vsOriginal"]["independent"]
    assert independent["faithfulness"]["meanAbsDiff"] == 0 and independent["traceLabel"]["agreementRate"] == 1
    assert summary["fullRun"]["gate"]["independent"]["passed"] is False


@pytest.mark.parametrize("raw", ARCHIVES, ids=[p.parent.name for p in ARCHIVES])
def test_archived_summary_is_reproducible(raw, tmp_path):
    archive = raw.parent
    saved = json.loads((archive / "summary.json").read_text(encoding="utf-8"))
    copy = tmp_path / archive.name
    copy.mkdir()
    for name in ("raw.jsonl", "llm-events.jsonl"):
        if (archive / name).exists():
            (copy / name).write_bytes((archive / name).read_bytes())
    assert jc.analyze(copy) == saved
