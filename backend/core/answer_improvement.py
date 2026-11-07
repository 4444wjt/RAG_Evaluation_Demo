"""Bounded numeric candidates from actual contexts; no model or semantic verdict.

The comparison unit is one entire context containing one declarative sentence,
not a fuzzy match or an extracted sentence/subclause. Only an ASCII positive number may change. Offsets are zero-based
Unicode character positions, with an exclusive end; original whitespace stays.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from uuid import uuid4

from backend.core.statute_check import QUANTITY


SCOPE = "single-quantity-literal-v1"
GENERATOR_VERSION = "literal-quantity-candidate/v2"
VERIFIER_VERSION = "literal-quantity-verifier/v2"
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?|[零〇一二三四五六七八九十百千两]+")
_UNSAFE = re.compile(
    r"不|未|无|非|没|拒|否|至|以上|以下|之间|以内|至少|最多|超过|不足|约|左右|"
    r"大于|小于|高于|低于|比|较|或|分别|增加|减少|合计|总计|求和|相加|相减|差额|"
    r"增长|下降|换算|计算|倍|±|[~～—–<>≤≥]"
)
_DERIVED_QUESTION = re.compile(r"换算|计算|求和|合计|总计|相加|相减|差|比|推算|派生|版本|新旧")
_VERSION = re.compile(r"旧版|新版|版本|修订|生效日期")


def digest(value) -> str:
    """Canonical JSON hash; no input is normalized or dropped."""
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_digest(record: dict) -> str:
    payload = deepcopy(record)
    if isinstance(payload.get("bindings"), dict):
        payload["bindings"].pop("record_sha256", None)
    return digest(payload)


def _check(code: str, passed: bool) -> dict:
    return {"code": code, "passed": bool(passed)}


def _contexts(value) -> list[str] | None:
    if isinstance(value, str):
        return [value] if value.strip() else None
    if isinstance(value, list) and value and all(isinstance(c, str) and c.strip() for c in value):
        return value
    return None


def _fact(text: str) -> str | None:
    text = text.strip()
    if text.endswith("。"):
        text = text[:-1]
    if not text or re.search(r"[。！？!?；;\r\n]", text):
        return None
    return text


def _quantity(text: str):
    """Return the sole entire number token; never salvage 200 from 1,200."""
    if (re.search(r"[０-９]|[0-9][,，][0-9]|[-+−][ \t]*[0-9]", text)
            or any(unicodedata.category(char) in {"Cf", "Cs"} for char in text)):
        return None
    matches = list(QUANTITY.finditer(text))
    numbers = list(_NUMBER.finditer(text))
    if len(matches) != 1 or len(numbers) != 1:
        return None
    match, number = matches[0], numbers[0]
    if (match.span(1) != number.span() or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", match.group(1))
            or match.group(2) == "年"):
        return None
    return {"start": match.start(1), "end": match.end(1), "number": match.group(1),
            "value": Decimal(match.group(1)), "unit": match.group(2),
            "skeleton": text[:match.start(1)] + "\0" + text[match.end(1):]}


def _input_problem(row: dict) -> str | None:
    if row.get("case_type") not in (None, "positive"):
        return "负向或未知类型用例不自动改写，请保留拒答并人工复核。"
    answer, contexts, question = row.get("answer"), _contexts(row.get("contexts")), row.get("question")
    if not isinstance(answer, str) or not answer.strip():
        return "原回答缺失或格式不支持，未生成候选。"
    if contexts is None:
        return "实际上下文缺失或含非法条目；未过滤条目，也未生成候选。"
    if question is not None and not isinstance(question, str):
        return "问题字段格式不支持，未生成候选。"
    if isinstance(question, str) and (_DERIVED_QUESTION.search(question) or _NUMBER.search(question)):
        return "问题含数值条件、换算、比较、派生或版本要求，本范围不自动改写。"
    if _UNSAFE.search(answer):
        return "回答含拒答、否定、范围、比较或推算表达，本范围不自动改写。"
    if any(_VERSION.search(context) for context in contexts):
        return "上下文含版本或修订说明，须先核对适用版本，未生成候选。"
    if _fact(answer) is None:
        return "仅支持一个完整事实句；不截取子句或忽略附带条件。"
    return None


def _options(fact: str, contexts: list[str]) -> tuple[list[dict], str | None]:
    quantity = _quantity(fact)
    if quantity is None:
        return [], "仅支持单个 ASCII 正数及原样单位；多数值、日期、全角、千分位或符号格式须复核。"
    options = []
    for index, context in enumerate(contexts):
        # Keep the whole context as the comparison unit. Splitting even at a
        # period can discard a condition in the preceding sentence; stripping
        # a question mark can turn a question into an unsupported assertion.
        evidence = _fact(context)
        if evidence is None:
            return [], "每个实际上下文须为单个完整声明事实句；多句、问句或前后条件不拆开取证。"
        source = _quantity(evidence)
        if source is None or source["unit"] != quantity["unit"]:
            continue
        if source["skeleton"] == quantity["skeleton"]:
            if not _UNSAFE.search(evidence):
                options.append({"context_index": index, "evidence": evidence, **source})
        elif (quantity["skeleton"] in source["skeleton"] or source["skeleton"] in quantity["skeleton"]):
            # This only blocks a candidate; substring matching never
            # authorizes a replacement or discards a source condition.
            return [], "来源与回答含不同附带条件；不从子句选择替换数值。"
    return options, None


def _patch_checks(answer: str, contexts: list[str], candidate: str, edits: list[dict]) -> list[dict]:
    checks = []
    single = isinstance(edits, list) and len(edits) == 1 and isinstance(edits[0], dict)
    checks.append(_check("single_patch", single))
    if not single:
        return checks
    edit = edits[0]
    start, end = edit.get("start"), edit.get("end")
    indices = (type(start) is int and type(end) is int and 0 <= start < end <= len(answer))
    before, after = edit.get("before"), edit.get("after")
    exact = (indices and isinstance(before, str) and isinstance(after, str)
             and answer[start:end] == before and before != after
             and candidate == answer[:start] + after + answer[end:])
    checks.append(_check("original_span_and_unchanged_surroundings", exact))
    original_fact, candidate_fact = _fact(answer), _fact(candidate)
    old = _quantity(original_fact) if original_fact else None
    new = _quantity(candidate_fact) if candidate_fact else None
    numeric = (old is not None and new is not None and old["skeleton"] == new["skeleton"]
               and old["value"] != new["value"] and old["unit"] == new["unit"]
               and not _UNSAFE.search(candidate_fact)
               and start == len(answer) - len(answer.lstrip()) + old["start"]
               and end == len(answer) - len(answer.lstrip()) + old["end"]
               and before == old["number"] and after == new["number"])
    checks.append(_check("one_quantity_same_unit_and_complete_fact", numeric))
    index, evidence = edit.get("context_index"), edit.get("evidence")
    located = (type(index) is int and 0 <= index < len(contexts) and isinstance(evidence, str)
               and evidence == candidate_fact and evidence in contexts[index]
               and _fact(contexts[index]) == evidence)
    checks.append(_check("whole_fact_in_one_actual_context", located))
    options, problem = _options(original_fact, contexts) if original_fact else ([], "unsupported")
    values = {option["value"] for option in options}
    unique = (problem is None and old is not None and new is not None
              and values == {new["value"]} and old["value"] not in values)
    checks.append(_check("unique_value_without_conflicting_original", unique))
    return checks


def improve_row(row_index: int, row: dict) -> dict:
    """Return one row without editing its answer, contexts or missing values."""
    result = {"row_index": row_index, "question": deepcopy(row.get("question")),
              "original_answer": deepcopy(row.get("answer")), "contexts": deepcopy(row.get("contexts")),
              "status": "review", "candidate_answer": None, "edits": [],
              "verification": {"status": "not_applicable", "checks": [], "semantic_evaluation": "NOT_EVALUATED"},
              "unresolved_reasons": []}
    problem = _input_problem(row)
    if problem:
        result["unresolved_reasons"] = [problem]
        return result
    answer, contexts = row["answer"], _contexts(row["contexts"])
    fact = _fact(answer)
    options, problem = _options(fact, contexts)
    if problem:
        result["unresolved_reasons"] = [problem]
        return result
    if not options:
        result["unresolved_reasons"] = ["未找到数字外逐字相同、单位相同且保留全部条件的完整来源事实。"]
        return result
    old = _quantity(fact)
    values = {option["value"] for option in options}
    if len(values) != 1:
        result["unresolved_reasons"] = ["同一事实在实际上下文中出现不同数值，不能选择一个替换。"]
        return result
    if old["value"] in values:
        result.update(status="no_candidate", unresolved_reasons=["原数值已与完整来源事实一致，无需生成改动。"])
        return result
    source = options[0]
    offset = len(answer) - len(answer.lstrip())
    start, end = offset + old["start"], offset + old["end"]
    candidate = answer[:start] + source["number"] + answer[end:]
    edits = [{"start": start, "end": end, "before": answer[start:end], "after": source["number"],
              "context_index": source["context_index"], "evidence": source["evidence"]}]
    checks = _patch_checks(answer, contexts, candidate, edits)
    if not all(check["passed"] for check in checks):
        result["unresolved_reasons"] = ["候选未满足完整事实与补丁检查，保留原回答。"]
        return result
    result.update(status="verified_candidate", candidate_answer=candidate, edits=edits,
                  verification={"status": "verified", "checks": checks, "semantic_evaluation": "NOT_EVALUATED"})
    return result


def _inputs(rows: list[dict]) -> list[dict]:
    return [{key: deepcopy(row.get(key)) for key in ("question", "answer", "contexts", "case_type")} for row in rows]


def _counts(items: list[dict]) -> dict:
    return {"total_rows": len(items), "changed_rows": sum(item["candidate_answer"] is not None for item in items),
            "verified_rows": sum(item["status"] == "verified_candidate" for item in items),
            "no_candidate_rows": sum(item["status"] == "no_candidate" for item in items),
            "review_rows": sum(item["status"] == "review" for item in items)}


def build_record(parent_record_id: str, parent_result: dict, model_name: str, *, parent_record_sha256: str) -> dict:
    rows = parent_result["model_dfs"][model_name]
    items = [improve_row(index, row) for index, row in enumerate(rows)]
    record = {"schema_version": 1, "id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
              "parent_record_id": parent_record_id, "parent_result_sha256": digest(parent_result),
              "model_name": model_name, "scope": SCOPE,
              "original_gate": {"passed": parent_result["gate_passed"], "message": parent_result["gate_message"],
                                "faith_threshold": parent_result.get("faith_threshold"),
                                "ctx_prec_threshold": parent_result.get("ctx_prec_threshold")},
              "bindings": {"input_sha256": digest(_inputs(rows)), "parent_record_sha256": parent_record_sha256,
                           "generator_version": GENERATOR_VERSION, "verifier_version": VERIFIER_VERSION},
              "items": items, "counts": _counts(items), "rescoring": {"status": "not_run", "result": None}}
    record["bindings"]["record_sha256"] = record_digest(record)
    return record


def verify_record(record: dict, parent_result: dict, *, parent_record_sha256: str) -> dict:
    """Recheck literal evidence, parent binding and patch integrity, not semantics.

    SHA256 detects changes to the saved package. It does not authenticate the
    provenance of user-imported data or establish external factual truth.
    """
    bindings = record.get("bindings") if isinstance(record.get("bindings"), dict) else {}
    model_name = record.get("model_name")
    rows = parent_result.get("model_dfs", {}).get(model_name, [])
    items = record.get("items") if isinstance(record.get("items"), list) else []
    checks = [_check("record_sha256", record_digest(record) == bindings.get("record_sha256")),
              _check("model_in_parent_record", model_name in parent_result.get("model_dfs", {})),
              _check("parent_record_sha256", parent_record_sha256 == bindings.get("parent_record_sha256")),
              _check("parent_result_sha256", digest(parent_result) == record.get("parent_result_sha256")),
              _check("original_inputs_sha256", digest(_inputs(rows)) == bindings.get("input_sha256")),
              _check("complete_row_denominator", len(rows) == len(items)),
              _check("scope_and_versions", record.get("scope") == SCOPE and record.get("schema_version") == 1
                     and bindings.get("generator_version") == GENERATOR_VERSION and bindings.get("verifier_version") == VERIFIER_VERSION),
              _check("no_rescoring_or_new_gate", record.get("rescoring") == {"status": "not_run", "result": None})]
    expected_gate = {"passed": parent_result.get("gate_passed"), "message": parent_result.get("gate_message"),
                     "faith_threshold": parent_result.get("faith_threshold"), "ctx_prec_threshold": parent_result.get("ctx_prec_threshold")}
    checks.append(_check("original_gate_unchanged", record.get("original_gate") == expected_gate))
    verified_items = []
    for index, item in enumerate(items):
        row = rows[index] if index < len(rows) else {}
        if not isinstance(item, dict):
            verified_items.append({"row_index": index, "status": "failed", "checks": [_check("item_shape", False)]})
            continue
        row_checks = [_check("bound_parent_row", item.get("row_index") == index
                      and item.get("question") == row.get("question") and item.get("original_answer") == row.get("answer")
                      and item.get("contexts") == row.get("contexts"))]
        if item.get("status") == "verified_candidate":
            contexts = _contexts(row.get("contexts"))
            eligible = _input_problem(row) is None
            row_checks.append(_check("supported_input_scope", eligible))
            if eligible and isinstance(item.get("candidate_answer"), str):
                row_checks.extend(_patch_checks(row["answer"], contexts, item["candidate_answer"], item.get("edits")))
            else:
                row_checks.append(_check("candidate_shape", False))
        else:
            row_checks.append(_check("unchanged_unresolved_row", item.get("status") in {"no_candidate", "review"}
                              and item.get("candidate_answer") is None and item.get("edits") == []))
        row_checks.append(_check("semantic_verdict_not_claimed", isinstance(item.get("verification"), dict)
                          and item["verification"].get("semantic_evaluation") == "NOT_EVALUATED"))
        verified_items.append({"row_index": index, "status": "verified" if all(c["passed"] for c in row_checks) else "failed",
                               "checks": row_checks})
    try:
        counts_match = _counts(items) == record.get("counts")
    except (KeyError, TypeError):
        counts_match = False
    checks.append(_check("counts_preserve_all_rows", counts_match))
    passed = all(check["passed"] for check in checks) and all(item["status"] == "verified" for item in verified_items)
    return {"schema_version": 1, "improvement_id": record.get("id"), "status": "verified" if passed else "failed",
            "checks": checks, "items": verified_items, "semantic_evaluation": "NOT_EVALUATED",
            "rescoring": {"status": "not_run", "result": None}}
