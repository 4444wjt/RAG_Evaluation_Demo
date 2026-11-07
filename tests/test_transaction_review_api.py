"""Real in-memory API transport for deterministic transaction review."""
import asyncio
import importlib
import json

import httpx
import pytest

from backend.api.app import create_app


_LOOP = asyncio.new_event_loop()


def payload():
    return {"schemaVersion": 1, "currency": "CNY", "transactions": [
        {"transaction_id": "t1", "timestamp": "2026-11-01T10:00:00+08:00",
         "customer_id": "甲", "terminal_id": "m1", "amount": "10000.00"}],
        "rules": {"highAmount": "10000.00", "windowMinutes": 10, "minTransactions": 3}, "claims": []}


def post(raw, path="analyze", content_type="application/json"):
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
            return await client.post("/transaction-review/" + path, content=raw,
                                     headers={"Content-Type": content_type})
    return _LOOP.run_until_complete(invoke())


def teardown_module():
    _LOOP.close()


def test_api_analyze_and_replay_equal_real_core_including_provenance():
    source = payload()
    response = post(json.dumps(source, ensure_ascii=False).encode("utf-8"))
    assert response.status_code == 200, response.text
    module = importlib.import_module("backend.core.transaction_review")
    assert response.json() == module.analyze_request(source)
    original = {"schema": "TransactionReviewBundleV1", "request": source, "report": response.json(),
                "decisions": [{"transaction_id": "t1", "decision": "dismiss", "reason": "已联系客户，正常大额。"}]}
    replayed = post(json.dumps(original, ensure_ascii=False).encode("utf-8"), "replay")
    assert replayed.status_code == 200 and replayed.json() == {**original, "replayed": True}
    original["report"]["summary"]["flaggedTransactions"] = 0
    tampered = post(json.dumps(original).encode(), "replay")
    assert tampered.status_code == 422 and tampered.json()["detail"]


@pytest.mark.parametrize("raw", [
    b"{", b"\xff", b'{"schemaVersion":1,"schemaVersion":1}', b'{"amount":NaN}',
    b'{"amount":Infinity}', b"[]", b"true", b"null", b"{}",
])
@pytest.mark.parametrize("operation", ["analyze", "replay"])
def test_strict_json_and_schema_fail_with_422(raw, operation):
    response = post(raw, operation)
    assert response.status_code == 422 and response.json()["detail"]


@pytest.mark.parametrize("operation", ["analyze", "replay"])
def test_media_type_raw_request_limit_and_no_truncation(operation):
    limit = (24 if operation == "replay" else 2) * 1024 * 1024
    assert post(b"{}", operation, "text/plain").status_code == 415
    assert post(b" " * (limit + 1), operation).status_code == 413
    assert post(b" " * limit, operation).status_code == 422


def test_row_limit_is_413_and_unknown_columns_are_422():
    source = payload()
    source["transactions"] = [{**source["transactions"][0], "transaction_id": str(i)} for i in range(5001)]
    assert post(json.dumps(source).encode()).status_code == 413
    source = payload()
    source["transactions"][0]["label"] = "fraud"
    assert post(json.dumps(source).encode()).status_code == 422


def test_numeric_json_money_preserves_decimal_cents_through_replay():
    raw = json.dumps(payload()).replace('"10000.00"', '12345678901234567890.01').encode()
    response = post(raw)
    assert response.status_code == 200
    report = response.json()
    assert report["cases"][0]["transaction"]["amount"] == "12345678901234567890.01"
    raw_bundle = b'{"schema":"TransactionReviewBundleV1","request":' + raw + b',"report":' + json.dumps(report).encode() + b',"decisions":[]}'
    verified = post(raw_bundle, "replay")
    assert verified.status_code == 200, verified.text
    reopened = post(verified.content, "replay")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["report"] == report


@pytest.mark.parametrize("operation", ["analyze", "replay"])
def test_extreme_exponent_json_returns_422_without_report_or_traceback(operation):
    source = payload()
    source["transactions"][0]["amount"] = "EXTREME_AMOUNT"
    invalid = json.dumps(source).replace('"EXTREME_AMOUNT"', '1e99999999999999999999999999999').encode()
    raw = invalid if operation == "analyze" else b'{"schema":"TransactionReviewBundleV1","request":' + invalid + b',"report":{},"decisions":[]}'
    response = post(raw, operation)
    assert response.status_code == 422 and set(response.json()) == {"detail"}
    assert response.json()["detail"] and "Traceback" not in response.text


@pytest.mark.parametrize("timestamp", ["2026-11-01T02:10:00.0000001Z", "2026-11-01T02:10:00,0000001Z"])
def test_submicrosecond_timestamp_returns_actionable_422(timestamp):
    source = payload()
    source["transactions"][0]["timestamp"] = timestamp
    response = post(json.dumps(source).encode())
    assert response.status_code == 422 and ("6" in response.json()["detail"] or "微秒" in response.json()["detail"])


@pytest.mark.parametrize("operation", ["analyze", "replay"])
def test_read_only_checks_before_body_stream_and_analysis(monkeypatch, operation):
    from starlette.requests import Request
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")

    async def unreadable(_self):
        raise AssertionError("read-only route consumed request body")
        yield b""

    monkeypatch.setattr(Request, "stream", unreadable)
    response = post(b"invalid", operation, "text/plain")
    assert response.status_code == 423
