"""Real subprocess, clean-export, path, binding, and output-failure checks.

Run: python -B -I -S tests/test_knowledge_update_cli.py -v
Set KNOWLEDGE_UPDATE_TEST_LOG to retain subprocess stdout/stderr and hashes.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_knowledge_update import encoded, revision, revisions, task  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class KnowledgeUpdateCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="knowledge-update-")
        self.base = Path(self.temporary.name)
        self.directory_links: list[Path] = []
        self.export = self.base / "干净 导出"
        self.export.mkdir()
        for relative in ("backend/__init__.py", "backend/core/__init__.py", "backend/core/knowledge_update.py",
                         "scripts/knowledge_update.py"):
            destination = self.export / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        self.cli = self.export / "scripts/knowledge_update.py"
        self.package = self.base / "中文 材料"
        self.package.mkdir()
        self.task_path = self.package / "任务.json"
        self.revisions_path = self.package / "修订.json"
        self.value = task()
        self.write_package()
        self.revisions_path.write_bytes(encoded(revisions([revision()])))
        self.output_index = 0

    def tearDown(self):
        # Never recursively clean a junction: detach only our checked link first.
        for link in self.directory_links:
            self.assertTrue(link.parent.resolve().is_relative_to(self.base.resolve()))
            self.assertTrue(link.resolve().is_relative_to(self.base.resolve()))
            if os.name == "nt":
                os.rmdir(link)
            else:
                link.unlink()
        self.temporary.cleanup()

    def directory_link(self, link: Path, target: Path) -> None:
        self.assertTrue(link.parent.resolve().is_relative_to(self.base.resolve()))
        self.assertTrue(target.resolve().is_relative_to(self.base.resolve()))
        if os.name == "nt":
            # Directory junctions cover resolved link behavior without symlink privileges.
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            self.assertEqual(result.returncode, 0, repr(result.stderr))
        else:
            link.symlink_to(target, target_is_directory=True)
        self.directory_links.append(link)
        self.assertEqual(link.resolve(), target.resolve())

    def write_package(self, payloads: dict | None = None):
        self.task_path.write_bytes(encoded(self.value))
        for name, data in (payloads if payloads is not None else {"old.txt": "命中\n".encode(), "new.txt": "命中\n".encode()}).items():
            destination = self.package / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)

    def fresh_output(self) -> Path:
        self.output_index += 1
        return self.base / f"输出 {self.output_index}"

    def invoke(self, mode: str = "scan", *, output: Path | None = None, cwd: Path | None = None,
               extra: list[str] | None = None, command: list[str] | None = None) -> tuple[subprocess.CompletedProcess, Path]:
        output = output or self.fresh_output()
        arguments = [mode, "--input", str(self.task_path), "--output", str(output)]
        if mode == "recheck":
            arguments += ["--revisions", str(self.revisions_path)]
        arguments += extra or []
        actual = [sys.executable, "-B", "-I", "-S", str(self.cli), *arguments] if command is None else command + arguments
        process = subprocess.run(actual, cwd=cwd or self.base, capture_output=True)
        if os.environ.get("KNOWLEDGE_UPDATE_TEST_LOG"):
            paths = [self.task_path, self.revisions_path, self.cli, self.export / "backend/core/knowledge_update.py"]
            paths.extend(path for path in self.package.rglob("*") if path.is_file())
            if output.is_dir():
                paths.extend(path for path in output.iterdir() if path.is_file())
            record = {"test": self.id(), "command": actual, "cwd": str(cwd or self.base),
                      "python": sys.version, "platform": platform.platform(), "exit_code": process.returncode,
                      "stdout": process.stdout.decode("utf-8", errors="replace"),
                      "stderr": process.stderr.decode("utf-8", errors="replace"),
                      "files": [{"path": str(path.relative_to(self.base)), "sha256": digest(path.read_bytes()),
                                 "size": path.stat().st_size} for path in dict.fromkeys(paths) if path.exists()]}
            for name in ("report.json", "run.json"):
                path = output / name
                if path.is_file():
                    try:
                        record[name] = json.loads(path.read_bytes())
                    except ValueError:
                        record[name] = "INVALID_OR_INCOMPLETE_JSON"
            with Path(os.environ["KNOWLEDGE_UPDATE_TEST_LOG"]).open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return process, output

    def report(self, output: Path) -> dict:
        return json.loads((output / "report.json").read_bytes())

    def assert_exit(self, result: subprocess.CompletedProcess, code: int):
        self.assertEqual(result.returncode, code, result.stderr.decode("utf-8", errors="replace"))
        if code:
            self.assertEqual(result.stdout, b"")
            self.assertNotEqual(result.stderr, b"")
        else:
            self.assertIn(b"COMPLETED", result.stdout)
            self.assertEqual(result.stderr, b"")

    def test_clean_export_without_work_or_third_party_packages(self):
        self.assertFalse((self.export / "work").exists())
        result, output = self.invoke()
        self.assert_exit(result, 0)
        self.assertEqual(self.report(output)["summary"]["result_item_count"], 1)
        self.assertFalse(list(self.export.rglob("*.pyc")))

    def test_cp1252_streams_preserve_exit_codes_manifest_and_utf8_report(self):
        wrapper = (
            "import runpy,sys; "
            "sys.stdout.reconfigure(encoding='cp1252',errors='strict'); "
            "sys.stderr.reconfigure(encoding='cp1252',errors='strict'); "
            "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
        )
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                if invalid:
                    self.value["拼错字段"] = True
                    self.write_package()
                original_input = self.task_path.read_bytes()
                result, output = self.invoke(command=[sys.executable, "-B", "-I", "-S", "-c", wrapper, str(self.cli)])
                expected = 2 if invalid else 0
                self.assert_exit(result, expected)
                manifest = json.loads((output / "run.json").read_bytes())
                self.assertEqual(manifest["exit_code"], result.returncode)
                self.assertEqual(self.task_path.read_bytes(), original_input)
                report = self.report(output)
                if invalid:
                    self.assertEqual(report["run_status"], "INPUT_ERROR")
                    self.assertIn("拼错字段".encode("cp1252", errors="backslashreplace"), result.stderr)
                    self.assertIn("拼错字段", (output / "report.json").read_text(encoding="utf-8"))
                else:
                    self.assertEqual(report["run_status"], "COMPLETED")
                    self.assertIn(str(output).encode("cp1252", errors="backslashreplace"), result.stdout)
                    self.assertEqual(report["items"][0]["original"]["draft"], "旧稿")
                    self.assertIn("旧稿".encode("utf-8"), (output / "report.json").read_bytes())
                    self.assertIn("旧稿".encode("utf-8"), (output / "handover.md").read_bytes())

    def test_stringio_streams_remain_supported(self):
        wrapper = (
            "import importlib.util,io,sys; "
            "spec=importlib.util.spec_from_file_location('ku',sys.argv[1]); "
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            "actual_stdout,actual_stderr=sys.stdout,sys.stderr; "
            "sys.stdout,sys.stderr=io.StringIO(),io.StringIO(); "
            "code=m.main(sys.argv[2:]); "
            "actual_stdout.write(sys.stdout.getvalue()); actual_stderr.write(sys.stderr.getvalue()); "
            "sys.exit(code)"
        )
        result, output = self.invoke(command=[sys.executable, "-B", "-I", "-S", "-c", wrapper, str(self.cli)])
        self.assert_exit(result, 0)
        self.assertIn(str(output).encode("utf-8"), result.stdout)
        self.assertEqual(json.loads((output / "run.json").read_bytes())["exit_code"], result.returncode)

    def test_different_cwds_and_unicode_paths_produce_identical_business_bytes(self):
        self.value["sources"][0]["new"]["path"] = "子 目录/来源.MD"
        self.write_package({"old.txt": "命中\n".encode(), "子 目录/来源.MD": "命中\n".encode()})
        first, out1 = self.invoke(cwd=self.base)
        second, out2 = self.invoke(cwd=self.package)
        self.assert_exit(first, 0); self.assert_exit(second, 0)
        self.assertEqual((out1 / "report.json").read_bytes(), (out2 / "report.json").read_bytes())
        self.assertEqual((out1 / "handover.md").read_bytes(), (out2 / "handover.md").read_bytes())
        self.assertNotIn(str(self.base), (out1 / "report.json").read_text(encoding="utf-8"))

    def test_two_material_sets_without_changing_code(self):
        first, out1 = self.invoke()
        self.assert_exit(first, 0)
        self.value["task_id"] = "第二套"
        self.value["items"][0]["old_citations"][0]["quote"] = "新材料"
        self.write_package({"old.txt": "新材料\n".encode(), "new.txt": "其他\n".encode()})
        second, out2 = self.invoke()
        self.assert_exit(second, 0)
        self.assertEqual(self.report(out1)["items"][0]["new_checks"][0]["status"], "MATCHED")
        self.assertEqual(self.report(out2)["items"][0]["new_checks"][0]["status"], "NOT_FOUND")
        self.assertNotEqual(self.report(out1)["bindings"]["task_fingerprint"], self.report(out2)["bindings"]["task_fingerprint"])

    def test_relative_cli_arguments_are_relative_to_calling_cwd(self):
        output = self.package / "结果"
        command = [sys.executable, "-B", "-I", "-S", str(self.cli), "scan", "--input", "任务.json", "--output", "结果"]
        result = subprocess.run(command, cwd=self.package, capture_output=True)
        self.assert_exit(result, 0)
        self.assertTrue((output / "run.json").exists())

    def test_add_delete_and_zero_byte_files_exit_zero(self):
        self.value["sources"] = [{"source_id": "S", "old": None, "new": {"path": "new.txt"}},
                                 {"source_id": "D", "old": {"path": "old.txt"}, "new": None}]
        self.write_package({"old.txt": b"", "new.txt": b""})
        result, output = self.invoke()
        self.assert_exit(result, 0)
        report = self.report(output)
        self.assertEqual([source["status"] for source in report["sources"]], ["ADDED", "DELETED"])
        self.assertEqual(report["summary"]["declared_source_count"], 2)

    def test_structural_invalid_inputs_exit_two_with_null_business_denominators(self):
        valid = encoded(self.value)
        duplicate = deepcopy(self.value); duplicate["items"].append(deepcopy(duplicate["items"][0]))
        both_null = deepcopy(self.value); both_null["sources"][0].update(old=None, new=None)
        missing = deepcopy(self.value); del missing["sources"][0]["new"]
        for data in (b"{", b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff", encoded(duplicate), encoded(both_null), encoded(missing)):
            with self.subTest(data=data):
                self.task_path.write_bytes(data)
                result, output = self.invoke()
                self.assert_exit(result, 2)
                self.assertIsNone(self.report(output)["summary"]["result_item_count"])
                self.assertEqual(json.loads((output / "run.json").read_bytes())["exit_code"], 2)
        self.task_path.write_bytes(valid)

    def test_source_read_decode_errors_retain_all_items_and_exit_two(self):
        for data in (None, b"\xff"):
            with self.subTest(data=data):
                path = self.package / "new.txt"
                if data is None:
                    path.unlink()
                else:
                    path.write_bytes(data)
                result, output = self.invoke()
                self.assert_exit(result, 2)
                report = self.report(output)
                self.assertEqual(report["summary"]["result_item_count"], 1)
                self.assertEqual(report["sources"][0]["status"], "READ_ERROR")
                self.assertEqual(report["items"][0]["new_checks"][0]["status"], "SOURCE_READ_ERROR")

    def test_unknown_revision_and_mapping_errors_exit_two_without_shrinking_items(self):
        self.revisions_path.write_bytes(encoded(revisions([revision(item_id="UNKNOWN"),
                                                          revision(citations=[{"source_id": "MISSING", "quote": "x"}])])))
        result, output = self.invoke("recheck")
        self.assert_exit(result, 2)
        report = self.report(output)
        self.assertEqual(report["summary"]["result_item_count"], 1)
        self.assertEqual(report["unapplied_revisions"][0]["item_id"], "UNKNOWN")
        self.assertEqual(report["items"][0]["new_checks"][0]["status"], "UNKNOWN_SOURCE")

    def test_mapping_outside_association_exit_two_and_missing_quote_is_zero(self):
        self.value["items"][0]["source_ids"] = []
        self.write_package()
        result, output = self.invoke()
        self.assert_exit(result, 2)
        self.assertEqual(self.report(output)["items"][0]["new_checks"][0]["status"], "CITATION_OUTSIDE_ASSOCIATION")
        self.value["items"][0].update(source_ids=["S"], old_citations=[])
        self.write_package()
        result, output = self.invoke()
        self.assert_exit(result, 0)
        self.assertEqual(self.report(output)["items"][0]["new_checks"][0]["status"], "MISSING_QUOTE")

    def test_existing_directory_file_input_and_links_never_overwrite(self):
        directory = self.base / "existing"; directory.mkdir()
        existing_file = self.base / "existing.txt"; existing_file.write_bytes(b"keep")
        before = {self.task_path: self.task_path.read_bytes(), existing_file: b"keep"}
        for output in (directory, existing_file, self.task_path, self.package):
            with self.subTest(output=output):
                result, _ = self.invoke(output=output)
                self.assert_exit(result, 1)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        link = self.base / "existing-link"
        self.directory_link(link, directory)
        result, _ = self.invoke(output=link)
        self.assert_exit(result, 1)

    def test_source_escape_via_directory_link_is_an_input_error(self):
        outside = self.base / "outside"; outside.mkdir()
        (outside / "escaped.txt").write_bytes(b"outside")
        link = self.package / "link"
        self.directory_link(link, outside)
        self.value["sources"][0]["new"]["path"] = "link/escaped.txt"; self.write_package()
        result, output = self.invoke()
        self.assert_exit(result, 2)
        self.assertEqual(self.report(output)["sources"][0]["versions"]["new"]["read_error"]["code"], "PATH_OUTSIDE_PACKAGE")

    def test_midwrite_failure_is_actual_exit_one_no_success_no_manifest(self):
        wrapper = (
            "import importlib.util,sys; from pathlib import Path; "
            "spec=importlib.util.spec_from_file_location('ku',sys.argv[1]); "
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            "original=m._write_new; counter=[0]; "
            "exec('def injected(path,payload):\\n counter[0]+=1\\n if counter[0]==2: raise OSError(\"injected write failure\")\\n original(path,payload)'); "
            "m._write_new=injected; sys.exit(m.main(sys.argv[2:]))"
        )
        # The wrapper itself runs in a separate process; main's status is the process status.
        for invalid_input in (False, True):
            with self.subTest(invalid_input=invalid_input):
                if invalid_input:
                    self.task_path.write_bytes(b"{")
                result, output = self.invoke(command=[sys.executable, "-B", "-I", "-S", "-c", wrapper, str(self.cli)])
                self.assert_exit(result, 1)
                self.assertTrue((output / "report.json").exists())
                self.assertFalse((output / "run.json").exists())
                if invalid_input:
                    self.assertIn(b"INVALID_JSON", result.stderr)
                self.assertIn(b"Execution/output failure", result.stderr)
        self.write_package()
        result, output = self.invoke()
        self.assert_exit(result, 0)
        self.assertTrue((output / "run.json").exists())

    def test_manifest_hashes_sources_and_result_bytes_are_actual_and_lf(self):
        result, output = self.invoke("recheck")
        self.assert_exit(result, 0)
        report = self.report(output)
        manifest = json.loads((output / "run.json").read_bytes())
        binding = report["bindings"]
        self.assertEqual(binding["task_sha256"], digest(self.task_path.read_bytes()))
        self.assertEqual(binding["revisions_sha256"], digest(self.revisions_path.read_bytes()))
        self.assertEqual(binding["implementation"]["cli_sha256"], digest(self.cli.read_bytes()))
        self.assertEqual(binding["implementation"]["core_sha256"], digest((self.export / "backend/core/knowledge_update.py").read_bytes()))
        for source in binding["source_inputs"]:
            self.assertEqual(source["sha256"], digest((self.package / source["path"]).read_bytes()))
        self.assertEqual(manifest["bindings"], binding)
        self.assertEqual(manifest["exit_code"], result.returncode)
        self.assertEqual(set(manifest["outputs"]), {"report.json", "handover.md"})
        for name, value in manifest["outputs"].items():
            data = (output / name).read_bytes()
            self.assertEqual(value, {"sha256": digest(data), "size": len(data)})
            self.assertNotIn(b"\r\n", data)
        for field in ("cost", "human_time_seconds", "time_saved_seconds"):
            self.assertIsNone(manifest[field])

    def test_completion_manifest_refuses_injected_existing_file(self):
        wrapper = (
            "import importlib.util,sys; "
            "spec=importlib.util.spec_from_file_location('ku',sys.argv[1]); "
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            "original=m._write_new; "
            "exec('def injected(path,payload):\\n original(path,payload)\\n if path.name==\"run.json.tmp\": (path.parent/\"run.json\").write_bytes(b\"keep existing\")'); "
            "m._write_new=injected; sys.exit(m.main(sys.argv[2:]))"
        )
        result, output = self.invoke(command=[sys.executable, "-B", "-I", "-S", "-c", wrapper, str(self.cli)])
        self.assert_exit(result, 1)
        self.assertEqual((output / "run.json").read_bytes(), b"keep existing")

    def test_source_rule_and_cli_changes_bind_fresh_current_bytes(self):
        first, out1 = self.invoke("recheck"); self.assert_exit(first, 0)
        initial = self.report(out1)
        self.revisions_path.write_bytes(encoded(revisions([revision(proposed_draft="只换修订稿")])))
        second, out2 = self.invoke("recheck"); self.assert_exit(second, 0)
        changed = self.report(out2)
        self.assertEqual(initial["bindings"]["task_fingerprint"], changed["bindings"]["task_fingerprint"])
        self.assertNotEqual(initial["bindings"]["run_fingerprint"], changed["bindings"]["run_fingerprint"])
        core_path = self.export / "backend/core/knowledge_update.py"
        core_path.write_bytes(core_path.read_bytes() + b"\n# test binding change\n")
        third, out3 = self.invoke("recheck"); self.assert_exit(third, 0)
        self.assertNotEqual(changed["bindings"]["task_fingerprint"], self.report(out3)["bindings"]["task_fingerprint"])
        self.cli.write_bytes(self.cli.read_bytes() + b"\n# test CLI binding change\n")
        fourth, out4 = self.invoke("recheck"); self.assert_exit(fourth, 0)
        self.assertNotEqual(self.report(out3)["bindings"]["run_fingerprint"], self.report(out4)["bindings"]["run_fingerprint"])

    def test_scan_partial_revision_empty_revision_recheck_consumption(self):
        self.value["items"].append({"item_id": "SECOND", "old_draft": "保留", "source_ids": [], "old_citations": []})
        self.write_package()
        scanned, scan_output = self.invoke(); self.assert_exit(scanned, 0)
        scan_bytes = (scan_output / "report.json").read_bytes()
        scan_report = json.loads(scan_bytes)
        # Consume the exported actual item IDs/draft; retain its source report binding.
        selected = scan_report["items"][0]
        given = revisions([revision(item_id=selected["item_id"], proposed_draft=selected["original"]["draft"] + "修订")])
        self.revisions_path.write_bytes(encoded(given))
        revised, recheck_output = self.invoke("recheck"); self.assert_exit(revised, 0)
        rechecked = self.report(recheck_output)
        chain = {"scan_report_sha256": digest(scan_bytes), "given_revisions_sha256": digest(self.revisions_path.read_bytes()),
                 "recheck_report_sha256": digest((recheck_output / "report.json").read_bytes()),
                 "consumed_item_ids": [selected["item_id"]]}
        self.assertEqual(chain["given_revisions_sha256"], rechecked["bindings"]["revisions_sha256"])
        self.assertEqual(rechecked["summary"]["result_item_count"], 2)
        self.assertEqual(rechecked["summary"]["unsubmitted_item_count"], 1)
        self.assertEqual(rechecked["items"][1]["effective"], scan_report["items"][1]["effective"])
        self.revisions_path.write_bytes(encoded(revisions([])))
        empty, empty_output = self.invoke("recheck"); self.assert_exit(empty, 0)
        self.assertEqual(self.report(empty_output)["summary"]["unsubmitted_item_count"], 2)
        if os.environ.get("KNOWLEDGE_UPDATE_TEST_LOG"):
            with Path(os.environ["KNOWLEDGE_UPDATE_TEST_LOG"]).open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"test": self.id(), "consumption_chain": chain, "given_revisions": given}, ensure_ascii=False) + "\n")

    def test_missing_task_and_missing_revisions_are_input_errors(self):
        self.task_path.unlink()
        result, output = self.invoke()
        self.assert_exit(result, 2)
        self.assertIn("INPUT_READ_ERROR", [issue["code"] for issue in self.report(output)["input_errors"]])
        self.write_package(); self.revisions_path.unlink()
        result, output = self.invoke("recheck")
        self.assert_exit(result, 2)
        self.assertIn("INPUT_READ_ERROR", [issue["code"] for issue in self.report(output)["input_errors"]])

    def test_argument_errors_are_actual_exit_two(self):
        for extra in (["--revisions", str(self.revisions_path)], ["--unknown"]):
            result, output = self.invoke(extra=extra)
            self.assert_exit(result, 2)
            self.assertFalse(output.exists())
        result = subprocess.run([sys.executable, "-B", "-I", "-S", str(self.cli), "recheck", "--input", str(self.task_path),
                                 "--output", str(self.fresh_output())], capture_output=True)
        self.assert_exit(result, 2)


if __name__ == "__main__":
    unittest.main()
