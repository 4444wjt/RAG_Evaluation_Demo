"""Boundary tests use constructed text, not held-out questions or human labels."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from jsonschema import Draft202012Validator
import pytest

from backend.core import statute_check as sc
from backend.core.statute_locate import check_answer_extended
from scripts import statute_diagnostic as sd

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/statute-diagnostic.v1.schema.json").read_text(encoding="utf-8"))
SOURCE = "第二十七条　资料窗口应当在3个工作日内答复申请。\n申请材料不完整的，资料窗口应当通知申请人补正。"


def request(**changes):
    return {"schemaVersion": 1, "sourceFormat": "article-lines-v1", "source": SOURCE,
            "answer": "根据第二十七条第三款，申请材料不完整的，资料窗口应当通知申请人补正。",
            "contexts": [SOURCE], **changes}


def validate_report(report):
    schema = {**SCHEMA, "$ref": "#/$defs/report"}
    Draft202012Validator(schema).validate(report)


def call_cli(path, cwd, script=None):
    return subprocess.run([sys.executable, "-I", "-S", "-B", "-X", "utf8",
                           str(script or ROOT / "scripts/statute_diagnostic.py"),
                           "--input", str(path), "--json"], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=20)


@pytest.mark.parametrize("engine", ["base", "extended"])
def test_constructed_input_preserves_core_and_explicit_missing_question(engine):
    payload = request(engine=engine)
    before = deepcopy(payload)
    report = sd.analyze_request(payload)
    runner = sc.check_answer if engine == "base" else check_answer_extended
    assert report["result"] == runner(payload["answer"], payload["contexts"], payload["source"], "")
    assert report["status"] == "findings" and report["flagged"] is True
    assert payload == before and "question" not in payload
    assert report["sourceAudit"] == {"nonblankLines": 2, "parsedLines": 2, "unparsedLines": [], "articleCount": 1}
    assert sd.exit_code(report) == 1
    if engine == "extended":
        assert report["result"]["recoveries"][0]["recovered"] == "第二十七条第二款"
    validate_report(report)


def test_no_findings_is_not_supported_or_quality_gate_pass():
    report = sd.analyze_request(request(answer="根据第二十七条第二款，申请材料不完整的，资料窗口应当通知申请人补正。"))
    assert report["status"] == "no_error_found" and report["flagged"] is False
    assert "supported" not in report and "gate_passed" not in report
    assert any("未检出不等于" in text for text in report["limitations"])
    assert sd.exit_code(report) == 0
    validate_report(report)


@pytest.mark.parametrize("changes", [
    {"schemaVersion": 2}, {"schemaVersion": True}, {"schemaVersion": "1"},
    {"sourceFormat": "arbitrary-text"}, {"source": None}, {"source": "  "},
    {"answer": None}, {"answer": "\n"}, {"contexts": SOURCE}, {"contexts": []},
    {"contexts": [None]}, {"contexts": [""]}, {"question": None}, {"question": {}},
    {"engine": "auto"}, {"authority": 99},
])
def test_schema_and_runtime_reject_bad_fields_before_engine(changes, monkeypatch):
    payload = request(**changes)
    assert not Draft202012Validator(SCHEMA).is_valid(payload)
    monkeypatch.setattr(sc, "check_answer", lambda *a: pytest.fail("Invalid input reached core"))
    report = sd.analyze_request(payload)
    assert report["status"] == "invalid_input" and report["flagged"] is None and report["result"] is None
    assert sd.exit_code(report) == 2
    validate_report(report)


@pytest.mark.parametrize("payload", [None, [], "text", {}, {"schemaVersion": 1}])
def test_invalid_top_level_or_missing_fields(payload):
    report = sd.analyze_request(payload)
    assert report["status"] == "invalid_input"
    validate_report(report)


@pytest.mark.parametrize("source,code", [
    ("这是一段没有条标题的正文。", "NO_PROVISIONS"),
    ("第二十七条\n资料窗口应当通知申请人补正。", "EMPTY_ARTICLE_BODY"),
    ("第二十七条　资料窗口办理申请。\n\n没有标题的后续正文。", "UNPARSED_SOURCE_LINE"),
    ("文档导语。\n第二十七条　资料窗口办理申请。", "UNPARSED_SOURCE_LINE"),
    ("第二十七条　正文。\n\n第二十七条　另一段正文。", "DUPLICATE_ARTICLE"),
    ("第二十七条　情形包括：\n（一）红色材料。\n（一）蓝色材料。", "DUPLICATE_ITEM"),
    ("第二十七条　情形包括：\n（一）", "EMPTY_ITEM_BODY"),
    ("第二十七条　（一）红色材料。", "ITEM_ON_HEADER_LINE"),
    ("第二十七条第一款　正文。", "LABELED_LAYOUT_UNSUPPORTED"),
    ("第二十七条　正文。\n第二款　另一段正文。", "PROVISION_LAYOUT_UNSUPPORTED"),
    ("第二十七条　正文。\n(1) 另一段正文。", "PROVISION_LAYOUT_UNSUPPORTED"),
    ("第一千条　正文。", "ARTICLE_NUMBER_UNSUPPORTED"),
])
def test_unsupported_source_never_returns_no_error(source, code, monkeypatch):
    monkeypatch.setattr(sc, "check_answer", lambda *a: pytest.fail("Unsupported source reached core"))
    report = sd.analyze_request(request(source=source))
    assert report["status"] == "review" and report["result"] is None and report["flagged"] is None
    assert code in {issue["code"] for issue in report["diagnostics"]}
    validate_report(report)


def test_layout_coverage_includes_all_nonblank_lines():
    report = sd.analyze_request(request(source="导语。\n第二十七条　正文。\n\n后续正文。"))
    assert report["sourceAudit"] == {"nonblankLines": 3, "parsedLines": 1, "unparsedLines": [1, 4], "articleCount": 1}


@pytest.mark.parametrize("number", ["零", "一千", "9" * 5000])
def test_malformed_reference_is_structured_review(number):
    report = sd.analyze_request(request(answer=f"根据第{number}条，资料窗口办理申请。"))
    assert report["status"] == "review" and report["flagged"] is None
    validate_report(report)


def test_source_huge_integer_is_structured_review():
    report = sd.analyze_request(request(source="第" + "9" * 5000 + "条　正文。"))
    assert report["status"] == "review"


def test_runtime_failure_cannot_become_no_error(monkeypatch):
    def broken(*args):
        raise ValueError("internal detail must not be printed")
    monkeypatch.setattr(sc, "check_answer", broken)
    report = sd.analyze_request(request())
    assert report["status"] == "review" and report["diagnostics"][0]["code"] == "ENGINE_FAILURE"
    assert "internal detail" not in json.dumps(report)
    validate_report(report)


@pytest.mark.parametrize("field", ["source", "answer", "question", "contexts"])
def test_lone_unicode_surrogates_are_rejected_not_replaced(field, tmp_path):
    bad = "资料" + chr(0xD800) + "窗口"
    payload = request(**{field: [bad] if field == "contexts" else bad})
    assert not Draft202012Validator(SCHEMA).is_valid(payload)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    done = call_cli(path, tmp_path)
    report = json.loads(done.stdout)
    assert done.returncode == 2 and done.stderr == ""
    assert report["status"] == "invalid_input" and report["result"] is None
    assert "TEXT_ENCODING_INVALID" in {x["code"] for x in report["diagnostics"]}
    validate_report(report)


def test_valid_non_bmp_unicode_is_not_rejected():
    payload = request(question="资料窗口🌐如何受理？")
    assert Draft202012Validator(SCHEMA).is_valid(payload)
    assert sd.analyze_request(payload)["status"] == "findings"


def test_extended_partial_recovery_remains_partial():
    source = "第一条　资料窗口办理登记申请。\n资料窗口办理登记申请。"
    report = sd.analyze_request(request(source=source, contexts=[source], engine="extended",
                                        answer="根据第一条第三款，资料窗口办理登记申请。"))
    recovery, = report["result"]["recoveries"]
    assert report["status"] == "findings"
    assert recovery["recovered"] == "第一条" and recovery["location"]["paragraph"] is None


@pytest.mark.parametrize("raw", [b"{", b"\xff", b'{"schemaVersion":1,"schemaVersion":2}', b'{"bad":NaN}', b"[" * 1500])
def test_file_parse_errors_do_not_echo_input_or_traceback(raw, tmp_path):
    path = tmp_path / "request.json"
    path.write_bytes(raw)
    report = sd.diagnose_file(path)
    assert report["status"] == "invalid_input"
    assert report["provenance"]["inputSha256"] == hashlib.sha256(raw).hexdigest()
    validate_report(report)


def test_unreadable_and_oversized_inputs_have_no_fake_input_hash(tmp_path):
    report = sd.diagnose_file(tmp_path / "missing.json")
    assert report["diagnostics"][0]["code"] == "INPUT_UNREADABLE" and report["provenance"] is None
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (sd.MAX_BYTES + 2))
    report = sd.diagnose_file(path)
    assert report["diagnostics"][0]["code"] == "INPUT_TOO_LARGE" and report["provenance"] is None


@pytest.mark.parametrize("version", [1, 1.0])
def test_json_schema_integer_semantics_match_runtime(version):
    payload = request(schemaVersion=version)
    assert Draft202012Validator(SCHEMA).is_valid(payload)
    assert sd.analyze_request(payload)["status"] == "findings"


@pytest.mark.parametrize("status", ["findings", "no_error_found", "review", "invalid_input"])
def test_cli_outside_repo_uses_stdlib_only_and_exit_contract(tmp_path, status):
    payload = request()
    if status == "no_error_found":
        payload["answer"] = "根据第二十七条第二款，申请材料不完整的，资料窗口应当通知申请人补正。"
    elif status == "review":
        payload["source"] = "无条标题。"
    elif status == "invalid_input":
        payload["contexts"] = "not-an-array"
    path = tmp_path / "request.json"
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    done = call_cli(path.name, tmp_path)
    report = json.loads(done.stdout)
    assert done.stderr == "" and done.returncode == sd.exit_code(report)
    assert report["status"] == status
    assert report["provenance"]["inputSha256"] == hashlib.sha256(raw).hexdigest()
    validate_report(report)


def test_candidate_minimum_files_run_without_project_env_or_archives(tmp_path):
    names = ["scripts/statute_diagnostic.py", "backend/__init__.py", "backend/core/__init__.py",
             "backend/core/statute_check.py", "backend/core/statute_locate.py"]
    root = tmp_path / "candidate"
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    path = tmp_path / "own-data.json"
    path.write_text(json.dumps(request(engine="extended"), ensure_ascii=False), encoding="utf-8")
    done = call_cli(path, tmp_path, root / "scripts/statute_diagnostic.py")
    report = json.loads(done.stdout)
    assert done.returncode == 1 and done.stderr == "" and report["status"] == "findings"
    assert report["result"]["recoveries"][0]["recovered"] == "第二十七条第二款"
    assert all(not (root / name).exists() for name in (".env", "work", "docs", "scenarios", "venv"))
    assert report["provenance"]["codeSha256"] == {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("scripts/statute_diagnostic.py", "backend/core/statute_check.py", "backend/core/statute_locate.py")}


def test_constructed_example_is_schema_valid_and_declares_source_boundary():
    payload = json.loads((ROOT / "examples/statute_check/constructed-request.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(SCHEMA)
    Draft202012Validator(SCHEMA).validate(payload)
    report = sd.analyze_request(payload)
    assert report["status"] == "findings" and report["engine"] == "extended"
    validate_report(report)


# Layout and independent-reference regressions use newly constructed material only.
def structure_unit(body, article=27, paragraph=1, item=None):
    return {"article": article, "paragraph": paragraph, "item": item, "body": body}


def structure_reference(*units):
    return {"schemaVersion": 1, "units": list(units)}


def block_diagnostic_engines(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unverified source structure reached a rule engine")
    monkeypatch.setattr(sc, "check_answer", forbidden)
    monkeypatch.setattr(sd, "check_answer_extended", forbidden)


@pytest.mark.parametrize("engine,marker,on_header", [
    ("base", "1.", False), ("extended", "1.", True),
    ("extended", "１２．", False), ("base", "１２．", True),
    ("base", "一、", False), ("extended", "一、", True),
    ("extended", "一百、", False), ("base", "一百、", True),
    ("base", "(一)", True),
])
def test_noncontract_item_prefixes_require_review(engine, marker, on_header, monkeypatch):
    body = marker + "资料窗口应当核对红色材料。"
    source = "第二十七条　" + (body if on_header else "申请材料包括：\n" + body)
    block_diagnostic_engines(monkeypatch)
    report = sd.analyze_request(request(source=source, contexts=[source], engine=engine))
    assert report["status"] == "review"
    assert report["flagged"] is None and report["result"] is None
    issue = next(x for x in report["diagnostics"] if x["code"] == "PROVISION_LAYOUT_UNSUPPORTED")
    assert issue["line"] == (1 if on_header else 2)
    validate_report(report)


@pytest.mark.parametrize("engine", ["base", "extended"])
def test_canonical_item_on_header_keeps_existing_review_code(engine, monkeypatch):
    block_diagnostic_engines(monkeypatch)
    report = sd.analyze_request(request(source="第二十七条　（一）红色材料。", engine=engine))
    assert report["status"] == "review"
    assert "ITEM_ON_HEADER_LINE" in {x["code"] for x in report["diagnostics"]}
    validate_report(report)


@pytest.mark.parametrize("engine,body,on_header", [
    ("base", "1.5万元是本次申请的金额。", False),
    ("extended", "1.5万元是本次申请的金额。", True),
    ("extended", "１２．５万元是本次申请的金额。", False),
    ("base", "１２．５万元是本次申请的金额。", True),
    ("base", "10.25%为本次申请的比例。", True),
    ("extended", "2026.10.23发布的申请材料应当留存。", False),
    ("base", "2026年10月23日发布的申请材料应当留存。", True),
    ("extended", "申请事项1.应当留存材料。", False),
    ("base", "申请材料分为一、二类。", True),
])
def test_decimal_date_and_inline_text_are_not_item_prefixes(engine, body, on_header):
    source = "第二十七条　" + (body if on_header else "资料窗口办理申请。\n" + body)
    payload = request(source=source, contexts=[source], answer="本资料说明申请事项。", engine=engine)
    report = sd.analyze_request(payload)
    runner = sc.check_answer if engine == "base" else check_answer_extended
    assert report["result"] == runner(payload["answer"], [source], source, "")
    assert report["status"] == "no_error_found"
    assert not report["diagnostics"]
    validate_report(report)


@pytest.mark.parametrize("engine", ["base", "extended"])
def test_absent_structure_reference_preserves_result_and_explicitly_limits_scope(engine):
    payload = request(engine=engine)
    report = sd.analyze_request(payload)
    runner = sc.check_answer if engine == "base" else check_answer_extended
    assert report["result"] == runner(payload["answer"], payload["contexts"], SOURCE, "")
    assert report["sourceAudit"] == {"nonblankLines": 2, "parsedLines": 2, "unparsedLines": [], "articleCount": 1}
    assert report["structureCheck"] == {
        "status": "not_provided", "referenceSha256": None, "expectedUnits": None,
        "observedUnits": 2, "scope": "all_paragraph_item_locations"}
    assert any("未核验" in text and ("款" in text or "结构" in text) for text in report["limitations"])
    validate_report(report)


@pytest.mark.parametrize("engine", ["base", "extended"])
def test_structure_reference_matches_normalized_bodies_and_item_labels(engine):
    source = ("  第２７条　申请金额为２．５万元，材料包括：  \r\n"
              "  （一）红色材料。  \r\n"
              "  申请材料不完整的，资料窗口应当通知申请人补正。  ")
    reference = structure_reference(
        structure_unit("  申请金额为2.5万元，材料包括：  "),
        structure_unit("红色材料。", item=1),
        structure_unit("申请材料不完整的，资料窗口应当通知申请人补正。", paragraph=2))
    payload = request(source=source, contexts=[source], structureReference=reference, engine=engine,
                      answer="根据第二十七条第二款，申请材料不完整的，资料窗口应当通知申请人补正。")
    before = deepcopy(payload)
    assert Draft202012Validator(SCHEMA).is_valid(payload)
    report = sd.analyze_request(payload)
    runner = sc.check_answer if engine == "base" else check_answer_extended
    assert report["result"] == runner(payload["answer"], [source], source, "")
    assert report["status"] == "no_error_found" and payload == before
    check = report["structureCheck"]
    assert check["status"] == "matched" and check["expectedUnits"] == check["observedUnits"] == 3
    assert check["scope"] == "all_paragraph_item_locations"
    assert len(check["referenceSha256"]) == 64
    assert all(char in "0123456789abcdef" for char in check["referenceSha256"])
    validate_report(report)


@pytest.mark.parametrize("engine", ["base", "extended"])
@pytest.mark.parametrize("source,reference,code", [
    ("第二十七条　资料窗口应当在3个工作日内\n答复登记申请。",
     structure_reference(structure_unit("资料窗口应当在3个工作日内答复登记申请。")),
     "SOURCE_STRUCTURE_MISMATCH"),
    ("第二十七条　申请材料包括：\n1.红色材料。",
     structure_reference(structure_unit("申请材料包括："), structure_unit("红色材料。", item=1)),
     "PROVISION_LAYOUT_UNSUPPORTED"),
    ("第二十七条　申请材料包括：\n一、红色材料。",
     structure_reference(structure_unit("申请材料包括："), structure_unit("红色材料。", item=1)),
     "PROVISION_LAYOUT_UNSUPPORTED"),
    ("第二十七条　资料窗口办理登记申请。\n申请材料不完整的，应当补正。",
     structure_reference(structure_unit("资料窗口办理登记申请。申请材料不完整的，应当补正。")),
     "SOURCE_STRUCTURE_MISMATCH"),
    ("第二十七条　资料窗口办理登记申请。\n申请材料应当完整。\n申请人应当补正材料。资料窗口应当通知结果。",
     structure_reference(structure_unit("资料窗口办理登记申请。申请材料应当完整。"),
                         structure_unit("申请人应当补正材料。", paragraph=2),
                         structure_unit("资料窗口应当通知结果。", paragraph=3)),
     "SOURCE_STRUCTURE_MISMATCH"),
])
def test_independent_reference_blocks_new_constructed_layout_failures(engine, source, reference, code, monkeypatch):
    block_diagnostic_engines(monkeypatch)
    report = sd.analyze_request(request(source=source, contexts=[source], structureReference=reference, engine=engine))
    assert report["status"] == "review" and report["flagged"] is None and report["result"] is None
    assert code in {x["code"] for x in report["diagnostics"]}
    if code == "SOURCE_STRUCTURE_MISMATCH":
        assert report["structureCheck"]["status"] == "mismatch"
        assert report["structureCheck"]["expectedUnits"] == len(reference["units"])
        assert report["structureCheck"]["observedUnits"] == len(sc.parse_provisions(source))
    validate_report(report)


@pytest.mark.parametrize("reference", [
    structure_reference(structure_unit("另一段并不相同的正文。")),
    structure_reference(structure_unit("资料窗口办理登记申请。", article=28)),
])
def test_equal_unit_counts_do_not_hide_body_or_article_mismatch(reference, monkeypatch):
    block_diagnostic_engines(monkeypatch)
    source = "第二十七条　资料窗口办理登记申请。"
    report = sd.analyze_request(request(source=source, structureReference=reference))
    assert report["status"] == "review" and report["result"] is None
    assert "SOURCE_STRUCTURE_MISMATCH" in {x["code"] for x in report["diagnostics"]}
    assert report["structureCheck"]["expectedUnits"] == report["structureCheck"]["observedUnits"] == 1
    validate_report(report)


@pytest.mark.parametrize("reference", [
    None, {}, [], {"schemaVersion": 1, "units": []},
    {"schemaVersion": 2, "units": [structure_unit("正文。")]},
    {"schemaVersion": True, "units": [structure_unit("正文。")]},
    {"schemaVersion": 1, "units": [structure_unit("正文。")], "authority": "trusted"},
    structure_reference({"article": 27, "paragraph": 1, "body": "正文。"}),
    structure_reference({**structure_unit("正文。"), "extra": 1}),
    structure_reference(structure_unit("")), structure_reference(structure_unit(" \n ")),
    structure_reference(structure_unit(123)), structure_reference(structure_unit("正文。", article=True)),
    structure_reference(structure_unit("正文。", article=0)),
    structure_reference(structure_unit("正文。", article=1000)),
    structure_reference(structure_unit("正文。", paragraph=0)),
    structure_reference(structure_unit("正文。", paragraph=1000)),
    structure_reference(structure_unit("正文。", item=0)),
    structure_reference(structure_unit("正文。", item=True)),
    structure_reference(structure_unit("正文。", item=1000)),
    structure_reference(structure_unit("资料" + chr(0xD800) + "窗口。")),
])
def test_invalid_structure_reference_shape_is_rejected_before_core(reference, monkeypatch):
    payload = request(structureReference=reference)
    assert not Draft202012Validator(SCHEMA).is_valid(payload)
    block_diagnostic_engines(monkeypatch)
    report = sd.analyze_request(payload)
    assert report["status"] == "invalid_input" and report["flagged"] is None and report["result"] is None
    assert report["structureCheck"] is None
    validate_report(report)


@pytest.mark.parametrize("units", [
    [structure_unit("正文。"), structure_unit("重复第一款。")],
    [structure_unit("正文。", paragraph=2)],
    [structure_unit("正文。"), structure_unit("跳到第三款。", paragraph=3)],
    [structure_unit("没有所属款。", item=1)],
    [structure_unit("正文。"), structure_unit("跳到第二项。", item=2)],
    [structure_unit("正文。"), structure_unit("第一项。", item=1), structure_unit("重复第一项。", item=1)],
    [structure_unit("正文。"), structure_unit("第二款。", paragraph=2), structure_unit("返回第一款。")],
    [structure_unit("正文。"), structure_unit("另一条。", article=28), structure_unit("重复前一条。")],
])
def test_structure_reference_requires_unique_contiguous_units(units, monkeypatch):
    block_diagnostic_engines(monkeypatch)
    report = sd.analyze_request(request(structureReference=structure_reference(*units)))
    assert report["status"] == "invalid_input" and report["result"] is None and report["flagged"] is None
    validate_report(report)


@pytest.mark.parametrize("surrogate", [False, True])
def test_structure_reference_cli_review_or_invalid_keeps_provenance_without_env(tmp_path, surrogate):
    names = ["scripts/statute_diagnostic.py", "backend/__init__.py", "backend/core/__init__.py",
             "backend/core/statute_check.py", "backend/core/statute_locate.py"]
    root = tmp_path / "isolated-structure"
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    body = "资料" + chr(0xD800) + "窗口。" if surrogate else "独立参照中的另一段正文。"
    payload = request(structureReference=structure_reference(structure_unit(body)))
    path = tmp_path / "own-structure.json"
    raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    path.write_bytes(raw)
    done = call_cli(path, tmp_path, root / "scripts/statute_diagnostic.py")
    report = json.loads(done.stdout)
    assert done.returncode == 2 and done.stderr == ""
    assert report["status"] == ("invalid_input" if surrogate else "review")
    assert report["flagged"] is None and report["result"] is None
    assert report["provenance"]["inputSha256"] == hashlib.sha256(raw).hexdigest()
    assert report["provenance"]["codeSha256"] == {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("scripts/statute_diagnostic.py", "backend/core/statute_check.py", "backend/core/statute_locate.py")}
    assert all(not (root / name).exists() for name in (".env", "work", "docs", "scenarios", "venv"))
    validate_report(report)


@pytest.mark.parametrize("engine", ["base", "extended"])
def test_matched_structure_reference_preserves_real_rule_findings(engine):
    reference = structure_reference(
        structure_unit("资料窗口应当在3个工作日内答复申请。"),
        structure_unit("申请材料不完整的，资料窗口应当通知申请人补正。", paragraph=2))
    payload = request(structureReference=reference, engine=engine)
    report = sd.analyze_request(payload)
    runner = sc.check_answer if engine == "base" else check_answer_extended
    assert report["structureCheck"]["status"] == "matched"
    assert report["status"] == "findings" and report["flagged"] is True
    assert report["result"] == runner(payload["answer"], payload["contexts"], SOURCE, "")
    assert sd.exit_code(report) == 1
    validate_report(report)


@pytest.mark.parametrize("field,char", [
    ("answer", "\u2060"), ("source", "\ufeff"), ("source", "\u200b"),
    ("question", "\u00ad"), ("contexts[1]", "\u2060"),
    ("structureReference.units[0].body", "\u2060"),
])
def test_invisible_formats_block_before_parsing(field, char, monkeypatch):
    payload = request()
    text = "首行。\r\n第" + char + "三款" + char
    if field == "contexts[1]":
        payload["contexts"] = [SOURCE, text]
    elif field.startswith("structureReference"):
        payload["structureReference"] = {"schemaVersion": 1, "units": [
            {"article": 27, "paragraph": 1, "item": None, "body": text}]}
    else:
        payload[field] = text
    before = deepcopy(payload)
    monkeypatch.setattr(sd, "_audit_source", lambda *a: pytest.fail("Cf reached parser"))
    report = sd.analyze_request(payload)
    assert (report["status"], sd.exit_code(report), report["flagged"], report["result"]) == ("review", 2, None, None)
    assert report["sourceAudit"] is None and report["structureCheck"] is None
    assert report["diagnostics"] == [{"code": "INVISIBLE_FORMAT_CHARACTER", "message":
        f"{field}：首次出现于第2行第2列；U+{ord(char):04X} "
        + {"\u2060": "WORD JOINER", "\ufeff": "ZERO WIDTH NO-BREAK SPACE",
           "\u200b": "ZERO WIDTH SPACE", "\u00ad": "SOFT HYPHEN"}[char]
        + "；出现2次。不自动删除；请清理后重试。"}]
    assert payload == before
    validate_report(report)


def test_cf_grouping_and_validation_precedence():
    report = sd.analyze_request(request(answer="第\u2060三款\u2060\u200b", question="\u2060"))
    assert len(report["diagnostics"]) == 3
    assert [d["message"].split("：")[0] for d in report["diagnostics"]] == ["answer", "answer", "question"]
    assert "出现2次" in report["diagnostics"][0]["message"]
    invalid = sd.analyze_request(request(answer="第\u2060三款", contexts=[]))
    assert invalid["status"] == "invalid_input"
    assert "INVISIBLE_FORMAT_CHARACTER" not in [d["code"] for d in invalid["diagnostics"]]
    validate_report(report)
    validate_report(invalid)


def test_invisible_reference_cli_and_file_bom(tmp_path):
    payload = json.loads((ROOT / "examples/statute_check/constructed-request.json").read_text(encoding="utf-8"))
    original = sd.analyze_request(payload)
    assert original["status"] == "findings" and sd.exit_code(original) == 1
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")
    assert sd.diagnose_file(path)["result"] == original["result"]
    payload["answer"] = payload["answer"].replace("第三款", "第\u2060三款")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")
    cli = call_cli(path, tmp_path)
    report = json.loads(cli.stdout)
    assert cli.returncode == 2 and report["status"] == "review"
    index = payload["answer"].index("\u2060")
    assert f"第1行第{index + 1}列" in report["diagnostics"][0]["message"]
    assert "U+2060 WORD JOINER" in report["diagnostics"][0]["message"]
    assert report["result"] is None and report["flagged"] is None
    validate_report(report)


@pytest.mark.parametrize('char', [chr(n) for n in [0xad, *range(0x200b, 0x2010),
    *range(0x202a, 0x202f), *range(0x2060, 0x2065), 0xfeff]])
def test_cf_categories_in_source_heading(char):
    payload = request()
    payload['source'] = char + payload['source']
    report = sd.analyze_request(payload)
    assert (report['status'], sd.exit_code(report), report['flagged'], report['result']) == ('review', 2, None, None)
    assert report['diagnostics'][0]['code'] == 'INVISIBLE_FORMAT_CHARACTER'
    assert f'U+{ord(char):04X}' in report['diagnostics'][0]['message']
    assert '第1行第1列' in report['diagnostics'][0]['message']
    validate_report(report)
