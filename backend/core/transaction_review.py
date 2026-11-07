"""Deterministic demo transaction signals and field-level evidence checks.

No model, network, persistence or truth labels are used. Money is calculated in
integer minor units after Decimal validation. Evidence references are zero-based
half-open slices of the report's deterministic per-customer evidenceIndex.

requestSha256 hashes the normalized request (all rows retained, UTC timestamps,
two-decimal money strings, explicit defaults) serialized as UTF-8 with
json.dumps(ensure_ascii=False, sort_keys=True, separators=(',', ':'),
allow_nan=False). Claims keep their authored order; transaction rows are sorted
by UTC time then ID. Code hashes bind only the three named file bytes, and are
not signatures or evidence that the input is authentic.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = 1
ENGINE_VERSION = "transaction-review-v1.1"
BUNDLE_SCHEMA = "TransactionReviewBundleV1"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 24 * 1024 * 1024
MAX_TRANSACTIONS = 5000
DEFAULT_RULES = {"highAmount": "10000.00", "windowMinutes": 10, "minTransactions": 3}
CODE_PATHS = ("backend/core/transaction_review.py", "backend/api/transaction_review.py", "scripts/transaction_review.py")
CLAIM_FIELDS = ("amount", "customer_id", "terminal_id", "timestamp", "window_count", "window_total", "fraud_confirmed")
LIMITATIONS = [
    "仅使用大额与同主体短时笔数两条可配置演示规则；线索不是概率、训练模型结论或欺诈事实认定。",
    "未触发线索不等于安全；未检查普通自由文本语义，也不使用标签列。",
    "只核对所列结构化字段和引用交易；一致不证明交易、主体身份或业务来源真实。",
    "人工决定是复核记录，不自动封号、扣款、转账或认定诈骗。",
    "SHA-256 只绑定规范化输入和命名代码字节，不提供数字签名或真实性背书。",
]


class TransactionReviewError(ValueError):
    """Actionable input/replay error; limit errors use HTTP 413, others 422."""

    def __init__(self, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _object(value: Any, name: str, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TransactionReviewError(f"{name} 必须是 JSON 对象。")
    if not required <= value.keys() or value.keys() - required - (optional or set()):
        raise TransactionReviewError(f"{name} 字段缺失或包含未支持字段，请按接口合同提交。")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransactionReviewError(f"{name} 必须是非空字符串。")
    # Keep names and IDs verbatim, including Chinese text; do not execute them.
    return value


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise TransactionReviewError(f"{name} 必须是 >= {minimum} 的整数，布尔值不作数字。")
    return value


def _money(value: Any, name: str, *, nonnegative: bool = True) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise TransactionReviewError(f"{name} 必须是有限十进制金额，最多两位小数。")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise TransactionReviewError(f"{name} 必须是有限十进制金额，最多两位小数。") from None
    if not number.is_finite() or number.as_tuple().exponent < -2 or (nonnegative and number < 0):
        raise TransactionReviewError(f"{name} 必须是非负有限金额，最多两位小数。")
    if number.adjusted() > MAX_REQUEST_BYTES:
        raise TransactionReviewError("金额展开后超过 2 MiB，请缩小输入。", status_code=413)
    return format(number.copy_abs() if number.is_zero() else number, ".2f")


def _minor_units(amount: str) -> int:
    numerator, denominator = Decimal(amount).as_integer_ratio()
    return numerator * 100 // denominator


def _amount_text(cents: int) -> str:
    sign, digits, exponent = Decimal(cents).as_tuple()
    return format(Decimal((sign, digits, exponent - 2)), ".2f")


def _time(value: Any, name: str) -> datetime:
    text = _text(value, name)
    # fromisoformat silently truncates finer-than-microsecond fractions,
    # including offset fractions. Reject before either window boundary moves.
    if re.search(r"[.,][0-9]{7,}", text):
        raise TransactionReviewError(f"{name} 时间精度最多支持微秒（6 位小数秒），请勿提交更高精度。")
    try:
        # A time component and explicit offset are required; date-only/naive
        # timestamps must never be interpreted in the host machine's timezone.
        if "T" not in text and "t" not in text:
            raise ValueError
        result = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise TransactionReviewError(f"{name} 必须是带时区的有效 ISO 8601 日期时间。") from None


def _time_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise TransactionReviewError("需要可编码为 UTF-8 的有效 JSON，拒绝非有限数值与非法类型。") from None


def _bounded_input(value: Any, maximum: int) -> None:
    # Decimal is used by the strict JSON reader to avoid binary float rounding.
    # This initial check also bounds direct Python callers before normalization.
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
                         default=lambda item: str(item) if isinstance(item, Decimal) else _unsupported_json()).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise TransactionReviewError("需要可编码为 UTF-8 的有效 JSON，拒绝非有限数值与非法类型。") from None
    if len(raw) > maximum:
        limit = "2 MiB" if maximum == MAX_REQUEST_BYTES else "24 MiB"
        raise TransactionReviewError(f"完整输入超过 {limit}，请缩小输入；没有截断或执行分析。", status_code=413)


def _unsupported_json():
    raise TypeError("unsupported JSON value")


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransactionReviewError("JSON 含重复键，请删除重复字段。")
        result[key] = value
    return result


def _reject_constant(_value):
    raise TransactionReviewError("JSON 不接受 NaN 或 Infinity。")


def load_json_bytes(raw: bytes, *, bundle: bool = False) -> Any:
    """Read bounded strict UTF-8 JSON for the API/CLI, preserving decimal input."""
    maximum = MAX_BUNDLE_BYTES if bundle else MAX_REQUEST_BYTES
    if len(raw) > maximum:
        label = "24 MiB" if bundle else "2 MiB"
        raise TransactionReviewError(f"完整输入超过 {label}，请缩小输入。", status_code=413)
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_strict_pairs,
                          parse_constant=_reject_constant, parse_float=Decimal)
    except TransactionReviewError:
        raise
    except (UnicodeError, ValueError, RecursionError, InvalidOperation):
        raise TransactionReviewError("需要有效 UTF-8 JSON，拒绝重复键、非有限数值或超出十进制解析范围的数字。") from None


def _normalize_request(payload: Any) -> dict:
    _object(payload, "request", {"schemaVersion", "currency", "transactions"}, {"rules", "claims"})
    _bounded_input(payload, MAX_REQUEST_BYTES)
    if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != SCHEMA_VERSION:
        raise TransactionReviewError("只支持 schemaVersion=1，请使用当前请求格式。")
    currency = payload["currency"]
    if not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
        raise TransactionReviewError("currency 必须是单一的三位大写币种代码，例如 CNY；不执行换汇。")
    records = payload["transactions"]
    if not isinstance(records, list) or not records:
        raise TransactionReviewError("transactions 必须是至少一笔交易的数组。")
    if len(records) > MAX_TRANSACTIONS:
        raise TransactionReviewError("交易上限为 5000 行，请分批提交；没有截断。", status_code=413)
    rows = []
    ids = set()
    for record in records:
        _object(record, "transaction", {"transaction_id", "timestamp", "customer_id", "terminal_id", "amount"})
        ident = _text(record["transaction_id"], "transaction_id")
        if ident in ids:
            raise TransactionReviewError("交易 transaction_id 重复，请使用唯一 ID。")
        ids.add(ident)
        rows.append({"transaction_id": ident, "timestamp": _time_text(_time(record["timestamp"], "timestamp")),
                     "customer_id": _text(record["customer_id"], "customer_id"),
                     "terminal_id": _text(record["terminal_id"], "terminal_id"),
                     "amount": _money(record["amount"], "amount")})
    rows.sort(key=lambda item: (_time(item["timestamp"], "timestamp"), item["transaction_id"]))
    supplied_rules = payload.get("rules", DEFAULT_RULES)
    _object(supplied_rules, "rules", set(DEFAULT_RULES))
    rules = {"highAmount": _money(supplied_rules["highAmount"], "rules.highAmount"),
             "windowMinutes": _integer(supplied_rules["windowMinutes"], "rules.windowMinutes", minimum=1),
             "minTransactions": _integer(supplied_rules["minTransactions"], "rules.minTransactions", minimum=1)}
    supplied_claims = payload.get("claims", [])
    if not isinstance(supplied_claims, list):
        raise TransactionReviewError("claims 必须是数组；不提供时可留空。")
    claims = []
    seen = set()
    for claim in supplied_claims:
        _object(claim, "claim", {"transaction_id", "field", "value"}, {"evidence_ids", "evidence_ref"})
        ident = _text(claim["transaction_id"], "claim.transaction_id")
        field = claim["field"]
        if not isinstance(field, str) or field not in CLAIM_FIELDS:
            raise TransactionReviewError("claim.field 未支持，请使用合同列出的结构化字段。")
        if (ident, field) in seen:
            raise TransactionReviewError("同一交易的同一字段只允许一条 claim，请删除重复项。")
        seen.add((ident, field))
        value = claim["value"]
        if field in ("amount", "window_total"):
            value = _money(value, "claim.value", nonnegative=False)
        elif field == "window_count":
            value = _integer(value, "claim.value")
        elif field == "timestamp":
            value = _time_text(_time(value, "claim.value"))
        elif field == "fraud_confirmed":
            if type(value) is not bool:
                raise TransactionReviewError("fraud_confirmed 的值必须是布尔值，核对结果始终待复核。")
        else:
            value = _text(value, "claim.value")
        normalized = {"transaction_id": ident, "field": field, "value": value}
        if "evidence_ref" in claim:
            if "evidence_ids" in claim:
                raise TransactionReviewError("claim 只能提供 evidence_ids 或 evidence_ref 之一。")
            reference = _object(claim["evidence_ref"], "evidence_ref", {"group_id", "start", "end"})
            normalized["evidence_ref"] = {"group_id": _text(reference["group_id"], "evidence_ref.group_id"),
                "start": _integer(reference["start"], "evidence_ref.start"),
                "end": _integer(reference["end"], "evidence_ref.end")}
        else:
            evidence = claim.get("evidence_ids", [])
            if not isinstance(evidence, list):
                raise TransactionReviewError("evidence_ids 必须是交易 ID 数组。")
            evidence = [_text(item, "evidence_ids") for item in evidence]
            if len(set(evidence)) != len(evidence):
                raise TransactionReviewError("evidence_ids 含重复 ID，请去重。")
            normalized["evidence_ids"] = evidence
        claims.append(normalized)
    normalized = {"schemaVersion": SCHEMA_VERSION, "currency": currency, "transactions": rows, "rules": rules, "claims": claims}
    if len(_canonical(normalized)) > MAX_REQUEST_BYTES:
        raise TransactionReviewError("规范化请求超过 2 MiB，请缩小输入。", status_code=413)
    return normalized


def _evidence_context(rows: list[dict], minutes: int):
    by_customer = defaultdict(list)
    by_id = {}
    for row in rows:
        by_customer[row["customer_id"]].append(row)
        by_id[row["transaction_id"]] = row
    index, locations, windows = {}, {}, {}
    try:
        duration = timedelta(minutes=minutes)
        for group_number, customer in enumerate(sorted(by_customer), 1):
            group_id = f"g{group_number:04d}"
            records = by_customer[customer]
            index[group_id] = {"customer_id": customer, "transaction_ids": [item["transaction_id"] for item in records]}
            times = [_time(item["timestamp"], "timestamp") for item in records]
            totals = [0]
            for position, record in enumerate(records):
                locations[record["transaction_id"]] = (group_id, position)
                totals.append(totals[-1] + _minor_units(record["amount"]))
            same_time = {}
            for record, instant in zip(records, times):
                if instant not in same_time:
                    start_time = instant - duration
                    left, right = bisect_left(times, start_time), bisect_right(times, instant)
                    same_time[instant] = {"start": _time_text(start_time), "end": _time_text(instant),
                        "evidence_ref": {"group_id": group_id, "start": left, "end": right},
                        "count": right - left, "total": _amount_text(totals[right] - totals[left])}
                windows[record["transaction_id"]] = same_time[instant]
    except (OverflowError, ValueError):
        raise TransactionReviewError("时间与窗口超出可计算日期范围，请检查 timestamp 和 windowMinutes。") from None
    return index, locations, windows, by_id


def _validate_reference(reference: dict, index: dict) -> None:
    group = index.get(reference["group_id"])
    if group is None or not 0 <= reference["start"] <= reference["end"] <= len(group["transaction_ids"]):
        raise TransactionReviewError("evidence_ref 的组或区间无效，请针对当前输入重新选择证据。")


def _claim_check(claim: dict, by_id: dict, locations: dict, windows: dict) -> dict:
    result = dict(claim)
    ident, field = claim["transaction_id"], claim["field"]
    if ident not in by_id:
        return {**result, "expected": None, "status": "review", "reasons": ["TRANSACTION_UNKNOWN"]}
    if field == "fraud_confirmed":
        return {**result, "expected": None, "status": "review", "reasons": ["FRAUD_NOT_PROVABLE"]}
    if field in ("window_count", "window_total"):
        window = windows[ident]
        expected = window["count" if field == "window_count" else "total"]
        needed = window["evidence_ref"]
        result["required_evidence_ref"] = needed
    else:
        expected = by_id[ident][field]
        group, position = locations[ident]
        needed = {"group_id": group, "start": position, "end": position + 1}
        result["required_evidence_ids"] = [ident]
    result["expected"] = expected
    inconsistent, review = [], []
    if claim["value"] != expected:
        inconsistent.append("VALUE_MISMATCH")
    if "evidence_ref" in claim:
        supplied = claim["evidence_ref"]
        if supplied["start"] == supplied["end"]:
            review.append("EVIDENCE_MISSING")
        elif (supplied["group_id"] != needed["group_id"] or supplied["start"] < needed["start"]
              or supplied["end"] > needed["end"]):
            inconsistent.append("EVIDENCE_OUTSIDE_SCOPE")
        elif supplied != needed:
            review.append("EVIDENCE_INCOMPLETE")
    else:
        evidence = claim["evidence_ids"]
        if not evidence:
            review.append("EVIDENCE_MISSING")
        else:
            covered = 0
            for evidence_id in evidence:
                location = locations.get(evidence_id)
                if location is None:
                    if "EVIDENCE_UNKNOWN" not in review:
                        review.append("EVIDENCE_UNKNOWN")
                elif location[0] != needed["group_id"] or not needed["start"] <= location[1] < needed["end"]:
                    if "EVIDENCE_OUTSIDE_SCOPE" not in inconsistent:
                        inconsistent.append("EVIDENCE_OUTSIDE_SCOPE")
                else:
                    covered += 1
            if covered < needed["end"] - needed["start"]:
                review.append("EVIDENCE_INCOMPLETE")
    result["status"] = "inconsistent" if inconsistent else "review" if review else "consistent"
    result["reasons"] = inconsistent + review if inconsistent or review else ["MATCH"]
    return result


def analyze_request(payload: dict) -> dict:
    """Analyze every input row; schema failures raise TransactionReviewError."""
    request = _normalize_request(payload)
    rows, rules = request["transactions"], request["rules"]
    index, locations, windows, by_id = _evidence_context(rows, rules["windowMinutes"])
    for claim in request["claims"]:
        if "evidence_ref" in claim:
            _validate_reference(claim["evidence_ref"], index)
    cases = []
    threshold = _minor_units(rules["highAmount"])
    for transaction in rows:
        ident = transaction["transaction_id"]
        window = windows[ident]
        signals = []
        if _minor_units(transaction["amount"]) >= threshold:
            signals.append({"rule": "high_amount", "value": transaction["amount"],
                            "threshold": rules["highAmount"], "unit": request["currency"]})
        if window["count"] >= rules["minTransactions"]:
            signals.append({"rule": "window_count", "value": window["count"],
                            "threshold": rules["minTransactions"], "windowMinutes": rules["windowMinutes"]})
        if signals:
            cases.append({"id": ident, "transaction": transaction, "signals": signals,
                          "window": window, "claims": [], "evidenceStatus": "review"})
    claims = request["claims"]
    if not claims:
        claims = []
        for case in cases:
            transaction, ident = case["transaction"], case["id"]
            for field in CLAIM_FIELDS[:-1]:
                if field in ("window_count", "window_total"):
                    value = case["window"]["count" if field == "window_count" else "total"]
                    claims.append({"transaction_id": ident, "field": field, "value": value,
                                   "evidence_ref": case["window"]["evidence_ref"]})
                else:
                    claims.append({"transaction_id": ident, "field": field, "value": transaction[field], "evidence_ids": [ident]})
    checks = [_claim_check(claim, by_id, locations, windows) for claim in claims]
    by_transaction = defaultdict(list)
    for item in checks:
        by_transaction[item["transaction_id"]].append(item)
    counts = {status: sum(item["status"] == status for item in checks) for status in ("consistent", "inconsistent", "review")}
    for case in cases:
        case_checks = by_transaction[case["id"]]
        case["claims"] = case_checks
        statuses = {item["status"] for item in case_checks}
        case["evidenceStatus"] = "inconsistent" if "inconsistent" in statuses else "review" if not statuses or "review" in statuses else "consistent"
    root = Path(__file__).resolve().parents[2]
    return {"schemaVersion": SCHEMA_VERSION, "engineVersion": ENGINE_VERSION,
        "status": "review" if cases or counts["inconsistent"] or counts["review"] else "no_signal",
        "currency": request["currency"], "rules": rules,
        "summary": {"totalTransactions": len(rows), "flaggedTransactions": len(cases),
                    **{status + "Claims": count for status, count in counts.items()}},
        "evidenceIndex": index, "cases": cases, "claimChecks": checks, "limitations": list(LIMITATIONS),
        "provenance": {"requestSha256": hashlib.sha256(_canonical(request)).hexdigest(),
            "codeSha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in CODE_PATHS}}}


def replay_bundle(payload: dict) -> dict:
    """Recompute the entire report before accepting any imported decision."""
    _object(payload, "bundle", {"schema", "request", "report", "decisions"}, {"replayed"})
    _bounded_input(payload, MAX_BUNDLE_BYTES)
    if payload["schema"] != BUNDLE_SCHEMA or ("replayed" in payload and type(payload["replayed"]) is not bool):
        raise TransactionReviewError("需要 TransactionReviewBundleV1 复核包，请重新导出当前格式。")
    report = payload["report"]
    if not isinstance(report, dict):
        raise TransactionReviewError("复核包 report 必须是完整分析报告。")
    fresh = analyze_request(payload["request"])
    if (report.get("engineVersion") != fresh["engineVersion"]
            or not isinstance(report.get("provenance"), dict)
            or report["provenance"].get("codeSha256") != fresh["provenance"]["codeSha256"]):
        raise TransactionReviewError("代码版本或代码绑定已改变，需要重新分析；没有采信旧报告或决定。")
    if _canonical(report) != _canonical(fresh):
        raise TransactionReviewError("复核包报告与当前请求的完整重算结果不一致，请重新分析；没有采信旧报告或决定。")
    decisions = payload["decisions"]
    if not isinstance(decisions, list):
        raise TransactionReviewError("decisions 必须是复核决定数组。")
    valid_ids, seen = {case["id"] for case in fresh["cases"]}, set()
    for decision in decisions:
        _object(decision, "decision", {"transaction_id", "decision", "reason"})
        ident = _text(decision["transaction_id"], "decision.transaction_id")
        if ident not in valid_ids or ident in seen:
            raise TransactionReviewError("决定包含未筛出、过时或重复的案件 ID，请针对当前案件重新决定。")
        seen.add(ident)
        if decision["decision"] not in ("pending", "escalate", "dismiss", "need_more"):
            raise TransactionReviewError("decision 只支持 pending/escalate/dismiss/need_more。")
        reason = decision["reason"]
        if not isinstance(reason, str) or (decision["decision"] != "pending" and not reason.strip()):
            raise TransactionReviewError("非 pending 决定必须填写非空原因，reason 必须是字符串。")
    # Preserve every original row, its order and authored reasons. Numeric money
    # is exported as a lossless two-decimal string, including JSON Decimal input;
    # no binary-float encoding may remove cents from a later replay.
    original = payload["request"]
    exported = {**original, "transactions": [
        {**row, "amount": _money(row["amount"], "amount")} if not isinstance(row["amount"], str) else dict(row)
        for row in original["transactions"]]}
    if "rules" in original:
        exported["rules"] = dict(original["rules"])
        if not isinstance(original["rules"]["highAmount"], str):
            exported["rules"]["highAmount"] = _money(original["rules"]["highAmount"], "rules.highAmount")
    if "claims" in original:
        exported["claims"] = [
            {**claim, "value": _money(claim["value"], "claim.value", nonnegative=False)}
            if claim["field"] in ("amount", "window_total") and not isinstance(claim["value"], str) else dict(claim)
            for claim in original["claims"]]
    return {"schema": BUNDLE_SCHEMA, "request": exported, "report": fresh,
            "decisions": decisions, "replayed": True}
