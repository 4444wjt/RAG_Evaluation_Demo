"""Public finance transport preserves the transaction contract and hides other APIs."""

import asyncio
import importlib
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
_LOOP = asyncio.new_event_loop()


def public_module():
    return importlib.import_module("tools.finance.public_app")


def request(app, method, path, **kwargs):
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)
    return _LOOP.run_until_complete(invoke())


def teardown_module():
    _LOOP.close()


@pytest.fixture
def app(tmp_path, monkeypatch):
    module = public_module()
    for name in ("dist-finance-public",):
        dist = tmp_path / "frontend" / name
        (dist / "assets").mkdir(parents=True)
        (dist / "api").mkdir()
        (dist / "index.html").write_text('<html><script src="/finance/assets/public.js"></script>finance fixture</html>', encoding="utf-8")
        (dist / "assets" / "public.js").write_text("console.log('finance fixture');", encoding="utf-8")
        (dist / "api" / "models").write_text("private static collision", encoding="utf-8")
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("VERISCOPE_FINANCE_DIST", raising=False)
    monkeypatch.delenv("VERISCOPE_READ_ONLY", raising=False)
    monkeypatch.delenv("VERISCOPE_BYOK", raising=False)
    api = importlib.import_module("backend.api.app")

    def forbidden(*args, **kwargs):
        raise AssertionError("A non-public API reached model or evaluation business code")

    monkeypatch.setattr(api, "run_evaluation", forbidden)
    monkeypatch.setattr(api, "list_models", forbidden)
    return module.create_app()


def source_request():
    return {"schemaVersion": 1, "currency": "CNY", "transactions": [
        {"transaction_id": "public-1", "timestamp": "2026-11-01T10:00:00Z", "customer_id": "模拟客户", "terminal_id": "m1", "amount": "10000.00"}],
        "rules": {"highAmount": "10000.00", "windowMinutes": 10, "minTransactions": 3}, "claims": []}


def test_static_frontend_and_health_are_served_after_proxy_strips_finance_prefix(app):
    page = request(app, "GET", "/")
    assert page.status_code == 200 and "finance fixture" in page.text
    asset = request(app, "GET", "/assets/public.js")
    assert asset.status_code == 200 and "javascript" in asset.headers["content-type"]
    health = request(app, "GET", "/api/health")
    assert health.status_code == 200 and health.json() == {"status": "ok"}


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/models"), ("PUT", "/api/models/local"), ("POST", "/api/evaluate"),
    ("POST", "/api/inputs"), ("GET", "/api/evaluations"), ("GET", "/api/evaluations/example"),
    ("POST", "/api/finance-review"), ("GET", "/api/docs"), ("GET", "/api/redoc"),
    ("GET", "/api/openapi.json"), ("GET", "/docs"), ("GET", "/redoc"), ("GET", "/openapi.json"),
    ("GET", "/api/transaction-review/analyze"), ("GET", "/api/transaction-review/replay"),
    ("POST", "/api/health"), ("GET", "/api/health/"), ("POST", "/api/transaction-review/analyze/"),
    ("GET", "/api/unknown"), ("GET", "/api"),
])
def test_non_public_api_paths_and_methods_are_404(app, method, path):
    response = request(app, method, path, content=b"{}", headers={"Content-Type": "application/json"})
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("path", ["/.env", "/backend/core/transaction_review.py", "/%2e%2e/.env", "/health"])
def test_static_server_does_not_expose_repository_or_unmounted_routes(app, path):
    assert request(app, "GET", path).status_code == 404


def test_public_analyze_and_replay_use_real_core_and_reject_tampering(app):
    source = source_request()
    analyzed = request(app, "POST", "/api/transaction-review/analyze", json=source)
    assert analyzed.status_code == 200, analyzed.text
    report = analyzed.json()
    assert report["summary"] == {"totalTransactions": 1, "flaggedTransactions": 1, "consistentClaims": 6, "inconsistentClaims": 0, "reviewClaims": 0}
    assert report["cases"][0]["transaction"]["amount"] == "10000.00"
    bundle = {"schema": "TransactionReviewBundleV1", "request": source, "report": report,
              "decisions": [{"transaction_id": "public-1", "decision": "need_more", "reason": "模拟复核：补充证据"}]}
    replayed = request(app, "POST", "/api/transaction-review/replay", json=bundle)
    assert replayed.status_code == 200 and replayed.json() == {**bundle, "replayed": True}
    bundle["report"]["summary"]["flaggedTransactions"] = 0
    bad = request(app, "POST", "/api/transaction-review/replay", json=bundle)
    assert bad.status_code == 422 and set(bad.json()) == {"detail"}


@pytest.mark.parametrize("operation,limit", [("analyze", 2 * 1024 * 1024), ("replay", 24 * 1024 * 1024)])
def test_public_transport_keeps_media_json_and_size_errors(app, operation, limit):
    path = "/api/transaction-review/" + operation
    assert request(app, "POST", path, content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    for body in (b"{", b"{}", b" " * limit):
        bad = request(app, "POST", path, content=body, headers={"Content-Type": "application/json"})
        assert bad.status_code == 422 and set(bad.json()) == {"detail"}
    too_large = request(app, "POST", path, content=b" " * (limit + 1), headers={"Content-Type": "application/json"})
    assert too_large.status_code == 413


@pytest.mark.parametrize("operation", ["analyze", "replay"])
def test_public_read_only_423_precedes_body_stream(app, monkeypatch, operation):
    from starlette.requests import Request
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")

    async def unreadable(_self):
        raise AssertionError("Public read-only route consumed the body")
        yield b""

    monkeypatch.setattr(Request, "stream", unreadable)
    response = request(app, "POST", "/api/transaction-review/" + operation, content=b"invalid", headers={"Content-Type": "text/plain"})
    assert response.status_code == 423
