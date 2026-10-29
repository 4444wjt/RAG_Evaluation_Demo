"""The guided case on the fix-verification page must be copied from the frozen runs verbatim, and must stop on any doubt."""

from pathlib import Path
import hashlib
import json
import shutil
import subprocess

import pytest

from tools.competition import build_fix_case as case

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built():
    return case.extract()


def _by_id(built, case_id):
    return next(c for c in built["cases"] if c["id"] == case_id)


def test_committed_case_matches_evidence(built):
    assert json.loads(case.OUTPUT.read_text(encoding="utf-8")) == built


def test_sources_are_bound_to_the_same_hashes_as_the_fix_fixture(built):
    fixture = json.loads(case.FIX_FIXTURE.read_text(encoding="utf-8"))["source"]
    for run in ("baseline", "fixed"):
        for name, digest in built["sources"][run]["sha256"].items():
            assert fixture[run]["sha256"][name] == digest
    assert built["purpose"] == "历史案例展示，未执行新的评测"


def test_f03_is_copied_verbatim_and_its_empty_trace_stays_empty(built):
    f03 = _by_id(built, "F03")
    response = json.loads((case.FIXED / "api-response.json").read_text(encoding="utf-8"))["model_dfs"][case.MODEL][22]
    assert f03["question"] == response["question"] and f03["contexts"] == response["contexts"]
    assert f03["after"]["answer"] == response["answer"]
    assert f03["after"]["trace"]["statements"] == json.loads(response["hallucination_trace"])["statements"]
    assert [s["verdict"] for s in f03["after"]["trace"]["statements"]] == ["supported"] * 4
    # The baseline recorded no statements at all: not "unsupported", not "passed".
    assert f03["before"] == {"answer": "根据提供的文档，无法回答该问题。", "trace": {"status": None, "statements": []}}


def test_highlights_come_from_registered_evidence_and_occur_once(built):
    for item in built["cases"]:
        for mark in item["highlights"]:
            assert sum(context.count(mark["text"]) for context in item["contexts"]) == 1
            assert mark["text"] in item["contexts"][mark["context"]]
    assert [m["referenceIds"] for m in _by_id(built, "F03")["highlights"]] == [["E04"], ["E15"]]


def test_f04_still_refuses_with_the_answer_in_its_context(built):
    f04 = _by_id(built, "F04")
    assert f04["before"]["answer"] == f04["after"]["answer"] == "根据提供的文档，无法回答该问题。"
    assert f04["after"]["trace"] == {"status": "empty_answer", "statements": []}
    assert "7个工作日内足额偿付存款" in f04["contexts"][f04["highlights"][0]["context"]]


def test_pinned_commit_and_checkout_match_their_respective_hashes(built):
    """Verify linked blob bytes and archive checkout bytes against their separate hashes."""
    commit = built["pinned"]["commit"]
    probe = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=PROJECT_ROOT, capture_output=True)
    if probe.returncode != 0:
        pytest.skip("固定提交不在本地克隆中")
    for source in built["sources"].values():
        for name in source["sha256"]:
            path = f'{source["path"]}/{name}'
            pinned = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=PROJECT_ROOT, capture_output=True, check=True)
            assert hashlib.sha256(pinned.stdout).hexdigest() == source["gitBlobSha256"][name], path
            assert hashlib.sha256((PROJECT_ROOT / path).read_bytes()).hexdigest() == source["sha256"][name], path


def test_baseline_analysis_blob_and_checkout_differ_only_in_line_endings(built):
    """The existing CRLF archive hash stays intact; the linked LF blob has its own SHA."""
    source = built["sources"]["baseline"]
    name = "analysis.json"
    path = f'{source["path"]}/{name}'
    commit = built["pinned"]["commit"]
    probe = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=PROJECT_ROOT, capture_output=True)
    if probe.returncode != 0:
        pytest.skip("固定提交不在本地克隆中")
    blob = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=PROJECT_ROOT, capture_output=True, check=True).stdout
    checkout = (PROJECT_ROOT / path).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == source["gitBlobSha256"][name]
    assert hashlib.sha256(checkout).hexdigest() == source["sha256"][name]
    assert source["gitBlobSha256"][name] != source["sha256"][name]
    assert b"\r\n" not in blob and b"\r\n" in checkout
    assert blob == checkout.replace(b"\r\n", b"\n")


# ---- counterexamples: each must stop the extraction instead of guessing ----

@pytest.fixture
def copies(tmp_path):
    """Writable copies of only the files the extraction reads; the frozen archives are never touched."""
    folders = {}
    for key, folder in (("baseline", case.BASELINE), ("fixed", case.FIXED)):
        target = tmp_path / folder.name
        target.mkdir()
        for name in (*case.RUNS[key][1], "SHA256SUMS.txt"):
            shutil.copyfile(folder / name, target / name)
        folders[key] = target
    return folders


def _rewrite(folder: Path, name: str, value) -> None:
    """Change a copied file and re-register its hash, so the test reaches the check after the hash check."""
    path = folder / name
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sums = folder / "SHA256SUMS.txt"
    lines = [f"{digest}  {name}" if line.split(maxsplit=1)[-1].lstrip("*") == name else line
             for line in sums.read_text(encoding="utf-8").splitlines()]
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_wrong_source_hash_stops(copies):
    path = copies["fixed"] / "api-response.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(case.CaseError, match="来源哈希不符：20261014T073859Z-full-1c6f091f/api-response.json"):
        case.extract(copies["baseline"], copies["fixed"])


def test_missing_question_stops(copies):
    analysis = json.loads((copies["baseline"] / "analysis.json").read_text(encoding="utf-8"))
    analysis["records"] = [r for r in analysis["records"] if r["id"] != "F03"]
    _rewrite(copies["baseline"], "analysis.json", analysis)
    with pytest.raises(case.CaseError, match="题目缺失：基线 analysis.json 中没有题号 F03"):
        case.extract(copies["baseline"], copies["fixed"])


def test_question_missing_from_a_run_stops(copies):
    response = json.loads((copies["fixed"] / "api-response.json").read_text(encoding="utf-8"))
    response["model_dfs"][case.MODEL][23]["question"] += "（改）"
    _rewrite(copies["fixed"], "api-response.json", response)
    with pytest.raises(case.CaseError, match="题目缺失：fixed 的 api-response.json 中找不到 F04"):
        case.extract(copies["baseline"], copies["fixed"])


def test_ambiguous_row_match_stops(copies):
    response = json.loads((copies["fixed"] / "api-response.json").read_text(encoding="utf-8"))
    rows = response["model_dfs"][case.MODEL]
    rows.append(dict(rows[22]))
    _rewrite(copies["fixed"], "api-response.json", response)
    with pytest.raises(case.CaseError, match="匹配不唯一：F03 的问题在 fixed 的 api-response.json 中出现 2 次"):
        case.extract(copies["baseline"], copies["fixed"])


def test_ambiguous_highlight_stops(monkeypatch):
    # 「（四）经国务院批准的其他情形。」is registered evidence for F04, but two retrieved passages contain it.
    specs = tuple({**spec, "highlights": ("（四）经国务院批准的其他情形。",)} if spec["id"] == "F04" else spec for spec in case.CASES)
    monkeypatch.setattr(case, "CASES", specs)
    with pytest.raises(case.CaseError, match="匹配不唯一：F04 的高亮片段「（四）经国务院批准的其他情形。」在检索上下文中出现 2 次"):
        case.extract()


def test_check_mode_is_read_only(monkeypatch, capsys):
    before = case.OUTPUT.read_bytes()
    monkeypatch.setattr("sys.argv", ["build_fix_case.py", "--check"])
    assert case.main() == 0
    assert case.OUTPUT.read_bytes() == before
    assert "一致" in capsys.readouterr().out
