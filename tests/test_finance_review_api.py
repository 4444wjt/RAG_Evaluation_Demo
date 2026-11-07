"""Finance transport keeps the existing offline diagnostic contract intact."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from backend.api.app import create_app
from scripts import statute_diagnostic as diagnostic


# Windows' event-loop socketpair is created before the suite's network guard.
_LOOP = asyncio.new_event_loop()
ROOT = Path(__file__).resolve().parents[1]
SOURCE = "第一条　资料窗口应当在7个工作日内答复申请。"


def request(**changes):
    return {
        "schemaVersion": 1, "sourceFormat": "article-lines-v1", "engine": "extended",
        "source": SOURCE, "answer": "根据第一条，资料窗口应当在14个工作日内答复申请。",
        "contexts": [SOURCE], "question": "", **changes,
    }


def post(raw, *, content_type="application/json"):
    async def invoke():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver",
        ) as client:
            return await client.post("/finance-review", content=raw,
                                     headers={"Content-Type": content_type})
    return _LOOP.run_until_complete(invoke())


def teardown_module():
    _LOOP.close()


@pytest.mark.parametrize("changes,status,flagged,codes", [
    ({}, "findings", True, ["QUANTITY_UNSUPPORTED"]),
    ({"answer": "根据第一条，资料窗口应当在7个工作日内答复申请。"}, "no_error_found", False, []),
    ({"source": "没有条号的原文。"}, "review", None, []),
    ({"contexts": []}, "invalid_input", None, []),
])
@pytest.mark.parametrize("engine", ["base", "extended"])
def test_real_http_preserves_status_and_unmodified_core_report(changes, status, flagged, codes, engine):
    payload = request(engine=engine, **changes)
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    response = post(raw)
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == status and report["flagged"] is flagged
    expected = diagnostic.analyze_request(payload)
    assert {key: value for key, value in report.items() if key != "provenance"} == {
        key: value for key, value in expected.items() if key != "provenance"
    }
    if report["result"] is not None:
        result = report["result"] if engine == "base" else report["result"]["original"]
        assert [finding["code"] for finding in result["findings"]] == codes
    assert report["provenance"]["inputSha256"] == hashlib.sha256(raw).hexdigest()
    assert set(report["provenance"]["codeSha256"]) == {
        "scripts/statute_diagnostic.py", "backend/core/statute_check.py",
        "backend/core/statute_locate.py", "backend/api/finance_review.py",
    }
    for name, digest in report["provenance"]["codeSha256"].items():
        assert digest == hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


@pytest.mark.parametrize("raw", [
    b'{"schemaVersion":1,"schemaVersion":1}', b'{"source":NaN}', b"\xff", b"{",
])
def test_strict_json_rejection_retains_raw_byte_binding(raw):
    response = post(raw)
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "invalid_input" and report["result"] is None
    assert report["diagnostics"][0]["code"] == "INPUT_JSON_INVALID"
    assert report["provenance"]["inputSha256"] == hashlib.sha256(raw).hexdigest()


def test_raw_utf8_size_limit_and_content_type():
    assert post(b" " * (1024 * 1024 + 1)).status_code == 413
    assert post(b" " * (1024 * 1024)).status_code == 200
    assert post(b"{}", content_type="text/plain").status_code == 415


def test_read_only_refuses_before_reading_or_analyzing(monkeypatch):
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")
    response = post(b"invalid", content_type="text/plain")
    assert response.status_code == 423


def test_scope_exclusion_and_missing_structure_are_not_promoted_to_support():
    response = post(json.dumps(request(answer="这名客户一定符合全部业务准入条件。")).encode("utf-8"))
    report = response.json()
    assert report["status"] == "no_error_found" and report["flagged"] is False
    assert report["structureCheck"]["status"] == "not_provided"
    assert "supported" not in report and "gate_passed" not in report


@pytest.mark.parametrize("example_id,status,codes", [
    ("normal", "no_error_found", []),
    ("period-error", "findings", ["QUANTITY_UNSUPPORTED"]),
    ("missing-rate", "findings", ["QUANTITY_UNSUPPORTED"]),
])
def test_exposed_development_examples_use_real_base_rules(example_id, status, codes):
    fixtures = json.loads((ROOT / "frontend/src/fixtures/finance-examples.json").read_text(encoding="utf-8"))
    example = next(item for item in fixtures["examples"] if item["id"] == example_id)
    source = example.get("source", fixtures["source"])
    payload = request(engine="base", source=source, contexts=[source], answer=example["answer"])
    response = post(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert response.status_code == 200
    report = response.json()
    assert report["engine"] == "base" and report["status"] == status
    assert [item["code"] for item in report["result"]["findings"]] == codes
