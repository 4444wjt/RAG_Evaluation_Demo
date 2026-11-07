"""Print source-local citation replacement blocks as JSON, without writing drafts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import knowledge_suggestions as suggestions  # noqa: E402
from backend.core import knowledge_update as core  # noqa: E402
from scripts import knowledge_update as scan_cli  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Return 0 for completion, 2 for input errors, and 1 for execution errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    try:
        task_path = Path(args.input).resolve()
        inputs = [task_path]
        read_errors: list[dict] = []
        task_bytes = scan_cli._read_input(task_path, "$task", read_errors)
        payloads = scan_cli._source_payloads(core.source_paths(task_bytes), task_path.parent, inputs)
        scan_implementation = {"core_sha256": core.sha256(Path(core.__file__).read_bytes()),
                               "cli_sha256": core.sha256(Path(scan_cli.__file__).read_bytes()),
                               "checker_version": core.CHECKER_VERSION}
        scan = core.check(task_bytes, payloads, mode="scan", implementation=scan_implementation)
        if read_errors:
            scan["input_errors"] = read_errors + [issue for issue in scan["input_errors"]
                                                  if issue["code"] != "MISSING_INPUT"]
            scan["summary"]["input_error_count"] = len(scan["input_errors"])
        report = suggestions.suggest(scan, payloads, implementation={
            "core_sha256": core.sha256(Path(suggestions.__file__).read_bytes()),
            "cli_sha256": core.sha256(Path(__file__).read_bytes()),
            "suggester_version": suggestions.SUGGESTER_VERSION})
        exit_code = 2 if report["run_status"] == "INPUT_ERROR" else 0
    except Exception as error:
        report = {"schema_version": "knowledge-suggestions-report/v1", "run_status": "EXECUTION_ERROR",
                  "error": {"code": "EXECUTION_ERROR", "type": type(error).__name__},
                  "semantic_evaluation": "NOT_EVALUATED", "publication_approved": False}
        exit_code = 1
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
