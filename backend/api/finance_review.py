"""Bounded, in-memory transport for the existing offline statute diagnostic."""
import hashlib
import json
from pathlib import Path
import sys

from fastapi import Request

from backend.api.service import EvaluationAPIError


async def finance_review_request(request: Request) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise EvaluationAPIError(415, "请以 UTF-8 JSON 提交原文与待核对文本。")

    # Import on the explicit request; health/startup never loads a model engine.
    from scripts import statute_diagnostic as diagnostic

    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > diagnostic.MAX_BYTES:
            raise EvaluationAPIError(413, "完整请求超过 1 MiB，未执行核对。")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=diagnostic._strict_pairs,
                             parse_constant=diagnostic._reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        report = diagnostic._base()
        report["diagnostics"] = [diagnostic._issue(
            "INPUT_JSON_INVALID", "需要有效 UTF-8 JSON，拒绝重复键和非有限数值。",
        )]
    else:
        report = diagnostic.analyze_request(payload)

    code_paths = (
        "scripts/statute_diagnostic.py", "backend/core/statute_check.py",
        "backend/core/statute_locate.py", "backend/api/finance_review.py",
    )
    root = Path(__file__).resolve().parents[2]
    report["provenance"] = {
        "inputSha256": hashlib.sha256(raw).hexdigest(),
        "codeSha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in code_paths},
        "pythonVersion": sys.version.split()[0],
    }
    return report
