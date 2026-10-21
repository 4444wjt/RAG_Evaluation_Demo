"""Real loopback HTTP fixtures only; no AnythingLLM, model, key or external network."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import importlib.util
from pathlib import Path
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from backend.adapters.rag_application import ApplicationResponse
_SPEC = importlib.util.spec_from_file_location(
    "anythingllm_shim_under_test", Path(__file__).resolve().parents[1] / "examples/anythingllm_shim/shim.py")
shim = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(shim)

_CONNECT = socket.socket.connect
_CREATE = socket.create_connection
_ADDRINFO = socket.getaddrinfo
TEST_KEY = "constructed-local-test-token-not-a-real-key"


def payload(**patch):
    return {"type": "textResponse", "textResponse": "  最高偿付限额为50万元。\n",
            "sources": [{"text": " 条文原文\n不截断。 ", "title": "条例", "score": 0.83},
                        {"text": "第二段", "title": "第二条"}], **patch}


def question(**patch):
    return {"schemaVersion": shim.SCHEMA, "id": "Q01", "question": "最高偿付限额？", **patch}


@pytest.fixture
def local_only(offline_guard, monkeypatch):
    """Narrowly allow this test's fake server ports; retain the suite's external deny."""
    ports = set()

    def checked_address(address):
        if address[0] != "127.0.0.1" or address[1] not in ports:
            pytest.fail("Test attempted a connection outside its fake loopback servers")

    def connect(sock, address):
        checked_address(address)
        return _CONNECT(sock, address)

    def create(address, *args, **kwargs):
        checked_address(address)
        return _CREATE(address, *args, **kwargs)

    def addrinfo(host, port, *args, **kwargs):
        checked_address((host, port))
        return _ADDRINFO(host, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create)
    monkeypatch.setattr(socket, "getaddrinfo", addrinfo)
    return ports


@contextmanager
def serve(handler, ports):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    ports.add(server.server_port)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        ports.discard(server.server_port)


@pytest.fixture
def fake_anything(local_only):
    @contextmanager
    def fake(*, response=None, http_status=200, raw=None, delay=0, settings=None, settings_status=200, truncated=False):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def send(self, status, data):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data) + (10 if truncated else 0)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_GET(self):
                calls.append(("GET", self.path, None, self.headers.get("Authorization")))
                value = settings if settings is not None else {"workspace": [{"topN": 4, "similarityThreshold": 0.25,
                                                                            "vectorSearchMode": "default", "privateField": "not forwarded"}]}
                self.send(settings_status, json.dumps(value).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(("POST", self.path, body, self.headers.get("Authorization")))
                time.sleep(delay)
                data = raw if raw is not None else json.dumps(response if response is not None else payload(), ensure_ascii=False).encode()
                self.send(http_status, data)

            def log_message(self, *_args):
                pass

        with serve(Handler, local_only) as (url, _server):
            yield url, calls

    return fake


def client(url, *, refusal="资料不足。", timeout=1):
    return shim.AnythingLLM(url, "workspace slug", TEST_KEY, refusal, timeout=timeout)


def test_answer_preserves_sources_answer_order_and_only_forwards_question(fake_anything):
    with fake_anything() as (url, calls):
        app = client(url)
        app.read_settings()
        value = app.answer(question(ground_truth="秘密参考", case_type="negative", contexts=["禁止发送"]))
    ApplicationResponse.model_validate(value)
    assert value["status"] == "answered"
    assert value["answer"] == payload()["textResponse"]
    assert value["contexts"] == [{"text": " 条文原文\n不截断。 ", "sourceTitle": "条例", "score": 0.83},
                                 {"text": "第二段", "sourceTitle": "第二条"}]
    assert calls[0][:3] == ("GET", "/api/v1/workspace/workspace%20slug", None)
    forwarded = calls[1][2]
    assert set(forwarded) == {"message", "mode", "sessionId"}
    assert forwarded["message"] == question()["question"] and forwarded["mode"] == "query"
    assert calls[1][3] == "Bearer " + TEST_KEY
    assert value["meta"]["workspaceSettings"] == {"topN": 4, "similarityThreshold": 0.25, "vectorSearchMode": "default"}
    assert value["meta"]["latencyMs"] >= 0
    assert value["meta"]["contextProvenance"].endswith("unverified")


@pytest.mark.parametrize("answer,sources,refusal,expected", [
    ("资料不足。", [], "资料不足。", "refused"),
    ("其他回答", [], "资料不足。", "answered"),
    ("资料不足。 ", [], "资料不足。", "answered"),
    ("资料不足。", [{"text": "原文"}], "资料不足。", "answered"),
    ("资料不足。", [], None, "answered"),
])
def test_refusal_requires_empty_sources_and_exact_configured_text(fake_anything, answer, sources, refusal, expected):
    with fake_anything(response=payload(textResponse=answer, sources=sources)) as (url, _):
        result = client(url, refusal=refusal).answer(question())
    assert result["status"] == expected
    assert result["answer"] == answer and result["contexts"] == sources
    if refusal is None:
        assert result["meta"]["refusalDetection"] == "not_configured_all_answered"


@pytest.mark.parametrize("options,code", [
    ({"response": {"type": "abort", "error": TEST_KEY}}, "CREDENTIAL_IN_RESPONSE"),
    ({"response": {"type": "abort", "error": "failure"}}, "UPSTREAM_ABORT"),
    ({"http_status": 500, "raw": TEST_KEY.encode()}, "UPSTREAM_HTTP"),
    ({"http_status": 302, "raw": b"redirect"}, "UPSTREAM_HTTP"),
    ({"raw": b"not JSON"}, "UPSTREAM_JSON"),
    ({"raw": b"[]"}, "UPSTREAM_JSON"),
    ({"raw": b'{"x": 1, "x": 2}'}, "UPSTREAM_JSON"),
    ({"raw": b'{"x": NaN}'}, "UPSTREAM_JSON"),
    ({"raw": b'{"x": 1e999}'}, "UPSTREAM_JSON"),
    ({"raw": b'{"x": "\\ud800"}'}, "UPSTREAM_JSON"),
    ({"raw": b'{"x": "\xff"}'}, "UPSTREAM_JSON"),
    ({"raw": b"x" * (shim.MAX_BYTES + 1)}, "RESPONSE_TOO_LARGE"),
    ({"truncated": True}, "UPSTREAM_CONNECTION"),
    ({"delay": 0.15}, "UPSTREAM_TIMEOUT"),
])
def test_upstream_errors_are_contract_errors_without_retry_or_secret(fake_anything, options, code, capsys):
    with fake_anything(**options) as (url, calls):
        value = client(url, timeout=0.03 if options.get("delay") else 1).answer(question())
    ApplicationResponse.model_validate(value)
    assert value["status"] == "error" and value["error"]["code"] == code
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert TEST_KEY not in json.dumps(value) + captured.out + captured.err


@pytest.mark.parametrize("patch,code", [
    ({"sources": [{"text": "文" * 8001}]}, "LIMIT_EXCEEDED"),
    ({"sources": [{"text": "原文"}] * 21}, "LIMIT_EXCEEDED"),
    ({"textResponse": "答" * 32001}, "LIMIT_EXCEEDED"),
    ({"sources": [{"text": "原文", "score": True}]}, "UPSTREAM_SHAPE"),
    ({"sources": [{"text": "原文", "score": "0.8"}]}, "UPSTREAM_SHAPE"),
    ({"sources": [{"text": "原文", "title": 42}]}, "UPSTREAM_SHAPE"),
    ({"sources": [{"title": "没有text"}]}, "UPSTREAM_SHAPE"),
    ({"sources": None}, "UPSTREAM_SHAPE"),
    ({"textResponse": " "}, "UPSTREAM_SHAPE"),
    ({"textResponse": TEST_KEY}, "CREDENTIAL_IN_RESPONSE"),
])
def test_invalid_or_over_limit_fields_are_not_truncated(fake_anything, patch, code):
    with fake_anything(response=payload(**patch)) as (url, _):
        result = client(url).answer(question())
    assert result["error"]["code"] == code and result["status"] == "error"
    assert "answer" not in result and "contexts" not in result


def test_boundaries_are_kept_without_truncation(fake_anything):
    sources = [{"text": "原" * 8000}] * 20
    with fake_anything(response=payload(textResponse="答" * 32000, sources=sources)) as (url, _):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == "answered" and result["contexts"] == sources
    assert len(result["answer"]) == 32000


def test_each_question_gets_a_new_session_even_if_id_repeats(fake_anything):
    with fake_anything() as (url, calls):
        app = client(url)
        first, second = app.answer(question()), app.answer(question())
    assert len(calls) == 2
    assert first["meta"]["sessionId"] != second["meta"]["sessionId"]
    assert all(call[2]["sessionId"].startswith("veriscope-Q01-") for call in calls)


@pytest.mark.parametrize("settings,status", [({"workspace": []}, 200), ({"workspace": {}}, 200), ({}, 500)])
def test_unavailable_workspace_settings_remain_null(fake_anything, settings, status):
    with fake_anything(settings=settings, settings_status=status) as (url, calls):
        app = client(url)
        app.read_settings()
        result = app.answer(question())
    assert len(calls) == 2 and result["status"] == "answered"
    assert all(value is None for value in result["meta"]["workspaceSettings"].values())
    assert result["meta"]["workspaceSettingsStatus"] != "available"


@pytest.mark.parametrize("patch", [{"schemaVersion": "other"}, {"id": ""}, {"question": ""},
                                    {"question": "问" * 8001}, {"question": TEST_KEY}, {"id": TEST_KEY}])
def test_bad_inbound_request_never_calls_upstream(fake_anything, patch):
    with fake_anything() as (url, calls):
        result = client(url).answer(question(**patch))
    assert result["status"] == "error" and not calls
    assert TEST_KEY not in json.dumps(result)


def test_full_shim_http_path_and_logs_do_not_expose_key(fake_anything, local_only, capsys):
    with fake_anything() as (url, calls):
        app = client(url)
        app.read_settings()
        with serve(shim.make_handler(app), local_only) as (shim_url, server):
            assert server.server_address[0] == "127.0.0.1"
            raw = json.dumps(question(), ensure_ascii=False).encode()
            request = Request(shim_url + "/rag", data=raw, headers={"Content-Type": "application/json"})
            with build_opener(ProxyHandler({})).open(request) as response:
                result = json.loads(response.read())
            bad = Request(shim_url + "/rag", data=b"bad", headers={"Content-Type": "application/json"})
            with pytest.raises(HTTPError) as error:
                build_opener(ProxyHandler({})).open(bad)
            assert json.loads(error.value.read())["status"] == "error"
    ApplicationResponse.model_validate(result)
    assert len(calls) == 2
    captured = capsys.readouterr()
    assert TEST_KEY not in json.dumps(result) + captured.out + captured.err


def test_cli_reads_only_named_environment_variables(monkeypatch, capsys):
    monkeypatch.delenv("ONLY_THIS_API_KEY", raising=False)
    monkeypatch.setenv("ANYTHINGLLM_API_KEY", TEST_KEY)
    assert shim.main(["--workspace-slug", "test", "--api-key-env", "ONLY_THIS_API_KEY"]) == 2
    captured = capsys.readouterr()
    assert TEST_KEY not in captured.out + captured.err

# Round 2: additive provenance records; original tests above are unchanged.
def test_workspace_details_preserve_prompt_bytes_and_only_whitelisted_metadata(fake_anything):
    from hashlib import sha256

    prompt = "  仅根据资料回答。\r\n保留空白和 {datetime}。  "
    workspace = {
        "topN": 4, "similarityThreshold": 0.25, "vectorSearchMode": "default",
        "chatProvider": "deepseek", "chatModel": "deepseek-chat", "chatMode": "query",
        "openAiTemp": 0.25, "openAiHistory": 0, "openAiPrompt": prompt,
        "queryRefusalResponse": "资料不足。",
        "documents": [
            {"filename": "条例.json", "pinned": False, "metadata": "do-not-forward-metadata"},
            {"filename": "附录.json", "pinned": True, "pageContent": "do-not-forward-content"},
        ],
        "unreviewedSetting": "do-not-forward-setting",
    }
    with fake_anything(settings={"workspace": [workspace]}) as (url, calls):
        app = client(url)
        app.read_settings()
        result = app.answer(question())
    ApplicationResponse.model_validate(result)
    details = result["meta"]["workspaceDetails"]
    assert details == {
        "chatProvider": "deepseek", "chatModel": "deepseek-chat", "chatMode": "query",
        "openAiTemp": 0.25, "openAiHistory": 0, "openAiPrompt": prompt,
        "queryRefusalResponse": "资料不足。", "openAiPromptUtf8Bytes": len(prompt.encode("utf-8")),
        "openAiPromptSha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "documentCount": 2, "pinnedDocumentCount": 1, "documentNames": ["条例.json", "附录.json"],
    }
    assert all(value == "recorded" for value in result["meta"]["workspaceDetailsFieldStatus"].values())
    assert result["meta"]["refusalTextMismatch"] is False
    assert result["meta"]["workspaceSettings"] == {
        "topN": 4, "similarityThreshold": 0.25, "vectorSearchMode": "default"}
    assert result["status"] == "answered" and len(calls) == 2
    assert "do-not-forward" not in json.dumps(result)


@pytest.mark.parametrize("field,invalid", [
    ("chatProvider", 7), ("chatModel", ["deepseek-chat"]), ("chatMode", False),
    ("openAiTemp", "0.25"), ("openAiHistory", 2.5),
    ("openAiPrompt", {"text": "不得转换对象"}), ("queryRefusalResponse", True),
])
@pytest.mark.parametrize("state", ["missing", "null", "invalid_type"])
def test_workspace_detail_missing_null_and_wrong_types_are_distinct(fake_anything, field, invalid, state):
    workspace = {} if state == "missing" else {field: None if state == "null" else invalid}
    with fake_anything(settings={"workspace": [workspace]}) as (url, _):
        app = client(url)
        app.read_settings()
        result = app.answer(question())
    meta = result["meta"]
    assert result["status"] == "answered"
    assert meta["workspaceDetails"][field] is None
    assert meta["workspaceDetailsFieldStatus"][field] == state
    assert meta["workspaceDetails"]["openAiPromptUtf8Bytes"] is None
    assert meta["workspaceDetails"]["openAiPromptSha256"] is None
    if field == "queryRefusalResponse":
        assert meta["refusalTextMismatch"] is (False if state == "null" else None)


@pytest.mark.parametrize("field,value", [("openAiTemp", False), ("openAiHistory", True)])
def test_workspace_numeric_fields_reject_boolean_without_coercion(fake_anything, field, value):
    with fake_anything(settings={"workspace": [{field: value}]}) as (url, _):
        app = client(url)
        app.read_settings()
        result = app.answer(question())
    assert result["meta"]["workspaceDetails"][field] is None
    assert result["meta"]["workspaceDetailsFieldStatus"][field] == "invalid_type"


@pytest.mark.parametrize("workspace,count,pins,names,state", [
    ({}, None, None, None, "missing"),
    ({"documents": None}, None, None, None, "invalid_type"),
    ({"documents": {}}, None, None, None, "invalid_type"),
    ({"documents": []}, 0, 0, [], "recorded"),
    ({"documents": [{"filename": "条例.json", "pinned": False}]}, 1, 0, ["条例.json"], "recorded"),
    ({"documents": [{"filename": "条例.json"}]}, 1, None, ["条例.json"], "incomplete_or_invalid"),
    ({"documents": [{"filename": "条例.json", "pinned": None}]}, 1, None, ["条例.json"], "incomplete_or_invalid"),
    ({"documents": [{"filename": "条例.json", "pinned": 0}]}, 1, None, ["条例.json"], "incomplete_or_invalid"),
    ({"documents": [{"pinned": False}]}, 1, 0, None, "incomplete_or_invalid"),
    ({"documents": [{"filename": 12, "pinned": False}]}, 1, 0, None, "incomplete_or_invalid"),
    ({"documents": ["not-a-document-object"]}, 1, None, None, "incomplete_or_invalid"),
])
def test_document_statistics_do_not_treat_missing_information_as_zero(fake_anything, workspace, count, pins, names, state):
    with fake_anything(settings={"workspace": [workspace]}) as (url, _):
        app = client(url)
        app.read_settings()
        result = app.answer(question())
    details = result["meta"]["workspaceDetails"]
    assert details["documentCount"] == count
    assert details["pinnedDocumentCount"] == pins
    assert details["documentNames"] == names
    assert result["meta"]["workspaceDetailsFieldStatus"]["documents"] == state
    assert result["status"] == "answered"


def test_upstream_metrics_preserve_zero_and_fractions_but_exclude_unknown_fields(fake_anything):
    expected = {"prompt_tokens": 0, "completion_tokens": 31, "total_tokens": 31,
                "duration": 1.25, "outputTps": 24.8}
    response = payload(metrics={**expected, "model": "deepseek-chat", "provider": "DeepSeek",
                                "timestamp": "2026-10-20T00:00:00Z", "totalCost": 0.001,
                                "unreviewed_number": 99, "nested": {"prompt_tokens": 999}})
    with fake_anything(response=response) as (url, _):
        result = client(url).answer(question())
    assert result["meta"]["upstreamMetrics"] == expected
    assert result["meta"]["upstreamMetricsStatus"] == "recorded"
    assert result["meta"]["upstreamMetricsInvalidFields"] == []
    assert "token_zero_may_be_upstream_default" in result["meta"]["upstreamMetricsProvenance"]
    assert result["status"] == "answered"


@pytest.mark.parametrize("invalid", [None, True, "12", [], {}, -1])
def test_non_numeric_or_negative_metric_is_not_coerced_or_forwarded(fake_anything, invalid):
    with fake_anything(response=payload(metrics={"prompt_tokens": invalid, "completion_tokens": 12,
                                                "duration": 0.5})) as (url, _):
        result = client(url).answer(question())
    assert result["meta"]["upstreamMetrics"] == {"completion_tokens": 12, "duration": 0.5}
    assert result["meta"]["upstreamMetricsInvalidFields"] == ["prompt_tokens"]
    assert result["status"] == "answered"


@pytest.mark.parametrize("metrics", [None, [], "not-metrics", 3])
def test_invalid_metrics_container_does_not_invent_usage_or_change_answer(fake_anything, metrics):
    with fake_anything(response=payload(metrics=metrics)) as (url, _):
        result = client(url).answer(question())
    assert result["meta"]["upstreamMetrics"] == {}
    assert result["meta"]["upstreamMetricsStatus"] == "missing_or_invalid"
    assert result["meta"]["upstreamMetricsInvalidFields"] == []
    assert result["status"] == "answered"


def test_absent_metrics_and_empty_metrics_remain_distinct(fake_anything):
    with fake_anything(response=payload()) as (url, _):
        absent = client(url).answer(question())
    with fake_anything(response=payload(metrics={})) as (url, _):
        empty = client(url).answer(question())
    assert absent["meta"]["upstreamMetrics"] == empty["meta"]["upstreamMetrics"] == {}
    assert absent["meta"]["upstreamMetricsStatus"] == "missing_or_invalid"
    assert empty["meta"]["upstreamMetricsStatus"] == "recorded"


@pytest.mark.parametrize("workspace_text,configured,answer,expected,mismatch", [
    ("上游无资料。", "资料不足。", "资料不足。", "refused", True),
    ("上游无资料。", "资料不足。", "上游无资料。", "answered", True),
    ("资料不足。", "资料不足。", "资料不足。", "refused", False),
    ("资料不足。 ", "资料不足。", "资料不足。", "refused", True),
    (None, "资料不足。", "资料不足。", "refused", False),
    ("上游无资料。", None, "上游无资料。", "answered", True),
])
def test_refusal_setting_mismatch_is_only_metadata(fake_anything, workspace_text, configured, answer, expected, mismatch):
    with fake_anything(settings={"workspace": [{"queryRefusalResponse": workspace_text}]},
                       response=payload(textResponse=answer, sources=[])) as (url, _):
        app = client(url, refusal=configured)
        app.read_settings()
        result = app.answer(question())
    assert result["status"] == expected
    assert result["answer"] == answer and result["contexts"] == []
    assert result["meta"]["refusalTextMismatch"] is mismatch


@pytest.mark.parametrize("text,has_command", [("  中文问题\n保留空白？  ", False),
                                               ("请执行 /reset", True), ("@AGENT 查看资料", True)])
def test_request_evidence_hashes_the_exact_question_without_forwarding_reference(fake_anything, text, has_command):
    from hashlib import sha256

    with fake_anything() as (url, calls):
        result = client(url).answer(question(question=text, ground_truth="do-not-send-reference"))
    assert result["meta"]["requestEvidence"] == {
        "questionUtf8Bytes": len(text.encode("utf-8")),
        "questionSha256": sha256(text.encode("utf-8")).hexdigest(),
        "attachments": 0, "freshSession": True, "hasCommandSyntax": has_command,
    }
    assert calls[0][2]["message"] == text
    assert "do-not-send-reference" not in json.dumps(calls)


def test_workspace_snapshot_metadata_is_not_shared_between_answers(fake_anything):
    with fake_anything(settings={"workspace": [{"documents": [{"filename": "条例.json", "pinned": False}]}]}) as (url, _):
        app = client(url)
        app.read_settings()
        first = app.answer(question())
        first["meta"]["workspaceDetails"]["documentNames"].append("not-an-upstream-document")
        second = app.answer(question())
    assert second["meta"]["workspaceDetails"]["documentNames"] == ["条例.json"]
    assert first["meta"]["sessionId"] != second["meta"]["sessionId"]


def test_large_integer_metric_does_not_crash_finite_check(fake_anything):
    large = 10 ** 400
    with fake_anything(response=payload(metrics={"prompt_tokens": large})) as (url, _):
        result = client(url).answer(question())
    assert result["status"] == "answered"
    assert result["meta"]["upstreamMetrics"] == {"prompt_tokens": large}


# Round 3: runtime snapshot for --dump-settings; tests above are unchanged.
def system_settings(storage=None, **patch):
    value = {"MemoryEnabled": False, "MemoryAutoExtraction": False, "MultiUserMode": False,
             "LLMProvider": "deepseek", "DeepSeekModelPref": "deepseek-chat", "EmbeddingEngine": "generic-openai",
             "VectorDB": "lancedb", "OpenAiKey": True, "ComplexUnreviewed": {"nested": "do-not-forward"}}
    if storage is not None:
        value["StorageDir"] = str(storage)
    return {"settings": {**value, **patch}}


@pytest.fixture
def fake_runtime(local_only):
    @contextmanager
    def fake(*, workspace=None, system=None, system_status=200):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def send(self, status, value):
                data = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                calls.append(("GET", self.path))
                if self.path == "/api/v1/system":
                    return self.send(system_status, system if system is not None else system_settings())
                self.send(200, {"workspace": [workspace if workspace is not None else {
                    "topN": 4, "chatProvider": None, "chatModel": None, "openAiPrompt": None,
                    "documents": [{"filename": "deposit.json", "pinned": False}]}]})

            def do_POST(self):
                calls.append(("POST", self.path))
                self.send(500, {})

            def log_message(self, *_args):
                pass

        with serve(Handler, local_only) as (url, _server):
            yield url, calls

    return fake


def write_cache(storage, content):
    folder = storage / "models" / "context-windows"
    folder.mkdir(parents=True)
    (folder / "context-windows.json").write_text(content, encoding="utf-8")
    (folder / ".cached_at").write_text("1760000000000", encoding="utf-8")


def test_snapshot_reads_whitelist_and_window_without_writing_paths_or_chatting(fake_runtime, tmp_path):
    storage = tmp_path / "private-storage-path"
    write_cache(storage, json.dumps({"deepseek": {"deepseek-chat": 65536}}))
    with fake_runtime(system=system_settings(storage)) as (url, calls):
        snapshot = shim.runtime_snapshot(client(url))
    assert [method for method, _ in calls] == ["GET", "GET"]
    assert snapshot["schemaVersion"] == shim.SNAPSHOT_SCHEMA and snapshot["storageDirReported"] is True
    assert snapshot["system"]["MemoryEnabled"] is False and snapshot["systemFieldStatus"]["MemoryEnabled"] == "recorded"
    assert set(snapshot["system"]) == set(shim.SYSTEM_FIELDS)
    assert snapshot["effective"] == {"provider": "deepseek", "model": "deepseek-chat",
                                     "modelBasis": "system.DeepSeekModelPref"}
    cache = snapshot["contextWindowCache"]
    assert cache["status"] == "cached_value" and cache["effectiveWindow"] == 65536
    assert cache["cacheFile"]["exists"] is True and len(cache["cacheFile"]["sha256"]) == 64
    text = json.dumps(snapshot, ensure_ascii=False)
    for leaked in ("private-storage-path", str(tmp_path), "OpenAiKey", "do-not-forward", TEST_KEY):
        assert leaked not in text


@pytest.mark.parametrize("content,status,window", [
    (None, "cache_file_missing_fallback", 8192),
    (json.dumps({"openai": {"gpt-4o": 128000}}), "provider_missing_fallback", 8192),
    (json.dumps({"deepseek": {}}), "provider_missing_fallback", 8192),
    (json.dumps({"deepseek": {"other": 1000}}), "model_missing_fallback", 8192),
    (json.dumps({"deepseek": {"deepseek-chat": 0}}), "model_missing_fallback", 8192),
    (json.dumps({"deepseek": {"deepseek-chat": 4096}}), "cached_value", 4096),
    (json.dumps({"deepseek": {"deepseek-chat": {}}}), "cached_value_not_a_positive_number", None),
    (json.dumps({"deepseek": {"deepseek-chat": "65536"}}), "cached_value_not_a_positive_number", None),
    (json.dumps({"deepseek": {"deepseek-chat": True}}), "cached_value_not_a_positive_number", None),
    ('{"deepseek": {"deepseek-chat": 1}, "deepseek": {}}', "cache_file_invalid_json", None),
    ("not json", "cache_file_invalid_json", None),
])
def test_window_cache_mirrors_lookup_and_8192_fallback_without_guessing(tmp_path, content, status, window):
    if content is None:
        (tmp_path / "models" / "context-windows").mkdir(parents=True)
    else:
        write_cache(tmp_path, content)
    result = shim.snapshot_window_cache(str(tmp_path), "deepseek", "deepseek-chat")
    assert (result["status"], result["effectiveWindow"]) == (status, window)


def test_window_unknown_without_storage_dir_or_deepseek(tmp_path):
    write_cache(tmp_path, json.dumps({"deepseek": {"deepseek-chat": 65536}}))
    assert shim.snapshot_window_cache(None, "deepseek", "deepseek-chat")["effectiveWindow"] is None
    other = shim.snapshot_window_cache(str(tmp_path), "openai", None)
    assert (other["status"], other["effectiveWindow"]) == ("provider_not_deepseek_or_unknown", None)


@pytest.mark.parametrize("details,system,expected", [
    ({"chatProvider": "deepseek", "chatModel": "deepseek-flash"}, {"LLMProvider": "openai"},
     ("deepseek", "deepseek-flash", "workspace.chatModel")),
    ({"chatProvider": None, "chatModel": None}, {"LLMProvider": "deepseek", "DeepSeekModelPref": "deepseek-chat"},
     ("deepseek", "deepseek-chat", "system.DeepSeekModelPref")),
    ({"chatProvider": "deepseek", "chatModel": ""}, {"DeepSeekModelPref": None},
     ("deepseek", "deepseek-chat", "deepseek_adapter_default")),
    ({"chatProvider": None}, {"LLMProvider": "openai"}, ("openai", None, "provider_not_deepseek_or_unknown")),
    ({}, {}, (None, None, "provider_not_deepseek_or_unknown")),
])
def test_effective_provider_and_model_follow_workspace_then_system(details, system, expected):
    assert shim.effective_provider_model(details, system) == expected


@pytest.mark.parametrize("status,system", [(500, None), (200, {"settings": "not an object"})])
def test_system_read_failure_leaves_fields_unknown_not_false(fake_runtime, status, system):
    with fake_runtime(system=system if system is not None else system_settings(), system_status=status) as (url, _):
        snapshot = shim.runtime_snapshot(client(url))
    assert all(value is None for value in snapshot["system"].values())
    assert snapshot["systemFieldStatus"]["MemoryEnabled"] in ("UPSTREAM_HTTP", "shape_invalid")
    assert snapshot["workspaceSettingsStatus"] == "available"
    assert snapshot["contextWindowCache"]["effectiveWindow"] is None


def test_system_fields_distinguish_missing_null_and_invalid(fake_runtime):
    system = {"settings": {"MemoryEnabled": None, "LLMProvider": ["deepseek"], "MultiUserMode": 1.0}}
    with fake_runtime(system=system) as (url, _):
        fields, status, storage = client(url).read_system()
    assert status["MemoryEnabled"] == "null" and fields["MemoryEnabled"] is None
    assert status["LLMProvider"] == "invalid_type" and fields["LLMProvider"] is None
    assert status["VectorDB"] == "missing" and storage is None


def test_cli_dump_settings_writes_once_and_never_overwrites(fake_runtime, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROUND3_TEST_KEY", TEST_KEY)
    output = tmp_path / "before.json"
    with fake_runtime(system=system_settings(tmp_path / "storage")) as (url, calls):
        args = ["--anythingllm-base", url, "--workspace-slug", "w", "--api-key-env", "ROUND3_TEST_KEY",
                "--dump-settings", str(output)]
        assert shim.main(args) == 0
        first = output.read_bytes()
        assert shim.main(args) == 2
    assert output.read_bytes() == first
    assert all(method == "GET" for method, _ in calls)
    snapshot = json.loads(first)
    assert snapshot["contextWindowCache"]["status"] == "cache_file_missing_fallback"
    captured = capsys.readouterr()
    assert TEST_KEY not in first.decode() + captured.out + captured.err


# Preregistered think-block stripping; all earlier test bytes remain unchanged.
SMOKE02_RAW_ANSWER = "<think>We need answer Chinese. Need cite? User asks limit. Based context Article 5: RMB 500,000. Answer concise.</think>存款保险的最高偿付限额为人民币 **50万元**。同一存款人在同一家投保机构所有被保险存款账户的本金和利息合并计算，在50万元以内的实行全额偿付；超出部分依法从投保机构清算财产中受偿。"


def assert_reasoning_metadata(result, raw, status, reasoning=None):
    from hashlib import sha256

    raw_bytes = raw.encode("utf-8")
    reasoning_bytes = reasoning.encode("utf-8") if reasoning is not None else None
    assert result["meta"]["rawAnswer"] == raw
    assert result["meta"]["reasoningBlock"] == {
        "status": status,
        "rawAnswerUtf8Bytes": len(raw_bytes),
        "rawAnswerSha256": sha256(raw_bytes).hexdigest(),
        "reasoningUtf8Bytes": len(reasoning_bytes) if reasoning_bytes is not None else None,
        "reasoningSha256": sha256(reasoning_bytes).hexdigest() if reasoning_bytes is not None else None,
    }


def test_reasoning_smoke02_literal_sample(fake_anything):
    with fake_anything(response=payload(textResponse=SMOKE02_RAW_ANSWER)) as (url, calls):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    reasoning, expected_answer = SMOKE02_RAW_ANSWER[len("<think>"):].split("</think>")
    assert result["status"] == "answered"
    assert result["answer"].startswith("存款保险的最高偿付限额为人民币")
    assert result["answer"] == expected_answer
    assert_reasoning_metadata(result, SMOKE02_RAW_ANSWER, "stripped", reasoning)
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [
    "  没有推理块的正文。\n",
    " <think>推理</think>正文",
    "\n<think>推理</think>正文",
])
def test_reasoning_absent_preserves_answer(fake_anything, raw):
    with fake_anything(response=payload(textResponse=raw)) as (url, _):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == "answered" and result["answer"] == raw
    assert_reasoning_metadata(result, raw, "absent")


@pytest.mark.parametrize("raw", [
    "<think>没有闭合标签的推理和正文",
    "<think>推理</think>正文</think>",
])
def test_reasoning_ambiguous_preserves_answer(fake_anything, raw):
    with fake_anything(response=payload(textResponse=raw)) as (url, _):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == "answered" and result["answer"] == raw
    assert_reasoning_metadata(result, raw, "ambiguous_not_stripped")


@pytest.mark.parametrize("content", ["", " \t\r\n"])
def test_reasoning_empty_content_is_error_without_retry(fake_anything, content):
    raw = "<think>只有推理</think>" + content
    with fake_anything(response=payload(textResponse=raw)) as (url, calls):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == "error" and result["error"]["code"] == "UPSTREAM_SHAPE"
    assert "answer" not in result and "contexts" not in result
    assert_reasoning_metadata(result, raw, "stripped", "只有推理")
    assert len(calls) == 1


def test_reasoning_keeps_content_whitespace(fake_anything):
    content = "\n  正文。\r\n\t"
    raw = "<think>原样保留空白</think>" + content
    with fake_anything(response=payload(textResponse=raw)) as (url, _):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == "answered" and result["answer"] == content
    assert_reasoning_metadata(result, raw, "stripped", "原样保留空白")


@pytest.mark.parametrize("content,sources,refusal,expected", [
    ("资料不足。", [], "资料不足。", "refused"),
    ("资料不足。 ", [], "资料不足。", "answered"),
    ("资料不足。", [{"text": "原文"}], "资料不足。", "answered"),
    ("资料不足。", [], None, "answered"),
])
def test_reasoning_refusal_uses_stripped_exact_answer(fake_anything, content, sources, refusal, expected):
    raw = "<think>拒答识别仍按原规则</think>" + content
    with fake_anything(response=payload(textResponse=raw, sources=sources)) as (url, calls):
        result = client(url, refusal=refusal).answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == expected
    assert result["answer"] == content and result["contexts"] == sources
    assert_reasoning_metadata(result, raw, "stripped", "拒答识别仍按原规则")
    assert len(calls) == 1


@pytest.mark.parametrize("raw_length", [32000, 32001])
def test_reasoning_original_length_limit(fake_anything, raw_length):
    reasoning = "思" * (raw_length - len("<think></think>正文"))
    raw = "<think>" + reasoning + "</think>正文"
    assert len(raw) == raw_length
    with fake_anything(response=payload(textResponse=raw)) as (url, calls):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    if raw_length == 32000:
        assert result["status"] == "answered" and result["answer"] == "正文"
        assert_reasoning_metadata(result, raw, "stripped", reasoning)
    else:
        assert result["status"] == "error" and result["error"]["code"] == "LIMIT_EXCEEDED"
        assert "answer" not in result and "contexts" not in result
        assert result["meta"]["rawAnswer"] is None and result["meta"]["reasoningBlock"] is None
    assert len(calls) == 1


@pytest.mark.parametrize("patch", [
    {"textResponse": " \n"},
    {"textResponse": "<think>推理</think>正文", "sources": None},
])
def test_reasoning_metadata_stays_null_until_raw_is_valid(fake_anything, patch):
    with fake_anything(response=payload(**patch)) as (url, calls):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == "error" and result["error"]["code"] == "UPSTREAM_SHAPE"
    assert "answer" not in result and "contexts" not in result
    assert result["meta"]["rawAnswer"] is None and result["meta"]["reasoningBlock"] is None
    assert len(calls) == 1


def test_reasoning_metadata_survives_source_error(fake_anything):
    raw = "<think>已有推理留证</think>正文"
    with fake_anything(response=payload(textResponse=raw, sources=[{"title": "缺少 text"}])) as (url, calls):
        result = client(url).answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == "error" and result["error"]["code"] == "UPSTREAM_SHAPE"
    assert "answer" not in result and "contexts" not in result
    assert_reasoning_metadata(result, raw, "stripped", "已有推理留证")
    assert len(calls) == 1


def test_reasoning_raw_answer_counts_toward_total_limit(fake_anything):
    raw = "答" * 1000
    settings = {"workspace": [{"openAiPrompt": "x" * (shim.MAX_BYTES - 6000)}]}
    with fake_anything(settings=settings, response=payload(textResponse=raw, sources=[])) as (url, calls):
        app = client(url)
        app.read_settings()
        result = app.answer(question())
    ApplicationResponse.model_validate(result)
    assert result["status"] == "error" and result["error"]["code"] == "RESPONSE_TOO_LARGE"
    assert "answer" not in result and "contexts" not in result
    assert_reasoning_metadata(result, raw, "absent")
    assert [call[0] for call in calls] == ["GET", "POST"]
    oversized = {**result, "status": "answered", "answer": raw, "contexts": [], "error": None}
    assert len(json.dumps(oversized, ensure_ascii=False).encode("utf-8")) > shim.MAX_BYTES
    oversized["meta"] = {**result["meta"], "rawAnswer": None}
    assert len(json.dumps(oversized, ensure_ascii=False).encode("utf-8")) <= shim.MAX_BYTES
