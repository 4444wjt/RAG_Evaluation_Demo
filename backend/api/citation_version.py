"""Size-bounded JSON transport for offline citation-version checks."""
from fastapi import Request

from backend.api.service import EvaluationAPIError

MAX_REQUEST_BYTES = 8 * 1024 * 1024


async def citation_version_request(request: Request, *, operation: str) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise EvaluationAPIError(415, "请以 JSON 上传 CLML XML 文件。")
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise EvaluationAPIError(413, "请求体超过 8 MiB；原始文件合计不能超过 5 MiB。")
        chunks.append(chunk)
    # Keep API startup independent of the core and all model dependencies.
    from backend.core.citation_version import CitationVersionInputError, check, inspect, load_json
    try:
        body = load_json(b"".join(chunks))
        return (inspect if operation == "inspect" else check)(body)
    except CitationVersionInputError as error:
        raise EvaluationAPIError(error.status_code, error.detail) from None
