"""The fix-verification page must be regenerable from the frozen runs, and must say what the archives say — no more."""

from pathlib import Path
import json

import pytest

from tools.competition import build_fix_fixture as fix

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "frontend" / "src" / "fixtures" / "fix-verification.json"


@pytest.fixture(scope="module")
def built():
    return fix.build()


def test_committed_fixture_matches_evidence(built):
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == built


def test_gate_flips_but_both_conclusions_stay_uncertain(built):
    gate = built["formal"]["gate"]
    assert (gate["before"]["passed"], gate["before"]["posFaithfulness"]) == (False, 0.7375)
    assert (gate["after"]["passed"], gate["after"]["posFaithfulness"]) == (True, 0.8607)
    assert gate["before"]["replayed"] is False and gate["after"]["replayed"] is True
    # The PASS is not a confident PASS: the per-question interval still straddles the threshold.
    assert gate["before"]["certainty"] == {"low": 0.55, "high": 0.9, "state": "证据不足"}
    assert gate["after"]["certainty"] == {"low": 0.7286, "high": 0.9667, "state": "证据不足"}


def test_same_inputs_and_gate_but_not_only_one_line_of_code(built):
    formal = built["formal"]
    assert formal["sameContexts"] == 25
    assert "version_label" not in formal["request"]["compared"]
    assert built["fix"]["commit"] == built["code"]["commits"]["fixed"] == "b90a14c"
    code = built["code"]
    assert code["gateSame"] and code["judgePromptsSame"] and "backend/core/gate.py" not in code["changed"]
    # Other code changed between the runs too; the page must not claim a one-line difference for the formal rerun.
    assert len(code["changed"]) == 16 and {"backend/services/answering.py", "backend/services/trace.py"} <= set(code["changed"])


def test_f04_refused_although_its_context_had_the_answer():
    response = json.loads((fix.FIXED / "api-response.json").read_text(encoding="utf-8"))
    f04 = response["model_dfs"][fix.MODEL][23]
    assert f04["question"].startswith("第十九条要求在触发情形发生后7个自然日内偿付")
    assert any("7个工作日内足额偿付存款" in context for context in f04["contexts"])


def test_per_question_changes_match_the_archive_readme(built):
    rows = {row["id"]: row for row in built["formal"]["rows"]}
    assert [i for i, row in rows.items() if row["change"] == "corrected"] == ["F01", "F03", "F05"]
    assert rows["F04"]["change"] == "still_refused" and rows["F02"]["change"] == "same"
    assert all(rows[f"N0{n}"]["change"] == "kept_refusal" for n in range(1, 6))
    # D01's answer is word-for-word the same, yet its score moved: judge fluctuation, not a better answer.
    assert rows["D01"]["sameAnswer"] and rows["D01"]["change"] == "judge_only"
    d08 = rows["D08"]
    assert d08["change"] == "down" and d08["faithfulnessDelta"] == -0.6667
    assert d08["before"]["answer"].replace("规定", "") == d08["after"]["answer"]


def test_improvement_comes_from_the_corrected_questions(built):
    formal = built["formal"]
    regression = formal["regression"]
    assert (regression["meanDelta"], regression["status"], regression["drops"]) == (0.1232, "未退步", [{"id": "D08", "delta": -0.6667}])
    assert regression["interval"][0] < 0 < regression["interval"][1]
    assert formal["contribution"] == {"corrected": 0.1357, "rest": -0.0125, "correctedIds": ["F01", "F03", "F05"], "restCount": 17}


def test_heldout_counts(built):
    groups = {group["id"]: group for group in built["heldout"]["groups"]}
    assert (groups["false_premise"]["passBefore"], groups["false_premise"]["passAfter"], groups["false_premise"]["questions"]) == (9, 14, 14)
    assert (groups["true_premise"]["passBefore"], groups["true_premise"]["passAfter"]) == (4, 5)
    assert (groups["missing"]["passBefore"], groups["missing"]["passAfter"], groups["missing"]["refusedBefore"]) == (5, 5, None)
    assert groups["false_premise"]["signTestP"] == 0.0625 and all(not group["worsened"] for group in groups.values())
    assert built["heldout"]["notFullySupported"] == [{"id": "AF06", "statements": ["存款保险基金不是由财政拨款形成的。"]}]



def test_the_pass_does_not_survive_an_independent_judge(built):
    rerun = built["judgeRerun"]
    assert rerun["judgeModel"] == "Qwen/Qwen2.5-72B-Instruct"
    assert (rerun["gate"]["passed"], rerun["gate"]["replayed"], rerun["gate"]["posFaithfulness"]) == (False, False, 0.7992)
    assert rerun["gate"]["certainty"]["state"] == "证据不足"
    assert (rerun["sameAnswers"], rerun["samePositive"], rerun["samePositiveSelf"], rerun["samePositiveJudge"]) == (18, 13, 0.9377, 0.8718)


def _copy_archive(tmp_path, folder):
    for path in folder.iterdir():
        if path.is_file():
            (tmp_path / path.name).write_bytes(path.read_bytes())


def test_rejects_an_archive_whose_files_no_longer_match_their_checksums(tmp_path, monkeypatch):
    _copy_archive(tmp_path, fix.FIXED)
    request = json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))
    request["faith_threshold"] = 0.7
    (tmp_path / "request.json").write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setitem(fix.FILES, "fixed", (tmp_path, fix.FILES["fixed"][1]))
    with pytest.raises(ValueError, match="SHA256SUMS"):
        fix.verify_hashes()


def test_rejects_a_summary_the_rows_do_not_reproduce(tmp_path, monkeypatch):
    _copy_archive(tmp_path, fix.FIXED)
    response = json.loads((tmp_path / "api-response.json").read_text(encoding="utf-8"))
    next(s for s in response["summary"] if s["model"] == fix.MODEL)["pos_faithfulness"] = 0.9
    (tmp_path / "api-response.json").write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(fix, "FIXED", tmp_path)
    with pytest.raises(ValueError, match="重新算出"):
        fix.formal_runs()
