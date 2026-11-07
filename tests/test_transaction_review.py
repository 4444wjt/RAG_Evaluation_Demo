"""Hand-checked transaction rules, evidence checks and complete bundle replay."""
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def core():
    assert importlib.util.find_spec("backend.core.transaction_review") is not None, "Transaction-review core is missing"
    module = importlib.import_module("backend.core.transaction_review")
    assert Path(module.__file__).resolve() == ROOT / "backend/core/transaction_review.py"
    return module


def row(ident="t1", timestamp="2026-11-01T10:00:00+08:00", customer="甲", terminal="m1", amount="12000.00"):
    return {"transaction_id": ident, "timestamp": timestamp, "customer_id": customer,
            "terminal_id": terminal, "amount": amount}


def request(rows=None, **changes):
    return {"schemaVersion": 1, "currency": "CNY", "transactions": rows if rows is not None else [row()],
            "rules": {"highAmount": "10000.00", "windowMinutes": 10, "minTransactions": 3},
            "claims": [], **changes}


def check(field, value, evidence=None, ident="t1"):
    result = {"transaction_id": ident, "field": field, "value": value}
    if evidence is not None:
        result["evidence_ids"] = evidence
    return result


def bundle(payload=None, decisions=None):
    payload = request() if payload is None else payload
    return {"schema": "TransactionReviewBundleV1", "request": payload,
            "report": core().analyze_request(payload), "decisions": [] if decisions is None else decisions}


@pytest.mark.parametrize("amount,flagged", [("0", False), ("0.10", False), ("9999.99", False), ("10000", True)])
def test_high_amount_threshold_includes_equality_and_preserves_zero(amount, flagged):
    report = core().analyze_request(request([row(amount=amount)]))
    assert report["status"] == ("review" if flagged else "no_signal")
    assert report["summary"]["totalTransactions"] == 1
    assert report["summary"]["flaggedTransactions"] == int(flagged)
    if flagged:
        case = report["cases"][0]
        assert case["id"] == "t1" and case["transaction"]["amount"] == "10000.00"
        assert [signal["rule"] for signal in case["signals"]] == ["high_amount"]
        assert case["evidenceStatus"] == "consistent"
        assert len(case["claims"]) == 6 and len(report["claimChecks"]) == 6


def test_windows_include_exact_ten_minutes_and_all_equal_time_rows_without_future_or_foreign_rows():
    rows = [row("z", "2026-11-01T02:10:00Z", amount="0.30"),
            row("past", "2026-11-01T02:00:00Z", amount="0.10"),
            row("old", "2026-11-01T01:59:59Z", amount="99"),
            row("a", "2026-11-01T10:10:00+08:00", amount="0.20"),
            row("foreign", "2026-11-01T02:10:00Z", customer="乙", amount="88"),
            row("future", "2026-11-01T02:10:01Z", amount="0.40")]
    report = core().analyze_request(request(rows))
    reverse = core().analyze_request(request(list(reversed(rows))))
    assert report == reverse
    cases = {case["id"]: case for case in report["cases"]}
    assert set(cases) == {"a", "z", "future"}
    assert cases["a"]["window"] == {"start": "2026-11-01T02:00:00Z", "end": "2026-11-01T02:10:00Z",
        "evidence_ref": {"group_id": "g0002", "start": 1, "end": 4}, "count": 3, "total": "0.60"}
    assert cases["z"]["window"] == cases["a"]["window"]
    assert cases["future"]["window"]["evidence_ref"] == {"group_id": "g0002", "start": 2, "end": 5}
    assert cases["future"]["window"]["total"] == "0.90"
    assert report["summary"]["totalTransactions"] == 6
    assert report["summary"]["flaggedTransactions"] == 3


def test_timestamp_offsets_money_and_omitted_defaults_share_one_canonical_binding():
    raw = {"schemaVersion": 1, "currency": "CNY", "transactions": [row(amount=12000)]}
    report = core().analyze_request(raw)
    equivalent = request([row(timestamp="2026-11-01T02:00:00Z", amount="12000.0")])
    assert report == core().analyze_request(equivalent)
    normalized = request([row(timestamp="2026-11-01T02:00:00Z", amount="12000.00")])
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert report["provenance"]["requestSha256"] == hashlib.sha256(canonical).hexdigest()
    assert report["engineVersion"] == "transaction-review-v1.1"
    assert set(report["provenance"]["codeSha256"]) == {
        "backend/core/transaction_review.py", "backend/api/transaction_review.py", "scripts/transaction_review.py"}
    for name, digest in report["provenance"]["codeSha256"].items():
        assert digest == hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


@pytest.mark.parametrize("current,other", [
    ("2026-11-01T02:10:00Z", "2026-11-01T02:10:00.0000001Z"),
    ("2026-11-01T02:10:00.0000001Z", "2026-11-01T02:00:00Z"),
])
def test_submicrosecond_times_reject_future_and_left_boundary_rounding(current, other):
    rows = [row("now", current, amount="1.00"), row("other", other, amount="2.00")]
    with pytest.raises(core().TransactionReviewError, match="6|微秒"):
        core().analyze_request(request(rows))


@pytest.mark.parametrize("timestamp", [
    "2026-11-01T02:10:00.0000000Z",
    "2026-11-01T02:10:00,0000001Z",
    "2026-11-01T02:10:00+00:00:00.0000001",
])
def test_timestamp_precision_limit_covers_fraction_spellings_and_offset(timestamp):
    with pytest.raises(core().TransactionReviewError, match="6|微秒"):
        core().analyze_request(request([row(timestamp=timestamp)]))


def test_submicrosecond_claim_is_not_silently_changed_into_a_matching_timestamp():
    claim = check("timestamp", "2026-11-01T10:00:00.0000001+08:00", ["t1"])
    with pytest.raises(core().TransactionReviewError, match="6|微秒"):
        core().analyze_request(request(claims=[claim]))


def test_supported_microseconds_preserve_future_and_exact_left_boundary():
    rows = [row("before", "2026-11-01T01:59:59.999999Z", amount="1.00"),
            row("boundary", "2026-11-01T02:00:00Z", amount="2.00"),
            row("now", "2026-11-01T02:10:00Z", amount="3.00"),
            row("future", "2026-11-01T02:10:00.000001Z", amount="4.00")]
    report = core().analyze_request(request(rows, rules={"highAmount": "10000.00", "windowMinutes": 10, "minTransactions": 2}))
    cases = {case["id"]: case for case in report["cases"]}
    assert cases["now"]["window"]["count"] == 2 and cases["now"]["window"]["total"] == "5.00"
    assert cases["future"]["window"]["count"] == 2 and cases["future"]["window"]["total"] == "7.00"
    assert cases["future"]["transaction"]["timestamp"] == "2026-11-01T02:10:00.000001Z"


@pytest.mark.parametrize("is_bundle", [False, True])
def test_extreme_json_exponent_raises_transaction_error_at_strict_reader(is_bundle):
    with pytest.raises(core().TransactionReviewError):
        core().load_json_bytes(b'{"amount":1e99999999999999999999999999999}', bundle=is_bundle)


@pytest.mark.parametrize("claim,status,reason", [
    (check("amount", "12000.00", ["t1"]), "consistent", "MATCH"),
    (check("amount", "12001.00", ["t1"]), "inconsistent", "VALUE_MISMATCH"),
    (check("amount", "12000.00"), "review", "EVIDENCE_MISSING"),
    (check("amount", "12000.00", []), "review", "EVIDENCE_MISSING"),
    (check("amount", "12000.00", ["missing"]), "review", "EVIDENCE_UNKNOWN"),
    (check("customer_id", "甲", ["t1"]), "consistent", "MATCH"),
    (check("customer_id", "乙", ["t1"]), "inconsistent", "VALUE_MISMATCH"),
    (check("terminal_id", "other", ["t1"]), "inconsistent", "VALUE_MISMATCH"),
    (check("timestamp", "2026-11-01T10:00:00+08:00", ["t1"]), "consistent", "MATCH"),
    (check("timestamp", "2026-11-01T02:00:01Z", ["t1"]), "inconsistent", "VALUE_MISMATCH"),
    (check("fraud_confirmed", True, ["t1"]), "review", "FRAUD_NOT_PROVABLE"),
    (check("fraud_confirmed", False, ["t1"]), "review", "FRAUD_NOT_PROVABLE"),
    (check("amount", "12000.00", ["t1"], ident="not-provided"), "review", "TRANSACTION_UNKNOWN"),
])
def test_caller_claim_is_checked_without_replacing_it_with_a_true_fact(claim, status, reason):
    report = core().analyze_request(request(claims=[claim]))
    result = report["claimChecks"][0]
    assert result["status"] == status and reason in result["reasons"]
    assert len(report["claimChecks"]) == 1
    assert result["value"] == claim["value"] or claim["field"] == "timestamp"
    assert report["summary"][status + "Claims"] == 1
    if claim["transaction_id"] == "t1":
        assert report["cases"][0]["claims"] == [result]
        assert report["cases"][0]["evidenceStatus"] == status


@pytest.mark.parametrize("evidence,status,reason", [
    (["t1", "t2", "t3"], "consistent", "MATCH"),
    (["t3"], "review", "EVIDENCE_INCOMPLETE"),
    (["t1", "t2", "t3", "foreign"], "inconsistent", "EVIDENCE_OUTSIDE_SCOPE"),
    (["t1", "t2", "t3", "future"], "inconsistent", "EVIDENCE_OUTSIDE_SCOPE"),
    (["t1", "t2", "t3", "old"], "inconsistent", "EVIDENCE_OUTSIDE_SCOPE"),
])
def test_window_evidence_must_cover_exact_needed_records(evidence, status, reason):
    rows = [row("old", "2026-11-01T01:59:59Z", amount="1"),
            row("t1", "2026-11-01T02:00:00Z", amount="0.10"),
            row("t2", "2026-11-01T02:05:00Z", amount="0.20"),
            row("t3", "2026-11-01T02:10:00Z", amount="0.30"),
            row("foreign", "2026-11-01T02:10:00Z", customer="乙", amount="50"),
            row("future", "2026-11-01T02:10:01Z", amount="100")]
    report = core().analyze_request(request(rows, claims=[check("window_total", "0.60", evidence, "t3")]))
    result = report["claimChecks"][0]
    assert result["status"] == status and reason in result["reasons"]
    assert result["expected"] == "0.60"
    assert result["required_evidence_ref"] == {"group_id": "g0002", "start": 1, "end": 4}


def test_wrong_window_count_and_known_wrong_evidence_are_inconsistent_even_without_complete_evidence():
    report = core().analyze_request(request([row(), row("other", customer="乙")], claims=[
        check("window_count", 99, []), check("amount", "12000", ["other"])]))
    assert [item["status"] for item in report["claimChecks"]] == ["inconsistent", "inconsistent"]
    assert report["summary"]["inconsistentClaims"] == 2


def test_unflagged_explicit_unresolved_claim_keeps_review_status():
    report = core().analyze_request(request([row(amount="1")], claims=[check("amount", "2", ["t1"])]))
    assert report["status"] == "review" and report["cases"] == []
    assert report["claimChecks"][0]["status"] == "inconsistent"


@pytest.mark.parametrize("payload", [
    None, [], True, {}, request(schemaVersion=2), request(schemaVersion=True),
    request(currency=""), request(currency="CNY/USD"), request(transactions=[]),
    request(transactions=[row(), row()]), request(transactions=[{**row(), "currency": "USD"}]),
    request(transactions=[{**row(), "label": 1}]), request(transactions=[{**row(), "unknown": "ignored?"}]),
    request(transactions=[{k: v for k, v in row().items() if k != "amount"}]),
    request(transactions=[row(timestamp="2026-11-01T10:00:00")]), request(transactions=[row(timestamp="invalid")]),
    request(transactions=[row(amount="-0.01")]), request(transactions=[row(amount="0.001")]),
    request(transactions=[row(amount="NaN")]), request(transactions=[row(amount="Infinity")]),
    request(transactions=[row(amount=float("inf"))]), request(transactions=[row(amount=True)]),
    request(transactions=[row(customer=" ")]), request(transactions=[row(terminal=None)]),
    request(rules={"highAmount": "1"}), request(rules={"highAmount": "1", "windowMinutes": True, "minTransactions": 3}),
    request(rules={"highAmount": "1", "windowMinutes": 0, "minTransactions": 3}),
    request(rules={"highAmount": "1", "windowMinutes": 10, "minTransactions": 0}),
    request(rules={"highAmount": "1", "windowMinutes": 10, "minTransactions": 3, "probability": 0.9}),
    request(claims=[check("unknown", "x", ["t1"])]),
    request(claims=[check("amount", "1"), check("amount", "2")]),
    request(claims=[check("amount", "1", ["t1", "t1"])]),
    request(claims=[check("amount", True, ["t1"])]),
    request(claims=[check("window_count", True, ["t1"])]),
    request(claims=[{**check("amount", "1"), "free_text": "reviewed?"}]),
    request(extra="unknown"),
])
def test_invalid_schema_never_silently_repairs_or_ignores_fields(payload):
    module = core()
    with pytest.raises(module.TransactionReviewError):
        module.analyze_request(payload)


def test_row_and_utf8_size_caps_reject_instead_of_truncating():
    module = core()
    payload = request([row(str(i), customer=str(i), amount="0") for i in range(5000)])
    report = module.analyze_request(payload)
    assert report["summary"]["totalTransactions"] == 5000
    payload["transactions"].append(row("too-many", amount="0"))
    with pytest.raises(module.TransactionReviewError, match="5000"):
        module.analyze_request(payload)
    with pytest.raises(module.TransactionReviewError, match="2 MiB"):
        module.analyze_request(request([row(customer="甲" * 750000)]))


def test_valid_large_decimal_amount_does_not_depend_on_python_integer_string_limits():
    amount = "9" * 5000 + ".01"
    report = core().analyze_request(request([row(amount=amount)]))
    assert report["cases"][0]["window"]["total"] == amount


def test_compact_request_within_two_mib_is_not_rejected_by_added_serialization_spaces():
    payload = request([row(amount="0", customer="x")])
    base = len(json.dumps(payload, separators=(",", ":")).encode())
    payload["transactions"][0]["customer_id"] = "x" * (2 * 1024 * 1024 - base - 1)
    assert len(json.dumps(payload, separators=(",", ":")).encode()) < 2 * 1024 * 1024
    assert core().analyze_request(payload)["summary"]["totalTransactions"] == 1


@pytest.mark.parametrize("decision", ["pending", "escalate", "dismiss", "need_more"])
def test_replay_recomputes_report_and_preserves_review_decisions(decision):
    original = bundle(decisions=[{"transaction_id": "t1", "decision": decision, "reason": "人工核对，继续调查"}])
    verified = core().replay_bundle(original)
    assert verified == {**original, "replayed": True}
    assert core().replay_bundle(verified) == verified


@pytest.mark.parametrize("mutate", [
    lambda b: b["report"]["summary"].update(flaggedTransactions=0),
    lambda b: b["report"]["cases"][0]["transaction"].update(amount="1.00"),
    lambda b: b["report"]["cases"][0]["window"].update(total="1.00"),
    lambda b: b["report"]["claimChecks"][0].update(status="review"),
    lambda b: b["report"]["provenance"].update(requestSha256="0" * 64),
    lambda b: b["report"]["provenance"]["codeSha256"].update({"backend/core/transaction_review.py": "0" * 64}),
    lambda b: b["report"].update(engineVersion="old"),
    lambda b: b["report"].update(extra=True),
    lambda b: b["request"]["transactions"][0].update(amount="1.00"),
    lambda b: b.update(schema="OtherBundle"),
    lambda b: b.update(extra=True),
])
def test_replay_refuses_forged_facts_bindings_versions_or_bundle_fields(mutate):
    original = bundle()
    mutate(original)
    with pytest.raises(core().TransactionReviewError):
        core().replay_bundle(original)


def test_replay_does_not_trust_a_digest_recomputed_from_forged_report():
    original = bundle()
    original["report"]["cases"][0]["transaction"]["amount"] = "1.00"
    digest = hashlib.sha256(json.dumps(original["report"], sort_keys=True).encode()).hexdigest()
    original["report"]["provenance"]["reportSha256"] = digest
    with pytest.raises(core().TransactionReviewError):
        core().replay_bundle(original)


def test_replay_ignores_only_the_top_level_replayed_marker():
    original = bundle()
    original["replayed"] = False
    assert core().replay_bundle(original) == {**original, "replayed": True}


@pytest.mark.parametrize("reference,status", [
    ({"group_id": "g0002", "start": 0, "end": 1}, "consistent"),
    ({"group_id": "g0002", "start": 0, "end": 0}, "review"),
    ({"group_id": "g0001", "start": 0, "end": 1}, "inconsistent"),
    ({"group_id": "g0002", "start": 0, "end": 2}, "inconsistent"),
])
def test_evidence_references_check_actual_input_scope(reference, status):
    rows = [row(), row("future", "2026-11-01T02:01:00Z"), row("foreign", customer="乙")]
    claim = {**check("window_count", 1), "evidence_ref": reference}
    result = core().analyze_request(request(rows, claims=[claim]))["claimChecks"][0]
    assert result["status"] == status
    assert result["evidence_ref"] == reference and "evidence_ids" not in result


@pytest.mark.parametrize("reference", [
    {"group_id": "missing", "start": 0, "end": 1},
    {"group_id": "g0001", "start": -1, "end": 1},
    {"group_id": "g0001", "start": 1, "end": 0},
    {"group_id": "g0001", "start": 0, "end": 2},
    {"group_id": "g0001", "start": False, "end": 1},
    {"group_id": "g0001", "start": 0, "end": 1, "extra": True},
])
def test_malformed_reference_ranges_are_schema_errors(reference):
    with pytest.raises(core().TransactionReviewError):
        core().analyze_request(request(claims=[{**check("amount", "12000"), "evidence_ref": reference}]))


def test_claim_cannot_supply_both_reference_and_explicit_ids():
    with pytest.raises(core().TransactionReviewError):
        core().analyze_request(request(claims=[{**check("amount", "12000", ["t1"]),
            "evidence_ref": {"group_id": "g0001", "start": 0, "end": 1}}]))


@pytest.mark.parametrize("simultaneous", [True, False])
def test_5000_flagged_rows_have_bounded_shared_evidence_and_exact_window_arithmetic(simultaneous, tmp_path):
    from datetime import datetime, timedelta, timezone
    epoch = datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows = [row(f"t{i:04}", (epoch + timedelta(seconds=0 if simultaneous else i)).isoformat(), amount="0.01")
            for i in range(5000)]
    payload = request(rows)
    report = core().analyze_request(payload)
    assert report["summary"]["totalTransactions"] == 5000
    assert report["summary"]["flaggedTransactions"] == (5000 if simultaneous else 4998)
    assert len(report["evidenceIndex"]) == 1
    assert len(report["evidenceIndex"]["g0001"]["transaction_ids"]) == 5000
    case = report["cases"][-1]
    assert case["window"]["count"] == (5000 if simultaneous else 601)
    assert case["window"]["total"] == ("50.00" if simultaneous else "6.01")
    assert len(json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) < 15 * 1024 * 1024
    original = {"schema": "TransactionReviewBundleV1", "request": payload, "report": report, "decisions": []}
    assert core().replay_bundle(original)["replayed"] is True
    if simultaneous:
        source, output = tmp_path / "5000-bundle.json", tmp_path / "5000-verified.json"
        source.write_text(json.dumps(original, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        run = subprocess.run([sys.executable, str(ROOT / "scripts/transaction_review.py"), "replay", str(source),
                              "--out", str(output)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert run.returncode == 0, run.stderr
        assert output.stat().st_size < 24 * 1024 * 1024
        verified = core().load_json_bytes(output.read_bytes(), bundle=True)
        assert core().replay_bundle(verified)["report"] == report


@pytest.mark.parametrize("decisions", [
    [{"transaction_id": "t1", "decision": "safe", "reason": "x"}],
    [{"transaction_id": "t1", "decision": "dismiss", "reason": "  "}],
    [{"transaction_id": "missing", "decision": "pending", "reason": ""}],
    [{"transaction_id": "t1", "decision": "pending", "reason": ""}] * 2,
    [{"transaction_id": "t1", "decision": "pending", "reason": "", "extra": True}],
    [{"transaction_id": "t1", "decision": "pending", "reason": None}], {},
])
def test_replay_rejects_invalid_or_stale_decisions(decisions):
    with pytest.raises(core().TransactionReviewError):
        core().replay_bundle(bundle(decisions=decisions))


def test_cli_real_analyze_and_replay_use_same_core_and_only_explicit_output(tmp_path):
    payload = request()
    source = tmp_path / "request.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    script = ROOT / "scripts/transaction_review.py"
    stdout_run = subprocess.run([sys.executable, str(script), "analyze", str(source)], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert stdout_run.returncode == 0, stdout_run.stderr
    assert json.loads(stdout_run.stdout) == core().analyze_request(payload)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["request.json"]
    source.write_text(json.dumps(bundle(payload)), encoding="utf-8")
    output = tmp_path / "verified.json"
    replay_run = subprocess.run([sys.executable, str(script), "replay", str(source), "--out", str(output)], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert replay_run.returncode == 0, replay_run.stderr
    assert replay_run.stdout == "" and json.loads(output.read_text(encoding="utf-8"))["replayed"] is True


def test_cli_replay_exports_numeric_money_as_lossless_strings(tmp_path):
    payload = request([row(amount=12000.25)])
    original = bundle(payload)
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(original), encoding="utf-8")
    run = subprocess.run([sys.executable, str(ROOT / "scripts/transaction_review.py"), "replay", str(source)],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stderr
    verified = json.loads(run.stdout)
    assert verified["request"]["transactions"][0]["amount"] == "12000.25"
    assert core().replay_bundle(verified)["replayed"] is True
    assert payload["transactions"][0]["amount"] == 12000.25


@pytest.mark.parametrize("raw", [b"{", b"\xff", b'{"schemaVersion":1,"schemaVersion":1}', b'{"amount":NaN}'])
def test_cli_invalid_input_exits_two_with_useful_stderr_and_no_report(tmp_path, raw):
    source, output = tmp_path / "bad.json", tmp_path / "report.json"
    source.write_bytes(raw)
    run = subprocess.run([sys.executable, str(ROOT / "scripts/transaction_review.py"), "analyze", str(source),
                          "--out", str(output)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert run.returncode == 2 and run.stderr.strip() and run.stdout == ""
    assert not output.exists()


@pytest.mark.parametrize("operation", ["analyze", "replay"])
def test_cli_extreme_exponent_exits_two_without_report_or_traceback(tmp_path, operation):
    invalid = json.dumps(request([row(amount="EXTREME_AMOUNT")])).replace(
        '"EXTREME_AMOUNT"', '1e99999999999999999999999999999').encode()
    raw = invalid if operation == "analyze" else b'{"schema":"TransactionReviewBundleV1","request":' + invalid + b',"report":{},"decisions":[]}'
    source, output = tmp_path / "extreme.json", tmp_path / "report.json"
    source.write_bytes(raw)
    run = subprocess.run([sys.executable, str(ROOT / "scripts/transaction_review.py"), operation, str(source),
                          "--out", str(output)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert run.returncode == 2 and run.stderr.strip() and run.stdout == ""
    assert "Traceback" not in run.stderr and not output.exists()
