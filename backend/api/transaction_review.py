"""Bounded, in-memory transport; the route checks read-only before importing it."""
from fastapi import Request

from backend.api.service import EvaluationAPIError
from backend.core.transaction_review import (
    MAX_BUNDLE_BYTES, MAX_REQUEST_BYTES, TransactionReviewError,
    analyze_request, load_json_bytes, replay_bundle,
)


async def transaction_review_request(request: Request, *, operation: str) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise EvaluationAPIError(415, "请以 UTF-8 application/json 提交交易请求或复核包。")
    is_bundle = operation == "replay"
    maximum = MAX_BUNDLE_BYTES if is_bundle else MAX_REQUEST_BYTES
    chunks, size = [], 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > maximum:
                label = "24 MiB" if is_bundle else "2 MiB"
                raise TransactionReviewError(f"完整输入超过 {label}，未执行分析或重开。", status_code=413)
            chunks.append(chunk)
        payload = load_json_bytes(b"".join(chunks), bundle=is_bundle)
        return replay_bundle(payload) if is_bundle else analyze_request(payload)
    except TransactionReviewError as error:
        raise EvaluationAPIError(error.status_code, str(error)) from None
