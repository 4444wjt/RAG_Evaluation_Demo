#!/usr/bin/env python3
"""Batch CLML B1 checking and strict, self-contained offline bundle replay.

All input files are explicit command-line paths. No registry, network, model,
working-directory discovery, or expectation inferred from source is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.citation_version import (  # noqa: E402
    CitationVersionInputError, MAX_BUNDLE_BYTES, MAX_REQUEST_BYTES, check, load_json,
    validate_artifact_size,
)


def _read(path: str, *, limit: int):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise CitationVersionInputError("无法读取指定的输入文件。") from None
    return load_json(raw, limit=limit)


def _json(value, *, pretty: bool = True) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      indent=2 if pretty else None, allow_nan=False)


def _write(path: str, value):
    validate_artifact_size(value, bundle=value.get("schema") == "CitationVersionBundleV1")
    try:
        with Path(path).open("x", encoding="utf-8", newline="\n") as output:
            output.write(_json(value) + "\n")
    except OSError:
        raise CitationVersionInputError("无法新建输出文件；不会覆盖已有文件。") from None


def _validate_outputs(source: str, outputs):
    """Reject collisions before creating either requested output."""
    seen = {Path(source).resolve()}
    for name in outputs:
        if name is None:
            continue
        path = Path(name)
        resolved = path.resolve()
        if resolved in seen or path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise CitationVersionInputError("输出路径必须互异、不同于输入，且文件尚不存在、父目录已存在。")
        seen.add(resolved)


def verify_bundle(bundle) -> bool:
    if (not isinstance(bundle, dict) or set(bundle) != {"schema", "input", "report"}
            or bundle.get("schema") != "CitationVersionBundleV1"):
        raise CitationVersionInputError("复核包必须包含 schema、input 与 report。")
    actual = check(bundle["input"])
    # JSON serialization makes bool/number type changes detectable as well as
    # values. A forged self-declared hash never substitutes for recomputation.
    return _json(actual, pretty=False) == _json(bundle["report"], pretty=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    batch = subparsers.add_parser("check", help="一次批量检查 API 请求中的全部引用")
    batch.add_argument("--input", required=True, help="CitationVersionCheckV1 UTF8 JSON 的显式路径")
    batch.add_argument("--report", help="保存完整报告到此显式路径")
    batch.add_argument("--bundle", help="保存自包含复核包到此显式路径")
    replay = subparsers.add_parser("verify", help="从包内原字节和请求重算并严格比较完整报告")
    replay.add_argument("--bundle", required=True, help="CitationVersionBundleV1 的显式路径")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            matches = verify_bundle(_read(args.bundle, limit=MAX_BUNDLE_BYTES))
            print(_json({"schema": "CitationVersionVerificationV1", "verified": matches,
                         "detail": "完整报告离线重算一致。" if matches else "复核失败：重算报告与保存报告不同。"}))
            return 0 if matches else 3
        _validate_outputs(args.input, (args.report, args.bundle))
        body = _read(args.input, limit=MAX_REQUEST_BYTES)
        report = check(body)
        if args.report:
            _write(args.report, report)
        if args.bundle:
            _write(args.bundle, {"schema": "CitationVersionBundleV1", "input": body, "report": report})
        if not args.report:
            print(_json(report))
        else:
            print(_json({"schema": report["schema"], "counts": report["counts"],
                         "detail": "批量检查已保存；检查结果包含三轴与七分项，没有总 PASS。"}))
        return 0
    except CitationVersionInputError as error:
        print(_json({"error": error.detail}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
