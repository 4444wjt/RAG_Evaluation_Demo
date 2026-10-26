"""Offline parse checks using constructed samples and hand-built minimal PDFs.

The PDF builder uses only bytes and the standard library. Pipeline tests compile
the real orchestration functions, as conftest does for the legacy pipeline, so
importing model runtimes or loading embeddings is unnecessary.
"""
import ast
from datetime import datetime
import functools
import hashlib
import json
import os
from pathlib import Path
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pypdf
import pytest

from backend import config
from backend.core.gate import check_quality_gate
from backend.services import parse_check
from backend.storage.json_history import save_eval_record


FIXTURES = Path(__file__).parent / "fixtures" / "parse_check"
CHINESE_TEXT = "文档解析可信检查。中文正文。"


def _minimal_pdf(pages):
    """Build PDF bytes: str = ToUnicode text, None = image without text.

    Every character has an explicit UTF-16BE ToUnicode mapping. The image is a
    real, one-pixel RGB XObject. An empty string makes an ordinary blank page.
    """
    characters = list(dict.fromkeys("".join(page for page in pages if page)))
    codes = {char: index + 1 for index, char in enumerate(characters)}
    mappings = b"\n".join(
        f"<{codes[char]:04X}> <{char.encode('utf-16-be').hex().upper()}>".encode("ascii")
        for char in characters
    )
    cmap = (
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /ConstructedUnicode def\n/CMapType 2 def\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        + f"{len(characters)} beginbfchar\n".encode("ascii") + mappings
        + b"\nendbfchar\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    )

    def stream(data):
        return f"<< /Length {len(data)} >>\nstream\n".encode("ascii") + data + b"\nendstream"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # Filled after page object numbers are known.
        b"<< /Type /Font /Subtype /Type0 /BaseFont /ConstructedChinese /Encoding /Identity-H "
        b"/DescendantFonts [4 0 R] /ToUnicode 5 0 R >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ConstructedChinese "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>",
        stream(cmap),
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Length 3 >>\nstream\n\x00\x00\x00\nendstream",
    ]
    page_ids = []
    for page in pages:
        page_id = len(objects) + 1
        content_id = page_id + 1
        page_ids.append(page_id)
        resources = b"/Font << /F1 3 0 R >>"
        if page is None:
            resources += b" /XObject << /Im0 6 0 R >>"
            content = b"q 1 0 0 1 0 0 cm /Im0 Do Q"
        elif page:
            encoded = "".join(f"{codes[char]:04X}" for char in page).encode("ascii")
            content = b"BT /F1 12 Tf 20 60 Td <" + encoded + b"> Tj ET"
        else:
            content = b""
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources << "
            + resources + b" >> /Contents " + f"{content_id} 0 R >>".encode("ascii")
        )
        objects.append(stream(content))
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode("ascii")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def _write_pdf(tmp_path, pages, name="constructed_test.pdf"):
    path = tmp_path / name
    path.write_bytes(_minimal_pdf(pages))
    return path


@pytest.mark.parametrize("filename,expected", [
    ("constructed_cjk_builtin_font_mojibake.pdf", "FAIL"),
    ("constructed_english_control.pdf", "PASS"),
    ("constructed_blank.pdf", "PASS"),
])
def test_constructed_pdf_samples(filename, expected):
    report = parse_check.check_pdf(FIXTURES / filename)
    assert report["verdict"] == expected
    assert report["pages"]
    if expected == "FAIL":
        assert any("GARBLED_TEXT_LAYER" in page["reasons"] for page in report["pages"])
    if filename == "constructed_blank.pdf":
        assert all("BLANK_PAGE" in page["reasons"] for page in report["pages"])
        assert all(page["verdict"] != "FAIL" for page in report["pages"])


def test_tounicode_chinese_text_passes(tmp_path):
    path = _write_pdf(tmp_path, [CHINESE_TEXT])
    assert pypdf.PdfReader(path).pages[0].extract_text() == CHINESE_TEXT
    report = parse_check.check_document(path)
    assert report["verdict"] == "PASS"
    page = report["pages"][0]
    assert page["page_number"] == 1
    assert page["char_count"] == len(CHINESE_TEXT)
    assert page["han_count"] == sum("\u4e00" <= char <= "\u9fff" for char in CHINESE_TEXT)
    assert not page["has_image"]
    for key in ("replacement_count", "c0_control_count", "c1_control_count", "control_count", "cid_count"):
        assert page[key] == 0


def test_image_only_page_requires_review(tmp_path):
    report = parse_check.check_pdf(_write_pdf(tmp_path, [None]))
    assert report["verdict"] == "REVIEW"
    assert report["pages"][0]["has_image"] is True
    assert report["pages"][0]["char_count"] == 0
    assert report["pages"][0]["verdict"] == "REVIEW"
    assert "SUSPECTED_SCAN_PAGE" in report["pages"][0]["reasons"]


def test_mixed_pdf_locates_scan_on_second_page(tmp_path):
    report = parse_check.check_pdf(_write_pdf(tmp_path, [CHINESE_TEXT, None, ""]))
    assert report["verdict"] == "REVIEW"
    assert [page["verdict"] for page in report["pages"]] == ["PASS", "REVIEW", "PASS"]
    reviews = [page for page in report["pages"] if page["verdict"] == "REVIEW"]
    assert [page["page_number"] for page in reviews] == [2]
    assert "SUSPECTED_SCAN_PAGE" in reviews[0]["reasons"]
    assert "BLANK_PAGE" in report["pages"][2]["reasons"]


@pytest.mark.parametrize("text,count_key", [
    ("\x01" * 30, "c0_control_count"),
    ("\x81" * 30, "c1_control_count"),
    ("\x85" * 30, "c1_control_count"),
    ("\x7f" * 30, "del_control_count"),
    ("\ufffd" * 30, "replacement_count"),
    ("(cid:123)" * 30, "cid_count"),
])
def test_each_strong_garbling_signal_fails(tmp_path, text, count_key):
    path = _write_pdf(tmp_path, [text])
    assert pypdf.PdfReader(path).pages[0].extract_text() == text
    report = parse_check.check_pdf(path)
    assert report["verdict"] == "FAIL"
    page = report["pages"][0]
    assert page[count_key] == 30
    assert "GARBLED_TEXT_LAYER" in page["reasons"]
    assert page["control_count"] == page["c0_control_count"] + page["c1_control_count"] + page["del_control_count"]


def test_normal_newlines_tabs_and_spaces_are_not_garbling(tmp_path):
    path = _write_pdf(tmp_path, ["正常正文\n\r\t    Another paragraph\n"])
    report = parse_check.check_pdf(path)
    assert report["verdict"] == "PASS"
    assert report["pages"][0]["control_count"] == 0


def test_fail_takes_priority_over_scan_review(tmp_path):
    report = parse_check.check_pdf(_write_pdf(tmp_path, [None, "\ufffd" * 30]))
    assert report["verdict"] == "FAIL"
    assert [page["verdict"] for page in report["pages"]] == ["REVIEW", "FAIL"]


class _TextPage(dict):
    def __init__(self, text, resources=None):
        super().__init__({"/Resources": resources or {}})
        self.text = text

    def extract_text(self):
        return self.text


@pytest.mark.parametrize("text,expected", [
    ("\x81" * 5 + "A" * 95, "PASS"),
    ("\x81" * 6 + "A" * 94, "FAIL"),
    ("\ufffd" + "A" * 99, "PASS"),
    ("\ufffd" * 2 + "A" * 98, "FAIL"),
    ("(cid:123)" + "A" * 491, "PASS"),
    ("(cid:123)" + "A" * 490, "FAIL"),
])
def test_garbling_thresholds_are_strictly_greater_than_boundaries(monkeypatch, tmp_path, text, expected):
    path = tmp_path / "constructed_threshold.pdf"
    path.write_bytes(b"source bytes read by fingerprint; parser output is controlled")
    monkeypatch.setattr(parse_check.pypdf, "PdfReader", lambda source: SimpleNamespace(pages=[_TextPage(text)]))
    assert parse_check.check_pdf(path)["verdict"] == expected


def test_image_inside_form_resources_and_cycle_is_detected(monkeypatch, tmp_path):
    path = tmp_path / "constructed_nested_image.pdf"
    path.write_bytes(b"constructed parser double for nested PDF resources")
    resources = {"/XObject": {}}
    resources["/XObject"]["/Cycle"] = {"/Subtype": "/Form", "/Resources": resources}
    resources["/XObject"]["/Nested"] = {
        "/Subtype": "/Form", "/Resources": {"/XObject": {"/Image": {"/Subtype": "/Image"}}},
    }
    page = _TextPage("", resources)
    monkeypatch.setattr(parse_check.pypdf, "PdfReader", lambda source: SimpleNamespace(pages=[page]))
    report = parse_check.check_pdf(path)
    assert report["verdict"] == "REVIEW"
    assert report["pages"][0]["has_image"] is True
    assert "SUSPECTED_SCAN_PAGE" in report["pages"][0]["reasons"]


@pytest.mark.parametrize("extension", [".txt", ".md", ".docx"])
def test_other_supported_formats_are_not_applicable(tmp_path, extension):
    path = tmp_path / ("constructed_source" + extension)
    path.write_bytes(b"The check does not interpret this non-PDF source.")
    report = parse_check.check_document(path)
    assert report["verdict"] == "NOT_APPLICABLE"
    assert report["source_fingerprint"] == parse_check.fingerprint_source(path)
    assert report["pages"] == []
    # 检查器没有解析非 PDF 文件，不得把 pypdf 记成它的加载器。
    assert report["loader"] is None


def test_missing_document_keeps_loader_error_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="文档不存在"):
        parse_check.check_document(tmp_path / "constructed_missing.pdf")


def test_fingerprint_binds_exact_source_bytes_and_normalizes_extension(tmp_path):
    path = tmp_path / "constructed_source.PDF"
    original = b"one\r\ntwo\r\n"
    path.write_bytes(original)
    before = parse_check.fingerprint_source(path)
    assert before == {"sha256": hashlib.sha256(original).hexdigest(), "size_bytes": len(original), "extension": ".pdf"}
    path.write_bytes(original.replace(b"\r\n", b"\n"))
    after = parse_check.fingerprint_source(path)
    assert after["sha256"] != before["sha256"]
    assert after["size_bytes"] < before["size_bytes"]


def test_pdf_report_binds_loader_checker_and_chunk_metadata(tmp_path):
    path = _write_pdf(tmp_path, [CHINESE_TEXT])
    report = parse_check.check_pdf(path)
    assert report["source_fingerprint"] == parse_check.fingerprint_source(path)
    assert report["loader"] == {"name": "pypdf", "version": pypdf.__version__}
    assert report["checker_version"] == parse_check.CHECKER_VERSION
    assert report["chunk_parameters"] == {"chunk_size": config.CHUNK_SIZE, "chunk_overlap": config.CHUNK_OVERLAP}
    assert json.loads(json.dumps(report, ensure_ascii=False)) == report


@pytest.fixture
def isolated_pipeline(tmp_path):
    """Execute unchanged production function bodies with offline service doubles."""
    source = Path(__file__).resolve().parents[1] / "backend" / "core" / "pipeline.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
    names = {"prepare_resources", "evaluate"}
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in definitions} == names
    module = ModuleType("parse_check_pipeline_under_test")
    module.__dict__.update({key: value for key, value in vars(config).items() if key.isupper()})
    module.__dict__.update(
        json=json, os=os, time=time, datetime=datetime, pd=pd,
        check_document=parse_check.check_document,
        fingerprint_source=parse_check.fingerprint_source,
        HuggingFaceEmbeddings=Mock(return_value="offline-embeddings"),
        load_and_split_doc=Mock(return_value=[SimpleNamespace(page_content="offline text")]),
        build_vector_store=Mock(return_value=SimpleNamespace(as_retriever=Mock(return_value="offline-retriever"))),
        select_model_configs=Mock(return_value=[{"name": "offline-model"}]),
        prepare_testset=Mock(return_value=[{"question": "q", "ground_truth": "g", "case_type": "positive"}]),
        run_models=Mock(return_value={"summary": [{
            "model": "offline-model", "faithfulness": 0.7375, "context_precision": 0.9,
            "pos_faithfulness": 0.7375, "pos_context_precision": 0.9,
        }], "model_dfs": {}}),
        generate_evaluation_chart=Mock(), generate_html_report=Mock(),
        check_quality_gate=check_quality_gate,
        SUMMARY_OUTPUT=str(tmp_path / "summary.csv"),
        save_eval_record=functools.partial(save_eval_record, path=str(tmp_path / "history.json")),
    )
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), "exec"), module.__dict__)
    return module


def test_pipeline_fail_stops_before_loading_and_vector_store(isolated_pipeline):
    with pytest.raises(RuntimeError) as error:
        isolated_pipeline.prepare_resources(str(FIXTURES / "constructed_cjk_builtin_font_mojibake.pdf"))
    assert "GARBLED_TEXT_LAYER" in str(error.value)
    assert "1" in str(error.value)
    isolated_pipeline.HuggingFaceEmbeddings.assert_not_called()
    isolated_pipeline.load_and_split_doc.assert_not_called()
    isolated_pipeline.build_vector_store.assert_not_called()


def test_pipeline_review_warns_continues_and_persists_without_rewriting_history(
    isolated_pipeline, tmp_path, capsys,
):
    path = _write_pdf(tmp_path, [CHINESE_TEXT, None])
    old_record = {"doc_name": "older.txt", "gate_passed": False, "models": []}
    history_path = tmp_path / "history.json"
    history_path.write_text(json.dumps([old_record]), encoding="utf-8")
    result = isolated_pipeline.evaluate({"doc_path": str(path)}, model_factory=Mock())
    isolated_pipeline.load_and_split_doc.assert_called_once_with(str(path))
    isolated_pipeline.build_vector_store.assert_called_once()
    output = capsys.readouterr().out
    assert "REVIEW" in output
    assert "SUSPECTED_SCAN_PAGE" in output
    assert "2" in output
    previous, record = json.loads(history_path.read_text(encoding="utf-8"))
    assert previous == old_record
    assert record["source_fingerprint"] == parse_check.fingerprint_source(path)
    assert record["parse_check"]["verdict"] == "REVIEW"
    assert record["parse_check"]["pages"][1]["page_number"] == 2
    assert record["parse_check"]["source_fingerprint"] == record["source_fingerprint"]
    assert record["gate_passed"] is False
    assert result["gate_passed"] is False
    assert len(result) == 8


def test_pipeline_presupplied_resources_do_not_invent_parse_pass(isolated_pipeline, tmp_path):
    resources = {"embeddings": "E", "chunks": "C", "retriever": "R", "testset": []}
    isolated_pipeline.evaluate({"doc_path": "unavailable.pdf"}, resources=resources, model_factory=Mock())
    isolated_pipeline.load_and_split_doc.assert_not_called()
    record = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))[0]
    assert record["source_fingerprint"] is None
    assert record["parse_check"] is None
