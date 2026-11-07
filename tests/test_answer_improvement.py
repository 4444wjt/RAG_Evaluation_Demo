"""User-result improvements must preserve evidence, original FAIL and every row."""

from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest


ANSWER = "申请材料齐全后，资料窗口应当在7个工作日内答复登记申请。"
EVIDENCE = "申请材料齐全后，资料窗口应当在3个工作日内答复登记申请。"


def core():
    path = Path(__file__).resolve().parents[1] / "backend/core/answer_improvement.py"
    assert path.is_file(), "The user-answer candidate and verifier feature is not implemented"
    module = importlib.import_module("backend.core.answer_improvement")
    assert Path(module.__file__).resolve() == path
    return module


def row(**changes):
    return {"question": "申请材料齐全后应何时答复？", "answer": ANSWER,
            "contexts": [EVIDENCE], "case_type": "positive", **changes}


def parent(rows=None):
    return {"summary": [], "summary_df": [], "gate_passed": False,
            "gate_message": "原始门禁 FAIL", "chart_path": None, "html_path": None,
            "total_elapsed_seconds": None, "faith_threshold": 0.8,
            "ctx_prec_threshold": 0.8, "model_dfs": {"own-model": rows or [row()]}}


def test_numeric_candidate_changes_only_original_span_and_verifies_complete_fact():
    result = core().improve_row(0, row(answer="  " + ANSWER + "  "))
    assert result["status"] == "verified_candidate"
    assert result["candidate_answer"] == "  " + EVIDENCE + "  "
    patch, = result["edits"]
    assert patch["before"] == "7" and patch["after"] == "3"
    assert result["original_answer"][patch["start"]:patch["end"]] == "7"
    assert patch["evidence"] == EVIDENCE.removesuffix("。")
    assert result["verification"]["status"] == "verified"
    assert result["verification"]["semantic_evaluation"] == "NOT_EVALUATED"


@pytest.mark.parametrize("changes", [
    {"answer": EVIDENCE},
    {"answer": "根据提供的文档，无法回答该问题。"},
    {"answer": "资料中没有答复时限。"},
    {"contexts": []},
    {"contexts": [EVIDENCE, None]},
    {"contexts": {"text": EVIDENCE}},
    {"contexts": ["申请材料齐全后，资料窗口应当在3个自然日内答复登记申请。"]},
    {"contexts": [EVIDENCE, EVIDENCE.replace("3", "5")]},
    {"contexts": [ANSWER, EVIDENCE]},
    {"contexts": ["退款应当在3个工作日内处理。"]},
    {"contexts": ["仅在紧急情况下，" + EVIDENCE]},
    {"contexts": ["仅在紧急情况下，\n" + EVIDENCE]},
    {"answer": ANSWER + "相关费用为8元。"},
    {"answer": "答复期限为7至9个工作日。", "contexts": ["答复期限为3至9个工作日。"]},
    {"answer": "答复期限超过7个工作日。", "contexts": ["答复期限超过3个工作日。"]},
    {"answer": "答复期限并非7个工作日。", "contexts": ["答复期限并非3个工作日。"]},
    {"answer": "答复期限为2026年。", "contexts": ["答复期限为2027年。"]},
    {"answer": "答复期限合计7个工作日。", "contexts": ["答复期限合计3个工作日。"]},
    {"question": "申请条件是7个工作日，如何计算？"},
    {"case_type": "negative"},
])
def test_ambiguous_unsupported_or_already_correct_rows_never_produce_a_patch(changes):
    result = core().improve_row(4, row(**changes))
    assert result["row_index"] == 4
    assert result["status"] in {"no_candidate", "review"}
    assert result["candidate_answer"] is None and result["edits"] == []
    assert result["unresolved_reasons"]
    assert result["contexts"] == row(**changes)["contexts"]


def test_string_context_and_same_value_repetition_are_supported_without_changing_offsets():
    for contexts in (EVIDENCE, [EVIDENCE, EVIDENCE]):
        result = core().improve_row(0, row(contexts=contexts))
        assert result["candidate_answer"] == EVIDENCE
        assert result["edits"][0]["context_index"] == 0
        assert result["contexts"] == contexts
    # The earlier contract wrongly accepted this multi-sentence string by
    # selecting an inner sentence. Preserve it as an explicit regression.
    multiple = "前置说明。" + EVIDENCE + EVIDENCE
    rejected = core().improve_row(0, row(contexts=multiple))
    assert rejected["status"] == "review" and rejected["candidate_answer"] is None
    assert rejected["contexts"] == multiple and rejected["unresolved_reasons"]


@pytest.mark.parametrize("answer,evidence,question", [
    ("登记费用为1,200元。", "登记费用为1,300元。", "登记费用是多少？"),
    ("登记费用为-7元。", "登记费用为-3元。", "登记费用是多少？"),
    ("登记费用为７元。", "登记费用为３元。", "登记费用是多少？"),
    (ANSWER, EVIDENCE, "请换算为自然日。"),
    (ANSWER, EVIDENCE, "请计算与延期时限的差。"),
    (ANSWER, EVIDENCE, "请给出两个时限的比值。"),
    (ANSWER, EVIDENCE, "请比较新旧版本。"),
])
def test_unhandled_number_formats_and_derived_questions_require_review(answer, evidence, question):
    result = core().improve_row(0, row(answer=answer, contexts=[evidence], question=question))
    assert result["status"] == "review" and result["candidate_answer"] is None
    assert result["unresolved_reasons"]


def test_record_keeps_all_rows_parent_fail_and_does_not_mutate_inputs():
    source = parent([row(), row(answer=EVIDENCE), row(contexts=[None]), row(answer="无法回答。")])
    before = deepcopy(source)
    report = core().build_record("a" * 32, source, "own-model", parent_record_sha256="b" * 64)
    assert source == before
    assert report["counts"] == {"total_rows": 4, "changed_rows": 1,
                                "verified_rows": 1, "no_candidate_rows": 1, "review_rows": 2}
    assert report["original_gate"]["passed"] is False
    assert report["original_gate"]["faith_threshold"] == 0.8
    assert report["rescoring"] == {"status": "not_run", "result": None}
    assert core().verify_record(report, source, parent_record_sha256="b" * 64)["status"] == "verified"


@pytest.mark.parametrize("field", ["parent", "contexts", "candidate", "patch", "evidence"])
def test_verifier_finds_changed_parent_or_sidecar(field):
    source = parent()
    report = core().build_record("a" * 32, source, "own-model", parent_record_sha256="b" * 64)
    if field == "parent":
        source["model_dfs"]["own-model"][0]["answer"] = EVIDENCE
    elif field == "contexts":
        report["items"][0]["contexts"] = ["伪造证据。"]
    elif field == "candidate":
        report["items"][0]["candidate_answer"] = EVIDENCE + "新增断言。"
    elif field == "patch":
        report["items"][0]["edits"][0]["start"] += 1
    else:
        report["items"][0]["edits"][0]["evidence"] = "3"
    # Resealing an edited sidecar must not bypass the actual patch/evidence checks.
    report["bindings"]["record_sha256"] = core().record_digest(report)
    assert core().verify_record(report, source, parent_record_sha256="b" * 64)["status"] == "failed"


def test_core_import_does_not_load_configuration_or_model_runtime():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    program = ("import sys; from pathlib import Path; import backend.core.answer_improvement as m; "
               "assert Path(m.__file__).resolve().is_relative_to(Path.cwd()); "
               "assert not {'backend.config','backend.core.pipeline','dotenv','torch','ragas'} & set(sys.modules); "
               "print(m.SCOPE)")
    done = subprocess.run([sys.executable, "-c", program], cwd=root, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "single-quantity-literal-v1"


def test_semicolon_does_not_discard_a_source_condition():
    result = core().improve_row(0, row(contexts=["仅限紧急申请；" + EVIDENCE]))
    assert result["candidate_answer"] is None


def test_verifier_rejects_patch_widened_beyond_the_number_after_resealing():
    source = parent()
    report = core().build_record("a" * 32, source, "own-model", parent_record_sha256="b" * 64)
    patch = report["items"][0]["edits"][0]
    patch.update(start=patch["start"] - 1, before="在7", after="在3")
    report["bindings"]["record_sha256"] = core().record_digest(report)
    assert core().verify_record(report, source, parent_record_sha256="b" * 64)["status"] == "failed"


def test_unknown_model_cannot_verify_as_an_empty_run_after_resealing():
    source = parent()
    report = core().build_record("a" * 32, source, "own-model", parent_record_sha256="b" * 64)
    report.update(model_name="missing", items=[], counts={"total_rows": 0, "changed_rows": 0,
        "verified_rows": 0, "no_candidate_rows": 0, "review_rows": 0})
    report["bindings"]["input_sha256"] = core().digest([])
    report["bindings"]["record_sha256"] = core().record_digest(report)
    assert core().verify_record(report, source, parent_record_sha256="b" * 64)["status"] == "failed"


@pytest.mark.parametrize("context", ["年费为10元？", "仅对新用户适用。年费为10元。"])
def test_whole_context_question_or_precondition_cannot_generate_unconditional_candidate(context):
    result = core().improve_row(0, row(question="年费是多少？", answer="年费为20元。", contexts=[context]))
    assert result["status"] == "review"
    assert result["candidate_answer"] is None and result["edits"] == []
    assert result["unresolved_reasons"]


@pytest.mark.parametrize("context", ["年费为10元？", "仅对新用户适用。年费为10元。"])
def test_whole_context_verifier_rejects_resigned_patch_using_question_or_omitted_precondition(context):
    source = parent([row(question="年费是多少？", answer="年费为20元。", contexts=[context])])
    report = core().build_record("a" * 32, source, "own-model", parent_record_sha256="b" * 64)
    report["items"][0].update(status="verified_candidate", candidate_answer="年费为10元。",
        edits=[{"start": 3, "end": 5, "before": "20", "after": "10", "context_index": 0, "evidence": "年费为10元"}])
    report["counts"].update(changed_rows=1, verified_rows=1, review_rows=0, no_candidate_rows=0)
    report["bindings"]["record_sha256"] = core().record_digest(report)
    assert core().verify_record(report, source, parent_record_sha256="b" * 64)["status"] == "failed"
