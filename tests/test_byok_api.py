"""Visitor isolation and credential handling, with no provider calls."""
import asyncio
import json
import os
import io
import zipfile
from pathlib import Path
from threading import Event
import time

from fastapi.testclient import TestClient as HTTPTestClient
import pytest

from backend.api.app import create_app

SESSION_A = "a" * 64
SESSION_B = "b" * 64
SECRET = "visitor-secret-for-offline-test"
_LOOPS = []


@pytest.fixture(scope="session", autouse=True)
def _windows_test_loops():
    # Windows creates asyncio wakeup sockets. Prepare them before conftest's
    # per-test external-network guard; all application requests remain guarded.
    _LOOPS.extend(asyncio.new_event_loop() for _ in range(40))
    yield
    for loop in _LOOPS:
        loop.close()


def TestClient(app):
    loop = _LOOPS.pop()
    return HTTPTestClient(app, backend_options={"loop_factory": lambda: loop})


def result():
    return {"summary": [{"model": "visitor"}], "summary_df": [],
            "gate_passed": False, "gate_message": "FAIL", "chart_path": None,
            "html_path": None, "model_dfs": {"visitor": [{"answer": "result"}]},
            "total_elapsed_seconds": 1.0}


@pytest.fixture
def visitors(tmp_path, monkeypatch):
    monkeypatch.setenv("VERISCOPE_BYOK", "1")
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "0")
    monkeypatch.setenv("VERISCOPE_BYOK_DATA_DIR", str(tmp_path / "visitors"))
    monkeypatch.setenv("OPENAI_API_KEY", "AUTHOR-KEY-MUST-NEVER-BE-USED")
    (tmp_path / "demo.txt").write_text("demo source", encoding="utf-8")
    return tmp_path


def headers(session=SESSION_A):
    return {"X-VeriScope-Session": session}


def upload(client, content=b"source", filename="source.txt", kind="document", session=SESSION_A):
    response = client.post("/inputs", params={"kind": kind, "filename": filename},
                           headers={**headers(session), "Content-Type": "application/octet-stream"},
                           content=content)
    assert response.status_code == 200, response.text
    return response.json()["relative_path"]


def submission(path, **overrides):
    return {"doc_path": path, "model_names": ["visitor"],
            "byok_models": [{"name": "visitor", "base_url": "https://api.deepseek.com/v1", "api_key": SECRET}],
            **overrides}


def completed(client, job_id, session=SESSION_A):
    for _ in range(200):
        response = client.get(f"/jobs/{job_id}", headers=headers(session))
        assert response.status_code == 200, response.text
        if response.json()["status"] != "running":
            return response.json()
        time.sleep(.01)
    pytest.fail("offline job did not finish")


def test_byok_upload_job_history_and_download_are_session_scoped(visitors):
    captured = {}

    def evaluator(request, **kwargs):
        captured.update(request=request, kwargs=kwargs)
        assert "byok_models" not in request
        assert Path(request["doc_path"]).read_bytes() == b"source"
        assert callable(kwargs["model_factory"])
        return result()

    with TestClient(create_app(evaluator, project_root=visitors)) as client:
        path = upload(client)
        denied = client.post("/evaluate", headers=headers(SESSION_B), json=submission(path))
        assert denied.status_code == 404
        response = client.post("/evaluate", headers=headers(), json=submission(path))
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        assert client.get(f"/jobs/{job_id}", headers=headers(SESSION_B)).status_code == 404
        job = completed(client, job_id)
        assert job["status"] == "completed", job
        record_id = job["result"]["record_id"]
        assert record_id
        assert len(client.get("/evaluations", headers=headers()).json()["records"]) == 1
        assert client.get("/evaluations", headers=headers(SESSION_B)).json()["records"] == []
        assert client.get(f"/evaluations/{record_id}", headers=headers(SESSION_B)).status_code == 404
        exported = client.get(f"/evaluations/{record_id}/download", headers=headers())
        assert exported.status_code == 200
        assert exported.json()["request"]["model_names"] == ["visitor"]
        assert SECRET not in exported.text
        assert str(visitors) not in exported.text
        assert "AUTHOR-KEY" not in exported.text
        assert client.get("/models", headers=headers()).json()["models"] == []
        assert client.put("/models/local", headers=headers(), json={"models": []}).status_code == 403
    assert captured["kwargs"]["runtime"]["model_configs"] == [{"name": "visitor", "base_url": "https://api.deepseek.com/v1"}]


@pytest.mark.parametrize("mutate", [
    lambda body: body.pop("byok_models"),
    lambda body: body["byok_models"][0].update(api_key=""),
    lambda body: body["byok_models"][0].update(base_url="http://127.0.0.1:8000/v1"),
    lambda body: body["byok_models"][0].update(base_url="https://api.deepseek.com.evil.example/v1"),
    lambda body: body["byok_models"][0].update(base_url="https://api.deepseek.com/v1?secret=x"),
    lambda body: body.update(benchmark="truthful_qa"),
    lambda body: body.update(model_names=["unknown"]),
    lambda body: body.update(judge_model_name="unknown"),
    lambda body: body.update(doc_path="../private.txt"),
])
def test_invalid_submissions_do_not_call_evaluator_or_echo_secret(visitors, mutate):
    called = []
    with TestClient(create_app(lambda *a, **kw: called.append(True), project_root=visitors)) as client:
        body = submission(upload(client))
        mutate(body)
        response = client.post("/evaluate", headers=headers(), json=body)
        assert response.status_code in (403, 422), response.text
        assert SECRET not in response.text
        assert not called


def test_invalid_testset_fails_before_model_call(visitors):
    called = []
    with TestClient(create_app(lambda *a, **kw: called.append(True), project_root=visitors)) as client:
        path = upload(client)
        cases = upload(client, b'[{"ground_truth":"answer"}]', "cases.json", "testset")
        response = client.post("/evaluate", headers=headers(), json=submission(path, testset_path=cases))
        assert response.status_code == 422
        assert not called


def test_job_error_does_not_echo_provider_exception_or_key(visitors):
    def fail(*args, **kwargs):
        raise RuntimeError("upstream echoed " + SECRET)
    with TestClient(create_app(fail, project_root=visitors)) as client:
        response = client.post("/evaluate", headers=headers(), json=submission(upload(client)))
        assert response.status_code == 202
        job = completed(client, response.json()["job_id"])
        assert job["status"] == "failed"
        assert SECRET not in json.dumps(job)
        assert client.get("/evaluations", headers=headers()).json()["records"] == []


def test_single_running_job_returns_busy_and_releases_slot(visitors):
    started, release = Event(), Event()
    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return result()
    with TestClient(create_app(delayed, project_root=visitors)) as client:
        path = upload(client)
        response = client.post("/evaluate", headers=headers(), json=submission(path))
        assert response.status_code == 202
        assert started.wait(5)
        try:
            busy = client.post("/evaluate", headers=headers(), json=submission(path))
            assert busy.status_code == 409
        finally:
            release.set()
        assert completed(client, response.json()["job_id"])["status"] == "completed"
        again = client.post("/evaluate", headers=headers(), json=submission(path))
        assert again.status_code == 202
        completed(client, again.json()["job_id"])


def test_missing_or_invalid_session_never_creates_data(visitors):
    with TestClient(create_app(project_root=visitors)) as client:
        for session in (None, "", "../bad", "a" * 63):
            response = client.get("/evaluations", headers={} if session is None else headers(session))
            assert response.status_code == 422
        assert not (visitors / "visitors").exists()


def test_readonly_still_wins_over_byok(visitors, monkeypatch):
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")
    with TestClient(create_app(project_root=visitors)) as client:
        assert client.post("/evaluate", json=submission("demo.txt"), headers=headers()).status_code == 423
        assert client.post("/inputs", params={"kind":"document","filename":"x.txt"}, content=b"x", headers=headers()).status_code == 423


def test_idle_cleanup_only_removes_expired_hex_sessions_and_excludes_active(visitors):
    from backend.api.byok import VisitorWorkspaces
    from starlette.requests import Request
    spaces = VisitorWorkspaces(visitors)
    def request(token):
        return Request({"type": "http", "headers": [(b"x-veriscope-session", token.encode())]})
    expired = spaces.scope(request(SESSION_A))
    (expired / "old.txt").write_text("old")
    active = spaces.scope(request(SESSION_B))
    spaces.jobs[(active.name, "job")] = {"status": "running"}
    unrelated = spaces.data_root / "keep-unrelated"
    unrelated.mkdir()
    for directory in (expired, active, unrelated):
        os.utime(directory, (1, 1))
    spaces.scope(request("c" * 64))
    assert not expired.exists()
    assert active.exists()
    assert unrelated.exists()


def test_report_and_chart_download_are_useful_without_absolute_paths_or_keys(visitors):
    def evaluator(request, **kwargs):
        folder = Path(kwargs["runtime"]["output_dir"])
        html = folder / "report.html"
        html.write_text("<html>result " + SECRET + "</html>", encoding="utf-8")
        chart = folder / "chart.png"
        chart.write_bytes(b"offline-png-fixture")
        return {**result(), "html_path": str(html), "chart_path": str(chart)}
    with TestClient(create_app(evaluator, project_root=visitors)) as client:
        response = client.post("/evaluate", headers=headers(), json=submission(upload(client)))
        job = completed(client, response.json()["job_id"])
        assert job["status"] == "completed", job
        assert not Path(job["result"]["html_path"]).is_absolute()
        ident = job["result"]["record_id"]
        report = client.get(f"/evaluations/{ident}/report", headers=headers())
        assert report.status_code == 200
        assert "result" in report.text and SECRET not in report.text
        record = client.get(f"/evaluations/{ident}/download", headers=headers()).json()
        assert record["chart_png_base64"]
        assert record["report_html"] == report.text
        assert client.get(f"/evaluations/{ident}/report", headers=headers(SESSION_B)).status_code == 404


@pytest.mark.parametrize("mode", ["scan", "recheck", "export-scan", "export-recheck"])
def test_byok_knowledge_upload_processing_is_available_but_collectors_stay_private(visitors, monkeypatch, mode):
    monkeypatch.setenv("VERISCOPE_KNOWLEDGE_UPDATE", "1")
    example = Path(__file__).resolve().parents[1] / "examples" / "knowledge_update"
    body = {"task": (example / "task.json").read_text(encoding="utf-8"),
            "files": {name: (example / name).read_text(encoding="utf-8")
                      for name in ("sources/old.txt", "sources/new.txt")}}
    if mode.endswith("recheck"):
        body["revisions"] = (example / "revisions.json").read_text(encoding="utf-8")
    operation = mode
    if mode.startswith("export-"):
        operation = "export"
        body["mode"] = mode.removeprefix("export-")
    with TestClient(create_app(project_root=visitors)) as client:
        response = client.post("/knowledge-update/" + operation, headers=headers(), json=body)
        assert response.status_code == 200, response.text
        if operation == "export":
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert "results/report.json" in archive.namelist()
        else:
            assert isinstance(response.json(), dict)
        assert client.get("/knowledge-update/sources", headers=headers()).status_code == 200
        for private in ("sources/example/versions", "sources/example/versions/100"):
            assert client.get("/knowledge-update/" + private, headers=headers()).status_code == 404


def test_byok_manual_catalogue_ignores_private_server_configuration(visitors, monkeypatch):
    from backend.core import knowledge_sources
    monkeypatch.setenv("VERISCOPE_KNOWLEDGE_UPDATE", "1")
    private = visitors / "private-sources.json"
    private.write_text(json.dumps({"schema_version": "knowledge-sources/v1", "sources": [
        {"source_id": "private-source", "title": "Private title", "url": "http://private.invalid/doc",
         "watch_uuid": "private-watch", "extraction": {"fetch_backend": "private", "include_filters": []}}]}))
    monkeypatch.setenv("VERISCOPE_KNOWLEDGE_SOURCES", str(private))
    monkeypatch.setenv("VERISCOPE_CHANGEDETECTION_URL", "http://private.invalid")
    monkeypatch.setenv("VERISCOPE_CHANGEDETECTION_API_KEY", "private-key")
    monkeypatch.setattr(knowledge_sources.KnowledgeSources, "__init__",
                        lambda self: pytest.fail("BYOK must not initialize the server collector"))
    with TestClient(create_app(project_root=visitors)) as client:
        response = client.get("/knowledge-update/sources", headers=headers())
        assert response.status_code == 200, response.text
        rows = response.json()["sources"]
        assert {row["source_id"] for row in rows} == {"nodejs-building", "curl-install"}
        assert all(row["collector_ready"] is False and "watch_uuid" not in row for row in rows)
        assert "private" not in response.text
        for row in rows:
            assert client.get(f"/knowledge-update/sources/{row['source_id']}/versions", headers=headers()).status_code == 404


def test_upload_rechecks_quota_after_concurrent_background_record_and_keeps_existing_files(visitors, monkeypatch):
    from backend.api import byok, evaluation_records
    from backend.api.service import EvaluationAPIError
    from starlette.requests import Request
    unit = 1024
    monkeypatch.setattr(byok, "MAX_SESSION_BYTES", 100 * unit)
    monkeypatch.setattr(byok, "MAX_INPUT_BYTES", 10 * unit)
    spaces = byok.VisitorWorkspaces(visitors)

    async def scenario():
        reading, release = asyncio.Event(), asyncio.Event()
        async def receive():
            reading.set()
            await release.wait()
            return {"type": "http.request", "body": b"x" * (10 * unit), "more_body": False}
        request = Request({"type": "http", "headers": [
            (b"x-veriscope-session", SESSION_A.encode()), (b"content-type", b"application/octet-stream")]}, receive)
        root = spaces.scope(request)
        prior = root / "work" / "uploads" / "prior.txt"
        prior.parent.mkdir(parents=True)
        prior.write_bytes(b"p" * (90 * unit))
        pending = asyncio.create_task(spaces.upload(request, kind="document", filename="new.txt"))
        await reading.wait()  # The reservation passed and the upload is now suspended.
        job_id = spaces.submit(root, submission("demo.txt"), evaluator=lambda *args, **kwargs:
                               {**result(), "gate_message": "x" * (5 * unit)})["job_id"]
        deadline = time.monotonic() + 5
        while spaces.jobs[(root.name, job_id)]["status"] == "running" and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        try:
            assert spaces.jobs[(root.name, job_id)]["status"] == "completed"
            records = evaluation_records.list_records(root)["records"]
            assert len(records) == 1
            release.set()
            with pytest.raises(EvaluationAPIError) as caught:
                await pending
            assert caught.value.status_code == 413
            assert prior.read_bytes() == b"p" * (90 * unit)
            assert list(prior.parent.iterdir()) == [prior]
            assert len(evaluation_records.list_records(root)["records"]) == 1
            assert sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) <= 100 * unit
        finally:
            release.set()
            if not pending.done():
                await pending
    loop = _LOOPS.pop()
    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()


def test_import_checks_indented_record_bytes_before_any_persistence(visitors, monkeypatch):
    from backend.api import byok
    monkeypatch.setattr(byok, "MAX_SESSION_BYTES", 3000)
    nested = [0] * 200
    for _ in range(30):
        nested = {"detail": nested}
    body = {**result(), "summary": [{"detail": nested}]}
    assert len(json.dumps(body).encode()) < 3000
    with TestClient(create_app(project_root=visitors)) as client:
        response = client.post("/evaluations/import", headers=headers(), json=body)
        assert response.status_code == 413, response.text
        assert "100 MiB" in response.json()["detail"]
        assert client.get("/evaluations", headers=headers()).json()["records"] == []
    assert not list((visitors / "visitors").rglob("*.json"))
    assert not list((visitors / "visitors").rglob("*.tmp"))


@pytest.mark.parametrize("artifact_bytes,engine_failure", [(100, False), (4000, False), (4000, True)])
def test_generated_and_captured_record_overquota_fail_and_clean_only_this_run(visitors, monkeypatch, artifact_bytes, engine_failure):
    from backend.api import byok
    monkeypatch.setattr(byok, "MAX_SESSION_BYTES", 3000)
    def evaluator(request, **kwargs):
        folder = Path(kwargs["runtime"]["output_dir"])
        (folder / "artifact.bin").write_bytes(b"x" * artifact_bytes)
        if engine_failure:
            raise RuntimeError("automatic generation failed")
        # Small generated files can still produce an oversized durable record.
        return {**result(), "gate_message": "x" * 4000}
    with TestClient(create_app(evaluator, project_root=visitors)) as client:
        prior = client.post("/evaluations/import", headers=headers(), json=result())
        assert prior.status_code == 201
        response = client.post("/evaluate", headers=headers(), json=submission("demo.txt"))
        assert response.status_code == 202
        job = completed(client, response.json()["job_id"])
        assert job["status"] == "failed", job
        assert "100 MiB" in job["error"]
        records = client.get("/evaluations", headers=headers()).json()["records"]
        assert [record["id"] for record in records] == [prior.json()["id"]]
        assert not list((visitors / "visitors").rglob("artifact.bin"))
