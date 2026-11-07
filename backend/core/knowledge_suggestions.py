"""Literal, source-local replacement blocks for a completed knowledge scan.

Candidates describe diff blocks, not semantic replacements or revised drafts.
The caller supplies the same source payloads used by the scan.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher

from backend.core.knowledge_update import canonical_json, sha256

SUGGESTER_VERSION = "knowledge-suggestions/v1"


def _location(start: int, end: int) -> dict | None:
    return {"line_start": start + 1, "line_end": end} if start < end else None


def _changed_blocks(source: dict, payloads: dict[str, bytes | dict]) -> list[dict]:
    line_sets = {}
    for side in ("old", "new"):
        version = source["versions"][side]
        payload = payloads[version["path"]]
        if not isinstance(payload, bytes) or sha256(payload) != version["sha256"]:
            raise ValueError("Source payload does not match the scan binding.")
        line_sets[side] = payload.decode("utf-8").splitlines()
    blocks = []
    for tag, old_start, old_end, new_start, new_end in SequenceMatcher(
            None, line_sets["old"], line_sets["new"], autojunk=False).get_opcodes():
        if tag not in {"replace", "delete"}:
            continue
        blocks.append({"kind": "BLOCK_CANDIDATE" if tag == "replace" else "DELETED_BLOCK",
                       "old_location": _location(old_start, old_end),
                       "new_location": _location(new_start, new_end),
                       "old_lines": line_sets["old"][old_start:old_end],
                       "new_lines": line_sets["new"][new_start:new_end] if tag == "replace" else None})
    return blocks


def _overlapping_blocks(blocks: list[dict], locations: list[dict]) -> list[dict]:
    result = []
    for block in blocks:
        start, end = block["old_location"]["line_start"], block["old_location"]["line_end"]
        overlaps = []
        for location in locations:
            match_start, match_end = location["line_start"], location["line_end"]
            if max(start, match_start) <= min(end, match_end):
                overlaps.append({"old_match_location": dict(location),
                                 "intersection": {"line_start": max(start, match_start),
                                                  "line_end": min(end, match_end)},
                                 "covers_entire_citation": start <= match_start and end >= match_end,
                                 "block_extends_beyond_citation": start < match_start or end > match_end})
        if overlaps:
            result.append({**block, "overlaps": overlaps, "scope": "DIFF_BLOCK",
                           "semantic_evaluation": "NOT_EVALUATED", "publication_approved": False})
    return result


def suggest(scan_report: dict, source_payloads: dict[str, bytes | dict], *,
            implementation: dict) -> dict:
    """Wrap one scan, retaining every real old citation and the full scan report.

    Only old MATCHED/AMBIGUOUS and new NOT_FOUND rows receive diff blocks.
    Explicit, valid old positions select the relevant occurrence; otherwise all
    old matches remain in scope. Synthetic missing-citation rows stay in scan.
    No source mapping, citation check, or semantic evaluation is performed here.
    """
    if scan_report["mode"] != "scan":
        raise ValueError("Suggestions require a scan report.")
    bindings = {"task_fingerprint": scan_report["bindings"]["task_fingerprint"],
                "scan_run_fingerprint": scan_report["bindings"]["run_fingerprint"],
                "implementation": {"scan": deepcopy(scan_report["bindings"]["implementation"]),
                                   "suggestions": deepcopy(implementation)}}
    bindings["suggestion_fingerprint"] = sha256(canonical_json(bindings))
    rows = None
    if scan_report["items"] is not None:
        rows = []
        sources = {source["source_id"]: source for source in scan_report["sources"]}
        block_cache: dict[str, list[dict]] = {}
        for item in scan_report["items"]:
            new_checks = {row["citation_index"]: row for row in item["new_checks"] if not row["synthetic"]}
            for old in item["old_checks"]:
                if old["synthetic"]:
                    continue
                new = new_checks[old["citation_index"]]
                locations = deepcopy(old["matched_locations"])
                if old["status"] == "MATCHED" and old["declared_line_start"] is not None:
                    locations = [{"line_start": old["declared_line_start"],
                                  "line_end": old["declared_line_end"]}]
                eligible = old["status"] in {"MATCHED", "AMBIGUOUS"} and new["status"] == "NOT_FOUND"
                blocks = []
                if eligible:
                    source_id = old["source_id"]
                    if source_id not in block_cache:
                        block_cache[source_id] = _changed_blocks(sources[source_id], source_payloads)
                    blocks = _overlapping_blocks(block_cache[source_id], locations)
                candidates = [block for block in blocks if block["kind"] == "BLOCK_CANDIDATE"]
                deleted = [block for block in blocks if block["kind"] == "DELETED_BLOCK"]
                status = ("BLOCK_CANDIDATES" if candidates else "DELETION_ONLY" if deleted else
                          "NO_REPLACE_BLOCK" if eligible else "NOT_ELIGIBLE")
                rows.append({"item_id": item["item_id"], "citation_index": old["citation_index"],
                             "source_id": old["source_id"],
                             "source_status": sources[old["source_id"]]["status"] if old["source_id"] in sources else "UNKNOWN_SOURCE",
                             "old_check": deepcopy(old), "new_check": deepcopy(new),
                             "selected_old_locations": locations, "ambiguous": old["status"] == "AMBIGUOUS",
                             "eligible": eligible, "status": status, "candidates": candidates,
                             "deleted_blocks": deleted, "semantic_evaluation": "NOT_EVALUATED",
                             "publication_approved": False})
    return {"schema_version": "knowledge-suggestions-report/v1", "suggester_version": SUGGESTER_VERSION,
            "run_status": scan_report["run_status"], "task_id": scan_report["task_id"],
            "bindings": bindings, "scan_report": scan_report, "suggestions": rows,
            "summary": {"original_citation_count": scan_report["summary"]["original_citation_count"],
                        "suggestion_record_count": len(rows) if rows is not None else None,
                        "eligible_citation_count": sum(row["eligible"] for row in rows) if rows is not None else None,
                        "candidate_block_count": sum(len(row["candidates"]) for row in rows) if rows is not None else None,
                        "status_counts": dict(Counter(row["status"] for row in rows)) if rows is not None else None},
            "semantic_evaluation": "NOT_EVALUATED", "publication_approved": False,
            "limits": ["Candidates are source-local line diff blocks, not semantic replacements.",
                       "Blocks may include other text or cover only part of a multiline citation.",
                       "No draft is generated or changed; deleted text has no replacement candidate."]}
