"""Controlled local inputs and synchronous access to the existing engine."""

from collections.abc import Callable, Mapping
import os
import re
from pathlib import Path, PureWindowsPath
from threading import Lock
from typing import Any

from backend.api.schemas import EvaluateRequest, EvaluateResponse
from backend.api.serialization import serialize_evaluation_result
from backend.api.evaluation_records import capture_record
from backend.input_errors import DocumentInputError


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EVALUATION_LOCK = Lock()
_DOCUMENT_TYPES = {".txt", ".pdf", ".docx", ".md"}
_TESTSET_TYPES = {".json", ".csv", ".txt"}
_RUNTIME_ERROR = "评测未能完成，请检查本地配置和服务日志后重试。"


class EvaluationAPIError(Exception):
    """An HTTP error with a fixed, client-safe message."""

    def __init__(self, status_code: int, detail: str, code: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


def _allowed_location(relative: Path, root_files: set[str]) -> bool:
    return (
        len(relative.parts) == 1 and relative.name in root_files
    ) or (
        len(relative.parts) > 1 and relative.parts[0] == "scenarios"
    ) or (
        len(relative.parts) == 3
        and relative.parts[:2] == ("work", "uploads")
        and re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]+", relative.name) is not None
    )


def _checked_path(raw_path: str, root: Path, *, testset: bool = False) -> str:
    if not raw_path or "\x00" in raw_path:
        raise EvaluationAPIError(422, "文件路径格式不合法。")
    windows_path = PureWindowsPath(raw_path)
    if windows_path.drive or windows_path.root or ":" in raw_path:
        raise EvaluationAPIError(403, "仅允许项目指定范围内的相对路径。")

    relative = Path(raw_path.replace("\\", "/"))
    root_files = {"testset_example.json", "testset_example.csv"} if testset else {"demo.txt"}
    extensions = _TESTSET_TYPES if testset else _DOCUMENT_TYPES
    if relative.is_absolute() or not _allowed_location(relative, root_files):
        raise EvaluationAPIError(403, "仅允许项目指定范围内的相对路径。")
    if relative.suffix.lower() not in extensions:
        raise EvaluationAPIError(422, "文件类型不支持。")

    try:
        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root):
            raise EvaluationAPIError(403, "文件真实路径超出允许范围。")
        actual_relative = resolved.relative_to(root)
        if not _allowed_location(actual_relative, root_files):
            raise EvaluationAPIError(403, "文件真实路径超出允许范围。")
        if resolved.suffix.lower() not in extensions:
            raise EvaluationAPIError(422, "文件真实类型不支持。")
        if not resolved.exists():
            raise EvaluationAPIError(404, "指定范围内的文件不存在。")
        if not resolved.is_file():
            raise EvaluationAPIError(422, "评测输入必须是文件。")
    except PermissionError:
        raise EvaluationAPIError(403, "文件路径无法访问。") from None
    except (OSError, ValueError, RuntimeError):
        raise EvaluationAPIError(422, "文件路径无法解析。") from None
    return str(resolved)


def _configured_model_names(root: Path | None = None) -> set[str]:
    # Configuration may load dotenv, so never import it on startup or health.
    from backend.config import MODELS_CONFIG
    from backend.core import model_registry

    builtin = [model["name"] for model in MODELS_CONFIG]
    try:
        local = model_registry.load(_PROJECT_ROOT if root is None else root, builtin)
    except model_registry.ModelConfigError as error:
        raise EvaluationAPIError(422, str(error)) from None
    return set(builtin) | {model["name"] for model in local}


def list_models(project_root: Path | None = None) -> dict[str, Any]:
    """内置模型与本机登记的模型；只给出密钥是否已设置，不返回密钥值。"""
    from backend.config import MODELS_CONFIG
    from backend.core import model_registry

    root = _PROJECT_ROOT if project_root is None else Path(project_root)
    builtin = [model_registry.describe(model, "builtin") for model in MODELS_CONFIG]
    try:
        local = model_registry.load(root, [model["name"] for model in MODELS_CONFIG])
    except model_registry.ModelConfigError as error:
        raise EvaluationAPIError(422, str(error)) from None
    return {"models": builtin + [model_registry.describe(model, "local") for model in local],
            "localFile": model_registry.FILE_NAME}


def save_local_models(models: Any, project_root: Path | None = None) -> dict[str, Any]:
    """校验并写入本机登记；密钥字段与未知字段会被拒绝。"""
    from backend.config import MODELS_CONFIG
    from backend.core import model_registry

    root = _PROJECT_ROOT if project_root is None else Path(project_root)
    try:
        model_registry.save(root, models, [model["name"] for model in MODELS_CONFIG])
    except model_registry.ModelConfigError as error:
        raise EvaluationAPIError(422, str(error)) from None
    except OSError:
        raise EvaluationAPIError(500, "模型登记未能保存，请检查本地服务后重试。") from None
    return list_models(root)


def _default_evaluator(request: dict[str, Any], **kwargs: Any) -> Mapping[str, Any]:
    # The API is a local, single-worker process; plotting must stay headless.
    os.environ["MPLBACKEND"] = "Agg"
    # Refuse unusable PDFs before importing the full engine. The RV64 API can
    # run this existing pypdf check without datasets/langchain/ragas installed.
    # Keep these refusal conditions identical to prepare_resources; valid and
    # mixed-text documents still enter the original pipeline and its recheck.
    doc_path = request.get("doc_path")
    if doc_path and Path(doc_path).suffix.lower() == ".pdf":
        from backend.services.parse_check import check_document
        parse_check = check_document(doc_path)
        if parse_check["verdict"] in ("FAIL", "REVIEW"):
            details = "; ".join(
                f"第 {page['page_number']} 页: {', '.join(page['reasons'])}"
                for page in parse_check["pages"]
                if page["verdict"] == parse_check["verdict"]
            )
            if parse_check["verdict"] == "FAIL":
                raise DocumentInputError("GARBLED_TEXT_LAYER", f"入库检查 FAIL，已阻止文档入库: {details}")
            pages = parse_check["pages"]
            scanned = [page["page_number"] for page in pages if "SUSPECTED_SCAN_PAGE" in page["reasons"]]
            if scanned and all(set(page["reasons"]) & {"SUSPECTED_SCAN_PAGE", "BLANK_PAGE"} for page in pages):
                raise DocumentInputError(
                    "SCANNED_DOCUMENT",
                    f"文档没有可提取的文字层，疑似扫描件（第 {', '.join(map(str, scanned))} 页）。"
                    "请先用识别插件转成文字并复核，再导入评测。",
                )
    from backend.core.pipeline import evaluate

    return evaluate(request, **kwargs)


def run_evaluation(
    request: EvaluateRequest,
    evaluator: Callable[..., Mapping[str, Any]] | None = None,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Validate used paths, execute exactly once, and serialize under one lock.

    This process-local lock is shared by all app factory instances. It does not
    synchronize a separate Gradio/CLI process using the same artifact directory.
    """
    try:
        root = (_PROJECT_ROOT if project_root is None else Path(project_root)).resolve()
        engine_request = request.model_dump()
        if request.judge_model_name is None:
            engine_request.pop("judge_model_name")
        engine_request["doc_path"] = _checked_path(
            "demo.txt" if request.doc_path is None else request.doc_path, root,
        )
        if request.benchmark is None and request.testset_path is not None:
            engine_request["testset_path"] = _checked_path(request.testset_path, root, testset=True)
        if request.model_names is not None or request.judge_model_name is not None:
            known_names = _configured_model_names(root)
            if any(name not in known_names for name in request.model_names or []):
                raise EvaluationAPIError(422, "模型名称不支持，请使用已配置的模型名称。")
            if request.judge_model_name is not None and request.judge_model_name not in known_names:
                raise EvaluationAPIError(422, "裁判模型不支持，请先在本机登记模型。")
    except EvaluationAPIError:
        raise
    except Exception:
        raise EvaluationAPIError(500, _RUNTIME_ERROR) from None

    if not _EVALUATION_LOCK.acquire(blocking=False):
        raise EvaluationAPIError(409, "已有评测正在运行，请完成后再试。")
    try:
        execute = _default_evaluator if evaluator is None else evaluator
        result = execute(
            engine_request, resources=None, model_factory=None, progress_cb=None,
        )
        payload = serialize_evaluation_result(result)
        payload = EvaluateResponse.model_validate(payload).model_dump(mode="json")
        try:
            record = capture_record(root, request, payload)
        except (OSError, ValueError):
            # The model call has already completed. A disk failure must not turn
            # it into an evaluation error that encourages a second paid call.
            return {**payload, "record_id": None, "saved_at": None,
                    "save_error": "评测已完成，但记录未能保存；请点击“保存记录”下载备份。"}
        return {**payload, "record_id": record.id,
                "saved_at": record.created_at.isoformat(),
                "save_error": ("评测结果已保存；" + "".join(record.artifact_warnings))
                    if record.artifact_warnings else None}
    except DocumentInputError as error:
        # Raised before any model call: an explicit input status, not a server fault.
        raise EvaluationAPIError(422, error.message, code=error.code) from None
    except Exception:
        raise EvaluationAPIError(500, _RUNTIME_ERROR) from None
    finally:
        _EVALUATION_LOCK.release()
