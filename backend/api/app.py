"""Local synchronous API, with lazy evaluation engine initialization."""

from collections.abc import Callable, Mapping
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.api.schemas import EvaluateRequest, EvaluateResponse
from backend.api.service import EvaluationAPIError, run_evaluation
from backend.api.input_files import import_input_file


def create_app(
    evaluator: Callable[..., Mapping[str, Any]] | None = None,
    *,
    project_root: Path | None = None,
) -> FastAPI:
    """Create the API without loading configuration, models or pipeline modules."""
    application = FastAPI(
        title="鉴真全域 VeriScope AI API",
        version="0.1.0",
        description="Local synchronous evaluation API. Health reports service liveness only.",
    )

    @application.exception_handler(RequestValidationError)
    async def request_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        # FastAPI's default validation body echoes raw input and error context.
        return JSONResponse(status_code=422, content={"detail": "请求参数不合法，请检查 API 合同。"})

    @application.exception_handler(EvaluationAPIError)
    async def evaluation_error(_request: Request, error: EvaluationAPIError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Report liveness, without checking credentials or model availability."""
        return {"status": "ok"}

    @application.post("/inputs", tags=["inputs"])
    async def import_input_request(
        request: Request, kind: Literal["document", "testset"], filename: str,
    ) -> dict[str, str | int]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放文件导入。")
        return await import_input_file(
            request, kind=kind, filename=filename, project_root=project_root,
        )

    @application.post("/evaluate", response_model=EvaluateResponse, tags=["evaluation"])
    def evaluate_request(request: EvaluateRequest) -> dict[str, Any]:
        # Read per request so a public demo deployment can switch it via environment alone.
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放在线评测。")
        return run_evaluation(request, evaluator, project_root=project_root)

    return application


app = create_app()
