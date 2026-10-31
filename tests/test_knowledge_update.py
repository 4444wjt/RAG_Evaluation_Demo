"""Behavioral contract tests, independent of local work/ research materials.

Run: python -B -I -S tests/test_knowledge_update.py -v
"""
from __future__ import annotations

from copy import deepcopy
import html
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.knowledge_update import (  # noqa: E402
    CHECKER_VERSION, canonical_json, check, render_handover, sha256, source_paths,
)

IMPLEMENTATION = {"core_sha256": "test-core", "cli_sha256": "test-cli", "checker_version": CHECKER_VERSION}


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def task() -> dict:
    return {"schema_version": "knowledge-update-task/v1", "task_id": "测试",
            "sources": [{"source_id": "S", "old": {"path": "old.txt"}, "new": {"path": "new.txt"}}],
            "items": [{"item_id": "I", "old_draft": "旧稿", "source_ids": ["S"],
                       "old_citations": [{"source_id": "S", "quote": "命中"}]}]}


def revisions(rows: list[dict]) -> dict:
    return {"schema_version": "knowledge-update-revisions/v1", "revisions": rows}


def revision(**changes: object) -> dict:
    return {"item_id": "I", "proposed_draft": "新稿", "citations": [{"source_id": "S", "quote": "命中"}], **changes}


def run(value: dict | None = None, payloads: dict | None = None, updates: dict | None = None,
        implementation: dict | None = None) -> dict:
    return check(encoded(value if value is not None else task()),
                 payloads if payloads is not None else {"old.txt": "命中\n".encode(), "new.txt": "命中\n".encode()},
                 encoded(updates) if updates is not None else None,
                 mode="recheck" if updates is not None else "scan",
                 implementation=implementation or IMPLEMENTATION)


class KnowledgeUpdateTests(unittest.TestCase):
    def assert_denominators(self, report: dict) -> None:
        summary = report["summary"]
        self.assertEqual(summary["declared_item_count"], len(report["items"]))
        self.assertEqual(summary["result_item_count"], len(report["items"]))
        self.assertEqual(summary["declared_source_count"], len(report["sources"]))
        self.assertEqual(sum(summary["source_status_counts"].values()), len(report["sources"]))
        self.assertEqual(sum(summary["item_status_counts"].values()), len(report["items"]))
        synthetic = 0
        for side, content in (("old", "original"), ("new", "effective")):
            real = sum(len(item[content]["citations"]) for item in report["items"])
            checks = [row for item in report["items"] for row in item[side + "_checks"]]
            placeholders = sum(row["synthetic"] for row in checks)
            synthetic += placeholders
            self.assertEqual(summary[side + "_check_count"], real + placeholders)
            self.assertEqual(summary[side + "_check_count"], sum(summary["citation_status_counts_by_side"][side].values()))
            self.assertEqual(summary[content + "_citation_count"], real)
            self.assertEqual(summary[content + "_association_count"],
                             sum(len(item[content]["source_ids"]) for item in report["items"]))
        self.assertEqual(summary["synthetic_check_count"], synthetic)
        self.assertEqual(summary["publication_approved_count"], 0)
        self.assertTrue(all(item["semantic_evaluation"] == "NOT_EVALUATED" and item["publication_approved"] is False
                            for item in report["items"]))

    def test_pure_api_does_not_mutate_inputs_and_is_deterministic(self):
        value = task()
        data = {"old.txt": "命中\n".encode(), "new.txt": "命中\n".encode()}
        before = deepcopy((value, data, IMPLEMENTATION))
        first = run(value, data)
        self.assertEqual(canonical_json(first), canonical_json(run(value, data)))
        self.assertEqual((value, data, IMPLEMENTATION), before)
        self.assertEqual(first["items"][0]["status"], "NOT_EVALUATED")
        self.assertEqual(first["items"][0]["new_checks"][0]["status"], "MATCHED")
        self.assert_denominators(first)

    def test_added_deleted_empty_and_null_bindings(self):
        for old, new, data, status, old_check, new_check in [
            (None, {"path": "new.txt"}, {"new.txt": b""}, "ADDED", "SOURCE_ADDED", "NOT_FOUND"),
            ({"path": "old.txt"}, None, {"old.txt": b""}, "DELETED", "NOT_FOUND", "SOURCE_DELETED"),
            ({"path": "old.txt"}, {"path": "new.txt"}, {"old.txt": b"", "new.txt": b""},
             "UNCHANGED", "NOT_FOUND", "NOT_FOUND"),
        ]:
            with self.subTest(status=status):
                value = task()
                value["sources"][0].update(old=old, new=new)
                report = run(value, data)
                source = report["sources"][0]
                self.assertEqual(source["status"], status)
                self.assertEqual(report["items"][0]["old_checks"][0]["status"], old_check)
                self.assertEqual(report["items"][0]["new_checks"][0]["status"], new_check)
                for side, descriptor in (("old", old), ("new", new)):
                    self.assertEqual(source["versions"][side]["sha256"], None if descriptor is None else sha256(b""))
                    self.assertEqual(source["versions"][side]["line_count"], None if descriptor is None else 0)
                self.assert_denominators(report)

    def test_missing_and_invalid_utf8_are_read_errors_not_deletion(self):
        for payload in (None, b"\xff", {"code": "SOURCE_READ_ERROR", "message": "Unreadable source."}):
            with self.subTest(payload=payload):
                data = {"old.txt": "命中\n".encode()}
                if payload is not None:
                    data["new.txt"] = payload
                report = run(payloads=data)
                self.assertEqual(report["run_status"], "INPUT_ERROR")
                self.assertEqual(report["sources"][0]["status"], "READ_ERROR")
                self.assertEqual(report["items"][0]["new_checks"][0]["status"], "SOURCE_READ_ERROR")
                self.assertEqual(report["sources"][0]["versions"]["new"]["read_error"]["side"], "new")
                self.assert_denominators(report)

    def test_structural_errors_have_raw_lengths_but_no_business_denominators(self):
        variants = []
        value = task(); value["sources"][0].update(old=None, new=None); variants.append(value)
        value = task(); del value["sources"][0]["old"]; variants.append(value)
        value = task(); value["sources"].append(deepcopy(value["sources"][0])); variants.append(value)
        value = task(); value["items"].append(deepcopy(value["items"][0])); variants.append(value)
        value = task(); value["items"][0]["source_ids"].append("S"); variants.append(value)
        value = task(); value["items"][0]["source_idz"] = []; variants.append(value)
        value = task(); value["schema_version"] = "wrong"; variants.append(value)
        value = task(); value["items"][0]["question"] = None; variants.append(value)
        value = task(); value["items"][0]["old_draft"] = 1; variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                report = run(value)
                self.assertEqual(report["run_status"], "INPUT_ERROR")
                self.assertEqual(report["raw_declarations"]["sources"], len(value["sources"]))
                self.assertEqual(report["raw_declarations"]["items"], len(value["items"]))
                self.assertIsNone(report["items"])
                self.assertIsNone(report["sources"])
                self.assertIsNone(report["summary"]["result_item_count"])
                self.assertIsNone(report["summary"]["original_citation_count"])
                self.assertEqual(report["denominator_state"], "UNAVAILABLE")

    def test_json_rejects_duplicate_keys_nonjson_numbers_bad_utf8_and_syntax(self):
        for data in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff', b'{',
                     b'{"x":"\\ud800"}', b'[]', b'null'):
            with self.subTest(data=data):
                report = check(data, {}, mode="scan", implementation=IMPLEMENTATION)
                self.assertEqual(report["run_status"], "INPUT_ERROR")
                self.assertIsNone(report["summary"]["result_item_count"])
                self.assertEqual(report["bindings"]["task_sha256"], sha256(data))

    def test_paths_are_relative_supported_and_stay_in_package(self):
        for path in ("/tmp/a.txt", "C:/a.txt", "C:a.txt", "../a.txt", "a/../../b.txt", "\\\\host\\a.txt",
                     "x.pdf", "", "a.txt\x00"):
            with self.subTest(path=path):
                value = task(); value["sources"][0]["new"]["path"] = path
                self.assertEqual(run(value)["run_status"], "INPUT_ERROR")
                self.assertEqual(source_paths(encoded(value)), [])
        value = task(); value["sources"][0]["new"]["path"] = "中文 目录/新.MD"
        self.assertEqual(source_paths(encoded(value)), ["old.txt", "中文 目录/新.MD"])

    def test_position_structure_and_type_errors(self):
        for position in ({"line_start": 1}, {"line_end": 1}, {"line_start": True, "line_end": 1},
                         {"line_start": 0, "line_end": 1}, {"line_start": 3, "line_end": 2},
                         {"line_start": 1.0, "line_end": 1}, {"line_start": "1", "line_end": "1"}):
            with self.subTest(position=position):
                value = task(); value["items"][0]["old_citations"][0].update(position)
                report = run(value)
                self.assertEqual(report["run_status"], "INPUT_ERROR")
                self.assertIn("INVALID_LOCATION", [issue["code"] for issue in report["input_errors"]])
        value = task(); value["items"][0]["old_citations"][0].update(line_start=None, line_end=None)
        self.assertEqual(run(value)["run_status"], "COMPLETED")

    def test_literal_complete_line_matching_and_all_locations(self):
        text = "命中\n其他\n命中\n  \n"
        cases = [("命中", {}, "AMBIGUOUS", [(1, 1), (3, 3)], None),
                 ("命中", {"line_start": 3, "line_end": 3}, "MATCHED", [(1, 1), (3, 3)], "命中"),
                 ("命中", {"line_start": 2, "line_end": 2}, "LINE_MISMATCH", [(1, 1), (3, 3)], "其他"),
                 ("命中", {"line_start": 9, "line_end": 9}, "LINE_MISMATCH", [(1, 1), (3, 3)], None),
                 ("不存在", {"line_start": 2, "line_end": 2}, "NOT_FOUND", [], "其他"),
                 ("命", {}, "NOT_FOUND", [], None),
                 ("命中\n其他\n", {}, "MATCHED", [(1, 2)], None),
                 ("  ", {}, "MATCHED", [(4, 4)], None),
                 ("命中 ", {}, "NOT_FOUND", [], None)]
        for quote, position, expected, locations, declared_text in cases:
            with self.subTest(quote=quote, position=position):
                value = task(); value["items"][0]["old_citations"] = [{"source_id": "S", "quote": quote, **position}]
                report = run(value, {"old.txt": text.encode(), "new.txt": text.encode()})
                for side in ("old", "new"):
                    row = report["items"][0][side + "_checks"][0]
                    self.assertEqual(row["status"], expected)
                    self.assertEqual([(p["line_start"], p["line_end"]) for p in row["matched_locations"]], locations)
                    self.assertEqual(row["declared_text"], declared_text)

    def test_empty_missing_and_null_quotes_take_priority_and_preserve_rows(self):
        for citation in ({"source_id": "S"}, {"source_id": "S", "quote": None}, {"source_id": "S", "quote": ""}):
            with self.subTest(citation=citation):
                value = task(); value["items"][0]["old_citations"] = [citation]
                report = run(value, {})
                self.assertEqual(report["items"][0]["new_checks"][0]["status"], "MISSING_QUOTE")
                self.assertFalse(report["items"][0]["new_checks"][0]["synthetic"])
                self.assert_denominators(report)

    def test_all_splitlines_separators_bom_and_terminal_line_ending(self):
        for separator in ("\n", "\r\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)):
                value = task(); value["items"][0]["old_citations"][0]["quote"] = "第一\n第二\n"
                text = ("第一" + separator + "第二" + separator).encode()
                report = run(value, {"old.txt": text, "new.txt": text})
                self.assertEqual(report["items"][0]["new_checks"][0]["status"], "MATCHED")
                self.assertEqual(report["sources"][0]["versions"]["new"]["line_count"], 2)
        report = run(payloads={"old.txt": "命中\n".encode(), "new.txt": "\ufeff命中\n".encode()})
        self.assertEqual(report["sources"][0]["status"], "CHANGED")
        self.assertEqual(report["items"][0]["new_checks"][0]["status"], "NOT_FOUND")

    def test_repeated_quotes_have_separate_stable_rows_not_synthetic(self):
        value = task(); value["items"][0]["old_citations"] *= 2
        report = run(value, {"old.txt": "命中\n命中\n".encode(), "new.txt": "命中\n命中\n".encode()})
        rows = report["items"][0]["new_checks"]
        self.assertEqual([row["citation_index"] for row in rows], [0, 1])
        self.assertEqual([row["status"] for row in rows], ["AMBIGUOUS", "AMBIGUOUS"])
        self.assertTrue(all(len(row["matched_locations"]) == 2 for row in rows))
        self.assertEqual(report["summary"]["synthetic_check_count"], 0)
        self.assert_denominators(report)

    def test_synthetic_missing_quote_for_each_unquoted_association(self):
        value = task(); value["sources"].append({"source_id": "T", "old": None, "new": {"path": "t.txt"}})
        value["items"][0]["source_ids"].append("T")
        report = run(value, {"old.txt": "命中\n".encode(), "new.txt": "命中\n".encode(), "t.txt": b""})
        rows = report["items"][0]["new_checks"]
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[1]["status"], rows[1]["synthetic"], rows[1]["association_index"], rows[1]["citation_index"]),
                         ("MISSING_QUOTE", True, 1, None))
        self.assert_denominators(report)

    def test_no_association_does_not_invent_citations(self):
        value = task(); value["items"][0].update(source_ids=[], old_citations=[])
        report = run(value)
        self.assertEqual(report["items"][0]["old_checks"], [])
        self.assertEqual(report["items"][0]["new_checks"], [])
        self.assertIn("MISSING_ASSOCIATION", [reason["code"] for reason in report["items"][0]["review_reasons"]])
        self.assert_denominators(report)

    def test_unknown_sources_associations_and_citations_are_retained(self):
        value = task(); value["items"][0].update(source_ids=["S", "UNKNOWN"],
                                              old_citations=[{"source_id": "UNKNOWN", "quote": "命中"}])
        report = run(value)
        self.assertEqual(report["run_status"], "INPUT_ERROR")
        self.assertEqual(report["summary"]["original_association_count"], 2)
        self.assertEqual(report["items"][0]["new_checks"][0]["status"], "UNKNOWN_SOURCE")
        self.assertEqual(report["coverage"]["effective_unknown_associations"][0]["association_index"], 1)
        self.assertEqual(report["items"][0]["effective"]["source_statuses"][1]["status"], "UNKNOWN_SOURCE")
        self.assert_denominators(report)

    def test_known_citation_outside_association_is_error_with_literal_status(self):
        value = task(); value["items"][0]["source_ids"] = []
        report = run(value)
        row = report["items"][0]["new_checks"][0]
        self.assertEqual(row["status"], "CITATION_OUTSIDE_ASSOCIATION")
        self.assertEqual(row["literal_status"], "MATCHED")
        self.assertEqual(row["matched_locations"], [{"line_start": 1, "line_end": 1}])
        self.assertEqual(report["run_status"], "INPUT_ERROR")
        self.assertEqual([issue["location"] for issue in report["input_errors"]],
                         ["$task.items[0].old_citations[0]", "$task.items[0].old_citations[0]"])
        self.assert_denominators(report)

    def test_revision_diagnostics_point_to_actual_citation_and_inherited_association(self):
        value = task(); value["items"][0]["source_ids"].append("UNKNOWN")
        report = run(value, updates=revisions([revision(citations=[{"source_id": "MISSING", "quote": "x"}])]))
        self.assertIn("$revisions.revisions[0].citations[0]", [issue["location"] for issue in report["input_errors"]])
        inherited = [issue for issue in report["input_errors"] if issue.get("source_id") == "UNKNOWN"]
        self.assertTrue(all(issue["location"] == "$task.items[0].source_ids[1]" for issue in inherited))

    def test_partial_empty_and_unknown_revisions_keep_every_item(self):
        value = task(); value["items"].append({"item_id": "SECOND", "old_draft": "留存", "source_ids": [], "old_citations": []})
        for rows, submitted in (([], 0), ([revision()], 1), ([revision(), revision(item_id="MISSING")], 1)):
            with self.subTest(rows=rows):
                report = run(value, updates=revisions(rows))
                self.assertEqual([item["item_id"] for item in report["items"]], ["I", "SECOND"])
                self.assertEqual(report["summary"]["submitted_revision_count"], submitted)
                self.assertEqual(report["summary"]["unsubmitted_item_count"], 2 - submitted)
                if len(rows) == 2:
                    self.assertEqual(report["run_status"], "INPUT_ERROR")
                    self.assertEqual(report["unapplied_revisions"][0]["revision_index"], 1)
                    self.assertEqual(report["unapplied_revisions"][0]["item_id"], "MISSING")
                self.assert_denominators(report)

    def test_revision_unknown_keys_duplicate_ids_and_types_are_structure_errors(self):
        for rows in ([revision(), revision()], [revision(source_ids=["S", "S"])],
                     [revision(open_questions="bad")], [revision(citationz=[])], [revision(citations=None)]):
            with self.subTest(rows=rows):
                report = run(updates=revisions(rows))
                self.assertEqual(report["run_status"], "INPUT_ERROR")
                self.assertIsNone(report["summary"]["result_item_count"])
                self.assertEqual(report["raw_declarations"]["revisions"], len(rows))

    def test_revision_inherits_replaces_adds_removes_and_clears_associations(self):
        value = task(); value["sources"].append({"source_id": "T", "old": None, "new": {"path": "t.txt"}})
        data = {"old.txt": "命中\n".encode(), "new.txt": "命中\n".encode(), "t.txt": "新引用\n".encode()}
        for row, effective, added, removed, states in [
            (revision(), ["S"], [], [], ["MATCHED"]),
            (revision(source_ids=["T"], citations=[{"source_id": "T", "quote": "新引用"}]), ["T"], ["T"], ["S"], ["MATCHED"]),
            (revision(source_ids=["S", "T"], citations=[{"source_id": "T", "quote": "新引用"}]), ["S", "T"], ["T"], [],
             ["MATCHED", "MISSING_QUOTE"]),
            (revision(source_ids=[], citations=[]), [], [], ["S"], []),
            (revision(citations=[]), ["S"], [], [], ["MISSING_QUOTE"]),
        ]:
            with self.subTest(row=row):
                report = run(value, data, revisions([row])); item = report["items"][0]
                self.assertEqual(item["effective"]["source_ids"], effective)
                self.assertEqual(item["added_source_ids"], added)
                self.assertEqual(item["removed_source_ids"], removed)
                self.assertEqual([check["status"] for check in item["new_checks"]], states)
                self.assertEqual(item["old_checks"][0]["source_id"], "S")
                self.assert_denominators(report)

    def test_new_citation_not_in_old_map_cannot_be_silently_filtered(self):
        value = task(); value["sources"].append({"source_id": "T", "old": None, "new": {"path": "t.txt"}})
        data = {"old.txt": "命中\n".encode(), "new.txt": "命中\n".encode(), "t.txt": "新引用\n".encode()}
        report = run(value, data, revisions([revision(citations=[{"source_id": "T", "quote": "新引用"}])]))
        self.assertEqual(report["run_status"], "INPUT_ERROR")
        rows = report["items"][0]["new_checks"]
        self.assertEqual([row["source_id"] for row in rows], ["T", "S"])
        self.assertEqual([row["status"] for row in rows], ["CITATION_OUTSIDE_ASSOCIATION", "MISSING_QUOTE"])
        self.assertEqual(rows[0]["literal_status"], "MATCHED")
        self.assert_denominators(report)

    def test_source_coverage_changed_added_deleted_and_last_removed_association(self):
        value = task()
        value["sources"].extend([
            {"source_id": "ADD", "old": None, "new": {"path": "add.txt"}},
            {"source_id": "DEL", "old": {"path": "del.txt"}, "new": None}])
        report = run(value, {"old.txt": b"old", "new.txt": b"new", "add.txt": b"add", "del.txt": b"del"},
                     revisions([revision(source_ids=[], citations=[])]))
        self.assertEqual(report["coverage"]["original_unassociated_changed_source_ids"], ["ADD", "DEL"])
        self.assertEqual(report["coverage"]["effective_unassociated_changed_source_ids"], ["S", "ADD", "DEL"])
        self.assertEqual(report["summary"]["coverage_gap_count"], 3)
        self.assert_denominators(report)

    def test_multiple_sources_keep_match_missing_and_read_failure_independent(self):
        value = task()
        for sid in ("T", "U"):
            value["sources"].append({"source_id": sid, "old": {"path": sid + ".txt"}, "new": {"path": sid + ".txt"}})
            value["items"][0]["source_ids"].append(sid)
            value["items"][0]["old_citations"].append({"source_id": sid, "quote": "命中"})
        report = run(value, {"old.txt": "命中\n".encode(), "new.txt": "命中\n".encode(), "T.txt": b"else", "U.txt": b"\xff"})
        self.assertEqual([row["status"] for row in report["items"][0]["new_checks"]],
                         ["MATCHED", "NOT_FOUND", "SOURCE_READ_ERROR"])
        self.assertEqual(report["run_status"], "INPUT_ERROR")
        self.assert_denominators(report)

    def test_semantics_never_approved_even_when_quote_survives_condition_change(self):
        value = task(); value["items"][0]["old_draft"] = "事实错误但工具不能判断"
        for old, new, expected_status in [("命中\n仅限A\n", "命中\n仅限B\n", "PENDING_REVIEW"),
                                           ("命中\n", "命中\n", "NOT_EVALUATED")]:
            report = run(value, {"old.txt": old.encode(), "new.txt": new.encode()})
            self.assertEqual(report["items"][0]["new_checks"][0]["status"], "MATCHED")
            self.assertEqual(report["items"][0]["status"], expected_status)
            self.assert_denominators(report)

    def test_open_questions_are_retained_without_interpretation(self):
        report = run(updates=revisions([revision(open_questions=["已解决？", "未解决|`\n内容"])]))
        self.assertEqual(report["items"][0]["open_questions"], ["已解决？", "未解决|`\n内容"])
        self.assertEqual(report["items"][0]["status"], "PENDING_REVIEW")

    def test_byte_only_changes_and_standard_unified_diff_labels(self):
        for old, new in ((b"a\r\n", b"a\n"), (b"a", b"a\n")):
            report = run(payloads={"old.txt": old, "new.txt": new})
            source = report["sources"][0]
            self.assertEqual(source["status"], "CHANGED")
            self.assertTrue(source["byte_only_change"])
            self.assertEqual(source["diff"], "")
            self.assertNotEqual(source["versions"]["old"]["sha256"], source["versions"]["new"]["sha256"])
        report = run(payloads={"old.txt": b"old\n", "new.txt": b"new\n"})
        self.assertEqual(report["sources"][0]["diff"], "--- old.txt\n+++ new.txt\n@@ -1 +1 @@\n-old\n+new\n")

    def test_task_fingerprint_changes_for_draft_association_source_and_core(self):
        baseline = run()
        variants = []
        value = task(); value["items"][0]["old_draft"] = "只改稿"; variants.append(run(value))
        value = task(); value["items"][0].update(source_ids=[], old_citations=[]); variants.append(run(value))
        variants.append(run(payloads={"old.txt": "命中\n".encode(), "new.txt": "命中\r\n".encode()}))
        variants.append(run(implementation={**IMPLEMENTATION, "core_sha256": "changed"}))
        variants.append(run(implementation={**IMPLEMENTATION, "checker_version": "changed"}))
        for changed in variants:
            self.assertNotEqual(baseline["bindings"]["task_fingerprint"], changed["bindings"]["task_fingerprint"])
            self.assertNotEqual(baseline["bindings"]["run_fingerprint"], changed["bindings"]["run_fingerprint"])
        cli = run(implementation={**IMPLEMENTATION, "cli_sha256": "changed"})
        self.assertEqual(baseline["bindings"]["task_fingerprint"], cli["bindings"]["task_fingerprint"])
        self.assertNotEqual(baseline["bindings"]["run_fingerprint"], cli["bindings"]["run_fingerprint"])

    def test_revision_draft_and_mapping_change_run_binding_not_task_binding(self):
        first = run(updates=revisions([revision()]))
        for row in (revision(proposed_draft="另一个稿"), revision(source_ids=[], citations=[])):
            changed = run(updates=revisions([row]))
            self.assertEqual(first["bindings"]["task_fingerprint"], changed["bindings"]["task_fingerprint"])
            self.assertNotEqual(first["bindings"]["run_fingerprint"], changed["bindings"]["run_fingerprint"])
            self.assertTrue(changed["items"][0]["draft_changed"])
        changed = run(updates=revisions([revision(proposed_draft="旧稿")]))
        self.assertFalse(changed["items"][0]["draft_changed"])
        self.assertEqual(changed["items"][0]["new_checks"][0]["status"], "MATCHED")

    def test_empty_arrays_have_explicit_zero_denominators(self):
        value = {"schema_version": "knowledge-update-task/v1", "task_id": "空", "sources": [], "items": []}
        report = run(value, {}, revisions([]))
        self.assertEqual(report["run_status"], "COMPLETED")
        self.assertEqual(report["summary"]["result_item_count"], 0)
        self.assert_denominators(report)

    def test_provenance_is_unverified_preserved_and_handover_has_no_truncation(self):
        value = task(); value["provenance"] = {"author": "声明<未经认证>", "custom": [1, None, {"x": True}]}
        special = "`|\n</pre><script>unsafe</script>```\n" + "长文" * 3000
        value["items"][0]["old_draft"] = special
        value["items"][0]["old_citations"][0]["quote"] = special
        report = run(value)
        handover = render_handover(report)
        self.assertEqual(report["provenance"], value["provenance"])
        self.assertNotIn("<script>", handover)
        blocks = [html.unescape(block.split("</pre>", 1)[0]) for block in handover.split("<pre>")[1:]]
        self.assertEqual(blocks.count(special), 4)  # both drafts and both citation rows
        self.assertIn(report["sources"][0]["diff"], blocks)
        self.assertIn(json.dumps(report["bindings"], ensure_ascii=False, indent=2), blocks)
        self.assertIn("原/有效真实引用：1 / 1", handover)
        self.assertIn("NOT_FOUND", handover)
        self.assertIn("SCAN_ONLY", handover)
        self.assertNotIn("<pre>\n<", handover)

    def test_handover_lists_every_repeated_match_and_open_question(self):
        report = run(payloads={"old.txt": "命中\n命中\n".encode(), "new.txt": "命中\n命中\n".encode()},
                     updates=revisions([revision(open_questions=["待核|`\n完整问题"])]))
        handover = render_handover(report)
        self.assertIn("全部命中位置：1-1, 2-2", handover)
        self.assertIn("待核|`\n完整问题", html.unescape(handover))
        self.assertIn("AMBIGUOUS", handover)
        self.assertIn("SUBMITTED", handover)

    def test_scan_forbids_revisions_and_recheck_requires_them(self):
        for mode, updates in (("scan", encoded(revisions([]))), ("recheck", None), ("invalid", None)):
            report = check(encoded(task()), {}, updates, mode=mode, implementation=IMPLEMENTATION)
            self.assertEqual(report["run_status"], "INPUT_ERROR")
            self.assertIsNone(report["items"])


if __name__ == "__main__":
    unittest.main()
