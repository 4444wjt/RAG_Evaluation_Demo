"""The workbench gate-audit view must be regenerable from frozen evidence, and must refuse a gate it no longer describes."""

from pathlib import Path
import json

import pytest

from tools.competition import build_audit_fixture as audit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "frontend" / "src" / "fixtures" / "gate-audit.json"


@pytest.fixture(scope="module")
def built():
    return audit.build()


def test_committed_fixture_matches_evidence(built):
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == built


def test_four_questions_match_the_credibility_document(built):
    recompute = built["recompute"]
    assert (recompute["questions"], recompute["statements"], recompute["supported"]) == (20, 52, 43)
    assert recompute["recomputedFaithfulness"] == recompute["recordedFaithfulness"] == 0.7375
    assert recompute["recordedPassed"] is recompute["recomputedPassed"] is recompute["replayedPassed"] is False

    assert [run["passed"] for run in built["judges"]["runs"]] == [False, False, False]
    assert built["judges"]["noise"]["faithfulness"] == {
        "sameJudge": 0.0167, "crossJudge": 0.0953, "crossingSameJudge": 1, "crossingCrossJudge": 6}

    assert built["sensitivity"]["totals"] == {"planned": 60, "valid": 59, "matches": 59, "parseErrors": 1}
    failed = [cell for cell in built["sensitivity"]["cells"] if cell["parseErrors"]]
    assert [(cell["judge"], cell["variant"]) for cell in failed] == [("qwen72b", "removal")]

    faith = built["certainty"]["metrics"]["faithfulness"]
    assert faith["bootstrap95"] == [0.55, 0.9] and faith["state"] == "证据不足"
    assert built["certainty"]["metrics"]["context_precision"]["state"] == "确定通过"


def test_self_check_reflects_tests_and_mutation_archive(built):
    assert len(built["properties"]["items"]) == 10
    mutation = built["mutation"]
    assert (mutation["total"], mutation["killed"], mutation["survived"], mutation["timeouts"]) == (42, 39, 3, 0)
    assert [m["id"] for m in mutation["mutants"]] == list(range(1, 43))
    assert [s["name"] for s in mutation["survivors"]] == ["Mutant #3", "Mutant #5", "Mutant #15"]
    assert all(s["original"] != s["mutated"] and s["note"] for s in mutation["survivors"])


def test_regression_example_only_warns(built):
    regression = built["regression"]
    assert (regression["baselinePassed"], regression["candidatePassed"], regression["confirmed"]) == (True, False, False)
    assert [m["status"] for m in regression["metrics"]] == ["可能退步", "未退步"]


def test_changed_gate_code_invalidates_the_mutation_archive(tmp_path, monkeypatch):
    changed = tmp_path / "gate.py"
    changed.write_bytes(audit.GATE.read_bytes() + b"\n# changed\n")
    monkeypatch.setattr(audit, "GATE", changed)
    with pytest.raises(ValueError, match="重新运行变异测试"):
        audit.mutation()


def test_gate_hash_ignores_crlf_checkout(tmp_path):
    crlf = tmp_path / "gate.py"
    crlf.write_bytes(audit.GATE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert audit._source_hash(crlf) == audit._source_hash(audit.GATE)
