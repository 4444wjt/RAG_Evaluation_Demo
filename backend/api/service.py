"""Controlled local inputs and synchronous access to the existing engine."""

from collections.abc import Callable, Mapping
import os
import re
from pathlib import Path, PureWindowsPath
from threading import Lock
from typing import Any

from backend.api.schemas import EvaluateRequest, EvaluateResponse
from backend.api.serialization import serialize_evaluation_result


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EVALUATION_LOCK = Lock()
_DOCUMENT_TYPES = {".txt", ".pdf", ".docx", ".md"}
_TESTSET_TYPES = {".json", ".csv", ".txt"}
_RUNTIME_ERROR = "评测未能完成，请检查本地配置和服务日志后重试。"


class EvaluationAPIError(Exception):
    """An HTTP error with a fixed, client-safe message."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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


def _configured_model_names() -> set[str]:
    # Configuration may load dotenv, so never import it on startup or health.
    from backend.config import MODELS_CONFIG

    return {model["name"] for model in MODELS_CONFIG}


def _default_evaluator(request: dict[str, Any], **kwargs: Any) -> Mapping[str, Any]:
    # The API is a local, single-worker process; plotting must stay headless.
    os.environ["MPLBACKEND"] = "Agg"
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
        engine_request["doc_path"] = _checked_path(
            "demo.txt" if request.doc_path is None else request.doc_path, root,
        )
        if request.benchmark is None and request.testset_path is not None:
            engine_request["testset_path"] = _checked_path(request.testset_path, root, testset=True)
        if request.model_names is not None:
            known_names = _configured_model_names()
            if any(name not in known_names for name in request.model_names):
                raise EvaluationAPIError(422, "模型名称不支持，请使用已配置的模型名称。")
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
        return EvaluateResponse.model_validate(payload).model_dump(mode="json")
    except Exception:
        raise EvaluationAPIError(500, _RUNTIME_ERROR) from None
    finally:
        _EVALUATION_LOCK.release()
