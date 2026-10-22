"""归档复算、实际条数、一致性失败与输出保护。"""
from pathlib import Path
from shutil import copytree

import pytest

from tools.competition import statute_injection as si
from tools.competition.statute_posthoc import _recovery_status, analyze

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs/competition/statute-check"


def test_pinpoint_replay_counts_recovery_and_determinism(tmp_path):
    run = ARCHIVE / "20261021-pinpoint-run/run"
    result = analyze(run, tmp_path / "a")
    analyze(run, tmp_path / "b")
    for name in ("posthoc.json", "SHA256SUMS.txt"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    si.verify_checksums(tmp_path / "a")
    archived = si.load(run.parent / "analysis/rules.json")
    assert result["consistency"]["originalFlaggedIds"] == sorted(k for k, v in archived["items"].items() if v["flagged"])
    for key, total, flagged in (("paragraph", 31, 30), ("item", 4, 1), ("all_errors", 35, 31), ("supported_controls", 31, 0)):
        row = result["summary"][key]
        assert (row["total"], row["originalFlagged"], row["extendedFlagged"]) == (total, flagged, flagged)
        assert row["addedIds"] == []
    assert not result["items"]["paragraph_existing-S27-P1"]["flagged"]
    recovery = result["recovery"]["errors"]
    assert [recovery[k]["count"] for k in ("consistent", "inconsistent", "undetermined")] == [28, 0, 3]
    assert recovery["undetermined"]["ids"] == ["paragraph_absent-S28", "paragraph_absent-S26", "item_absent-S26"]
    assert all(result["recovery"]["supported_controls"][k]["count"] == 0 for k in ("consistent", "inconsistent", "undetermined"))
    expected = {"control": (14, 0), "control_pinpoint": (13, 0), "paragraph_existing": (18, 17),
                "paragraph_absent": (13, 13), "item_existing": (3, 0), "item_absent": (1, 1), "paragraph_omitted": (4, 0)}
    assert {k: (v["total"], v["extendedFlagged"]) for k, v in result["byType"].items()} == expected


def test_formal_only_reports_added_flags_by_expected(tmp_path):
    result = analyze(ARCHIVE / "20261021-formal/run", tmp_path / "formal")
    assert result["mode"] == "formal-added-only"
    assert result["addedByExpected"] == {
        "not_supported": {"total": 29, "addedIds": [], "addedCount": 0},
        "supported": {"total": 13, "addedIds": [], "addedCount": 0}}
    assert "items" not in result and "summary" not in result


def test_refuses_nonempty_output(tmp_path):
    output = tmp_path / "occupied"
    output.mkdir()
    (output / "keep").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="空目录"):
        analyze(ARCHIVE / "20261021-pinpoint-run/run", output)
    assert (output / "keep").read_text() == "keep"


@pytest.mark.parametrize("tamper", ["source", "flags"])
def test_tampered_inputs_or_baseline_fail_before_writing(tmp_path, tamper):
    copied = tmp_path / "archive"
    copytree(ARCHIVE / "20261021-pinpoint-run", copied)
    if tamper == "source":
        path = copied / "run/inputs/scenarios/deposit_insurance.txt"
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        path = copied / "analysis/rules.json"
        rules = si.load(path)
        rules["items"]["control-S03"]["flagged"] = True
        si.write_json(path, rules)
        si.checksums(path.parent)
    with pytest.raises(ValueError, match="校验失败|flagged 集合"):
        analyze(copied / "run", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_partial_or_conflicting_recovery_is_not_counted_as_correct():
    target = {"article": 19, "paragraph": 1, "item": 3}
    assert _recovery_status([{"location": {**target, "item": None}}], target) == "undetermined"
    assert _recovery_status([{"location": {**target, "item": 2}}], target) == "inconsistent"
    assert _recovery_status([], target) == "undetermined"
