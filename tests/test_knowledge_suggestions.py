"""Standard-library contracts; CLI tests call main without host subprocesses."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from difflib import SequenceMatcher
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core import knowledge_update as core  # noqa: E402
from backend.core.knowledge_suggestions import SUGGESTER_VERSION, suggest  # noqa: E402
from scripts import knowledge_suggest as cli  # noqa: E402

SCAN_IMPLEMENTATION = {"core_sha256": "scan-core", "cli_sha256": "scan-cli",
                       "checker_version": core.CHECKER_VERSION}
IMPLEMENTATION = {"core_sha256": "suggest-core", "cli_sha256": "suggest-cli",
                  "suggester_version": SUGGESTER_VERSION}


def task(quote: str = "金额100") -> dict:
    return {"schema_version": "knowledge-update-task/v1", "task_id": "候选测试",
            "sources": [{"source_id": "S", "old": {"path": "old.txt"}, "new": {"path": "new.txt"}}],
            "items": [{"item_id": "I", "old_draft": "原稿保持", "source_ids": ["S"],
                       "old_citations": [{"source_id": "S", "quote": quote}]}]}


def run(value: dict | None = None, old: bytes = "金额100\n".encode(),
        new: bytes = "金额200\n".encode(), payloads: dict | None = None) -> dict:
    data = payloads if payloads is not None else {"old.txt": old, "new.txt": new}
    scan = core.check(core.canonical_json(value if value is not None else task()), data,
                      mode="scan", implementation=SCAN_IMPLEMENTATION)
    return suggest(scan, data, implementation=IMPLEMENTATION)


class KnowledgeSuggestionTests(unittest.TestCase):
    def assert_preserved(self, report: dict) -> None:
        scan = report["scan_report"]
        self.assertEqual(report["run_status"], scan["run_status"])
        self.assertEqual(report["summary"]["original_citation_count"], scan["summary"]["original_citation_count"])
        if scan["items"] is not None:
            self.assertEqual(len(report["suggestions"]), scan["summary"]["original_citation_count"])
            for row in report["suggestions"]:
                self.assertEqual(row["old_check"]["citation_index"], row["citation_index"])
                self.assertEqual(row["source_id"], row["new_check"]["source_id"])
                self.assertEqual(row["semantic_evaluation"], "NOT_EVALUATED")
                self.assertFalse(row["publication_approved"])
        self.assertEqual(report["semantic_evaluation"], "NOT_EVALUATED")
        self.assertFalse(report["publication_approved"])

    def test_numeric_replacement_keeps_full_block_and_original_scan(self):
        report = run(old="标题\n金额100\n结束\n".encode(), new="标题\n金额200\n结束\n".encode())
        row = report["suggestions"][0]
        self.assertEqual((row["item_id"], row["citation_index"], row["source_id"]), ("I", 0, "S"))
        self.assertEqual(row["status"], "BLOCK_CANDIDATES")
        self.assertEqual(row["old_check"]["status"], "MATCHED")
        self.assertEqual(row["new_check"]["status"], "NOT_FOUND")
        self.assertEqual(row["candidates"][0], {
            "kind": "BLOCK_CANDIDATE", "old_location": {"line_start": 2, "line_end": 2},
            "new_location": {"line_start": 2, "line_end": 2}, "old_lines": ["金额100"],
            "new_lines": ["金额200"], "overlaps": [{"old_match_location": {"line_start": 2, "line_end": 2},
            "intersection": {"line_start": 2, "line_end": 2}, "covers_entire_citation": True,
            "block_extends_beyond_citation": False}], "scope": "DIFF_BLOCK",
            "semantic_evaluation": "NOT_EVALUATED", "publication_approved": False})
        self.assertNotEqual(row["old_check"]["source_sha256"], row["new_check"]["source_sha256"])
        self.assertEqual(report["scan_report"]["items"][0]["original"]["draft"], "原稿保持")
        self.assert_preserved(report)

    def test_repeated_quote_without_location_retains_both_candidates(self):
        report = run(old="头\n金额100\n中\n金额100\n尾\n".encode(),
                     new="头\n金额200\n中\n金额300\n尾\n".encode())
        row = report["suggestions"][0]
        self.assertTrue(row["ambiguous"])
        self.assertEqual(row["old_check"]["status"], "AMBIGUOUS")
        self.assertEqual(row["selected_old_locations"], [{"line_start": 2, "line_end": 2}, {"line_start": 4, "line_end": 4}])
        self.assertEqual([block["new_lines"] for block in row["candidates"]], [["金额200"], ["金额300"]])

    def test_valid_declared_location_selects_only_that_occurrence(self):
        value = task(); value["items"][0]["old_citations"][0].update(line_start=4, line_end=4)
        row = run(value, old="头\n金额100\n中\n金额100\n尾\n".encode(),
                  new="头\n金额200\n中\n金额300\n尾\n".encode())["suggestions"][0]
        self.assertEqual(row["old_check"]["matched_locations"], [{"line_start": 2, "line_end": 2}, {"line_start": 4, "line_end": 4}])
        self.assertEqual(row["selected_old_locations"], [{"line_start": 4, "line_end": 4}])
        self.assertEqual([block["new_lines"] for block in row["candidates"]], [["金额300"]])
        self.assertFalse(row["ambiguous"])

    def test_wrong_declared_location_does_not_choose_other_match(self):
        value = task(); value["items"][0]["old_citations"][0].update(line_start=1, line_end=1)
        row = run(value, old="标题\n金额100\n".encode(), new="标题\n金额200\n".encode())["suggestions"][0]
        self.assertEqual(row["old_check"]["status"], "LINE_MISMATCH")
        self.assertEqual(row["old_check"]["matched_locations"], [{"line_start": 2, "line_end": 2}])
        self.assertFalse(row["eligible"])
        self.assertEqual(row["candidates"], [])

    def test_multiline_citation_spanning_blocks_marks_partial_coverage(self):
        report = run(task("甲旧\n中间\n乙旧"), old="头\n甲旧\n中间\n乙旧\n尾\n".encode(),
                     new="头\n甲新\n中间\n乙新\n尾\n".encode())
        blocks = report["suggestions"][0]["candidates"]
        self.assertEqual([block["old_lines"] for block in blocks], [["甲旧"], ["乙旧"]])
        self.assertEqual([block["new_lines"] for block in blocks], [["甲新"], ["乙新"]])
        self.assertTrue(all(not block["overlaps"][0]["covers_entire_citation"] for block in blocks))
        self.assertTrue(all(block["scope"] == "DIFF_BLOCK" for block in blocks))

    def test_larger_block_keeps_other_lines_and_discloses_extent(self):
        row = run(old="左旧\n金额100\n右旧\n".encode(), new="合并新段\n".encode())["suggestions"][0]
        block = row["candidates"][0]
        self.assertEqual(block["old_lines"], ["左旧", "金额100", "右旧"])
        self.assertEqual(block["old_location"], {"line_start": 1, "line_end": 3})
        self.assertTrue(block["overlaps"][0]["covers_entire_citation"])
        self.assertTrue(block["overlaps"][0]["block_extends_beyond_citation"])

    def test_pure_deletion_has_no_replacement_text(self):
        row = run(old="头\n金额100\n尾\n".encode(), new="头\n尾\n".encode())["suggestions"][0]
        self.assertEqual(row["status"], "DELETION_ONLY")
        self.assertEqual(row["candidates"], [])
        self.assertEqual(row["deleted_blocks"][0]["old_lines"], ["金额100"])
        self.assertIsNone(row["deleted_blocks"][0]["new_lines"])
        self.assertIsNone(row["deleted_blocks"][0]["new_location"])

    def test_empty_new_source_is_deletion_with_null_new_location(self):
        row = run(new=b"")["suggestions"][0]
        self.assertEqual(row["status"], "DELETION_ONLY")
        self.assertIsNone(row["deleted_blocks"][0]["new_location"])

    def test_new_source_and_deleted_source_keep_states(self):
        for missing, expected in (("old", "SOURCE_ADDED"), ("new", "SOURCE_DELETED")):
            with self.subTest(missing=missing):
                value = task(); value["sources"][0][missing] = None
                report = run(value)
                row = report["suggestions"][0]
                self.assertEqual(row[missing + "_check"]["status"], expected)
                self.assertFalse(row["eligible"])
                self.assertEqual(row["candidates"], [])
                self.assert_preserved(report)

    def test_insertions_and_moves_with_surviving_quote_have_no_candidates(self):
        for text in ("增加\n金额100\n", "尾\n金额100\n头\n"):
            with self.subTest(text=text):
                row = run(old="头\n金额100\n尾\n".encode(), new=text.encode())["suggestions"][0]
                self.assertEqual(row["new_check"]["status"], "MATCHED")
                self.assertEqual(row["candidates"], [])
                self.assertFalse(row["eligible"])

    def test_moved_declared_quote_keeps_line_mismatch_and_no_candidates(self):
        value = task(); value["items"][0]["old_citations"][0].update(line_start=1, line_end=1)
        row = run(value, new="增加\n金额100\n".encode())["suggestions"][0]
        self.assertEqual(row["new_check"]["status"], "LINE_MISMATCH")
        self.assertEqual(row["new_check"]["matched_locations"], [{"line_start": 2, "line_end": 2}])
        self.assertEqual(row["candidates"], [])

    def test_no_cross_source_matching(self):
        value = task()
        value["sources"].append({"source_id": "T", "old": None, "new": {"path": "other.txt"}})
        report = run(value, payloads={"old.txt": "金额100\n".encode(), "new.txt": "金额200\n".encode(),
                                      "other.txt": "金额100\n".encode()})
        row = report["suggestions"][0]
        self.assertEqual(row["new_check"]["status"], "NOT_FOUND")
        self.assertEqual(row["source_id"], "S")
        self.assertEqual(row["candidates"][0]["new_lines"], ["金额200"])
        self.assertEqual(report["scan_report"]["coverage"]["original_unassociated_changed_source_ids"], ["T"])

    def test_source_opcodes_are_cached_across_citations_and_items(self):
        value = task(); second = deepcopy(value["items"][0]); second["item_id"] = "J"; value["items"].append(second)
        with patch("backend.core.knowledge_suggestions.SequenceMatcher", wraps=SequenceMatcher) as matcher:
            report = run(value)
        self.assertEqual(matcher.call_count, 1)
        self.assertEqual(matcher.call_args.kwargs, {"autojunk": False})
        self.assertEqual([row["item_id"] for row in report["suggestions"]], ["I", "J"])
        self.assertEqual(report["summary"]["candidate_block_count"], 2)

    def test_empty_and_missing_quotes_keep_real_rows_and_synthetic_scan_rows(self):
        value = task("")
        value["items"][0]["old_citations"].append({"source_id": "S"})
        second = deepcopy(value["items"][0]); second.update(item_id="J", old_citations=[]); value["items"].append(second)
        report = run(value)
        self.assertEqual(len(report["suggestions"]), 2)
        self.assertEqual([row["citation_index"] for row in report["suggestions"]], [0, 1])
        self.assertTrue(all(row["old_check"]["status"] == "MISSING_QUOTE" for row in report["suggestions"]))
        self.assertEqual(report["scan_report"]["summary"]["synthetic_check_count"], 2)
        self.assert_preserved(report)

    def test_crlf_matches_logical_lines_and_hashes_stay_byte_specific(self):
        report = run(old="金额100\r\n".encode(), new="金额200\r\n".encode())
        row = report["suggestions"][0]
        self.assertEqual(row["candidates"][0]["new_lines"], ["金额200"])
        self.assertEqual(row["old_check"]["source_sha256"], core.sha256("金额100\r\n".encode()))
        self.assertNotEqual(row["old_check"]["source_sha256"], core.sha256("金额100\n".encode()))

    def test_byte_only_change_does_not_invent_candidate(self):
        report = run(old="金额100\r\n".encode(), new="金额100\n".encode())
        self.assertTrue(report["scan_report"]["sources"][0]["byte_only_change"])
        self.assertEqual(report["suggestions"][0]["new_check"]["status"], "MATCHED")
        self.assertEqual(report["suggestions"][0]["candidates"], [])

    def test_bom_is_preserved_as_literal_text(self):
        report = run(old="\ufeff金额100\n".encode(), new="\ufeff金额200\n".encode())
        self.assertEqual(report["suggestions"][0]["old_check"]["status"], "NOT_FOUND")
        self.assertEqual(report["suggestions"][0]["candidates"], [])
        literal = run(task("\ufeff金额100"), old="\ufeff金额100\n".encode(), new="\ufeff金额200\n".encode())
        self.assertEqual(literal["suggestions"][0]["candidates"][0]["new_lines"], ["\ufeff金额200"])

    def test_invalid_utf8_and_read_errors_preserve_input_errors(self):
        for payload in (b"\xff", {"code": "SOURCE_READ_ERROR", "message": "unreadable"}):
            with self.subTest(payload=payload):
                report = run(payloads={"old.txt": "金额100\n".encode(), "new.txt": payload})
                self.assertEqual(report["run_status"], "INPUT_ERROR")
                self.assertEqual(report["suggestions"][0]["new_check"]["status"], "SOURCE_READ_ERROR")
                self.assertEqual(report["suggestions"][0]["candidates"], [])
                self.assertTrue(report["scan_report"]["input_errors"])
                self.assert_preserved(report)

    def test_unknown_and_outside_mapping_errors_are_not_filtered(self):
        for source_id, associations, status in (("UNKNOWN", ["S"], "UNKNOWN_SOURCE"),
                                                ("S", [], "CITATION_OUTSIDE_ASSOCIATION")):
            with self.subTest(status=status):
                value = task(); value["items"][0]["source_ids"] = associations
                value["items"][0]["old_citations"][0]["source_id"] = source_id
                report = run(value)
                self.assertEqual(report["suggestions"][0]["old_check"]["status"], status)
                self.assertEqual(report["suggestions"][0]["new_check"]["status"], status)
                self.assertEqual(report["suggestions"][0]["candidates"], [])
                self.assert_preserved(report)

    def test_old_not_found_keeps_denominator(self):
        report = run(old="不是原引文\n".encode())
        self.assertEqual(report["suggestions"][0]["old_check"]["status"], "NOT_FOUND")
        self.assertEqual(report["suggestions"][0]["status"], "NOT_ELIGIBLE")
        self.assert_preserved(report)

    def test_empty_task_has_explicit_zero_counts(self):
        report = run({"schema_version": "knowledge-update-task/v1", "task_id": "空", "sources": [], "items": []}, payloads={})
        self.assertEqual(report["suggestions"], [])
        self.assertEqual(report["summary"], {"original_citation_count": 0, "suggestion_record_count": 0,
                                             "eligible_citation_count": 0, "candidate_block_count": 0, "status_counts": {}})
        self.assert_preserved(report)

    def test_structural_errors_do_not_invent_business_denominators(self):
        scan = core.check(b"{}", {}, mode="scan", implementation=SCAN_IMPLEMENTATION)
        report = suggest(scan, {}, implementation=IMPLEMENTATION)
        self.assertIsNone(report["suggestions"])
        self.assertIsNone(report["summary"]["suggestion_record_count"])
        self.assertEqual(report["scan_report"], scan)
        self.assert_preserved(report)

    def test_bindings_include_four_implementations_and_task_fingerprint(self):
        report = run()
        self.assertEqual(report["bindings"]["task_fingerprint"], report["scan_report"]["bindings"]["task_fingerprint"])
        self.assertEqual(report["bindings"]["implementation"], {"scan": SCAN_IMPLEMENTATION, "suggestions": IMPLEMENTATION})
        changed = suggest(report["scan_report"], {"old.txt": "金额100\n".encode(), "new.txt": "金额200\n".encode()},
                          implementation={**IMPLEMENTATION, "cli_sha256": "changed"})
        self.assertNotEqual(report["bindings"]["suggestion_fingerprint"], changed["bindings"]["suggestion_fingerprint"])
        self.assertEqual(report["bindings"]["task_fingerprint"], changed["bindings"]["task_fingerprint"])

    def test_payload_mismatch_is_not_given_a_false_binding(self):
        scan = run()["scan_report"]
        with self.assertRaises(ValueError):
            suggest(scan, {"old.txt": b"unbound", "new.txt": "金额200\n".encode()}, implementation=IMPLEMENTATION)

    def test_determinism_and_no_scan_mutation(self):
        report = run(); scan_before = deepcopy(report["scan_report"])
        again = suggest(report["scan_report"], {"old.txt": "金额100\n".encode(), "new.txt": "金额200\n".encode()},
                        implementation=IMPLEMENTATION)
        self.assertEqual(core.canonical_json(report), core.canonical_json(again))
        self.assertEqual(report["scan_report"], scan_before)

    def test_recheck_report_is_not_accepted_as_a_scan(self):
        scan = run()["scan_report"]; scan["mode"] = "recheck"
        with self.assertRaises(ValueError):
            suggest(scan, {}, implementation=IMPLEMENTATION)


class KnowledgeSuggestCliTests(unittest.TestCase):
    def invoke(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_existing_example_uses_exactly_one_scan_and_json_stdout(self):
        with patch.object(cli.core, "check", wraps=core.check) as checker:
            code, stdout, stderr = self.invoke(["--input", str(ROOT / "examples/knowledge_update/task.json")])
        self.assertEqual(code, 0)
        self.assertEqual(checker.call_count, 1)
        self.assertEqual(stderr, "")
        report = json.loads(stdout)
        self.assertEqual(report["task_id"], "demo-中文")
        self.assertEqual(report["summary"]["suggestion_record_count"], 1)
        self.assertFalse(report["publication_approved"])
        self.assertEqual(report["scan_report"]["mode"], "scan")
        for part in ("scan", "suggestions"):
            for key in ("core_sha256", "cli_sha256"):
                self.assertEqual(len(report["bindings"]["implementation"][part][key]), 64)

    def test_missing_input_file_retains_actual_read_error_and_exit_two(self):
        with tempfile.TemporaryDirectory() as directory:
            code, stdout, stderr = self.invoke(["--input", str(Path(directory) / "missing.json")])
        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual([row["code"] for row in report["scan_report"]["input_errors"]],
                         ["INPUT_READ_ERROR", "INVALID_STRUCTURE"])
        self.assertIsNone(report["suggestions"])
        self.assertEqual(stderr, "")

    def test_invalid_task_json_is_input_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.json"; path.write_bytes(b"{")
            code, stdout, _ = self.invoke(["--input", str(path)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout)["scan_report"]["input_errors"][0]["code"], "INVALID_JSON")

    def test_missing_argument_is_usage_error(self):
        code, stdout, stderr = self.invoke([])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--input", stderr)

    def test_execution_failure_has_exit_one_without_machine_path(self):
        with patch.object(cli.suggestions, "suggest", side_effect=RuntimeError("private machine path")):
            code, stdout, stderr = self.invoke(["--input", str(ROOT / "examples/knowledge_update/task.json")])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["run_status"], "EXECUTION_ERROR")
        self.assertNotIn("private machine path", stdout + stderr)


if __name__ == "__main__":
    unittest.main()
