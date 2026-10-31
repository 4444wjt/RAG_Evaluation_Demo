"""Offline contract tests for the fixed changedetection snapshot adapter."""
import hashlib
import json

import httpx
import pytest

from backend.core.knowledge_sources import KnowledgeSources, SourceError


BASE = "https://collector.invalid"
KEY = "fake-test-key-only"
WATCH = "fake-watch-id"
SOURCE = "example-source"
HISTORY = f"/api/v1/watch/{WATCH}/history"


@pytest.fixture
def collector_factory(monkeypatch, tmp_path):
    config = tmp_path / "sources.json"
    config.write_text(json.dumps({
        "schema_version": "knowledge-sources/v1",
        "sources": [{"source_id": SOURCE, "title": "Example", "url": "https://example.invalid/doc",
                     "watch_uuid": WATCH, "extraction": {"fetch_backend": "html_requests", "include_filters": []}}],
    }), encoding="utf-8")
    monkeypatch.setenv("VERISCOPE_KNOWLEDGE_SOURCES", str(config))
    monkeypatch.setenv("VERISCOPE_CHANGEDETECTION_URL", BASE)
    monkeypatch.setenv("VERISCOPE_CHANGEDETECTION_API_KEY", KEY)

    def make(handler):
        return KnowledgeSources(transport=httpx.MockTransport(handler))
    return make


def assert_error(call, status, code):
    with pytest.raises(SourceError) as caught:
        call()
    assert (caught.value.status, caught.value.code) == (status, code)
    assert all(secret not in str(caught.value) for secret in (KEY, WATCH, BASE))


@pytest.mark.parametrize("base", [BASE + "?", BASE + "#", BASE + "?x=1", "ftp://collector.invalid",
                                  "https://user:pass@collector.invalid"])
def test_malformed_collector_url_never_calls_upstream(collector_factory, monkeypatch, base):
    monkeypatch.setenv("VERISCOPE_CHANGEDETECTION_URL", base)
    collector = collector_factory(lambda request: pytest.fail(f"unexpected upstream call {request.url}"))
    assert_error(lambda: collector.versions(SOURCE), 503, "COLLECTOR_UNAVAILABLE")


def test_first_unchanged_and_changed_history(collector_factory):
    histories = [{"100": "/private/first"}, {"100": "/private/first"},
                 {"300": "/private/third", "100": "/private/first", "200": "/private/second"}]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=histories.pop(0))

    collector = collector_factory(handler)
    first = collector.versions(SOURCE)
    unchanged = collector.versions(SOURCE)
    changed = collector.versions(SOURCE)
    assert first == unchanged
    assert [v["timestamp"] for v in first["versions"]] == ["100"]
    assert [v["timestamp"] for v in changed["versions"]] == ["100", "200", "300"]
    assert changed["versions"][0]["captured_at"] == "1970-01-01T00:01:40Z"
    for request in requests:
        assert request.method == "GET" and request.url.path == HISTORY
        assert not request.url.query
        assert request.headers["x-api-key"] == KEY
        assert request.extensions["timeout"] == {"connect": 5.0, "read": 15.0, "write": 15.0, "pool": 15.0}
    assert all(secret not in json.dumps([first, unchanged, changed]) for secret in (KEY, WATCH, BASE, "/private/first"))


def test_empty_history_and_unknown_version(collector_factory):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})
    collector = collector_factory(handler)
    assert collector.versions(SOURCE) == {"source_id": SOURCE, "versions": []}
    assert_error(lambda: collector.snapshot(SOURCE, "100"), 404, "UNKNOWN_VERSION")
    assert all(request.url.path == HISTORY for request in requests)


@pytest.mark.parametrize("response", [
    httpx.Response(404), httpx.Response(500), httpx.Response(302, headers={"location": "https://other.invalid/"}),
    httpx.Response(200, content=b"not json"), httpx.Response(200, json=[]),
    httpx.Response(200, json={"latest": "/private"}), httpx.Response(200, json={"100": None}),
])
def test_bad_history_is_upstream_error(collector_factory, response):
    requests = []
    def handler(request):
        requests.append(request)
        return response
    collector = collector_factory(handler)
    assert_error(lambda: collector.versions(SOURCE), 502, "COLLECTOR_ERROR")
    assert len(requests) == 1 and requests[0].url.path == HISTORY


def test_timeout_is_upstream_error(collector_factory):
    def handler(request):
        raise httpx.ReadTimeout("fake timeout", request=request)
    assert_error(lambda: collector_factory(handler).versions(SOURCE), 502, "COLLECTOR_ERROR")


def test_snapshot_preserves_raw_bytes_and_limits_requests(collector_factory):
    raw = "第一行\r\nsecond\nthird\r\n".encode("utf-8")
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == HISTORY:
            return httpx.Response(200, json={"100": "/private/untrusted-path"})
        assert request.url.path == HISTORY + "/100"
        return httpx.Response(200, content=raw)
    collector = collector_factory(handler)
    snapshot = collector.snapshot(SOURCE, "100")
    assert snapshot["text"] == raw.decode("utf-8")
    assert snapshot["sha256"] == hashlib.sha256(raw).hexdigest()
    assert snapshot["size"] == len(raw)
    assert [r.url.path for r in requests] == [HISTORY, HISTORY + "/100"]
    assert all(r.method == "GET" and not r.url.query and r.headers["x-api-key"] == KEY for r in requests)
    assert all(secret not in json.dumps(snapshot) for secret in (KEY, WATCH, BASE, "/private/untrusted-path"))


@pytest.mark.parametrize("raw,code", [(b"", "EMPTY_SNAPSHOT"), (b" \r\n\t", "EMPTY_SNAPSHOT"), (b"\xff", "INVALID_UTF8")])
def test_invalid_snapshot(collector_factory, raw, code):
    def handler(request):
        return httpx.Response(200, json={"100": "ignored"}) if request.url.path == HISTORY else httpx.Response(200, content=raw)
    assert_error(lambda: collector_factory(handler).snapshot(SOURCE, "100"), 422, code)


@pytest.mark.parametrize("timestamp", ["latest", "-1", "1?html=1", "1234567890123"])
def test_invalid_timestamp_never_calls_upstream(collector_factory, timestamp):
    def handler(request):
        pytest.fail("Invalid timestamp must not reach the upstream")
    assert_error(lambda: collector_factory(handler).snapshot(SOURCE, timestamp), 422, "INVALID_TIMESTAMP")


def test_sources_and_configuration_boundaries(collector_factory, monkeypatch):
    def handler(request):
        pytest.fail("Unexpected upstream request")
    collector = collector_factory(handler)
    public = collector.public_sources()
    assert public["sources"][0]["collector_ready"] is True
    assert all(secret not in json.dumps(public) for secret in (KEY, WATCH, BASE))
    assert_error(lambda: collector.versions("missing"), 404, "UNKNOWN_SOURCE")
    monkeypatch.delenv("VERISCOPE_CHANGEDETECTION_API_KEY")
    unconfigured = collector_factory(handler)
    assert unconfigured.public_sources()["sources"][0]["collector_ready"] is False
    assert_error(lambda: unconfigured.versions(SOURCE), 503, "COLLECTOR_UNAVAILABLE")
