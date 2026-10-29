"""In-memory HTTP checks, with the repository's network guard active."""
import asyncio
import base64
import copy
import json

import httpx
import pytest

from backend.api.app import create_app
from backend.core import citation_version as cv
from test_citation_version import XML, body, inspection, make_file

# Windows creates an internal socketpair while constructing an event loop.
# Build it during collection before the offline guard; all HTTP uses ASGI RAM.
_LOOP = asyncio.new_event_loop()


def post(path, **kwargs):
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
            return await client.post(path, **kwargs)
    return _LOOP.run_until_complete(invoke())


def teardown_module():
    _LOOP.close()


@pytest.mark.parametrize("operation,data", [("inspect", inspection()), ("check", body())])
def test_actual_http_matches_core_without_file_writes(operation, data, tmp_path):
    response = post("/citations/versions/" + operation, json=data)
    assert response.status_code == 200
    assert response.json() == (cv.inspect if operation == "inspect" else cv.check)(data)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["inspect", "check"])
def test_read_only_423_before_transport_or_handler(monkeypatch, operation):
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")
    import backend.api.citation_version as transport
    async def forbidden(*args, **kwargs):
        raise AssertionError("read-only request reached file handler")
    monkeypatch.setattr(transport, "citation_version_request", forbidden)
    response = post("/citations/versions/" + operation, content="bad request without content type")
    assert response.status_code == 423


def test_invalid_xml_base64_and_request_rows_stay_in_order():
    data = body()
    data["files"].extend([make_file(b"<broken", "xml"), {"id": "b64", "name": "broken.xml", "base64": "!"}])
    for name in ("xml", "b64", "unknown"):
        row = copy.deepcopy(data["citations"][0]); row.update(id=name, file_id=name)
        data["citations"].append(row)
    data["citations"][2]["request"] = []
    response = post("/citations/versions/check", json=data)
    assert response.status_code == 200
    report = response.json()
    assert [r["id"] for r in report["rows"]] == ["c1", "xml", "b64", "unknown"]
    assert report["counts"]["total"] == 4 and report["counts"]["invalid"] == 3


@pytest.mark.parametrize("case", ["duplicate_file", "duplicate_citation", "extra", "missing", "bad_schema", "id_type", "null_files", "too_many_files", "too_many_rows"])
def test_package_errors_are_422(case):
    data = body()
    if case == "duplicate_file": data["files"].append(copy.deepcopy(data["files"][0]))
    if case == "duplicate_citation": data["citations"].append(copy.deepcopy(data["citations"][0]))
    if case == "extra": data["path"] = "private-path-marker"
    if case == "missing": del data["files"]
    if case == "bad_schema": data["schema"] = "other"
    if case == "id_type": data["files"][0]["id"] = 0
    if case == "null_files": data["files"] = None
    if case == "too_many_files": data["files"] = [make_file(XML, str(i)) for i in range(11)]
    if case == "too_many_rows": data["citations"] = [{**data["citations"][0], "id": str(i)} for i in range(21)]
    response = post("/citations/versions/check", json=data)
    assert response.status_code == 422 and "private-path-marker" not in response.text


@pytest.mark.parametrize("raw", ['{"schema":"a","schema":"private-path-marker"}', "{", '{"x":"\\ud800"}', '{"x":1e999}'])
def test_transport_json_rejected_without_echo(raw):
    response = post("/citations/versions/check", content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 422 and "private-path-marker" not in response.text


def test_stream_body_and_decoded_total_limits(monkeypatch):
    from backend.api import citation_version as transport
    monkeypatch.setattr(transport, "MAX_REQUEST_BYTES", 100)
    async def chunks():
        yield b"x" * 60
        yield b"x" * 60
    assert post("/citations/versions/check", content=chunks(), headers={"content-type": "application/json"}).status_code == 413
    monkeypatch.setattr(transport, "MAX_REQUEST_BYTES", cv.MAX_REQUEST_BYTES)
    monkeypatch.setattr(cv, "MAX_TOTAL_BYTES", len(XML))
    data = body(); data["files"].append(make_file(XML, "second"))
    assert post("/citations/versions/check", json=data).status_code == 413


def test_inspect_invalid_files_and_content_type():
    data = inspection()
    data["files"].append({"id": "invalid", "name": "x.xml", "base64": "!"})
    response = post("/citations/versions/inspect", json=data)
    assert response.status_code == 200
    assert [f["status"] for f in response.json()["files"]] == ["READY", "INVALID_INPUT"]
    assert post("/citations/versions/inspect", content="x").status_code == 415


def test_http_deep_xml_does_not_raise_recursionerror():
    raw = b'<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">' + b"<X>" * 1200 + b"</X>" * 1200 + b"</Legislation>"
    response = post("/citations/versions/check", json=body(raw))
    assert response.status_code == 200
    assert response.json()["counts"]["invalid"] == 1
    assert "深度" in response.json()["rows"][0]["output"]["input_errors"][0]


@pytest.mark.parametrize("operation", ["inspect", "check"])
def test_http_source_drift_returns_restart_409(monkeypatch, operation):
    from test_citation_version_hardening import drift
    drift(monkeypatch)
    response = post("/citations/versions/" + operation, json=inspection() if operation == "inspect" else body())
    assert response.status_code == 409 and "重启" in response.text


def test_http_resource_rejection_preserves_file_rows_or_rejects_batch():
    from test_citation_version_hardening import effect_source, repeated_body
    response = post("/citations/versions/check", json=repeated_body(effect_source()))
    assert response.status_code == 200
    assert response.json()["counts"]["total"] == response.json()["counts"]["invalid"] == 20
    response = post("/citations/versions/check", json=repeated_body(effect_source(30, 100000)))
    assert response.status_code == 413 and "整批" in response.text
