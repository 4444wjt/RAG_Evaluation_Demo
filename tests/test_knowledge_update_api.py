"""HTTP contracts, portable ZIP inputs, and real CLI replay of HTTP exports."""
import asyncio
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.api.app import create_app
from backend.api import knowledge_update as api
from backend.core import knowledge_sources
from backend.core import knowledge_update as core

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/knowledge_update"
PREFIX = "/knowledge-update"
OPERATIONS = ["scan", "recheck", "export-scan", "export-recheck"]
# Windows needs an internal loopback socketpair before the existing test guard.
_LOOP = asyncio.new_event_loop()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def body(mode="scan"):
    value = {"task": (EXAMPLE / "task.json").read_bytes().decode("utf-8"),
             "files": {path: (EXAMPLE / path).read_bytes().decode("utf-8")
                       for path in ("sources/old.txt", "sources/new.txt")}}
    if mode == "recheck":
        value["revisions"] = (EXAMPLE / "revisions.json").read_bytes().decode("utf-8")
    return value


def path_body(paths, mode="scan"):
    task = {"schema_version": "knowledge-update-task/v1", "task_id": "paths", "sources": [
        {"source_id": str(index), "old": None, "new": {"path": path}} for index, path in enumerate(paths)
    ], "items": []}
    value = {"task": json.dumps(task, ensure_ascii=False), "files": {path: "text\r\n" for path in paths}}
    if mode == "recheck":
        value["revisions"] = '{"schema_version":"knowledge-update-revisions/v1","revisions":[]}'
    return value


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(), backend_options={"loop_factory": lambda: _LOOP}) as value:
        yield value


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("VERISCOPE_KNOWLEDGE_UPDATE", "1")
    monkeypatch.delenv("VERISCOPE_READ_ONLY", raising=False)
    for name in ("VERISCOPE_CHANGEDETECTION_URL", "VERISCOPE_CHANGEDETECTION_API_KEY", "VERISCOPE_KNOWLEDGE_SOURCES"):
        monkeypatch.delenv(name, raising=False)


def post(client, operation, value):
    value = deepcopy(value)
    if operation.startswith("export-"):
        value["mode"] = operation.removeprefix("export-")
        operation = "export"
    return client.post(PREFIX + "/" + operation, json=value)


@pytest.mark.parametrize("method,path", [
    ("get", "/sources"), ("get", "/sources/S/versions"), ("get", "/sources/S/versions/100"),
    ("post", "/scan"), ("post", "/recheck"), ("post", "/export"),
])
def test_disabled_routes_are_404(client, monkeypatch, method, path):
    monkeypatch.delenv("VERISCOPE_KNOWLEDGE_UPDATE")
    response = client.request(method, PREFIX + path)
    assert response.status_code == 404
    assert response.json() == {"detail": "知识更新功能未开启。"}


def test_routes_registered_once_and_disabled_openapi_unchanged(monkeypatch):
    expected = {PREFIX + path for path in (
        "/sources", "/sources/{source_id}/versions", "/sources/{source_id}/versions/{timestamp}",
        "/scan", "/recheck", "/export")}
    paths = create_app().openapi()["paths"]
    assert {path for path in paths if "knowledge-update" in path} == expected
    assert all(len(paths[path]) == 1 for path in expected)
    monkeypatch.delenv("VERISCOPE_KNOWLEDGE_UPDATE")
    assert not any("knowledge-update" in path for path in create_app().openapi()["paths"])


@pytest.mark.parametrize("operation", OPERATIONS)
def test_content_type_and_streaming_size_limit(client, monkeypatch, operation):
    route = operation.split("-", 1)[0]
    assert client.post(PREFIX + "/" + route, content="{}").status_code == 415
    assert client.post(PREFIX + "/" + route, content="{}", headers={"content-type": "text/plain"}).status_code == 415
    assert api.MAX_REQUEST_BYTES == 8 * 1024 * 1024
    monkeypatch.setattr(api, "MAX_REQUEST_BYTES", 100)
    response = client.post(PREFIX + "/" + route, content=iter([b"x" * 60, b"x" * 60]),
                           headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_real_eight_mib_limit_and_json_charset(client):
    assert client.post(PREFIX + "/scan", content=b"x" * (8 * 1024 * 1024 + 1),
                       headers={"content-type": "application/json"}).status_code == 413
    response = client.post(PREFIX + "/scan", content=json_bytes(body()),
                           headers={"content-type": "application/json; charset=utf-8"})
    assert response.status_code == 200


@pytest.mark.parametrize("raw", [
    b"{", b"\xff", b"[]", b"null", b'{"task":"x","task":"private-marker"}',
    b'{"files":{"x.txt":"a","x.txt":"b"}}', b'{"x":NaN}', b'{"x":Infinity}',
    b'{"task":"\\ud800","files":{}}',
])
def test_invalid_json_is_422_without_echo(client, raw):
    response = client.post(PREFIX + "/scan", content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert "private-marker" not in response.text


@pytest.mark.parametrize("case", [
    "unknown", "task-missing", "task-null", "task-object", "files-missing", "files-null", "files-list",
    "file-null", "file-number", "undeclared", "absolute", "escape", "drive", "suffix", "surrogate-file",
    "surrogate-revisions", "scan-revisions", "recheck-missing", "recheck-null", "export-missing-mode",
    "export-invalid-mode", "export-list-mode", "export-object-mode", "mode-on-scan",
])
def test_request_shape_errors(client, case):
    value, operation = body(), "scan"
    if case == "unknown": value["report"] = {"publication_approved": True}
    if case == "task-missing": del value["task"]
    if case == "task-null": value["task"] = None
    if case == "task-object": value["task"] = {}
    if case == "files-missing": del value["files"]
    if case == "files-null": value["files"] = None
    if case == "files-list": value["files"] = []
    if case == "file-null": value["files"]["sources/old.txt"] = None
    if case == "file-number": value["files"]["sources/old.txt"] = 1
    if case == "undeclared": value["files"]["unused.txt"] = "private-marker"
    if case in {"absolute", "escape", "drive", "suffix"}:
        path = {"absolute": "/x.txt", "escape": "../x.txt", "drive": "C:/x.txt", "suffix": "x.pdf"}[case]
        value["files"] = {path: "private-marker"}
    if case == "surrogate-file": value["files"]["sources/old.txt"] = "\ud800"
    if case == "surrogate-revisions": operation, value["revisions"] = "recheck", "\ud800"
    if case == "scan-revisions": value["revisions"] = "{}"
    if case.startswith("recheck-"):
        operation = "recheck"
        if case == "recheck-null": value["revisions"] = None
    if case.startswith("export-"):
        operation = "export"
        if case != "export-missing-mode":
            value["mode"] = {"export-invalid-mode": "other", "export-list-mode": [], "export-object-mode": {}}[case]
    if case == "mode-on-scan": value["mode"] = "scan"
    response = client.post(PREFIX + "/" + operation, content=json.dumps(value), headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert "private-marker" not in response.text


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("paths", [
    ["sub/../old.txt"], ["./old.txt"], ["a//b.txt"], ["a\\b.txt"], ["Old.txt", "old.txt"],
    ["old.txt", "old.txt/new.txt"], ["old.txt/new.txt", "old.txt"], ["Old.txt", "old.txt/new.txt"],
    ["a/old.txt/x.txt", "A/OLD.txt"], ["task.json/new.txt"], ["TASK.JSON/new.txt"],
    ["revisions.json/new.txt"], ["REVISIONS.JSON/new.txt"], ["task.json"], ["revisions.json"],
    ["dir./file.txt"], ["dir /file.txt"], ["bad:dir/file.txt"], ["bad*dir/file.txt"],
    ["bad?dir/file.txt"], ['bad"dir/file.txt'], ["bad<dir/file.txt"], ["bad>dir/file.txt"],
    ["bad|dir/file.txt"], ["bad\x00dir/file.txt"], ["bad\x01dir/file.txt"], ["bad\x7fdir/file.txt"], ["bad\x85dir/file.txt"],
    ["../old.txt"], ["/old.txt"], ["C:old.txt"],
    ["a" * 256 + "/file.txt"], ["中" * 86 + "/file.txt"],
    ["a/" * 32768 + "x.txt"],
    ["knowledge-update.md", "KNOWLE~1.MD"], ["knowledge-update/x.txt", "KNOWLE~1/y.txt"],
    ["AB12CD~1/x.txt"], ["ab~12345.txt"],
])
def test_d1_d3_reject_before_core(client, monkeypatch, operation, paths):
    monkeypatch.setattr(api, "_check", lambda *args: pytest.fail("Invalid file tree reached core"))
    mode = "recheck" if operation.endswith("recheck") else "scan"
    response = post(client, operation, path_body(paths, mode))
    assert response.status_code == 422
    assert response.json()["code"] == "NON_CANONICAL_PATH"


@pytest.mark.parametrize("name", ["con", "PRN", "aux", "NUL"] +
                         [f"com{i}" for i in range(1, 10)] + [f"LPT{i}" for i in range(1, 10)] +
                         ["CON ", "com¹", "lpt²", "COM³"])
def test_d3_reserved_devices_in_files_and_directories(client, name):
    for paths in ([name + ".txt"], ["parent/" + name + "/file.md"]):
        response = post(client, "export-recheck", path_body(paths, "recheck"))
        assert response.status_code == 422
        assert response.json()["code"] == "NON_CANONICAL_PATH"


@pytest.mark.parametrize("path,accepted", [
    ("a/" * 122 + "x.txt", True), ("a/" * 122 + "xy.txt", False),
    (("😀" * 40 + "/") * 3 + "xx.txt", True), (("😀" * 40 + "/") * 3 + "xxx.txt", False),
])
def test_d3_total_member_length_counts_prefix_and_utf16_units(client, path, accepted):
    response = post(client, "scan", path_body([path]))
    assert response.status_code == (200 if accepted else 422)
    if not accepted: assert response.json()["code"] == "NON_CANONICAL_PATH"


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("paths", [
    ["a/x.txt", "a/y.txt"], ["A/x.txt", "a/y.txt"], ["same.txt", "same.txt"],
    ["中文/旧.md", "中文/新.rst"], ["com0.txt", "com10.txt", "console.txt"],
    ["notes~draft.txt", "notes~2026.txt", "AB~1234567.txt", "KNOWLE~1.more/x.txt"],
])
def test_portable_paths_and_duplicate_declarations_allowed(client, operation, paths):
    mode = "recheck" if operation.endswith("recheck") else "scan"
    response = post(client, operation, path_body(paths, mode))
    assert response.status_code == 200
    if operation.startswith("export-"):
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names))
            assert [name for name in names if name.startswith("inputs/") and name.endswith((".txt", ".md", ".rst"))] == [
                "inputs/" + path for path in dict.fromkeys(paths)]
    else:
        assert len(response.json()["report"]["sources"]) == len(paths)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("case", ["task-json", "task-structure", "task-path", "missing-file", "revisions-json"])
def test_core_input_error_retains_full_diagnostics_and_never_exports_zip(client, operation, case):
    mode = "recheck" if operation.endswith("recheck") else "scan"
    value = body(mode)
    if case == "task-json": value["task"] = "{"
    if case == "task-structure": value["task"] = '{"task_id":"bad"}'
    if case == "task-path":
        task = json.loads(value["task"])
        task["sources"][0]["old"]["path"] = "old.pdf"
        value["task"], value["files"] = json.dumps(task), {}
    if case == "missing-file": del value["files"]["sources/old.txt"]
    if case == "revisions-json":
        if mode == "recheck": value["revisions"] = "{"
        else: value["task"] = "{"
    response = post(client, operation, value)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    result = response.json()
    assert set(result) == {"report", "handover", "run"}
    assert result["report"]["run_status"] == "INPUT_ERROR"
    assert result["report"]["input_errors"]
    assert result["run"]["http_status"] == 422
    assert "exit_code" not in result["run"]
    if case == "missing-file":
        assert result["report"]["sources"][0]["status"] == "READ_ERROR"
        assert any(issue["code"] == "SOURCE_READ_ERROR" for issue in result["report"]["input_errors"])


def cli_report(package, value, mode, output):
    package.mkdir(parents=True, exist_ok=True)
    (package / "task.json").write_bytes(value["task"].encode("utf-8"))
    for name, text in value["files"].items():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    if mode == "recheck":
        (package / "revisions.json").write_bytes(value["revisions"].encode("utf-8"))
    return replay_cli(package, mode, output)


def replay_cli(package, mode, output):
    assert not output.exists()
    command = [sys.executable, str(ROOT / "scripts/knowledge_update.py"), mode,
               "--input", str(package / "task.json"), "--output", str(output)]
    if mode == "recheck": command += ["--revisions", str(package / "revisions.json")]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, repr(result.stdout + result.stderr)
    return json.loads((output / "report.json").read_bytes())


def business(report):
    return {key: value for key, value in report.items() if key not in {"schema_version", "bindings"}}


@pytest.mark.parametrize("mode", ["scan", "recheck"])
def test_http_matches_real_cli_and_v2_binds_actual_implementation(client, tmp_path, mode):
    value = body(mode)
    response = post(client, mode, value)
    assert response.status_code == 200
    result = response.json()
    report, run = result["report"], result["run"]
    cli = cli_report(tmp_path / "package", value, mode, tmp_path / "cli-output")
    assert business(report) == business(cli)
    binding = report["bindings"]
    assert binding["task_fingerprint"] == cli["bindings"]["task_fingerprint"]
    assert binding["run_fingerprint"] != cli["bindings"]["run_fingerprint"]
    assert report["schema_version"] == "knowledge-update-report/v2"
    assert cli["schema_version"] == "knowledge-update-report/v1"
    assert binding["entry"] == "http" and "entry" not in cli["bindings"]
    implementation = binding["implementation"]
    assert implementation == {"core_sha256": sha(Path(core.__file__).read_bytes()),
                               "http_sha256": sha(Path(api.__file__).read_bytes()), "checker_version": core.CHECKER_VERSION}
    assert "http_sha256" not in cli["bindings"]["implementation"]
    assert cli["bindings"]["implementation"]["cli_sha256"] == sha((ROOT / "scripts/knowledge_update.py").read_bytes())
    assert binding["task_sha256"] == sha(value["task"].encode("utf-8"))
    for source in binding["source_inputs"]:
        raw = value["files"][source["path"]].encode("utf-8")
        assert source["sha256"] == sha(raw) and source["size"] == len(raw)
    assert binding["task_fingerprint"] == sha(canonical({
        "task_sha256": binding["task_sha256"], "source_inputs": binding["source_inputs"],
        "core_sha256": implementation["core_sha256"], "checker_version": implementation["checker_version"]}))
    assert binding["run_fingerprint"] == sha(canonical({
        "task_fingerprint": binding["task_fingerprint"], "mode": mode,
        "revisions_sha256": sha(value["revisions"].encode("utf-8")) if mode == "recheck" else None,
        "http_sha256": implementation["http_sha256"]}))
    assert run["schema_version"] == "knowledge-update-run/v2" and run["http_status"] == 200
    assert "exit_code" not in run and run["bindings"] == binding
    for name, raw in {"report.json": json_bytes(report), "handover.md": result["handover"].encode("utf-8")}.items():
        assert run["outputs"][name] == {"sha256": sha(raw), "size": len(raw)}
    assert str(ROOT) not in response.text and str(tmp_path) not in response.text
    assert all(item["semantic_evaluation"] == "NOT_EVALUATED" and not item["publication_approved"] for item in report["items"])
    assert len(report["items"]) == 2
    if mode == "recheck": assert report["items"][1]["revision_state"] == "NOT_SUBMITTED"


@pytest.mark.parametrize("mode", ["scan", "recheck"])
@pytest.mark.parametrize("paths", [None, ["same.txt", "same.txt"], ["A/x.txt", "a/y.txt"]])
def test_export_unpacks_and_real_cli_replays_identical_business(client, tmp_path, mode, paths):
    value = body(mode) if paths is None else path_body(paths, mode)
    response = post(client, "export-" + mode, value)
    repeated = post(client, "export-" + mode, value)
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert repeated.content == response.content
    package = tmp_path / "unpacked"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = ["inputs/task.json"] + ["inputs/" + path for path in core.source_paths(value["task"].encode("utf-8"))]
        if mode == "recheck": names.append("inputs/revisions.json")
        names += ["results/report.json", "results/handover.md", "results/run.json", "README.txt"]
        assert archive.namelist() == names and len(names) == len(set(names))
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert archive.read("inputs/task.json") == value["task"].encode("utf-8")
        for path, text in value["files"].items():
            assert archive.read("inputs/" + path) == text.encode("utf-8")
        if mode == "recheck": assert archive.read("inputs/revisions.json") == value["revisions"].encode("utf-8")
        report = json.loads(archive.read("results/report.json"))
        run = json.loads(archive.read("results/run.json"))
        assert archive.read("results/report.json") == json_bytes(report)
        assert archive.read("results/run.json") == json_bytes(run)
        for name, record in run["outputs"].items():
            raw = archive.read("results/" + name)
            assert record == {"sha256": sha(raw), "size": len(raw)}
        readme = archive.read("README.txt").decode("utf-8")
        assert f"python scripts/knowledge_update.py {mode} --input inputs/task.json" in readme
        assert "--output work/knowledge-cli-recheck-new" in readme
        assert all(text in readme for text in ("task_fingerprint", "run_fingerprint", "这不代表 CLI 认证了 HTTP 运行"))
        archive.extractall(package)
    assert response.headers["content-disposition"] == (
        f'attachment; filename="knowledge-update-{mode}-{report["bindings"]["task_fingerprint"][:12]}.zip"')
    cli = replay_cli(package / "inputs", mode, tmp_path / "cli-replay")
    assert business(report) == business(cli)
    assert report["bindings"]["task_fingerprint"] == cli["bindings"]["task_fingerprint"]
    assert report["bindings"]["run_fingerprint"] != cli["bindings"]["run_fingerprint"]


def test_export_recomputes_current_input(client):
    value = body()
    original = post(client, "scan", value).json()["report"]
    value["files"]["sources/new.txt"] = value["files"]["sources/old.txt"]
    response = post(client, "export-scan", value)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        current = json.loads(archive.read("results/report.json"))
    assert original["sources"][0]["status"] == "CHANGED"
    assert current["sources"][0]["status"] == "UNCHANGED"
    assert original["bindings"]["task_fingerprint"] != current["bindings"]["task_fingerprint"]


def test_read_only_keeps_old_423_and_allows_knowledge_update(client, monkeypatch):
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")
    for method, path in [("post", "/evaluate"), ("get", "/models"), ("put", "/models/local"),
                         ("post", "/inputs?kind=document&filename=x.txt"), ("post", "/contexts/parent/prepare"),
                         ("post", "/citations/versions/inspect"), ("post", "/citations/versions/check")]:
        assert client.request(method, path, json={}).status_code == 423
    assert post(client, "scan", body()).status_code == 200
    assert client.get(PREFIX + "/sources").status_code == 200


@pytest.fixture
def collector(monkeypatch, tmp_path):
    key, base, watch = "fake-api-key-only", "https://collector.invalid", "fake-watch"
    config = tmp_path / "sources.json"
    config.write_bytes(json_bytes({"schema_version": "knowledge-sources/v1", "sources": [
        {"source_id": "S", "title": "Example", "url": "https://example.invalid/doc", "watch_uuid": watch,
         "extraction": {"fetch_backend": "html_requests", "include_filters": []}}]}))
    monkeypatch.setenv("VERISCOPE_KNOWLEDGE_SOURCES", str(config))
    monkeypatch.setenv("VERISCOPE_CHANGEDETECTION_URL", base)
    monkeypatch.setenv("VERISCOPE_CHANGEDETECTION_API_KEY", key)
    requests, replies = [], []
    def handler(request):
        requests.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception): raise reply
        return reply
    original = knowledge_sources.KnowledgeSources
    monkeypatch.setattr(knowledge_sources, "KnowledgeSources", lambda: original(transport=httpx.MockTransport(handler)))
    return requests, replies, (key, base, watch)


@pytest.mark.parametrize("case,status,code", [
    ("unconfigured", 503, "COLLECTOR_UNAVAILABLE"), ("unknown", 404, "UNKNOWN_SOURCE"),
    ("latest", 422, "INVALID_TIMESTAMP"), ("absent", 404, "UNKNOWN_VERSION"),
    ("timeout", 502, "COLLECTOR_ERROR"), ("404", 502, "COLLECTOR_ERROR"),
    ("500", 502, "COLLECTOR_ERROR"), ("json", 502, "COLLECTOR_ERROR"),
    ("empty", 422, "EMPTY_SNAPSHOT"), ("utf8", 422, "INVALID_UTF8"),
])
def test_collector_http_errors_never_expose_server_configuration(client, monkeypatch, collector, case, status, code):
    requests, replies, secrets = collector
    path = "/sources/S/versions/100"
    if case == "unconfigured": monkeypatch.delenv("VERISCOPE_CHANGEDETECTION_API_KEY")
    if case == "unknown": path = "/sources/unknown/versions"
    if case == "latest": path = "/sources/S/versions/latest"
    if case == "absent": replies.append(httpx.Response(200, json={}))
    if case == "timeout": replies.append(httpx.ReadTimeout(" ".join(secrets)))
    if case in {"404", "500"}: replies.append(httpx.Response(int(case), text=" ".join(secrets)))
    if case == "json": replies.append(httpx.Response(200, text=" ".join(secrets)))
    if case in {"empty", "utf8"}:
        replies.extend([httpx.Response(200, json={"100": "/private/history"}),
                        httpx.Response(200, content=b" \r\n" if case == "empty" else b"\xff")])
    response = client.get(PREFIX + path)
    assert response.status_code == status and response.json()["code"] == code
    assert all(secret not in response.text for secret in secrets)
    if case in {"unconfigured", "unknown", "latest"}: assert not requests


def test_collector_http_success_and_public_default_sources(client, collector):
    requests, replies, secrets = collector
    raw = "第一行\r\nsecond\n".encode("utf-8")
    replies.extend([httpx.Response(200, json={"200": "/private/two", "100": "/private/one"}),
                    httpx.Response(200, json={"100": "/private/one"}), httpx.Response(200, content=raw)])
    public = client.get(PREFIX + "/sources")
    versions = client.get(PREFIX + "/sources/S/versions")
    snapshot = client.get(PREFIX + "/sources/S/versions/100")
    assert public.status_code == versions.status_code == snapshot.status_code == 200
    assert set(public.json()["sources"][0]) == {"source_id", "title", "url", "extraction", "collector_ready"}
    assert public.json()["sources"][0]["collector_ready"] is True
    assert [v["timestamp"] for v in versions.json()["versions"]] == ["100", "200"]
    assert snapshot.json() == {"source_id": "S", "timestamp": "100", "captured_at": "1970-01-01T00:01:40Z",
                               "sha256": sha(raw), "size": len(raw), "text": raw.decode("utf-8")}
    history = f"{secrets[1]}/api/v1/watch/{secrets[2]}/history"
    assert [str(request.url) for request in requests] == [history, history, history + "/100"]
    assert all(request.method == "GET" and not request.url.query and request.headers["x-api-key"] == secrets[0] for request in requests)
    assert all(secret not in response.text for secret in secrets for response in (public, versions, snapshot))


def test_default_sources_have_no_fabricated_versions(client):
    response = client.get(PREFIX + "/sources")
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert [source["source_id"] for source in sources] == ["nodejs-building", "curl-install"]
    assert all(source["collector_ready"] is False and "watch_uuid" not in source for source in sources)
    for source in sources:
        assert client.get(PREFIX + f'/sources/{source["source_id"]}/versions').status_code == 503
