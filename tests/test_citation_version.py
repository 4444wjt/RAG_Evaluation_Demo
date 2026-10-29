"""Synthetic transport/contract examples, not legal correctness or held-out evidence."""
import base64
import copy
import hashlib

import pytest

from backend.core import _citation_version_rules as rules
from backend.core import citation_version as cv

DOC = "http://www.legislation.gov.uk/id/ukpga/2020/1"
PROVISION = DOC + "/section/1"
VIEW = "http://www.legislation.gov.uk/ukpga/2020/1/2020-06-01"
XML = f'''<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="{rules.L}" xmlns:ukm="{rules.M}" xmlns:dc="{rules.DC}"
 xmlns:dct="{rules.DCT}" xmlns:atom="{rules.ATOM}" IdURI="{DOC}" xml:lang="en">
 <ukm:Metadata><dc:title>Constructed example</dc:title><dc:identifier>{VIEW}</dc:identifier>
 <dc:language>en</dc:language><dct:valid>2020-06-01</dct:valid>
 <atom:link rel="self" href="{VIEW}/data.xml"/></ukm:Metadata>
 <Primary RestrictExtent="E+W"><Body><P1 IdURI="{PROVISION}" DocumentURI="{VIEW}"
 RestrictStartDate="2020-01-01" RestrictEndDate="2021-01-01"><Text>Example.</Text></P1></Body></Primary>
</Legislation>'''.encode("utf-8")


def make_request(raw=XML):
    return {"source_sha256": hashlib.sha256(raw).hexdigest(), "document_id": DOC,
            "provision_id": PROVISION, "view_uri": VIEW, "snapshot_date": "2020-06-01",
            "check_date": "2020-06-01", "extent": "E", "language": "en"}


def make_file(raw=XML, file_id="source", name="example.xml"):
    return {"id": file_id, "name": name, "base64": base64.b64encode(raw).decode("ascii")}


def body(raw=XML):
    return {"schema": "CitationVersionCheckV1", "files": [make_file(raw)],
            "citations": [{"id": "c1", "label": "", "file_id": "source", "request": make_request(raw)}]}


def inspection(raw=XML):
    return {"schema": "CitationVersionFilesV1", "files": [make_file(raw)]}


def test_raw_b1_output_preserved_and_report_deterministic():
    data = body()
    original = copy.deepcopy(data)
    result = cv.check(data)
    assert result["rows"][0]["output"] == rules.evaluate(XML, make_request(), "B1")
    assert result == cv.check(data) and data == original
    assert result["counts"] == dict(total=1, valid_input=1, conflicts=0, field_review=0, known_by_review=1, invalid=0)
    assert result["rows"][0]["output"]["axes"] == {"snapshot_binding": "MATCH", "fragment_metadata": "MATCH", "known_by": "REVIEW"}
    assert set(result["rows"][0]["output"]["subchecks"]) == set(rules.NAMES)
    assert result["inputs"]["citations"] == data["citations"]
    assert len(result["checker"]["source_sha256"]["rules"]) == 64
    assert "timestamp" not in result


@pytest.mark.parametrize("point,state", [("2020-01-01", "MATCH"), ("2020-12-31", "MATCH"), ("2021-01-01", "MISMATCH"), ("2019-12-31", "MISMATCH")])
def test_explicit_half_open_interval(point, state):
    data = body()
    data["citations"][0]["request"]["check_date"] = point
    assert cv.check(data)["rows"][0]["output"]["subchecks"]["fragment_interval"]["state"] == state


def test_inspection_observations_do_not_fill_expectations():
    result = cv.inspect(inspection())["files"][0]
    assert result["status"] == "READY" and result["sha256"] == hashlib.sha256(XML).hexdigest()
    assert result["observed"] == {"title": "Constructed example", "document_id": DOC, "language": "en",
        "provisions": [{"provision_id": PROVISION, "view_uri": VIEW, "snapshot_date": "2020-06-01",
                        "extent": "E+W", "start": "2020-01-01", "end": "2021-01-01"}], "ambiguities": []}
    request = make_request()
    del request["extent"]
    data = body()
    data["citations"][0]["request"] = request
    assert cv.check(data)["rows"][0]["output"]["axes"]["fragment_metadata"] == "INVALID_INPUT"


def test_ambiguous_fields_are_not_selected_first():
    raw = XML.replace(b"<dc:language>en</dc:language>", b"<dc:language>en</dc:language><dc:language>cy</dc:language>")
    raw = raw.replace(b"</Body>", f'<P1 IdURI="{PROVISION}" DocumentURI="{VIEW}"/></Body>'.encode())
    result = cv.inspect(inspection(raw))["files"][0]["observed"]
    assert result["language"] is None and len(result["provisions"]) == 2
    assert len(result["ambiguities"]) == 2
    output = cv.check(body(raw))["rows"][0]["output"]
    assert output["target_location"]["state"] == "REVIEW"


@pytest.mark.parametrize("change", ["hash", "missing", "bad_date", "request_type", "unknown_file"])
def test_mixed_invalid_rows_preserved(change):
    data = body()
    bad = copy.deepcopy(data["citations"][0])
    bad["id"] = "bad"
    if change == "hash": bad["request"]["source_sha256"] = "0" * 64
    if change == "missing": del bad["request"]["check_date"]
    if change == "bad_date": bad["request"]["check_date"] = "2020-99-01"
    if change == "request_type": bad["request"] = None
    if change == "unknown_file": bad["file_id"] = "absent"
    data["citations"].insert(0, bad)
    result = cv.check(data)
    assert [r["id"] for r in result["rows"]] == ["bad", "c1"]
    assert result["counts"]["total"] == 2 and result["counts"]["invalid"] == 1
    assert result["rows"][0]["output"]["axes"] == dict.fromkeys(rules.AXES, "INVALID_INPUT")
    assert result["rows"][1]["output"] == rules.evaluate(XML, make_request(), "B1")
    assert result["inputs"]["citations"] == data["citations"]


def test_same_name_distinct_bytes_bind_by_id_and_raw_hash():
    data = body()
    other = XML.replace(b"Example.", b"Other example.")
    data["files"].append(make_file(other, "other", "example.xml"))
    row = copy.deepcopy(data["citations"][0]); row.update(id="other", file_id="other")
    data["citations"].append(row)
    result = cv.check(data)
    assert result["counts"]["invalid"] == 1
    assert result["inputs"]["files"][0]["sha256"] != result["inputs"]["files"][1]["sha256"]
    data["citations"][1]["request"] = make_request(other)
    assert cv.check(data)["counts"]["valid_input"] == 2
    assert cv.inspect(inspection(XML.replace(b"\n", b"\r\n")))["files"][0]["sha256"] != hashlib.sha256(XML).hexdigest()


@pytest.mark.parametrize("kind", ["files", "citations"])
def test_duplicate_ids_reject_whole_package(kind):
    data = body()
    data[kind].append(copy.deepcopy(data[kind][0]))
    with pytest.raises(cv.CitationVersionInputError, match="重复"):
        cv.check(data)


@pytest.mark.parametrize("raw", [b"", b"<unrelated/>", b"<broken", b"\xff", b'<?xml version="1.0" encoding="UTF-16"?><x/>', b'<!DOCTYPE x [<!ENTITY z "a">]><x/>'])
def test_invalid_xml_becomes_inspect_error_and_preserved_row(raw):
    assert cv.inspect(inspection(raw))["files"][0]["status"] == "INVALID_INPUT"
    result = cv.check(body(raw))
    assert result["counts"]["invalid"] == 1
    assert result["rows"][0]["output"]["input_errors"]


def test_depth_and_node_limits_do_not_truncate_or_recurse(monkeypatch):
    raw = f'<Legislation xmlns="{rules.L}">'.encode() + b"<X>" * 130 + b"</X>" * 130 + b"</Legislation>"
    assert "深度" in cv.inspect(inspection(raw))["files"][0]["error"]
    assert cv.check(body(raw))["counts"]["invalid"] == 1
    monkeypatch.setattr(cv, "MAX_XML_NODES", 3)
    assert "节点" in cv.inspect(inspection())["files"][0]["error"]


def test_total_limit_includes_all_files(monkeypatch):
    data = body()
    data["files"].append(make_file(XML, "other"))
    monkeypatch.setattr(cv, "MAX_TOTAL_BYTES", len(XML))
    with pytest.raises(cv.CitationVersionInputError) as error:
        cv.check(data)
    assert error.value.status_code == 413


def test_conflict_and_field_review_overlap_without_losing_known_by():
    raw = XML.replace(b'RestrictExtent="E+W"', b'RestrictExtent="S"').replace(b' RestrictEndDate="2021-01-01"', b"")
    result = cv.check(body(raw))
    assert result["counts"] == dict(total=1, valid_input=1, conflicts=1, field_review=1, known_by_review=1, invalid=0)
    assert "overlap" in result["count_semantics"]


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":"\\ud800"}', b"[" * 2000, b"\xff"])
def test_strict_bounded_json(raw):
    with pytest.raises(cv.CitationVersionInputError):
        cv.load_json(raw)


def test_json_depth_limit_rejects_before_report_copy_or_render():
    raw = b'{"x":' * 140 + b'0' + b'}' * 140
    with pytest.raises(cv.CitationVersionInputError):
        cv.load_json(raw)
    nested = {}
    for _ in range(140):
        nested = {"x": nested}
    request = body()
    request["citations"][0]["request"] = nested
    with pytest.raises(cv.CitationVersionInputError):
        cv.check(request)
