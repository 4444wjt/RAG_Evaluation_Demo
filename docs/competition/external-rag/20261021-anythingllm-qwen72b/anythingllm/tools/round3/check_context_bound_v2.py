"""Offline per-question context check, v2: premises come from captured evidence files.

Standard library only; never calls an API. Compared with round-2 v1, the premises
that v1 left to manual attestation are derived here from files captured before
and after the formal run:

  --before/--after                shim.py --dump-settings snapshots
  --source-before/--source-after  compare_installed_source.py reports

Unknown or inconsistent evidence leaves a question "对应关系未证明"; it is never
removed from the denominator and never triggers a rerun. Not a quality gate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re

_V1_PATH = Path(__file__).resolve().parents[1] / "check_context_bound.py"
_SPEC = importlib.util.spec_from_file_location("check_context_bound_v1", _V1_PATH)
v1 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(v1)

SNAPSHOT_SCHEMA = "anythingllm-runtime-snapshot.v1"
SOURCE_SCHEMA = "anythingllm-installed-source.v1"
RELEASE_COMMIT = "3e4f9411b67f2dd7fa9fc6f2b95dfacbda0633ba"
# en LLLL in moment 2.30.1 is at most 41 bytes (round 2, kept at v1's 64).
DATETIME_EN_ALLOWANCE = v1.DATETIME_BOUND
# Used only when the installed code could not be shown to keep moment's default
# locale. An assumption, reported as such: 1024 bytes is 256 four-byte characters.
DATETIME_CAP_ASSUMED = 1024
SESSION_RE = re.compile(r"veriscope-.+-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
COMPARED_SNAPSHOT_KEYS = ("workspaceSettings", "workspaceSettingsStatus", "workspaceDetails",
                          "workspaceDetailsFieldStatus", "system", "systemFieldStatus", "effective", "shimSha256")


def mapping(value):
    return value if isinstance(value, dict) else {}


def derive_evidence(before, after, source_before, source_after):
    """Run-level evidence shared by every question. Returns (evidence, reasons, assumptions)."""
    reasons, ev = [], {}
    snapshots_ok = (mapping(before).get("schemaVersion") == SNAPSHOT_SCHEMA
                    and mapping(after).get("schemaVersion") == SNAPSHOT_SCHEMA
                    and isinstance(before.get("takenAtUtc"), str) and isinstance(after.get("takenAtUtc"), str)
                    and before["takenAtUtc"] < after["takenAtUtc"])
    ev["snapshotsValid"] = snapshots_ok
    if not snapshots_ok:
        reasons.append("runtime_snapshots_missing_or_out_of_order")
        before, after = mapping(before), mapping(after)

    changed = [key for key in COMPARED_SNAPSHOT_KEYS if before.get(key) != after.get(key)]
    ev["configurationChangedKeys"] = changed
    if changed or before.get("workspaceSettingsStatus") != "available":
        reasons.append("configuration_changed_or_unavailable")

    system, status = mapping(before.get("system")), mapping(before.get("systemFieldStatus"))
    memory_off = (status.get("MemoryEnabled") == "recorded" and system.get("MemoryEnabled") is False
                  and mapping(after.get("system")).get("MemoryEnabled") is False)
    ev["memoriesDisabled"] = memory_off
    if not memory_off:
        reasons.append("memory_enabled_or_unknown")

    effective = mapping(before.get("effective"))
    ev["effectiveProvider"], ev["effectiveModel"] = effective.get("provider"), effective.get("model")
    if effective.get("provider") != "deepseek" or not effective.get("model"):
        reasons.append("effective_provider_not_deepseek_or_unknown")

    cache_before, cache_after = mapping(before.get("contextWindowCache")), mapping(after.get("contextWindowCache"))
    window = cache_before.get("effectiveWindow")
    window_ok = (cache_before == cache_after and type(window) is int and window > 0
                 and cache_before.get("status") in ("cached_value", "cache_file_missing_fallback",
                                                    "provider_missing_fallback", "model_missing_fallback"))
    ev["effectiveWindow"] = window if window_ok else None
    ev["windowBasis"] = cache_before.get("status")
    if not window_ok:
        reasons.append("effective_window_unknown_or_changed")

    def source_ok(report):
        report = mapping(report)
        return (report.get("schemaVersion") == SOURCE_SCHEMA and report.get("referenceCommit") == RELEASE_COMMIT
                and report.get("allAuditedFilesMatched") is True and report.get("singleServerRoot") is True)

    def hashes(report):
        return {rel: [entry.get("sha256") for entry in mapping(item).get("found", [])]
                for rel, item in mapping(mapping(report).get("files")).items()}

    runtime_ok = source_ok(source_before) and source_ok(source_after) and hashes(source_before) == hashes(source_after)
    ev["runtimeSourceConfirmed"] = runtime_ok
    if not runtime_ok:
        reasons.append("installed_source_not_confirmed_or_changed")

    scans = [mapping(mapping(report).get("localeScan")) for report in (source_before, source_after)]
    locale_default = runtime_ok and all(scan.get("scanned") is True and scan.get("hits") == [] for scan in scans)
    ev["momentLocaleDefaultShown"] = locale_default
    assumptions = [] if locale_default else ["datetime_cap_1024_bytes_assumed"]
    ev["datetimeAllowanceBytes"] = DATETIME_EN_ALLOWANCE if locale_default else DATETIME_CAP_ASSUMED
    return ev, reasons, assumptions


def check_record(record, before, ev, run_reasons, assumptions, duplicate_sessions=()):
    response, request = v1.unpack(record)
    meta = mapping(response.get("meta"))
    details = mapping(before.get("workspaceDetails"))
    fields = mapping(before.get("workspaceDetailsFieldStatus"))
    reasons = list(run_reasons)

    def require(condition, reason):
        if not condition:
            reasons.append(reason)

    require(isinstance(record, dict) and not record.get("_read_error"), "invalid_input_record")
    require(response.get("schemaVersion") == "veriscope.e2e.v1", "response_schema_unknown")
    require(response.get("status") in ("answered", "refused"), "no_usable_response")
    require(meta.get("mode") == "query", "not_query_mode")
    # The shim's own startup snapshot must equal the pre-run snapshot.
    require(meta.get("workspaceDetails") == before.get("workspaceDetails")
            and meta.get("workspaceDetailsFieldStatus") == before.get("workspaceDetailsFieldStatus"),
            "shim_snapshot_differs_from_before")
    require(fields.get("documents") == "recorded" and details.get("pinnedDocumentCount") == 0,
            "pinned_documents_or_unknown")

    req = mapping(meta.get("requestEvidence"))
    require(req.get("freshSession") is True, "fresh_session_unconfirmed")
    require(req.get("attachments") == 0 and type(req.get("attachments")) is int, "attachments_or_unknown")
    require(req.get("hasCommandSyntax") is False, "question_transformation_not_excluded")
    session = meta.get("sessionId")
    require(isinstance(session, str) and SESSION_RE.fullmatch(session) is not None, "invalid_session_evidence")
    require(not isinstance(session, str) or session not in duplicate_sessions, "session_reused")

    qbytes = None
    question = request.get("question")
    if isinstance(question, str):
        qbytes = v1.utf8_len(question)
        require(bool(question.strip()) and "/" not in question and "@agent" not in question.lower(),
                "question_empty_or_command_syntax")
        require(req.get("questionUtf8Bytes") == qbytes
                and req.get("questionSha256") == sha256(question.encode("utf-8")).hexdigest(),
                "question_evidence_mismatch")
        require(request.get("id") == response.get("id"), "request_response_id_mismatch")
    elif type(req.get("questionUtf8Bytes")) is int and req["questionUtf8Bytes"] > 0:
        qbytes = req["questionUtf8Bytes"]
    else:
        require(False, "question_bytes_unknown")

    contexts = response.get("contexts")
    good = (isinstance(contexts, list) and 0 < len(contexts) <= 20
            and all(isinstance(c, dict) and isinstance(c.get("text"), str) and 0 < len(c["text"]) <= 8000
                    for c in contexts))
    # An empty retrieval returns before generation; nothing to prove about a prompt.
    require(good, "contexts_missing_empty_or_invalid")
    count = len(contexts) if good else None
    source_bytes = sum(v1.utf8_len(c["text"]) for c in contexts) if good else None
    wrapped = v1.wrapper_bytes(count) if good else None

    prompt = details.get("openAiPrompt")
    system_bytes, system_mode, row_assumptions = None, "unknown", []
    # Workspace.new stores the default template itself when no prompt is sent
    # (v1.18.0 models/workspace.js:210-218), so a byte-identical copy counts as the template.
    is_template = (prompt is None and fields.get("openAiPrompt") == "null") or (
        prompt == v1.DEFAULT_PROMPT and fields.get("openAiPrompt") == "recorded"
        and details.get("openAiPromptSha256") == sha256(prompt.encode("utf-8")).hexdigest())
    if is_template:
        system_bytes = v1.utf8_len(v1.DEFAULT_PROMPT) - len("{datetime}") + ev["datetimeAllowanceBytes"]
        system_mode = "default_template" if prompt is None else "stored_default_template"
        row_assumptions = list(assumptions)
    elif isinstance(prompt, str) and fields.get("openAiPrompt") == "recorded":
        require("{" not in prompt, "custom_prompt_contains_variable")
        require(details.get("openAiPromptUtf8Bytes") == v1.utf8_len(prompt)
                and details.get("openAiPromptSha256") == sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt_hash_or_size_mismatch")
        if "{" not in prompt:
            system_bytes, system_mode = v1.utf8_len(prompt), "literal_workspace_prompt"
    else:
        require(False, "system_prompt_missing_or_invalid")

    overhead = 2 * v1.PER_MESSAGE + v1.GLOBAL_OVERHEAD
    parts = (source_bytes, wrapped, system_bytes, qbytes)
    upper = sum(parts) + overhead if all(x is not None for x in parts) else None
    window = ev.get("effectiveWindow")
    below = upper + v1.BUFFER < window if upper is not None and type(window) is int else None
    require(below is True, "upper_plus_600_not_below_effective_window")
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        status = "对应关系未证明"
    elif row_assumptions:
        status = "对应关系已证明（含日期长度上限假设）"
    else:
        status = "对应关系已证明（所列前提下）"
    return {
        "id": response.get("id", mapping(record).get("id")), "inputLine": mapping(record).get("_line"),
        "status": status, "reasons": reasons, "assumptions": row_assumptions if not reasons else [],
        "sourceSegments": count, "sourceUtf8Bytes": source_bytes, "wrapperUtf8Bytes": wrapped,
        "systemPromptUtf8BytesUpper": system_bytes, "systemPromptMode": system_mode,
        "questionUtf8Bytes": qbytes, "messageExtraTokensTotal": overhead,
        "upperTokens": upper, "bufferTokens": v1.BUFFER, "effectiveWindow": window,
        "headroomTokens": window - v1.BUFFER - upper if below is not None else None,
        "documentCountInformational": details.get("documentCount"),
        "upstreamMetrics": mapping(meta.get("upstreamMetrics")),
    }


def analyze(records, before, after, source_before, source_after):
    ev, run_reasons, assumptions = derive_evidence(before, after, source_before, source_after)
    sessions = [mapping(v1.unpack(r)[0].get("meta")).get("sessionId") for r in records]
    counts = Counter(s for s in sessions if isinstance(s, str))
    duplicates = {s for s, n in counts.items() if n > 1}
    rows = [check_record(r, mapping(before), ev, run_reasons, assumptions, duplicates) for r in records]
    tally = Counter(row["status"] for row in rows)
    return {
        "schemaVersion": "anythingllm-context-bound.v2",
        "scope": "证明在所列前提下 AnythingLLM 本地压缩器不触发、API sources 即送入生成请求的资料；不是供应商侧抓包。",
        "evidence": ev, "runLevelReasons": run_reasons, "assumptions": assumptions,
        "inputRecords": len(rows),
        "proved": tally["对应关系已证明（所列前提下）"],
        "provedWithAssumption": tally["对应关系已证明（含日期长度上限假设）"],
        "unproved": tally["对应关系未证明"],
        "emptyInput": not rows, "rows": rows,
    }


def _load_json(path):
    if path is None:
        return {}
    value = v1.parse(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--source-before", type=Path)
    parser.add_argument("--source-after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = [p for p in (args.input, args.before, args.after, args.source_before, args.source_after) if p]
    if args.output.resolve() in {p.resolve() for p in inputs}:
        parser.error("output must not overwrite inputs")
    try:
        result = analyze(v1.load_records(args.input), _load_json(args.before), _load_json(args.after),
                         _load_json(args.source_before), _load_json(args.source_after))
        result["inputSha256"] = {p.name: sha256(p.read_bytes()).hexdigest() for p in inputs}
        result["checkerSha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
        result["v1HelperSha256"] = sha256(_V1_PATH.read_bytes()).hexdigest()
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        parser.exit(2, f"离线检查未完成：{type(error).__name__}\n")
    print(f"记录 {result['inputRecords']}；已证明 {result['proved']}；含假设 {result['provedWithAssumption']}；"
          f"未证明 {result['unproved']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
