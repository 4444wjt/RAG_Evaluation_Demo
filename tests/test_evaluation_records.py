"""History must survive a new app and never serve another run's report.

The paid evaluator alone is replaced; HTTP, serialization and disk are real.
"""

import base64
import asyncio
from copy import deepcopy
import json
import socket
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from backend.api.app import create_app


_LOOPBACK_CONNECT = socket.socket.connect


def client_for(app):
    # Windows asyncio creates its loop with a local socketpair. Requests stay
    # under the existing test network guard, as in test_model_registry.py.
    def new_loop():
        guarded = socket.socket.connect
        socket.socket.connect = _LOOPBACK_CONNECT
        try:
            return asyncio.new_event_loop()
        finally:
            socket.socket.connect = guarded
    return TestClient(app, backend_options={"loop_factory": new_loop})


def result():
    rows = [{"model": "judge", "pos_faithfulness": 0.7375,
             "pos_context_precision": None}]
    return {
        "summary": rows, "summary_df": deepcopy(rows), "gate_passed": False,
        "gate_message": "低于门槛", "chart_path": "result_chart.png",
        "html_path": "ragas_report.html", "total_elapsed_seconds": 5.25,
        "faith_threshold": 0.8, "ctx_prec_threshold": 0.8,
        "model_dfs": {"judge": [{"question": "问题一", "answer": "原始回答",
            "contexts": ["原始证据"], "faithfulness": 0.7375,
            "hallucination_trace": '{"statements": [{"verdict": "unsupported"}]}'}]},
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("VERISCOPE_READ_ONLY", raising=False)
    (tmp_path / "demo.txt").write_text("测试资料", encoding="utf-8")
    count = 0

    def engine(*_args, **_kwargs):
        nonlocal count
        count += 1
        (tmp_path / "ragas_report.html").write_text(f"<h1>报告 {count}</h1>", encoding="utf-8")
        (tmp_path / "result_chart.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([count]))
        value = result()
        value["model_dfs"]["judge"][0]["answer"] = f"回答 {count}"
        return value

    fake = Mock(side_effect=engine)
    return tmp_path, fake, client_for(create_app(fake, project_root=tmp_path))


def test_two_runs_keep_full_results_and_reports_after_app_restart(setup):
    root, fake, client = setup
    first = client.post("/evaluate", json={}).json()
    second = client.post("/evaluate", json={"faith_threshold": 0.8}).json()
    assert first["record_id"] != second["record_id"]
    restarted = client_for(create_app(fake, project_root=root))
    listing = restarted.get("/evaluations").json()
    assert [r["id"] for r in listing["records"]] == [second["record_id"], first["record_id"]]
    record = restarted.get(f"/evaluations/{first['record_id']}").json()
    assert record["result"]["model_dfs"]["judge"][0]["answer"] == "回答 1"
    assert record["result"]["gate_passed"] is False
    assert record["result"]["summary"][0]["pos_context_precision"] is None
    assert record["result"]["faith_threshold"] == 0.8
    assert record["request"]["doc_path"] is None
    assert record["report_html"] == "<h1>报告 1</h1>"
    assert base64.b64decode(record["chart_png_base64"])[-1] == 1
    assert restarted.get(f"/evaluations/{first['record_id']}/report").text == "<h1>报告 1</h1>"
    assert fake.call_count == 2


def test_download_import_roundtrip_and_repeated_import_never_overwrites(setup):
    root, fake, client = setup
    run = client.post("/evaluate", json={}).json()
    saved = client.get(f"/evaluations/{run['record_id']}/download")
    assert saved.status_code == 200
    assert f"{run['record_id']}.json" in saved.headers["content-disposition"]
    before = (root / "work/evaluations" / f"{run['record_id']}.json").read_bytes()
    imported = client.post("/evaluations/import", json=saved.json())
    assert imported.status_code == 201
    record = imported.json()
    assert record["id"] != run["record_id"]
    assert record["source"] == "imported"
    assert record["result"] == saved.json()["result"]
    assert record["report_html"] == saved.json()["report_html"]
    assert (root / "work/evaluations" / f"{run['record_id']}.json").read_bytes() == before
    assert len(client.get("/evaluations").json()["records"]) == 2
    assert fake.call_count == 1


def test_import_legacy_json_never_opens_paths_or_claims_report_binding(setup):
    root, fake, client = setup
    secret = root / "not-a-report.txt"
    secret.write_text("must not be read", encoding="utf-8")
    data = result()
    data.update(html_path=str(secret), chart_path=str(secret))
    response = client.post("/evaluations/import", json=data)
    assert response.status_code == 201
    record = response.json()
    assert record["report_html"] is None
    assert record["chart_png_base64"] is None
    assert record["artifact_warnings"]
    assert "must not be read" not in response.text
    assert record["result"]["model_dfs"] == data["model_dfs"]
    assert client.get(f"/evaluations/{record['id']}/report").status_code == 404
    fake.assert_not_called()


def test_save_failure_preserves_result_without_repeating_paid_evaluation(setup):
    root, fake, client = setup
    (root / "work").mkdir()
    (root / "work/evaluations").write_text("blocks directory creation", encoding="utf-8")
    response = client.post("/evaluate", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["record_id"] is None
    assert data["save_error"]
    assert data["model_dfs"]["judge"][0]["answer"] == "回答 1"
    assert data["gate_passed"] is False
    fake.assert_called_once()
    assert client.get("/evaluations").status_code == 500


def test_browser_backup_after_save_failure_can_be_imported_without_rerun(setup):
    root, fake, client = setup
    (root / "work").mkdir()
    blocked_store = root / "work/evaluations"
    blocked_store.write_text("blocks directory creation", encoding="utf-8")
    response = client.post("/evaluate", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["record_id"] is None and data["save_error"]
    # This is the browser's fallback export, including the server save status.
    backup = {
        "schema_version": 1, "id": "b" * 32,
        "created_at": "2026-11-02T12:00:00Z", "source": "evaluation",
        "request": None, "result": data, "report_html": None,
        "chart_png_base64": None,
        "artifact_warnings": ["浏览器结果备份未包含报告与图表文件。"],
    }
    blocked_store.unlink()
    imported = client.post("/evaluations/import", json=backup)
    assert imported.status_code == 201
    record = imported.json()
    expected = {key: value for key, value in data.items()
                if key not in {"record_id", "saved_at", "save_error"}}
    assert record["result"] == expected
    assert record["artifact_warnings"] == backup["artifact_warnings"]
    assert record["report_html"] is None
    assert record["source"] == "imported"
    assert record["id"] != backup["id"]
    fake.assert_called_once()


def test_missing_artifacts_save_results_with_notice(setup):
    root, _, _ = setup
    client = client_for(create_app(lambda *_a, **_k: result(), project_root=root))
    data = client.post("/evaluate", json={}).json()
    assert data["record_id"]
    assert data["save_error"]
    saved = client.get(f"/evaluations/{data['record_id']}").json()
    assert saved["result"]["gate_passed"] is False
    assert saved["report_html"] is None
    assert len(saved["artifact_warnings"]) == 2


def test_corrupt_record_is_reported_without_hiding_good_history(setup):
    root, _, client = setup
    run = client.post("/evaluate", json={}).json()
    corrupt = "a" * 32
    (root / "work/evaluations" / f"{corrupt}.json").write_text("broken", encoding="utf-8")
    listing = client.get("/evaluations").json()
    assert [r["id"] for r in listing["records"]] == [run["record_id"]]
    assert listing["unreadable_count"] == 1
    assert client.get(f"/evaluations/{corrupt}").status_code == 500


@pytest.mark.parametrize("suffix", ["", "/report", "/download"])
def test_bad_or_missing_id_cannot_read_arbitrary_files(setup, suffix):
    _, _, client = setup
    for ident in ["not-an-id", "a" * 32, "%2E%2E%5Cdemo.txt"]:
        assert client.get(f"/evaluations/{ident}{suffix}").status_code == 404


@pytest.mark.parametrize("path,method", [
    ("/evaluations", "get"), ("/evaluations/import", "post"),
    (f"/evaluations/{'a' * 32}", "get"),
    (f"/evaluations/{'a' * 32}/download", "get"),
    (f"/evaluations/{'a' * 32}/report", "get"),
])
def test_public_readonly_site_never_exposes_local_history(setup, monkeypatch, path, method):
    root, _, client = setup
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")
    assert getattr(client, method)(path).status_code == 423
    assert not (root / "work/evaluations").exists()


@pytest.mark.parametrize("body", [None, [], {"schema_version": 999}, {"summary": []}])
def test_invalid_import_never_creates_history(setup, body):
    root, fake, client = setup
    assert client.post("/evaluations/import", json=body).status_code == 422
    assert not (root / "work/evaluations").exists()
    fake.assert_not_called()


def test_saved_html_is_inert_even_if_opened_directly(setup):
    _, _, client = setup
    run = client.post("/evaluate", json={}).json()
    response = client.get(f"/evaluations/{run['record_id']}/report")
    assert "sandbox" in response.headers["content-security-policy"]
