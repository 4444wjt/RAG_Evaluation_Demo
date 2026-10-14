"""Validate source registration without trusting metadata as a truth verdict."""

from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "frontend/src/fixtures/source-registry.json"
REGISTRY = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
DOCUMENTS = REGISTRY["documents"]
VERSION_FIELDS = ("issuer", "promulgated", "effective", "sourceUrl", "obtained")
DATE_FIELDS = ("promulgated", "effective", "obtained")
SCALAR_FIELDS = (
    "issuer", "documentNumber", "promulgated", "effective", "revision",
    "sourceUrl", "sourcePublisher", "obtained",
)
SOURCE_HASHES = {
    "demo.txt": "3434d02a7d5206436cec7f97f5f539edf425a397a701e73f409c2b074cef09c6",
    "scenarios/deposit_insurance.txt": "fee557dc493eb2b92f52834cf9b164720cff6b13f276aaa2054babccbc4666eb",
    "scenarios/information_disclosure.txt": "70b406d7895b4750d048afde9de8942e123acef13064170331773be7373ff0b6",
}
DISCLAIMER = (
    "登记的是资料来源与版本信息，不代表资料现行有效；"
    "哈希只证明文件未变，不证明内容正确或有效。"
)


def _lf_bytes(path: Path) -> bytes:
    # This is the same normalization as build_audit_fixture._source_hash.
    return path.read_bytes().replace(b"\r\n", b"\n")


def _assert_string(value):
    assert isinstance(value, str) and value.strip()


def _assert_nullable_string(value):
    if value is not None:
        _assert_string(value)


def _assert_relative_path(value):
    _assert_string(value)
    assert not Path(value).is_absolute()
    assert "\\" not in value and ":" not in value
    assert ".." not in Path(value).parts
    assert (ROOT / value).resolve().is_relative_to(ROOT)


def _validate_entry(entry):
    assert set(entry) == {
        "path", "sha256", "title", "authority", *SCALAR_FIELDS,
        "coverage", "scope", "fieldSources", "archives",
    }
    _assert_relative_path(entry["path"])
    assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
    _assert_string(entry["title"])
    assert entry["authority"] in {"official", "fictional"}
    for key in SCALAR_FIELDS:
        _assert_nullable_string(entry[key])
    for key in DATE_FIELDS:
        if entry[key] is not None:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry[key])
            assert date.fromisoformat(entry[key]).isoformat() == entry[key]
    if entry["sourceUrl"] is not None:
        assert entry["sourceUrl"].startswith("https://")
    if entry["authority"] == "fictional":
        assert all(entry[key] is None for key in VERSION_FIELDS)
        assert entry["sourcePublisher"] is None
        assert entry["documentNumber"] is None and entry["revision"] is None
    coverage = entry["coverage"]
    assert set(coverage) == {"kind", "sections"}
    assert coverage["kind"] in {"full", "excerpt"}
    assert isinstance(coverage["sections"], list)
    for section in coverage["sections"]:
        _assert_string(section)
    assert set(entry["scope"]) == {"subjects", "region", "conditions"}
    for value in entry["scope"].values():
        _assert_nullable_string(value)
    assert isinstance(entry["fieldSources"], dict)
    for value in entry["fieldSources"].values():
        _assert_string(value)
    for key in ("path", "sha256", "title", "authority", *SCALAR_FIELDS):
        if entry[key] is not None:
            assert key in entry["fieldSources"], f"Missing provenance: {key}"
    for key in ("coverage.kind", "coverage.sections"):
        assert key in entry["fieldSources"]
    for key, value in entry["scope"].items():
        if value is not None:
            assert f"scope.{key}" in entry["fieldSources"]
    assert isinstance(entry["archives"], list)
    assert len({item["path"] for item in entry["archives"]}) == len(entry["archives"])
    for item in entry["archives"]:
        assert set(item) == {"path", "rawSha256"}
        _assert_relative_path(item["path"])
        assert re.fullmatch(r"[0-9a-f]{64}", item["rawSha256"])


def _archive_records():
    """Read original checksum lists; CRLF stays untouched on disk."""
    for checksums in (ROOT / "docs").rglob("SHA256SUMS.txt"):
        for line in checksums.read_text(encoding="utf-8-sig").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})\s+\*?(.+)", line)
            if match:
                path = (checksums.parent / match[2]).resolve()
                assert path.is_relative_to(ROOT), checksums
                yield path, match[1]


def test_source_registry_contract_and_all_three_documents():
    assert set(REGISTRY) == {"schema", "hashMethod", "note", "documents"}
    assert REGISTRY["schema"] == 1 and type(REGISTRY["schema"]) is int
    assert REGISTRY["hashMethod"] == "sha256-lf"
    assert REGISTRY["note"] == DISCLAIMER
    assert {entry["path"] for entry in DOCUMENTS} == set(SOURCE_HASHES)
    assert len(DOCUMENTS) == 3


@pytest.mark.parametrize("entry", DOCUMENTS, ids=lambda entry: entry["path"])
def test_source_fields_have_valid_types_real_dates_and_provenance(entry):
    _validate_entry(entry)


@pytest.mark.parametrize("entry", DOCUMENTS, ids=lambda entry: entry["path"])
def test_registered_lf_hash_and_frozen_source_content(entry):
    path = ROOT / entry["path"]
    assert path.is_file()
    digest = hashlib.sha256(_lf_bytes(path)).hexdigest()
    assert digest == entry["sha256"] == SOURCE_HASHES[entry["path"]]


@pytest.mark.parametrize("entry", DOCUMENTS, ids=lambda entry: entry["path"])
def test_archive_bytes_match_manifest_and_normalized_source(entry):
    expected = {}
    source_bytes = _lf_bytes(ROOT / entry["path"])
    for path, recorded_digest in _archive_records():
        if path.is_file() and path.suffix == ".txt" and _lf_bytes(path) == source_bytes:
            key = path.relative_to(ROOT).as_posix()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == recorded_digest
            if key in expected:
                assert expected[key] == recorded_digest
            expected[key] = recorded_digest
    registered = {item["path"]: item["rawSha256"] for item in entry["archives"]}
    # Also ensures every actual, checksummed public copy was registered.
    assert registered == expected
    for item in entry["archives"]:
        path = ROOT / item["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["rawSha256"]
        assert _lf_bytes(path) == source_bytes


def test_raw_archive_hash_remains_distinct_from_lf_content_hash():
    entry = next(item for item in DOCUMENTS if item["path"] == "scenarios/deposit_insurance.txt")
    hashes = {item["rawSha256"] for item in entry["archives"]}
    assert entry["sha256"] in hashes
    assert "359d73e05853b9fcb379bdc44c7d66e939db09e2f5af5d260f22ad796fd3720a" in hashes
    assert len(hashes) == 2


@pytest.mark.parametrize("entry", [item for item in DOCUMENTS if item["authority"] == "official"], ids=lambda entry: entry["path"])
def test_existing_verification_date_is_not_current_download_date(entry):
    original = (ROOT / entry["path"]).read_text(encoding="utf-8")
    assert f"核验日期：{entry['obtained']}" in original
    assert entry["obtained"] == "2026-09-28"
    assert "核验日期" in entry["fieldSources"]["obtained"]
    assert "不是本轮下载日期" in entry["fieldSources"]["obtained"]
    assert f"来源网址：{entry['sourceUrl']}" in original


def test_unavailable_authority_and_region_remain_unknown():
    deposit = next(item for item in DOCUMENTS if item["path"] == "scenarios/deposit_insurance.txt")
    assert deposit["issuer"] is None
    assert deposit["documentNumber"] is None
    assert deposit["promulgated"] is None
    assert deposit["effective"] == "2015-05-01"
    disclosure = next(item for item in DOCUMENTS if item["path"] == "scenarios/information_disclosure.txt")
    assert disclosure["scope"]["region"] is None
    assert disclosure["promulgated"] == "2019-04-03"
    assert disclosure["effective"] == "2019-05-15"
    assert disclosure["issuer"] != disclosure["sourcePublisher"]


def test_registry_does_not_assert_current_validity():
    # The single, exact disclaimer is allowed; arbitrary negation is not a bypass.
    text = json.dumps({key: value for key, value in REGISTRY.items() if key != "note"}, ensure_ascii=False)
    for prohibited in ("现行有效", "最新版本", "已生效"):
        assert prohibited not in text
    assert REGISTRY["note"] == DISCLAIMER


def test_frozen_demo_and_gate_are_unchanged():
    demo = ROOT / "frontend/src/fixtures/demo.json"
    assert hashlib.sha256(demo.read_bytes()).hexdigest() == "20290882fadbb68942f2c754262614e0858d8595d06a8b2e970fec1c7b4dfb7b"
    gate = ROOT / "backend/core/gate.py"
    assert hashlib.sha256(_lf_bytes(gate)).hexdigest() == "89d98d3ae8eeb076da163427a68477e59de3a8783c69d0e5bcd614cc29e9d6ae"
    gate_text = gate.read_text(encoding="utf-8")
    assert "source-registry" not in gate_text and "sourceStatus" not in gate_text


@pytest.mark.parametrize("field,value", [
    ("authority", "verified"),
    ("promulgated", "2019/04/03"),
    ("effective", "2019-02-29"),
    ("effective", "2019-13-01"),
    ("obtained", 20260928),
    ("issuer", ""),
    ("sourceUrl", "http://example.invalid"),
    ("path", "../demo.txt"),
    ("sha256", "fee557dc"),
])
def test_invalid_scalar_registration_is_rejected(field, value):
    entry = deepcopy(DOCUMENTS[1])
    entry[field] = value
    with pytest.raises((AssertionError, ValueError)):
        _validate_entry(entry)


@pytest.mark.parametrize("mutation", [
    lambda item: item["fieldSources"].pop("effective"),
    lambda item: item["fieldSources"].pop("scope.subjects"),
    lambda item: item["coverage"].update(kind="unknown"),
    lambda item: item["coverage"].update(sections="第一条"),
    lambda item: item["scope"].update(subjects=[]),
    lambda item: item["archives"].append(item["archives"][0]),
    lambda item: item.update(authority="fictional"),
])
def test_invalid_structure_or_missing_field_sources_is_rejected(mutation):
    entry = deepcopy(DOCUMENTS[1])
    mutation(entry)
    with pytest.raises(AssertionError):
        _validate_entry(entry)
