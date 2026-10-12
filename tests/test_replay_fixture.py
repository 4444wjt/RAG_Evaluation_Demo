"""The overview replay must be regenerable from the frozen evidence, and its gate must follow from the statement verdicts."""

from pathlib import Path
import json

from tools.competition import build_replay_fixture as replay

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "frontend" / "src" / "fixtures" / "formal-run-replay.json"


def test_committed_fixture_matches_evidence():
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == replay.build()


def test_gate_is_derived_from_statement_verdicts():
    fixture = replay.build()
    rows = fixture["rows"]
    assert len(rows) == 20 and sum(row["total"] for row in rows) == 52
    assert all(row["supported"] / row["total"] == row["faithfulness"] for row in rows)
    mean = sum(row["supported"] / row["total"] for row in rows) / len(rows)
    assert mean == fixture["gate"]["posFaithfulness"] == 0.7375
    assert fixture["gate"]["passed"] is False and mean < fixture["thresholds"]["faithfulness"]


def test_refusals_and_detail_row():
    fixture = replay.build()
    assert [row["rowIndex"] for row in fixture["rows"] if row["refusal"]] == [20, 22, 23, 24]
    answered = [row["faithfulness"] for row in fixture["rows"] if not row["refusal"]]
    assert sum(answered) / len(answered) == 0.921875
    assert [item["verdict"] for item in fixture["detail"]["statements"]] == [0, 1, 1, 1]


def test_source_hashes_match_archive_checksums():
    expected = replay._expected_hashes(replay.EVIDENCE)
    for name, digest in replay.build()["source"]["sha256"].items():
        assert expected[name] == digest
