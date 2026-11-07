"""Actual HTTP and local files exercise the user-result candidate lifecycle."""

import asyncio
from copy import deepcopy
import json
import hashlib
import socket

from fastapi.testclient import TestClient
import pytest

from backend.api.app import create_app
from backend.api import evaluation_records
from test_answer_improvement import ANSWER, EVIDENCE, parent, row


_LOOPBACK_CONNECT = socket.socket.connect


def client_for(app):
    def new_loop():
        guarded = socket.socket.connect
        socket.socket.connect = _LOOPBACK_CONNECT
        try:
            return asyncio.new_event_loop()
        finally:
            socket.socket.connect = guarded
    return TestClient(app, backend_options={"loop_factory": new_loop})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("VERISCOPE_BYOK", raising=False)
    monkeypatch.delenv("VERISCOPE_READ_ONLY", raising=False)
    original = evaluation_records.import_record(tmp_path, parent([row(), row(contexts=[None])]))
    return tmp_path, original, client_for(create_app(project_root=tmp_path))


def test_user_import_generate_reopen_verify_and_download_preserves_parent(setup):
    root, original, client = setup
    path = evaluation_records.record_path(root, original.id)
    before = path.read_bytes()
    response = client.post(f"/evaluations/{original.id}/improvements", json={"model_name": "own-model"})
    assert response.status_code == 201
    report = response.json()
    assert report["parent_record_id"] == original.id
    assert report["items"][0]["candidate_answer"] == EVIDENCE
    assert report["counts"]["total_rows"] == 2
    restarted = client_for(create_app(project_root=root))
    download = restarted.get(f"/improvements/{report['id']}")
    assert download.status_code == 200 and download.json() == report
    assert ".json" in download.headers["content-disposition"]
    assert download.content == (root / "work/improvements" / f"{report['id']}.json").read_bytes()
    assert download.headers["x-content-sha256"] == hashlib.sha256(download.content).hexdigest()
    verified = restarted.post(f"/improvements/{report['id']}/verify")
    assert verified.status_code == 200 and verified.json()["status"] == "verified"
    assert path.read_bytes() == before


@pytest.mark.parametrize("operation", ["generate", "read", "verify"])
def test_public_readonly_mode_blocks_entire_improvement_lifecycle(setup, monkeypatch, operation):
    _root, original, client = setup
    made = client.post(f"/evaluations/{original.id}/improvements", json={"model_name": "own-model"}).json()
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")
    if operation == "generate":
        response = client.post(f"/evaluations/{original.id}/improvements", json={"model_name": "own-model"})
    elif operation == "read":
        response = client.get(f"/improvements/{made['id']}")
    else:
        response = client.post(f"/improvements/{made['id']}/verify")
    assert response.status_code == 423


def test_api_rejects_unknown_model_and_missing_parent_without_creating_sidecar(setup):
    root, original, client = setup
    assert client.post(f"/evaluations/{original.id}/improvements", json={"model_name": "missing"}).status_code == 422
    assert client.post(f"/evaluations/{'f' * 32}/improvements", json={"model_name": "own-model"}).status_code == 404
    assert client.get("/improvements/not-a-local-record").status_code == 404
    assert not (root / "work/improvements").exists()


def test_disk_failure_reports_failure_without_modifying_parent(setup, monkeypatch):
    root, original, client = setup
    import backend.api.answer_improvement as api
    before = evaluation_records.record_path(root, original.id).read_bytes()
    def failed(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(api, "write_improvement", failed)
    response = client.post(f"/evaluations/{original.id}/improvements", json={"model_name": "own-model"})
    assert response.status_code == 500
    assert evaluation_records.record_path(root, original.id).read_bytes() == before


def test_api_reports_parent_byte_changes_and_sidecar_hash_tampering(setup):
    root, original, client = setup
    report = client.post(f"/evaluations/{original.id}/improvements", json={"model_name": "own-model"}).json()
    original_path = evaluation_records.record_path(root, original.id)
    original_path.write_bytes(original_path.read_bytes() + b"\n")
    checked = client.post(f"/improvements/{report['id']}/verify")
    assert checked.status_code == 200 and checked.json()["status"] == "failed"
    sidecar = root / "work/improvements" / f"{report['id']}.json"
    data = json.loads(sidecar.read_bytes())
    data["items"][0]["candidate_answer"] = "被篡改的候选"
    sidecar.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert client.post(f"/improvements/{report['id']}/verify").json()["status"] == "failed"


@pytest.mark.parametrize("bindings", [None, [], "broken"])
def test_bad_binding_shape_is_an_explicit_failed_verification_not_server_exception(setup, bindings):
    root, original, client = setup
    report = client.post(f"/evaluations/{original.id}/improvements", json={"model_name": "own-model"}).json()
    report["bindings"] = bindings
    (root / "work/improvements" / f"{report['id']}.json").write_text(json.dumps(report), encoding="utf-8")
    response = client.post(f"/improvements/{report['id']}/verify")
    assert response.status_code == 200 and response.json()["status"] == "failed"


@pytest.fixture
def visitor_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("VERISCOPE_BYOK", "1")
    monkeypatch.delenv("VERISCOPE_READ_ONLY", raising=False)
    monkeypatch.setenv("VERISCOPE_BYOK_DATA_DIR", str(tmp_path / "visitors"))
    return tmp_path, client_for(create_app(project_root=tmp_path))


def test_byok_improvement_lifecycle_uses_same_session_as_original_record(visitor_setup):
    root, client = visitor_setup
    headers = {"X-VeriScope-Session": "a" * 64}
    original = client.post("/evaluations/import", headers=headers, json=parent([row()])).json()
    session_root = root / "visitors" / hashlib.sha256(b"a" * 64).hexdigest()
    original_path = evaluation_records.record_path(session_root, original["id"])
    before = original_path.read_bytes()
    made = client.post(f"/evaluations/{original['id']}/improvements", headers=headers,
                       json={"model_name": "own-model"})
    assert made.status_code == 201, made.text
    record = made.json()
    download = client.get(f"/improvements/{record['id']}", headers=headers)
    assert download.status_code == 200 and download.json() == record
    assert download.content == (session_root / "work/improvements" / f"{record['id']}.json").read_bytes()
    checked = client.post(f"/improvements/{record['id']}/verify", headers=headers)
    assert checked.status_code == 200 and checked.json()["status"] == "verified"
    assert original_path.read_bytes() == before
    assert not (root / "work/improvements").exists()


@pytest.mark.parametrize("foreign_headers,status", [
    ({"X-VeriScope-Session": "b" * 64}, 404), ({}, 422),
])
def test_byok_improvements_reject_foreign_or_missing_session_and_local_parents(visitor_setup, foreign_headers, status):
    root, client = visitor_setup
    headers = {"X-VeriScope-Session": "a" * 64}
    original = client.post("/evaluations/import", headers=headers, json=parent([row()])).json()
    made = client.post(f"/evaluations/{original['id']}/improvements", headers=headers,
                       json={"model_name": "own-model"})
    assert made.status_code == 201, made.text
    ident = made.json()["id"]
    assert client.post(f"/evaluations/{original['id']}/improvements", headers=foreign_headers,
                       json={"model_name": "own-model"}).status_code == status
    assert client.get(f"/improvements/{ident}", headers=foreign_headers).status_code == status
    assert client.post(f"/improvements/{ident}/verify", headers=foreign_headers).status_code == status
    local = evaluation_records.import_record(root, parent([row()]))
    assert client.post(f"/evaluations/{local.id}/improvements", headers=headers,
                       json={"model_name": "own-model"}).status_code == 404
    assert client.get(f"/evaluations/{local.id}", headers=headers).status_code == 404


def test_byok_improvement_budget_rejects_sidecar_without_altering_parent(visitor_setup, monkeypatch):
    from backend.api import byok
    root, client = visitor_setup
    headers = {"X-VeriScope-Session": "a" * 64}
    original = client.post("/evaluations/import", headers=headers, json=parent([row()])).json()
    session_root = root / "visitors" / hashlib.sha256(b"a" * 64).hexdigest()
    original_path = evaluation_records.record_path(session_root, original["id"])
    before = original_path.read_bytes()
    monkeypatch.setattr(byok, "MAX_SESSION_BYTES", len(before))
    response = client.post(f"/evaluations/{original['id']}/improvements", headers=headers,
                           json={"model_name": "own-model"})
    assert response.status_code == 413, response.text
    assert original_path.read_bytes() == before
    assert not (session_root / "work/improvements").exists()
