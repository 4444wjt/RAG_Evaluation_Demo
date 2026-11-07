"""Local media and source-location companions preserve bytes and access scope."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

from fastapi.testclient import TestClient
import pytest

from backend.api.app import create_app
from backend.api import evaluation_records
from backend.recognition.textmap import build_text
from test_recognition import asr_result, ocr_result


_LOOPBACK_CONNECT = socket.socket.connect
IDENT = "0" * 32


def client_for(app):
    def new_loop():
        guarded = socket.socket.connect
        socket.socket.connect = _LOOPBACK_CONNECT
        try:
            return asyncio.new_event_loop()
        finally:
            socket.socket.connect = guarded
    return TestClient(app, backend_options={"loop_factory": new_loop})


def recognition_files(root, kind="ocr", *, ident=IDENT):
    folder = root / "work" / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / f"{ident}.{'png' if kind == 'ocr' else 'wav'}"
    source.write_bytes(b"original image" if kind == "ocr" else b"original audio")
    result = (ocr_result if kind == "ocr" else asr_result)(hashlib.sha256(source.read_bytes()).hexdigest())
    text, spans = build_text(result)
    text_path = folder / f"{ident}.txt"
    text_path.write_bytes(text.encode("utf-8"))
    sidecar = {
        "schema": "veriscope.recognition-sidecar.v1", "created_at": "2026-11-04T00:00:00+00:00",
        "source": {"relative_path": f"work/uploads/{source.name}",
                   "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        "text": {"relative_path": f"work/uploads/{text_path.name}",
                 "sha256": hashlib.sha256(text_path.read_bytes()).hexdigest()},
        "elapsed_ms": 12.0, "spans": spans, "recognition": result,
    }
    path = folder / f"{ident}.recognition.json"
    path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    return sidecar, f"work/uploads/{path.name}"


def save_parent(root, text_path, evidence="不得"):
    result = {
        "summary": [], "summary_df": [], "gate_passed": False, "gate_message": "原始 FAIL 保留",
        "chart_path": None, "html_path": None, "total_elapsed_seconds": None,
        "model_dfs": {"own-model": [
            {"hallucination_trace": json.dumps({"statements": [
                {"text": "原陈述", "verdict": "supported", "evidence": evidence},
                {"text": "无匹配", "verdict": "fact_error", "evidence": "不存在"},
                {"text": "无引用", "verdict": "fabrication", "evidence": ""},
            ]}, ensure_ascii=False)},
            {"hallucination_trace": None}, {"hallucination_trace": "invalid json"},
            {"hallucination_trace": {"statements": []}},
        ]},
    }
    return evaluation_records.import_record(root, {
        "schema_version": 1, "id": "f" * 32, "created_at": "2026-11-04T00:00:00+00:00",
        "source": "evaluation", "request": {"doc_path": text_path}, "result": result,
        "report_html": None, "chart_png_base64": None, "artifact_warnings": [],
    })


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("VERISCOPE_BYOK", raising=False)
    monkeypatch.delenv("VERISCOPE_READ_ONLY", raising=False)
    sidecar, recognition_path = recognition_files(tmp_path)
    parent = save_parent(tmp_path, sidecar["text"]["relative_path"])
    return tmp_path, client_for(create_app(project_root=tmp_path)), sidecar, recognition_path, parent


def test_read_sidecar_and_original_media_return_bound_original_bytes(setup):
    root, client, sidecar, recognition_path, _parent = setup
    response = client.get("/recognitions/sidecar", params={"relative_path": recognition_path})
    assert response.status_code == 200, response.text
    assert response.json() == {"sidecar": sidecar, "text": "金额12000元\n不得转让"}
    assert response.headers["cache-control"] == "no-store"
    media = client.get("/inputs/media", params={"relative_path": sidecar["source"]["relative_path"]})
    assert media.status_code == 200 and media.content == b"original image"
    assert media.headers["content-type"] == "image/png"
    assert media.headers["x-content-sha256"] == sidecar["source"]["sha256"]
    assert media.headers["cache-control"] == "no-store"
    assert media.headers["x-content-type-options"] == "nosniff"
    assert list((root / "work" / "uploads").iterdir())


def test_source_location_companion_reopens_without_modifying_parent(setup):
    root, client, sidecar, recognition_path, parent = setup
    original = evaluation_records.record_path(root, parent.id)
    before = original.read_bytes()
    endpoint = f"/evaluations/{parent.id}/source-locations"
    made = client.post(endpoint, json={"recognition_path": recognition_path})
    assert made.status_code == 201, made.text
    record = made.json()
    assert record["schema"] == "veriscope.source-locations.v1"
    assert record["parent_record_id"] == parent.id
    assert record["parent_record_sha256"] == hashlib.sha256(before).hexdigest()
    assert record["source"] == sidecar["source"] and record["text"] == sidecar["text"]
    rows = record["models"]["own-model"]
    assert [item["row_index"] for item in rows] == [0, 1, 2, 3]
    assert [item["trace_status"] for item in rows] == ["available", "missing", "invalid", "available"]
    assert rows[1]["statements"] is None and rows[2]["statements"] is None
    assert rows[3]["statements"] == []
    statements = rows[0]["statements"]
    assert [item["source_locations"]["status"] for item in statements] == ["located", "not_found", "empty_quote"]
    assert statements[0]["text"] == "原陈述" and statements[0]["verdict"] == "supported"
    match = statements[0]["source_locations"]["matches"][0]
    assert (match["start"], match["end"], match["min_conf"], match["conf_missing"]) == (9, 11, 0.3, False)
    assert match["refs"][0]["line_id"] == "p1-l1"
    reopened = client_for(create_app(project_root=root)).get(endpoint)
    assert reopened.status_code == 200 and reopened.json() == record
    saved = root / "work" / "source-locations" / f"{parent.id}.json"
    assert reopened.content == saved.read_bytes()
    assert reopened.headers["x-content-sha256"] == hashlib.sha256(reopened.content).hexdigest()
    assert original.read_bytes() == before
    assert parent.result.gate_passed is False


def test_asr_locations_keep_native_null_confidence_and_segment_times(setup):
    root, client, _sidecar, _path, _parent = setup
    sidecar, path = recognition_files(root, "asr", ident="1" * 32)
    parent = save_parent(root, sidecar["text"]["relative_path"], "未经批准")
    made = client.post(f"/evaluations/{parent.id}/source-locations", json={"recognition_path": path})
    assert made.status_code == 201, made.text
    match = made.json()["models"]["own-model"][0]["statements"][0]["source_locations"]["matches"][0]
    assert match["min_conf"] is None and match["conf_missing"] is True
    assert match["refs"] == [{"segment_id": "s1", "start_ms": 2500, "end_ms": 4900, "conf": None}]
    audio = client.get("/inputs/media", params={"relative_path": sidecar["source"]["relative_path"]})
    assert audio.status_code == 200 and audio.content == b"original audio"
    assert audio.headers["content-type"] == "audio/wav"


@pytest.mark.parametrize("target", ["source", "text", "sidecar", "parent", "locations"])
def test_reopening_companion_rejects_changed_bindings(setup, target):
    root, client, sidecar, path, parent = setup
    endpoint = f"/evaluations/{parent.id}/source-locations"
    assert client.post(endpoint, json={"recognition_path": path}).status_code == 201
    if target in ("source", "text"):
        changed = root / sidecar[target]["relative_path"]
        changed.write_bytes(changed.read_bytes() + b"changed")
    elif target == "sidecar":
        changed = root / path
        changed.write_bytes(changed.read_bytes() + b"\n")
    elif target == "parent":
        changed = evaluation_records.record_path(root, parent.id)
        changed.write_bytes(changed.read_bytes() + b"\n")
    else:
        changed = root / "work" / "source-locations" / f"{parent.id}.json"
        data = json.loads(changed.read_bytes())
        data["models"]["own-model"][0]["statements"][0]["source_locations"]["matches"][0]["refs"][0]["line_id"] = "false-line"
        changed.write_text(json.dumps(data), encoding="utf-8")
    response = client.get(endpoint)
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("target", ["source", "text", "mapping"])
def test_sidecar_read_rejects_changed_source_text_or_mapping(setup, target):
    root, client, sidecar, path, _parent = setup
    if target == "mapping":
        sidecar["spans"][0]["start"] = 4
        (root / path).write_text(json.dumps(sidecar), encoding="utf-8")
    else:
        (root / sidecar[target]["relative_path"]).write_bytes(b"changed")
    response = client.get("/recognitions/sidecar", params={"relative_path": path})
    assert response.status_code == 409, response.text


def test_companion_requires_exact_parent_document_and_strict_body(setup):
    root, client, _sidecar, path, parent = setup
    other = save_parent(root, "demo.txt")
    assert client.post(f"/evaluations/{other.id}/source-locations", json={"recognition_path": path}).status_code == 422
    endpoint = f"/evaluations/{parent.id}/source-locations"
    assert client.post(endpoint, json={"recognition_path": path, "ignored": True}).status_code == 422
    assert client.post(endpoint, content=b"not json").status_code == 422
    assert client.get(endpoint).status_code == 404
    assert not (root / "work" / "source-locations").exists()


@pytest.mark.parametrize("raw", [
    "../secret.png", "work/uploads/../secret.png", "work/uploads/" + "0" * 31 + ".png",
    "work/uploads/" + "A" * 32 + ".png", "work/uploads/" + IDENT + ".txt",
    "work/uploads/" + IDENT + ".png/extra", "C:/secret.png",
])
def test_media_only_accepts_exact_uploaded_media_names(setup, raw):
    _root, client, _sidecar, _path, _parent = setup
    assert client.get("/inputs/media", params={"relative_path": raw}).status_code == 422


def test_media_rejects_a_file_reported_as_a_symbolic_link(setup, monkeypatch):
    root, client, sidecar, _path, _parent = setup
    source = root / sidecar["source"]["relative_path"]
    original = Path.is_symlink
    # Windows file symlinks require privileges unavailable to this process.
    # Exercise the actual HTTP refusal when the filesystem reports a link.
    monkeypatch.setattr(Path, "is_symlink", lambda value: value == source or original(value))
    response = client.get("/inputs/media", params={"relative_path": sidecar["source"]["relative_path"]})
    assert response.status_code == 403


def test_media_rejects_an_actual_upload_directory_link(setup):
    root, client, sidecar, _path, _parent = setup
    source = root / sidecar["source"]["relative_path"]
    outside = root / "elsewhere"
    outside.mkdir()
    (outside / source.name).write_bytes(b"private bytes")
    linked_root = root / "linked-root"
    (linked_root / "work").mkdir(parents=True)
    upload_link = linked_root / "work" / "uploads"
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(upload_link), str(outside)],
                              capture_output=True, check=False)
        assert made.returncode == 0, "无法建立测试用目录 junction"
    else:
        upload_link.symlink_to(outside, target_is_directory=True)
    linked_client = client_for(create_app(project_root=linked_root))
    response = linked_client.get("/inputs/media", params={"relative_path": sidecar["source"]["relative_path"]})
    assert response.status_code == 403
    assert b"private bytes" not in response.content


@pytest.mark.parametrize("mode,status", [("VERISCOPE_READ_ONLY", 423), ("VERISCOPE_BYOK", 403)])
def test_all_recognition_read_and_companion_routes_keep_local_access_boundary(setup, monkeypatch, mode, status):
    root, _client, sidecar, path, parent = setup
    monkeypatch.setenv(mode, "1")
    monkeypatch.setenv("VERISCOPE_BYOK_DATA_DIR", str(root / "visitors"))
    client = client_for(create_app(project_root=root))
    requests = [
        client.get("/inputs/media", params={"relative_path": sidecar["source"]["relative_path"]}),
        client.get("/recognitions/sidecar", params={"relative_path": path}),
        client.post(f"/evaluations/{parent.id}/source-locations", json={"recognition_path": path}),
        client.get(f"/evaluations/{parent.id}/source-locations"),
    ]
    assert [response.status_code for response in requests] == [status] * 4
    assert not (root / "work" / "source-locations").exists()
