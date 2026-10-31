"""Offline scan/recheck CLI for explicit knowledge-source associations."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import knowledge_update as core  # noqa: E402


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _read_input(path: Path, location: str, errors: list[dict]) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        errors.append({"code": "INPUT_READ_ERROR", "location": location,
                       "message": "Cannot read the requested JSON input file."})
        return None


def _source_payloads(paths: list[str], base: Path, inputs: list[Path]) -> dict:
    payloads = {}
    cache = {}
    for relative in paths:
        # Treat either separator as a package separator on every platform.
        candidate = base.joinpath(*relative.replace("\\", "/").split("/"))
        try:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(base):
                payloads[relative] = {"code": "PATH_OUTSIDE_PACKAGE", "message": "Resolved source escapes the task package."}
                continue
            inputs.append(resolved)
            if resolved not in cache:
                try:
                    cache[resolved] = resolved.read_bytes()
                except OSError:
                    cache[resolved] = {"code": "SOURCE_READ_ERROR", "message": "Cannot read the declared source file."}
            payloads[relative] = cache[resolved]
        except (OSError, ValueError, RuntimeError):
            payloads[relative] = {"code": "SOURCE_READ_ERROR", "message": "Cannot resolve the declared source file."}
    return payloads


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)


def _write_results(output: Path, report: dict, exit_code: int, started: float, inputs: list[Path]) -> None:
    resolved = output.resolve()
    if any(path == resolved or path.is_relative_to(resolved) for path in inputs):
        raise OSError("Output would contain an input file")
    # mkdir(exist_ok=False) also rejects existing empty directories and links.
    output.mkdir(parents=True, exist_ok=False)
    documents = {"report.json": _json_bytes(report), "handover.md": core.render_handover(report).encode("utf-8")}
    hashes = {}
    for name, payload in documents.items():
        _write_new(output / name, payload)
        hashes[name] = {"sha256": core.sha256(payload), "size": len(payload)}
    run = {"schema_version": "knowledge-update-run/v1", "exit_code": exit_code,
           "elapsed_seconds": time.perf_counter() - started, "bindings": report["bindings"],
           "outputs": hashes, "cost": None, "human_time_seconds": None, "time_saved_seconds": None}
    # The completion manifest is published last; a partial file is never a valid manifest.
    temporary = output / "run.json.tmp"
    _write_new(temporary, _json_bytes(run))
    # Hard-link publication is atomic and refuses any existing target on both
    # Windows and POSIX (rename may overwrite on POSIX).
    os.link(temporary, output / "run.json")
    try:
        temporary.unlink()
    except OSError:
        # The complete manifest is already published; a leftover temp is harmless.
        return


def main(argv: list[str] | None = None) -> int:
    """Return the actual process status; never print success before all files exist."""
    # Redirected Windows streams may use an encoding that cannot represent paths
    # or input diagnostics. Preserve the exit status while escaping console text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("scan", "recheck"):
        subparser = subparsers.add_parser(mode)
        subparser.add_argument("--input", required=True)
        subparser.add_argument("--output", required=True)
        if mode == "recheck":
            subparser.add_argument("--revisions", required=True)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        task_path = Path(args.input).resolve()
        output = Path(args.output)
        inputs = [task_path]
        read_errors: list[dict] = []
        task_bytes = _read_input(task_path, "$task", read_errors)
        revisions_bytes = None
        if args.mode == "recheck":
            revision_path = Path(args.revisions).resolve()
            inputs.append(revision_path)
            revisions_bytes = _read_input(revision_path, "$revisions", read_errors)
        payloads = _source_payloads(core.source_paths(task_bytes, revisions_bytes, mode=args.mode), task_path.parent, inputs)
        implementation = {"core_sha256": core.sha256(Path(core.__file__).read_bytes()),
                          "cli_sha256": core.sha256(Path(__file__).read_bytes()),
                          "checker_version": core.CHECKER_VERSION}
        report = core.check(task_bytes, payloads, revisions_bytes, mode=args.mode, implementation=implementation)
        if read_errors:
            # Keep actual file-read errors instead of mislabelling absent bytes as malformed JSON.
            report["input_errors"] = read_errors + [issue for issue in report["input_errors"]
                                                     if issue["code"] != "MISSING_INPUT"]
            report["summary"]["input_error_count"] = len(report["input_errors"])
        exit_code = 2 if report["run_status"] == "INPUT_ERROR" else 0
        if exit_code:
            for issue in report["input_errors"]:
                print(f'{issue["code"]} {issue["location"]}: {issue["message"]}', file=sys.stderr)
        _write_results(output, report, exit_code, started, inputs)
    except Exception as error:
        # Deliberately omit exception text: OS errors may expose machine paths.
        print(f"Execution/output failure ({type(error).__name__}); no complete result was published.", file=sys.stderr)
        return 1
    if exit_code == 0:
        print(f"COMPLETED: {args.output}; items={report['summary']['result_item_count']}; publication_approved=0")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
