"""Deterministic, standard-library checks for explicit knowledge/source mappings.

All inputs are bytes in memory. This module never opens files or evaluates
whether a draft is true. JSON fingerprints use UTF-8, sorted keys, no ASCII
escaping, separators (',', ':'), and reject non-JSON numbers.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import difflib
import hashlib
import html
import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

CHECKER_VERSION = "knowledge-update/v1"
LIMITS = [
    "Only complete consecutive logical lines are checked; explicit mappings may be incomplete.",
    "MATCHED is literal evidence only. Semantic evaluation is NOT_EVALUATED; publication is never approved.",
    "Source changes do not establish that a draft is wrong. Hashes do not prove identity or semantics.",
]


def sha256(value: bytes) -> str:
    """Return the SHA256 of actual bytes."""
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> bytes:
    """Encode the documented canonical JSON used by both fingerprints."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _error(code: str, location: str, message: str, **details: Any) -> dict:
    return {"code": code, "location": location, "message": message, **details}


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("Non-JSON numeric constant")


def _parse(data: bytes | None, location: str, errors: list[dict]) -> Any:
    if data is None:
        errors.append(_error("MISSING_INPUT", location, "Required JSON input was not read."))
        return None
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        # Ensure that every accepted JSON string can also be emitted as UTF-8.
        canonical_json(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        errors.append(_error("INVALID_JSON", location,
                             "Expected strict UTF-8 JSON with unique keys and finite JSON numbers."))
        return None


def valid_source_path(value: str) -> bool:
    """Validate a task-relative text path without accessing the filesystem."""
    windows = PureWindowsPath(value)
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or "\x00" in value or windows.drive or windows.root or path.is_absolute():
        return False
    depth = 0
    for part in path.parts:
        depth += -1 if part == ".." else 1
        if depth < 0:
            return False
    return path.suffix.lower() in {".txt", ".md", ".rst"}


def _object(value: Any, required: set[str], optional: set[str], location: str,
            errors: list[dict]) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("INVALID_STRUCTURE", location, "Expected an object."))
        return False
    if required - value.keys():
        errors.append(_error("MISSING_FIELD", location, "Missing required fields: " +
                             ", ".join(sorted(required - value.keys()))))
    if value.keys() - required - optional:
        errors.append(_error("UNKNOWN_FIELD", location, "Unknown fields: " +
                             ", ".join(sorted(value.keys() - required - optional))))
    return required <= value.keys()


def _string(value: Any, location: str, errors: list[dict], *, nonempty: bool = False) -> bool:
    valid = isinstance(value, str) and (not nonempty or bool(value))
    if not valid:
        errors.append(_error("INVALID_STRUCTURE", location,
                             "Expected a nonempty string." if nonempty else "Expected a string."))
    return valid


def _array(value: Any, location: str, errors: list[dict]) -> bool:
    if not isinstance(value, list):
        errors.append(_error("INVALID_STRUCTURE", location, "Expected an array."))
        return False
    return True


def _ids(value: Any, location: str, errors: list[dict]) -> None:
    if not _array(value, location, errors):
        return
    seen = set()
    for index, entry in enumerate(value):
        row = f"{location}[{index}]"
        if _string(entry, row, errors, nonempty=True):
            if entry in seen:
                errors.append(_error("DUPLICATE_ID", row, "Duplicate source association."))
            seen.add(entry)


def _citations(value: Any, location: str, errors: list[dict]) -> None:
    if not _array(value, location, errors):
        return
    for index, entry in enumerate(value):
        row = f"{location}[{index}]"
        if not _object(entry, {"source_id"}, {"quote", "line_start", "line_end"}, row, errors):
            continue
        _string(entry["source_id"], row + ".source_id", errors, nonempty=True)
        if entry.get("quote") is not None:
            _string(entry["quote"], row + ".quote", errors)
        start, end = entry.get("line_start"), entry.get("line_end")
        if not (start is None and end is None):
            if type(start) is not int or type(end) is not int or start < 1 or end < start:
                errors.append(_error("INVALID_LOCATION", row, "Positions must be two positive integers, start <= end."))


def parse_inputs(task_bytes: bytes | None, revisions_bytes: bytes | None = None, *,
                 mode: str = "scan") -> tuple[Any, Any, list[dict], dict]:
    """Strictly parse/validate structures; mapping and read errors are checked later.

    Returns task, revisions, structural errors, and raw array lengths. An invalid
    structure must not be used to compute business denominators.
    """
    errors: list[dict] = []
    task = _parse(task_bytes, "$task", errors)
    revisions = None
    if mode not in {"scan", "recheck"}:
        errors.append(_error("INVALID_MODE", "$mode", "Expected scan or recheck."))
    if mode == "scan" and revisions_bytes is not None:
        errors.append(_error("UNEXPECTED_REVISIONS", "$revisions", "scan does not accept revisions."))
    if mode == "recheck":
        revisions = _parse(revisions_bytes, "$revisions", errors)
    raw = {"sources": None, "items": None, "revisions": None if mode == "recheck" else 0}
    if isinstance(task, dict):
        for name in ("sources", "items"):
            if isinstance(task.get(name), list):
                raw[name] = len(task[name])
    if isinstance(revisions, dict) and isinstance(revisions.get("revisions"), list):
        raw["revisions"] = len(revisions["revisions"])
    if _object(task, {"schema_version", "task_id", "sources", "items"}, {"provenance"}, "$task", errors):
        if task["schema_version"] != "knowledge-update-task/v1":
            errors.append(_error("UNSUPPORTED_SCHEMA", "$task.schema_version", "Unsupported task schema."))
        _string(task["task_id"], "$task.task_id", errors, nonempty=True)
        for name, id_key in (("sources", "source_id"), ("items", "item_id")):
            if not _array(task[name], "$task." + name, errors):
                continue
            seen = set()
            for index, entry in enumerate(task[name]):
                row = f"$task.{name}[{index}]"
                required = {"source_id", "old", "new"} if name == "sources" else {
                    "item_id", "old_draft", "source_ids", "old_citations"}
                if not _object(entry, required, set() if name == "sources" else {"question"}, row, errors):
                    continue
                if _string(entry[id_key], row + "." + id_key, errors, nonempty=True):
                    if entry[id_key] in seen:
                        errors.append(_error("DUPLICATE_ID", row + "." + id_key, "Duplicate declared ID."))
                    seen.add(entry[id_key])
                if name == "items":
                    _string(entry["old_draft"], row + ".old_draft", errors)
                    if "question" in entry:
                        _string(entry["question"], row + ".question", errors)
                    _ids(entry["source_ids"], row + ".source_ids", errors)
                    _citations(entry["old_citations"], row + ".old_citations", errors)
                else:
                    if entry["old"] is None and entry["new"] is None:
                        errors.append(_error("EMPTY_SOURCE", row, "Both source versions cannot be null."))
                    for side in ("old", "new"):
                        version = entry[side]
                        loc = row + "." + side
                        if version is None:
                            continue
                        if _object(version, {"path"}, {"version_tag"}, loc, errors):
                            if _string(version["path"], loc + ".path", errors, nonempty=True):
                                if not valid_source_path(version["path"]):
                                    errors.append(_error("INVALID_SOURCE_PATH", loc + ".path",
                                                         "Expected a relative .txt/.md/.rst path inside the task package."))
                            if "version_tag" in version:
                                _string(version["version_tag"], loc + ".version_tag", errors)
    if mode == "recheck" and _object(revisions, {"schema_version", "revisions"}, set(), "$revisions", errors):
        if revisions["schema_version"] != "knowledge-update-revisions/v1":
            errors.append(_error("UNSUPPORTED_SCHEMA", "$revisions.schema_version", "Unsupported revisions schema."))
        if _array(revisions["revisions"], "$revisions.revisions", errors):
            seen = set()
            for index, entry in enumerate(revisions["revisions"]):
                row = f"$revisions.revisions[{index}]"
                if not _object(entry, {"item_id", "proposed_draft", "citations"},
                               {"source_ids", "open_questions"}, row, errors):
                    continue
                if _string(entry["item_id"], row + ".item_id", errors, nonempty=True):
                    if entry["item_id"] in seen:
                        errors.append(_error("DUPLICATE_ID", row + ".item_id", "Duplicate revision ID."))
                    seen.add(entry["item_id"])
                _string(entry["proposed_draft"], row + ".proposed_draft", errors)
                _citations(entry["citations"], row + ".citations", errors)
                if "source_ids" in entry:
                    _ids(entry["source_ids"], row + ".source_ids", errors)
                if "open_questions" in entry and _array(entry["open_questions"], row + ".open_questions", errors):
                    for qindex, question in enumerate(entry["open_questions"]):
                        _string(question, f"{row}.open_questions[{qindex}]", errors)
    return task, revisions, errors, raw


def source_paths(task_bytes: bytes | None, revisions_bytes: bytes | None = None, *,
                 mode: str = "scan") -> list[str]:
    """Return ordered unique paths only when the complete structure is valid."""
    task, _, errors, _ = parse_inputs(task_bytes, revisions_bytes, mode=mode)
    if errors:
        return []
    return list(dict.fromkeys(source[side]["path"] for source in task["sources"]
                              for side in ("old", "new") if source[side] is not None))


def _version(descriptor: dict | None, payloads: dict, location: str, side: str,
             errors: list[dict]) -> tuple[dict, bytes | None, list[str] | None]:
    result = {"path": None if descriptor is None else descriptor["path"], "sha256": None,
              "size": None, "line_count": None, "read_error": None}
    if descriptor is None:
        return result, None, None
    path = descriptor["path"]
    payload = payloads.get(path, {"code": "SOURCE_READ_ERROR", "message": "Source bytes were not supplied."})
    if isinstance(payload, bytes):
        result.update(sha256=sha256(payload), size=len(payload))
        try:
            lines = payload.decode("utf-8").splitlines()
            result["line_count"] = len(lines)
            return result, payload, lines
        except UnicodeError:
            read_error = {"code": "INVALID_UTF8", "message": "Source is not strict UTF-8."}
    else:
        read_error = {"code": payload["code"], "message": payload["message"]}
    issue = _error(read_error["code"], location, read_error["message"], path=path, side=side)
    result["read_error"] = issue
    errors.append(issue)
    return result, payload if isinstance(payload, bytes) else None, None


def _literal(citation: dict, source: dict | None, side: str) -> dict:
    quote = citation.get("quote")
    start, end = citation.get("line_start"), citation.get("line_end")
    version = source["versions"][side] if source else None
    result = {"status": "MISSING_QUOTE", "matched_locations": [], "declared_text": None,
              "source_sha256": version["sha256"] if version else None}
    if not quote:
        return result
    if source is None:
        result["status"] = "UNKNOWN_SOURCE"
        return result
    if source[side] is None:
        result["status"] = "SOURCE_ADDED" if side == "old" else "SOURCE_DELETED"
        return result
    if version["read_error"] is not None:
        result["status"] = "SOURCE_READ_ERROR"
        return result
    lines = source["_lines"][side]
    wanted = quote.splitlines()
    matches = [{"line_start": index + 1, "line_end": index + len(wanted)}
               for index in range(len(lines) - len(wanted) + 1)
               if lines[index:index + len(wanted)] == wanted]
    result["matched_locations"] = matches
    declared_matches = False
    if start is not None and end <= len(lines):
        result["declared_text"] = "\n".join(lines[start - 1:end])
        declared_matches = lines[start - 1:end] == wanted
    if start is not None:
        result["status"] = "MATCHED" if declared_matches else "LINE_MISMATCH" if matches else "NOT_FOUND"
    else:
        result["status"] = "MATCHED" if len(matches) == 1 else "AMBIGUOUS" if matches else "NOT_FOUND"
    return result


def _checks(item_id: str, content: dict, side: str, sources: dict, location: str,
            citation_field: str, errors: list[dict], unknown: list[dict], *,
            association_location: str | None = None) -> list[dict]:
    associations = content["source_ids"]
    citations = content["citations"]
    for index, source_id in enumerate(associations):
        if source_id not in sources:
            issue = {"item_id": item_id, "source_id": source_id, "association_index": index, "side": side}
            unknown.append(issue)
            errors.append(_error("UNKNOWN_SOURCE", f"{association_location or location}.source_ids[{index}]",
                                 "Association source is not declared.", **issue))
    rows = [(citation, index, None) for index, citation in enumerate(citations)]
    present = {citation["source_id"] for citation in citations}
    rows.extend(({"source_id": source_id}, None, index) for index, source_id in enumerate(associations)
                if source_id not in present)
    result = []
    for citation, citation_index, association_index in rows:
        source_id = citation["source_id"]
        literal = _literal(citation, sources.get(source_id), side)
        mapping_error = ("UNKNOWN_SOURCE" if source_id not in sources else
                         "CITATION_OUTSIDE_ASSOCIATION" if source_id not in associations else None)
        row = {"item_id": item_id, "side": side, "citation_index": citation_index,
               "association_index": association_index, "source_id": source_id,
               "synthetic": citation_index is None, "quote": citation.get("quote"),
               "declared_line_start": citation.get("line_start"), "declared_line_end": citation.get("line_end"),
               **literal, "mapping_error": mapping_error,
               "literal_status": literal["status"] if mapping_error else None}
        if mapping_error:
            row["status"] = mapping_error
            if citation_index is not None:
                errors.append(_error(mapping_error, f"{location}.{citation_field}[{citation_index}]",
                                     "Citation source must be declared and associated on this side.",
                                     item_id=item_id, side=side, citation_index=citation_index, source_id=source_id))
        result.append(row)
    return result


def _bindings(task_bytes: bytes | None, revisions_bytes: bytes | None, mode: str,
              implementation: dict, source_inputs: list[dict]) -> dict:
    binding = {"task_sha256": sha256(task_bytes) if task_bytes is not None else None,
               "task_size": len(task_bytes) if task_bytes is not None else None,
               "revisions_sha256": sha256(revisions_bytes) if revisions_bytes is not None and mode == "recheck" else None,
               "revisions_size": len(revisions_bytes) if revisions_bytes is not None and mode == "recheck" else None,
               "source_inputs": source_inputs, "implementation": deepcopy(implementation)}
    task_identity = {"task_sha256": binding["task_sha256"], "source_inputs": source_inputs,
                     "core_sha256": implementation["core_sha256"], "checker_version": implementation["checker_version"]}
    binding["task_fingerprint"] = sha256(canonical_json(task_identity))
    entry_key = "cli_sha256" if "cli_sha256" in implementation else "http_sha256"
    if entry_key == "http_sha256":
        binding["entry"] = "http"
    binding["run_fingerprint"] = sha256(canonical_json({
        "task_fingerprint": binding["task_fingerprint"], "mode": mode,
        "revisions_sha256": binding["revisions_sha256"], entry_key: implementation[entry_key]}))
    return binding


def check(task_bytes: bytes | None, source_payloads: dict[str, bytes | dict],
          revisions_bytes: bytes | None = None, *, mode: str,
          implementation: dict) -> dict:
    """Check a whole current task without I/O; all business counts remain explicit.

    implementation requires core_sha256, cli_sha256 (or http_sha256) and checker_version. Tests
    may supply explicit test bindings; the production CLI hashes both sources.
    Source read errors are dictionaries containing code and message.
    """
    task, revisions, errors, raw = parse_inputs(task_bytes, revisions_bytes, mode=mode)
    summary = {key: None for key in (
        "declared_source_count", "declared_item_count", "declared_revision_count",
        "result_source_count", "result_item_count", "original_association_count", "effective_association_count",
        "original_citation_count", "effective_citation_count", "old_check_count", "new_check_count",
        "synthetic_check_count", "source_status_counts", "item_status_counts", "citation_status_counts_by_side",
        "submitted_revision_count", "unsubmitted_item_count", "unapplied_revision_count", "coverage_gap_count")}
    summary.update(input_error_count=len(errors), publication_approved_count=0)
    schema = "knowledge-update-report/v1" if "cli_sha256" in implementation else "knowledge-update-report/v2"
    report = {"schema_version": schema, "checker_version": implementation["checker_version"],
              "mode": mode, "task_id": task.get("task_id") if isinstance(task, dict) and isinstance(task.get("task_id"), str) else None,
              "run_status": "INPUT_ERROR" if errors else "COMPLETED", "raw_declarations": raw,
              "denominator_state": "UNAVAILABLE" if errors else "VALIDATED", "summary": summary,
              "sources": None, "items": None, "unapplied_revisions": None, "input_errors": errors,
              "coverage": None, "limits": list(LIMITS)}
    if isinstance(task, dict) and "provenance" in task:
        report["provenance"] = deepcopy(task["provenance"])
    source_inputs: list[dict] = []
    report["bindings"] = _bindings(task_bytes, revisions_bytes, mode, implementation, source_inputs)
    if errors:
        return report
    sources: list[dict] = []
    for index, declaration in enumerate(task["sources"]):
        source = deepcopy(declaration)
        versions, payloads, line_sets = {}, {}, {}
        for side in ("old", "new"):
            versions[side], payloads[side], line_sets[side] = _version(
                declaration[side], source_payloads, f"$task.sources[{index}].{side}", side, errors)
            source_inputs.append({"source_id": source["source_id"], "side": side, **versions[side]})
        if any(version["read_error"] for version in versions.values()):
            status = "READ_ERROR"
        elif source["old"] is None:
            status = "ADDED"
        elif source["new"] is None:
            status = "DELETED"
        else:
            status = "UNCHANGED" if payloads["old"] == payloads["new"] else "CHANGED"
        source.update(versions=versions, status=status, _lines=line_sets,
                      byte_only_change=status == "CHANGED" and line_sets["old"] == line_sets["new"],
                      diff=None if status == "READ_ERROR" else "".join(difflib.unified_diff(
                          [line + "\n" for line in (line_sets["old"] or [])],
                          [line + "\n" for line in (line_sets["new"] or [])],
                          fromfile=source["old"]["path"] if source["old"] else "/dev/null",
                          tofile=source["new"]["path"] if source["new"] else "/dev/null")),
                      original_associated_item_ids=[], effective_associated_item_ids=[])
        sources.append(source)
    source_map = {source["source_id"]: source for source in sources}
    revision_rows = revisions["revisions"] if mode == "recheck" else []
    revision_map = {revision["item_id"]: (index, revision) for index, revision in enumerate(revision_rows)}
    item_ids = {item["item_id"] for item in task["items"]}
    unapplied = []
    for index, revision in enumerate(revision_rows):
        if revision["item_id"] not in item_ids:
            unapplied.append({"revision_index": index, **deepcopy(revision)})
            errors.append(_error("UNKNOWN_ITEM", f"$revisions.revisions[{index}].item_id",
                                 "Revision item is not declared.", item_id=revision["item_id"]))
    unknown = {"original": [], "effective": []}
    items = []
    for index, declaration in enumerate(task["items"]):
        item_id = declaration["item_id"]
        original = {"draft": declaration["old_draft"], "source_ids": list(declaration["source_ids"]),
                    "citations": deepcopy(declaration["old_citations"])}
        effective = deepcopy(original)
        questions = []
        revision_state = "SCAN_ONLY" if mode == "scan" else "NOT_SUBMITTED"
        effective_location = f"$task.items[{index}]"
        association_location = effective_location
        citation_field = "old_citations"
        if item_id in revision_map:
            revision_index, revision = revision_map[item_id]
            effective.update(draft=revision["proposed_draft"], citations=deepcopy(revision["citations"]),
                             source_ids=list(revision.get("source_ids", original["source_ids"])))
            questions = list(revision.get("open_questions", []))
            revision_state = "SUBMITTED"
            effective_location = f"$revisions.revisions[{revision_index}]"
            citation_field = "citations"
            if "source_ids" in revision:
                association_location = effective_location
        old_checks = _checks(item_id, original, "old", source_map, f"$task.items[{index}]",
                             "old_citations", errors, unknown["original"])
        new_checks = _checks(item_id, effective, "new", source_map, effective_location,
                             citation_field, errors, unknown["effective"], association_location=association_location)
        reasons = []
        for label, content in (("original", original), ("effective", effective)):
            content["source_statuses"] = [{"source_id": sid,
                                           "status": source_map[sid]["status"] if sid in source_map else "UNKNOWN_SOURCE"}
                                          for sid in content["source_ids"]]
            for sid in content["source_ids"]:
                if sid in source_map:
                    source_map[sid][label + "_associated_item_ids"].append(item_id)
                    if source_map[sid]["status"] != "UNCHANGED":
                        reason = {"code": "SOURCE_" + source_map[sid]["status"], "source_id": sid, "content": label}
                        reasons.append(reason)
            if not content["source_ids"]:
                reasons.append({"code": "MISSING_ASSOCIATION", "content": label})
        for row in old_checks + new_checks:
            if row["status"] != "MATCHED":
                reasons.append({"code": row["status"], "side": row["side"], "citation_index": row["citation_index"],
                                "association_index": row["association_index"], "source_id": row["source_id"]})
        if questions:
            reasons.append({"code": "OPEN_QUESTIONS"})
        items.append({"item_id": item_id, "question": declaration.get("question"), "original": original,
                      "effective": effective, "revision_state": revision_state,
                      "draft_changed": original["draft"] != effective["draft"],
                      "association_changed": set(original["source_ids"]) != set(effective["source_ids"]),
                      "citations_changed": original["citations"] != effective["citations"],
                      "added_source_ids": [sid for sid in effective["source_ids"] if sid not in original["source_ids"]],
                      "removed_source_ids": [sid for sid in original["source_ids"] if sid not in effective["source_ids"]],
                      "old_checks": old_checks, "new_checks": new_checks, "review_reasons": reasons,
                      "open_questions": questions, "semantic_evaluation": "NOT_EVALUATED", "publication_approved": False})
    coverage = {}
    for label in ("original", "effective"):
        gaps = [source["source_id"] for source in sources if source["status"] in {"ADDED", "DELETED", "CHANGED"}
                and not source[label + "_associated_item_ids"]]
        coverage[label + "_unassociated_changed_source_ids"] = gaps
        coverage[label + "_coverage_gap_count"] = len(gaps)
        coverage[label + "_unknown_associations"] = unknown[label]
    for item in items:
        if errors:
            item["review_reasons"].append({"code": "INPUT_ERROR"})
        item["status"] = "PENDING_REVIEW" if item["review_reasons"] else "NOT_EVALUATED"
    all_checks = {side: [row for item in items for row in item[side + "_checks"]] for side in ("old", "new")}
    summary.update(
        declared_source_count=len(task["sources"]), declared_item_count=len(task["items"]),
        declared_revision_count=len(revision_rows), result_source_count=len(sources), result_item_count=len(items),
        original_association_count=sum(len(item["original"]["source_ids"]) for item in items),
        effective_association_count=sum(len(item["effective"]["source_ids"]) for item in items),
        original_citation_count=sum(len(item["original"]["citations"]) for item in items),
        effective_citation_count=sum(len(item["effective"]["citations"]) for item in items),
        old_check_count=len(all_checks["old"]), new_check_count=len(all_checks["new"]),
        synthetic_check_count=sum(row["synthetic"] for rows in all_checks.values() for row in rows),
        source_status_counts=dict(Counter(source["status"] for source in sources)),
        item_status_counts=dict(Counter(item["status"] for item in items)),
        citation_status_counts_by_side={side: dict(Counter(row["status"] for row in rows)) for side, rows in all_checks.items()},
        submitted_revision_count=sum(item["revision_state"] == "SUBMITTED" for item in items),
        unsubmitted_item_count=sum(item["revision_state"] == "NOT_SUBMITTED" for item in items),
        unapplied_revision_count=len(unapplied), input_error_count=len(errors),
        coverage_gap_count=coverage["effective_coverage_gap_count"])
    for source in sources:
        del source["_lines"]
    report.update(sources=sources, items=items, coverage=coverage, unapplied_revisions=unapplied,
                  run_status="INPUT_ERROR" if errors else "COMPLETED")
    report["bindings"] = _bindings(task_bytes, revisions_bytes, mode, implementation, source_inputs)
    return report


def render_handover(report: dict) -> str:
    """Render an untruncated human handover from the single report of facts.

    Arbitrary input text lives inside escaped HTML blocks so pipes, backticks,
    newlines and closing tags cannot rewrite surrounding Markdown.
    """
    def escaped(value: Any) -> str:
        return html.escape(str(value))

    def paragraph(value: str) -> str:
        return "<p>" + escaped(value) + "</p>"

    def pre(value: str | None) -> str:
        return "<pre>" + html.escape(value if value is not None else "（未提供）") + "</pre>"

    def json_detail(label: str, value: Any) -> str:
        return ("<details><summary>" + escaped(label) + "</summary>\n" +
                pre(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)) + "\n</details>")

    def ids(values: list[str]) -> str:
        return json.dumps(values, ensure_ascii=False)

    parts = ["# 知识更新交接", "", paragraph(
        f"运行：{report['run_status']}；模式：{report['mode']}；任务：{report['task_id']}"),
        "", "语义始终 NOT_EVALUATED；批准发布 0。字面命中不等于稿件正确。", ""]
    summary = report["summary"]
    parts.extend(["## 范围与未决", "", paragraph(
        f"来源 {summary['result_source_count']} / 声明 {summary['declared_source_count']}；"
        f"条目 {summary['result_item_count']} / 声明 {summary['declared_item_count']}；"
        f"输入错误 {summary['input_error_count']}；有效覆盖缺口 {summary['coverage_gap_count']}。"),
        paragraph(f"原/有效关联：{summary['original_association_count']} / {summary['effective_association_count']}；"
                  f"原/有效真实引用：{summary['original_citation_count']} / {summary['effective_citation_count']}；"
                  f"旧/新检查：{summary['old_check_count']} / {summary['new_check_count']}；"
                  f"占位检查：{summary['synthetic_check_count']}。"),
        paragraph(f"已提交修订 {summary['submitted_revision_count']}；未提交条目 {summary['unsubmitted_item_count']}；"
                  f"无法应用修订 {summary['unapplied_revision_count']}；条目状态 {summary['item_status_counts']}。")])
    if report["denominator_state"] == "UNAVAILABLE":
        parts.append(paragraph("结构无效，业务结果与分母未计算（null）；原始声明长度：" + str(report["raw_declarations"])))
    for issue in report["input_errors"]:
        parts.append(paragraph(f"{issue['code']} @ {issue['location']}：{issue['message']}"))
    coverage = report["coverage"]
    if coverage is not None:
        for label, title in (("original", "原关联"), ("effective", "有效关联")):
            parts.append(paragraph(title + "中未关联的变化来源：" + ids(coverage[label + "_unassociated_changed_source_ids"])))
            if coverage[label + "_unknown_associations"]:
                parts.append(json_detail(title + "中的未知来源关联", coverage[label + "_unknown_associations"]))
    parts.extend(["", "## 来源与差异", ""])
    for source in report["sources"] or []:
        parts.append("<h3>来源 " + escaped(source["source_id"]) + "：" + escaped(source["status"]) + "</h3>")
        for side, title in (("old", "旧版"), ("new", "新版")):
            descriptor, version = source[side], source["versions"][side]
            parts.append(paragraph(title + "：" + ("null（无此版本）" if descriptor is None else
                f"{descriptor['path']}；版本标签 {descriptor.get('version_tag')}；"
                f"{version['size']} bytes / {version['line_count']} 逻辑行；SHA256 {version['sha256']}")))
            if version["read_error"]:
                parts.append(paragraph(title + "读取错误：" + version["read_error"]["code"]))
        parts.append(paragraph("原关联条目：" + ids(source["original_associated_item_ids"]) +
                               "；有效关联条目：" + ids(source["effective_associated_item_ids"])))
        if source["byte_only_change"]:
            parts.append(paragraph("仅字节变化（byte_only_change=true），逻辑行未变化。"))
        if source["diff"] is not None:
            parts.append("<details><summary>完整 unified diff</summary>\n" + pre(source["diff"]) + "\n</details>")
    parts.extend(["", "## 条目与引用", ""])
    for item in report["items"] or []:
        parts.append("<h3>条目 " + escaped(item["item_id"]) + "：" + escaped(item["status"]) + "</h3>")
        parts.append(paragraph(f"修订：{item['revision_state']}；稿件变化 {item['draft_changed']}；"
                               f"关联变化 {item['association_changed']}；引用变化 {item['citations_changed']}。"))
        if item["question"] is not None:
            parts.extend([paragraph("问题"), pre(item["question"])])
        for label, title, side in (("original", "原稿", "old"), ("effective", "有效稿", "new")):
            content = item[label]
            parts.extend([paragraph(title), pre(content["draft"]),
                          paragraph(title + "来源关联：" + ids(content["source_ids"]))])
            for association in content["source_statuses"]:
                parts.append(paragraph(f"来源 {association['source_id']}：{association['status']}"))
            for row in item[side + "_checks"]:
                locator = (f"关联占位 {row['association_index']}" if row["synthetic"] else f"引用 {row['citation_index']}")
                positions = ", ".join(f"{p['line_start']}-{p['line_end']}" for p in row["matched_locations"]) or "无"
                parts.append(paragraph(f"{side} / {locator} / 来源 {row['source_id']}：{row['status']}；"
                                       f"声明位置 {row['declared_line_start']}-{row['declared_line_end']}；全部命中位置：{positions}"))
                parts.extend([paragraph("引文"), pre(row["quote"])])
                if row["declared_line_start"] is not None:
                    parts.extend([paragraph("声明区间的实际文本（null表示越界或不可读）"), pre(row["declared_text"])])
                if row["mapping_error"]:
                    parts.append(paragraph(f"映射错误 {row['mapping_error']}；附字面状态 {row['literal_status']}"))
                parts.append(paragraph("来源 SHA256：" + str(row["source_sha256"])))
        parts.append(paragraph("新增关联：" + ids(item["added_source_ids"]) + "；移除关联：" + ids(item["removed_source_ids"])))
        for reason in item["review_reasons"]:
            parts.append(paragraph("复核理由：" + json.dumps(reason, ensure_ascii=False)))
        for question in item["open_questions"]:
            parts.extend([paragraph("未决问题"), pre(question)])
        parts.append(paragraph("语义 NOT_EVALUATED；publication_approved=false。"))
    if report["unapplied_revisions"]:
        parts.append(json_detail("未应用修订（完整原件）", report["unapplied_revisions"]))
    parts.extend(["", "## 输入绑定与限制", "", json_detail("输入与实现 SHA256 / 指纹", report["bindings"])])
    if "provenance" in report:
        parts.append(json_detail("用户 provenance 声明（未经认证）", report["provenance"]))
    for limit in report["limits"]:
        parts.append(paragraph(limit))
    return "\n\n".join(parts) + "\n"
