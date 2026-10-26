"""Offline conditional context proof. Standard library; never calls an API.

This checks the application's local compressor, not provider billing tokens.
Evidence supplied in --settings must be independently verified by the operator;
this program checks its presence/consistency, not the truth of an attestation.
Every input record, including errors, remains in the output denominator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

FIXED_COMMIT = "feb0ebe8a0de61afd33bb491d5e8f12f0d901e32"
RELEASE_COMMIT = "3e4f9411b67f2dd7fa9fc6f2b95dfacbda0633ba"
DEFAULT_PROMPT = (
    "Given the following conversation, relevant context, and a follow up question, "
    "reply with an answer to the current question the user is asking. The current "
    "date and time is {datetime}. Return only your response to the question given "
    "the above information following the users instructions as needed."
)
DEFAULT_SOURCE = f"{FIXED_COMMIT}/server/models/systemSettings.js:38-39"
DATETIME_BOUND = 64  # Standard unmodified moment 2.30.1 en LLLL is at most 41.
PER_MESSAGE = 3
GLOBAL_OVERHEAD = 5
BUFFER = 600
REFERENCE_WINDOW = 8192


def utf8_len(text):
    return len(text.encode("utf-8"))


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON field")
        obj[key] = value
    return obj


def reject_constant(_):
    raise ValueError("non-finite JSON")


def parse(text):
    value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return value


def mapping(value):
    return value if isinstance(value, dict) else {}


def wrapper_bytes(count):
    return 0 if count == 0 else 10 + sum(29 + 2 * len(str(i)) for i in range(count))


def unpack(record):
    record = mapping(record)
    if "applicationCall" in record or "applicationRequest" in record:
        return mapping(mapping(record.get("applicationCall")).get("response")), mapping(record.get("applicationRequest"))
    return record, {}


def load_records(path):
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() != ".jsonl":
        try:
            return [parse(text)]
        except (ValueError, UnicodeError, RecursionError):
            return [{"_read_error": "invalid_json"}]
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        try:
            record = parse(line)
        except (ValueError, UnicodeError, RecursionError):
            record = {"_read_error": "invalid_json_line", "_line": number}
        records.append(record)
    return records


def check_record(record, settings, duplicate_sessions=()):
    response, request = unpack(record)
    meta = mapping(response.get("meta"))
    saved = mapping(settings.get("meta", settings))
    evidence = mapping(settings.get("evidence"))
    refs = mapping(evidence.get("references"))
    reasons = []

    def require(condition, reason):
        if not condition:
            reasons.append(reason)

    def attested(key, condition):
        ref = refs.get(key)
        require(condition and isinstance(ref, str) and bool(ref.strip()), f"evidence_missing_or_invalid:{key}")

    # Per-response settings take priority; never silently replace a conflicting
    # snapshot with a more convenient sidecar.
    details = mapping(meta.get("workspaceDetails", saved.get("workspaceDetails")))
    fields = mapping(meta.get("workspaceDetailsFieldStatus", saved.get("workspaceDetailsFieldStatus")))
    for name in ("workspaceDetails", "workspaceDetailsFieldStatus"):
        if name in saved and name in meta:
            require(saved[name] == meta[name], f"settings_conflict:{name}")

    require(isinstance(record, dict) and not record.get("_read_error"), "invalid_input_record")
    require(response.get("schemaVersion") == "veriscope.e2e.v1", "response_schema_unknown")
    require(response.get("status") in ("answered", "refused"), "no_usable_response")
    require(meta.get("mode") == "query", "not_query_mode")
    require(meta.get("workspaceSettingsStatus", saved.get("workspaceSettingsStatus")) == "available", "workspace_snapshot_unavailable")
    require(fields.get("documents") == "recorded"
            and type(details.get("documentCount")) is int and details["documentCount"] == 1,
            "workspace_not_exactly_one_document")
    require(type(details.get("pinnedDocumentCount")) is int and details["pinnedDocumentCount"] == 0,
            "pinned_documents_or_unknown")
    require(isinstance(details.get("documentNames"), list) and len(details["documentNames"]) == 1
            and isinstance(details["documentNames"][0], str), "document_name_unknown")
    require(fields.get("chatProvider") == "recorded" and details.get("chatProvider") == "deepseek",
            "effective_deepseek_provider_not_confirmed")

    attested("runtimeSourceConfirmed", evidence.get("runtimeSourceConfirmed") is True)
    require(evidence.get("sourceCommit") in (FIXED_COMMIT, RELEASE_COMMIT), "source_version_not_audited")
    for key in ("documentUploadedOnce", "configurationUnchanged", "memoriesDisabled"):
        attested(key, evidence.get(key) is True)
    window = evidence.get("effectiveWindow")
    window_valid = type(window) is int and window >= REFERENCE_WINDOW
    attested("effectiveWindow", window_valid)

    req = mapping(meta.get("requestEvidence"))
    require(req.get("freshSession") is True, "fresh_session_unconfirmed")
    require(type(req.get("attachments")) is int and req["attachments"] == 0, "attachments_or_unknown")
    require(req.get("hasCommandSyntax") is False, "question_transformation_not_excluded")
    session = meta.get("sessionId")
    require(isinstance(session, str)
            and re.fullmatch(r"veriscope-.+-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", session) is not None,
            "invalid_session_evidence")
    require(not isinstance(session, str) or session not in duplicate_sessions, "session_reused")

    qbytes = None
    question = request.get("question")
    if isinstance(question, str):
        qbytes = utf8_len(question)
        require(bool(question.strip()), "empty_question")
        require("/" not in question and "@agent" not in question.lower(), "question_contains_command_syntax")
        require(req.get("questionUtf8Bytes") == qbytes
                and req.get("questionSha256") == sha256(question.encode("utf-8")).hexdigest(),
                "question_evidence_mismatch")
        require(request.get("id") == response.get("id"), "request_response_id_mismatch")
    elif type(req.get("questionUtf8Bytes")) is int and req["questionUtf8Bytes"] > 0:
        qbytes = req["questionUtf8Bytes"]
        require(isinstance(req.get("questionSha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", req["questionSha256"]) is not None, "question_hash_missing")
    else:
        require(False, "question_bytes_unknown")

    contexts = response.get("contexts")
    good_contexts = (isinstance(contexts, list) and len(contexts) <= 20
                     and all(isinstance(c, dict) and isinstance(c.get("text"), str)
                             and 0 < len(c["text"]) <= 8000 for c in contexts))
    require(good_contexts, "contexts_missing_or_invalid")
    count = len(contexts) if isinstance(contexts, list) else None
    source_bytes = sum(utf8_len(c["text"]) for c in contexts) if good_contexts else None
    wrapped = wrapper_bytes(count) if good_contexts else None
    # Query mode with no retrieved sources returns before generation. Do not
    # claim that a prompt was sent merely because an empty list is small.
    require(good_contexts and count > 0, "no_context_generation_not_established")

    prompt = details.get("openAiPrompt")
    system_bytes = None
    system_mode = "unknown"
    if prompt is None and fields.get("openAiPrompt") == "null":
        attested("standardEnglishLocale", evidence.get("standardEnglishLocale") is True)
        system_mode = "default_template_plus_conditional_datetime_bound"
        # Count only when the locale assumption is supported.
        if evidence.get("standardEnglishLocale") is True and isinstance(refs.get("standardEnglishLocale"), str) and refs["standardEnglishLocale"].strip():
            system_bytes = utf8_len(DEFAULT_PROMPT) - len("{datetime}") + DATETIME_BOUND
    elif isinstance(prompt, str) and fields.get("openAiPrompt") == "recorded":
        require("{" not in prompt, "custom_prompt_contains_variable")
        require(details.get("openAiPromptUtf8Bytes") == utf8_len(prompt)
                and details.get("openAiPromptSha256") == sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt_hash_or_size_mismatch")
        if "{" not in prompt:
            system_bytes, system_mode = utf8_len(prompt), "literal_workspace_prompt"
    else:
        require(False, "system_prompt_missing_or_invalid")

    overhead = 2 * PER_MESSAGE + GLOBAL_OVERHEAD
    upper = (source_bytes + wrapped + system_bytes + qbytes + overhead
             if all(x is not None for x in (source_bytes, wrapped, system_bytes, qbytes)) else None)
    reference_ok = upper + BUFFER < REFERENCE_WINDOW if upper is not None else None
    actual_ok = upper + BUFFER < window if upper is not None and type(window) is int and window > 0 else None
    require(reference_ok is True, "upper_plus_600_not_below_8192")
    require(actual_ok is True, "actual_window_not_proven_sufficient")
    return {
        "id": response.get("id", mapping(record).get("id")),
        "inputLine": mapping(record).get("_line"),
        "status": "对应关系未证明" if reasons else "对应关系已证明（所列前提下）",
        "reasons": list(dict.fromkeys(reasons)),
        "sourceSegments": count, "sourceUtf8Bytes": source_bytes, "wrapperUtf8Bytes": wrapped,
        "systemPromptUtf8BytesUpper": system_bytes, "systemPromptMode": system_mode,
        "defaultPromptSource": DEFAULT_SOURCE, "defaultPromptTemplateUtf8Bytes": utf8_len(DEFAULT_PROMPT),
        "datetimeUtf8BytesAllowance": DATETIME_BOUND,
        "questionUtf8Bytes": qbytes, "messages": 2, "perMessageExtraTokens": PER_MESSAGE,
        "globalExtraTokens": GLOBAL_OVERHEAD, "messageExtraTokensTotal": overhead,
        "upperTokens": upper, "bufferTokens": BUFFER,
        "upperPlus600Below8192": reference_ok, "attestedEffectiveWindow": window,
        "upperPlus600BelowActualWindow": actual_ok,
        "upstreamMetrics": mapping(meta.get("upstreamMetrics")),
        "metricsNote": "上游自报用量不等于本地分词器计数；0可能由上游缺失填零。",
    }


def analyze(records, settings):
    sessions = [mapping(unpack(r)[0].get("meta")).get("sessionId") for r in records]
    counts = Counter(s for s in sessions if isinstance(s, str))
    duplicates = {s for s, count in counts.items() if count > 1}
    rows = [check_record(record, settings, duplicates) for record in records]
    proved = sum(not row["reasons"] for row in rows)
    return {
        "schemaVersion": "anythingllm-context-bound.v1",
        "scope": "仅证明经独立核实前提下应用压缩器不裁剪；不是服务端输入抓包或供应商token证明。",
        "evidenceVerification": "脚本检查证据声明与引用是否齐全；不自动认证引用内容或实机来源。",
        "inputRecords": len(rows), "conditionallyProved": proved, "unproved": len(rows) - proved,
        "emptyInput": not rows, "rows": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() in (args.input.resolve(), args.settings.resolve()):
        parser.error("output must not overwrite inputs")
    try:
        settings = parse(args.settings.read_text(encoding="utf-8-sig"))
        if not isinstance(settings, dict):
            raise ValueError("settings must be an object")
        result = analyze(load_records(args.input), settings)
        result["inputSha256"] = sha256(args.input.read_bytes()).hexdigest()
        result["settingsSha256"] = sha256(args.settings.read_bytes()).hexdigest()
        result["checkerSha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        parser.exit(2, f"离线检查未完成：{type(error).__name__}\n")
    print(f"记录 {result['inputRecords']}；条件证明 {result['conditionallyProved']}；未证明 {result['unproved']}")
    # A completed diagnostic is not a quality gate. Unproved rows do not stop
    # evaluation; errors in loading/writing have exit code 2.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
