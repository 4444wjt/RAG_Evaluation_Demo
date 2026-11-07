"""Local synchronous API, with lazy evaluation engine initialization."""

from collections.abc import Callable, Mapping
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from backend.api.schemas import EvaluateRequest, SavedEvaluateResponse
from backend.api import evaluation_records
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
    record_root = Path(__file__).resolve().parents[2] if project_root is None else Path(project_root)
    visitors = None
    if os.environ.get("VERISCOPE_BYOK") == "1":
        from backend.api.byok import VisitorWorkspaces
        visitors = VisitorWorkspaces(record_root)

    def request_root(request: Request) -> Path:
        return record_root if visitors is None else visitors.scope(request)

    if visitors is not None:
        @application.middleware("http")
        async def visitor_private_responses(request: Request, call_next):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

    @application.exception_handler(RequestValidationError)
    async def request_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        # FastAPI's default validation body echoes raw input and error context.
        return JSONResponse(status_code=422, content={"detail": "请求参数不合法，请检查 API 合同。"})

    @application.exception_handler(EvaluationAPIError)
    async def evaluation_error(_request: Request, error: EvaluationAPIError) -> JSONResponse:
        content = {"detail": error.detail}
        if error.code is not None:
            content["code"] = error.code
        return JSONResponse(status_code=error.status_code, content=content)

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Report liveness, without checking credentials or model availability."""
        return {"status": "ok"}

    @application.post("/inputs", tags=["inputs"])
    async def import_input_request(
        request: Request, kind: Literal["document", "testset", "image", "audio"], filename: str,
    ) -> dict[str, str | int]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放文件导入。")
        if visitors is not None:
            if kind in ("image", "audio"):
                raise EvaluationAPIError(403, "访客模式不运行本机识别插件，不接收图片或音频。")
            return await visitors.upload(request, kind=kind, filename=filename)
        return await import_input_file(
            request, kind=kind, filename=filename, project_root=project_root,
        )

    @application.get("/models", tags=["models"])
    def models_request(request: Request) -> dict[str, Any]:
        """Built-in and locally registered models; reports whether each key is set, never its value."""
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放模型配置。")
        if visitors is not None:
            request_root(request)
            return {"models": [], "localFile": ""}
        return list_models(project_root)

    @application.put("/models/local", tags=["models"])
    async def save_models_request(request: Request) -> dict[str, Any]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放模型配置。")
        if visitors is not None:
            raise EvaluationAPIError(403, "访客模型只在浏览器本次使用，不写入服务器模型配置。")
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

    @application.post("/finance-review", tags=["citations"])
    async def finance_review(request: Request) -> dict[str, Any]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点不接收资料，请在本地核对金融引用。")
        from backend.api.finance_review import finance_review_request
        return await finance_review_request(request)

    @application.post("/transaction-review/analyze", tags=["transaction-review"])
    async def analyze_transactions(request: Request) -> dict[str, Any]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点不接收交易数据，请在本地复核。")
        from backend.api.transaction_review import transaction_review_request
        return await transaction_review_request(request, operation="analyze")

    @application.post("/transaction-review/replay", tags=["transaction-review"])
    async def replay_transactions(request: Request) -> dict[str, Any]:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点不接收交易数据，请在本地重开复核包。")
        from backend.api.transaction_review import transaction_review_request
        return await transaction_review_request(request, operation="replay")

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

    def recognition_allowed() -> None:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放本机识别。")
        if visitors is not None:
            raise EvaluationAPIError(403, "访客模式不运行本机识别插件。")

    @application.post("/recognitions", tags=["inputs"])
    async def recognition_request(request: Request) -> dict[str, Any]:
        recognition_allowed()
        from backend.api.recognition import recognition_request as run_recognition
        return await run_recognition(request, record_root)

    @application.get("/recognitions/sidecar", tags=["inputs"])
    def recognition_sidecar(relative_path: str) -> Response:
        recognition_allowed()
        from backend.api.recognition import read_recognition
        return read_recognition(record_root, relative_path)

    @application.get("/inputs/media", tags=["inputs"])
    def original_media(relative_path: str) -> Response:
        recognition_allowed()
        from backend.api.recognition import read_media
        return read_media(record_root, relative_path)

    @application.post("/evaluations/{record_id}/source-locations", status_code=201, tags=["inputs"])
    async def create_source_locations(record_id: str, request: Request) -> dict[str, Any]:
        recognition_allowed()
        from backend.api.recognition import source_locations_request
        return await source_locations_request(request, record_root, record_id)

    @application.get("/evaluations/{record_id}/source-locations", tags=["inputs"])
    def source_locations(record_id: str) -> Response:
        recognition_allowed()
        from backend.api.recognition import read_locations
        return read_locations(record_root, record_id)

    def history_allowed() -> None:
        if os.environ.get("VERISCOPE_READ_ONLY") == "1":
            raise EvaluationAPIError(423, "公开演示站点未开放本机评测记录。")

    def get_record(ident: str, request: Request) -> evaluation_records.EvaluationRecord:
        history_allowed()
        try:
            return evaluation_records.read_record(request_root(request), ident)
        except FileNotFoundError:
            raise EvaluationAPIError(404, "没有找到这份评测记录。") from None
        except (OSError, ValueError):
            raise EvaluationAPIError(500, "这份评测记录无法读取，请保留原文件并检查本地存储。") from None

    @application.get("/evaluations", tags=["history"])
    def evaluation_history(request: Request) -> dict[str, Any]:
        history_allowed()
        try:
            return evaluation_records.list_records(request_root(request))
        except OSError:
            raise EvaluationAPIError(500, "历史记录无法读取，请检查本地存储。") from None

    @application.post("/evaluations/import", status_code=201, tags=["history"])
    async def import_evaluation(request: Request) -> dict[str, Any]:
        history_allowed()
        if visitors is not None:
            return await visitors.import_record(request)
        try:
            record = evaluation_records.import_record(record_root, await request.json())
        except ValueError:
            raise EvaluationAPIError(422, "记录格式不完整或版本不支持，请选择导出的 JSON 记录。") from None
        except OSError:
            raise EvaluationAPIError(500, "记录未能保存，请检查本地存储后重新导入。") from None
        return record.model_dump(mode="json")

    @application.get("/evaluations/{ident}", tags=["history"])
    def evaluation_record(ident: str, request: Request) -> dict[str, Any]:
        return get_record(ident, request).model_dump(mode="json")

    @application.get("/evaluations/{ident}/download", tags=["history"])
    def download_evaluation(ident: str, request: Request) -> Response:
        record = get_record(ident, request)
        return Response(record.model_dump_json(indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="veriscope-{record.id}.json"',
                                 "Cache-Control": "no-store"})

    @application.get("/evaluations/{ident}/report", tags=["history"])
    def evaluation_report(ident: str, request: Request) -> Response:
        record = get_record(ident, request)
        if record.report_html is None:
            raise EvaluationAPIError(404, "此记录没有附带 HTML 报告。")
        return Response(record.report_html, media_type="text/html", headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:",
            "Cache-Control": "no-store",
        })

    if visitors is None:
        @application.post("/evaluate", response_model=SavedEvaluateResponse, tags=["evaluation"])
        def evaluate_request(request: EvaluateRequest) -> dict[str, Any]:
            if os.environ.get("VERISCOPE_READ_ONLY") == "1":
                raise EvaluationAPIError(423, "公开演示站点未开放在线评测。")
            return run_evaluation(request, evaluator, project_root=project_root)
    else:
        @application.post("/evaluate", status_code=202, tags=["evaluation"])
        async def evaluate_visitor(request: Request) -> dict[str, str]:
            history_allowed()
            from backend.api.byok import read_json
            root = request_root(request)
            return visitors.submit(root, await read_json(request), evaluator)

        @application.get("/jobs/{ident}", tags=["evaluation"])
        def evaluation_job(ident: str, request: Request) -> dict[str, Any]:
            history_allowed()
            return visitors.job(request_root(request), ident)

    from backend.api.answer_improvement import make_router as improvement_router
    application.include_router(improvement_router(record_root, request_root=request_root,
        persist=None if visitors is None else visitors.persist_improvement))

    from backend.api.knowledge_update import router as knowledge_update_router
    application.include_router(knowledge_update_router,
                               include_in_schema=os.environ.get("VERISCOPE_KNOWLEDGE_UPDATE") == "1")

    return application


app = create_app()
