"""Local synchronous API, with lazy evaluation engine initialization."""

from collections.abc import Callable, Mapping
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.api.schemas import EvaluateRequest, EvaluateResponse
from backend.api.service import (
    EvaluationAPIError, list_models, run_evaluation, save_local_models,
)
from backend.api.input_files import import_input_file


_LOCAL_HOSTS = {"127.0.0.1", "::1"}


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

    @application.get("/models", tags=["models"])
    def models_request() -> dict[str, Any]:
        """Built-in and locally registered models; reports whether each key is set, never its value."""
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放模型配置。")
        return list_models(project_root)

    @application.put("/models/local", tags=["models"])
    async def save_models_request(request: Request) -> dict[str, Any]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放模型配置。")
        # Registration decides where an existing key may be sent, so only this machine may change it.
        if request.client is None or request.client.host not in _LOCAL_HOSTS:
            raise EvaluationAPIError(403, "模型登记只能在运行服务的本机上修改。")
        try:
            body = await request.json()
        except ValueError:
            raise EvaluationAPIError(422, "请求参数不合法，请检查 API 合同。") from None
        if not isinstance(body, dict) or set(body) != {"models"}:
            raise EvaluationAPIError(422, "请求体必须是 {\"models\": [...]}。")
        return save_local_models(body["models"], project_root)

    @application.post("/contexts/parent/prepare", tags=["contexts"])
    async def parent_context_request(request: Request) -> dict[str, Any]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点不接收文件，请在本地准备父条资料。")
        from backend.api.parent_context import prepare_parent_context_request
        return await prepare_parent_context_request(request)

    @application.post("/citations/versions/inspect", tags=["citations"])
    async def inspect_citation_versions(request: Request) -> dict[str, Any]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点不接收文件，请在本地检查引用版本。")
        from backend.api.citation_version import citation_version_request
        return await citation_version_request(request, operation="inspect")

    @application.post("/citations/versions/check", tags=["citations"])
    async def check_citation_versions(request: Request) -> dict[str, Any]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点不接收文件，请在本地检查引用版本。")
        from backend.api.citation_version import citation_version_request
        return await citation_version_request(request, operation="check")

    @application.post("/evaluate", response_model=EvaluateResponse, tags=["evaluation"])
    def evaluate_request(request: EvaluateRequest) -> dict[str, Any]:
        # Read per request so a public demo deployment can switch it via environment alone.
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放在线评测。")
        return run_evaluation(request, evaluator, project_root=project_root)

    from backend.api.knowledge_update import router as knowledge_update_router
    application.include_router(knowledge_update_router,
                               include_in_schema=os.environ.get("VERISCOPE_KNOWLEDGE_UPDATE") == "1")

    return application


app = create_app()
