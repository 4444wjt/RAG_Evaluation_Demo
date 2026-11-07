"""Trace evidence gets native source locations without changing its judgement."""

from copy import deepcopy

from test_recognition import asr_result, ocr_result


def test_statement_locations_keep_every_occurrence_and_do_not_mutate_inputs():
    from backend.recognition.locations import locate_statements

    result = ocr_result("b" * 64)
    repeated = deepcopy(result["pages"][0]["lines"][0])
    repeated["id"] = "p1-l2"
    result["pages"][0]["lines"].append(repeated)
    sidecar = {"recognition": result}
    statements = [{"text": "原陈述", "verdict": "supported", "evidence": "12000", "extra": None}]
    before = deepcopy((statements, sidecar))
    attached = locate_statements(statements, sidecar)
    assert (statements, sidecar) == before
    assert attached[0]["verdict"] == "supported" and attached[0]["extra"] is None
    assert attached[0]["source_locations"]["status"] == "ambiguous"
    matches = attached[0]["source_locations"]["matches"]
    assert [(item["start"], item["end"]) for item in matches] == [(2, 7), (16, 21)]
    assert [item["refs"][0]["line_id"] for item in matches] == ["p1-l0", "p1-l2"]


def test_empty_invalid_and_unmatched_evidence_remain_explicit_with_native_nulls():
    from backend.recognition.locations import locate_statements

    statements = [
        {"text": "原陈述", "verdict": "fact_error", "evidence": "未经批准"},
        {"text": "无引用", "verdict": "fabrication", "evidence": ""},
        {"text": "无引用字段", "verdict": "fabrication"},
        {"text": "缺失值", "verdict": "fabrication", "evidence": None},
        {"text": "未找到", "verdict": "supported", "evidence": "missing"},
    ]
    result = locate_statements(statements, {"recognition": asr_result("b" * 64)})
    assert [item["source_locations"]["status"] for item in result] == [
        "located", "empty_quote", "invalid_quote", "invalid_quote", "not_found",
    ]
    match = result[0]["source_locations"]["matches"][0]
    assert match["refs"] == [{"segment_id": "s1", "start_ms": 2500, "end_ms": 4900, "conf": None}]
    assert match["min_conf"] is None and match["conf_missing"] is True
    assert "evidence" not in result[2] and result[3]["evidence"] is None
