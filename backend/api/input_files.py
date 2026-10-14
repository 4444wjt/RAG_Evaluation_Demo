"""Bounded raw-file imports for the local workbench, without model imports."""

from pathlib import Path
import unicodedata
from uuid import uuid4

from fastapi import Request

from backend.api.service import EvaluationAPIError


MAX_INPUT_BYTES = 10 * 1024 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EXTENSIONS = {
    "document": {".txt", ".pdf", ".docx", ".md"},
    "testset": {".json", ".csv", ".txt"},
}


def _validate_filename(kind: str, filename: str) -> str:
    if kind not in _EXTENSIONS:
        raise EvaluationAPIError(422, "导入类型不支持。")
    if (
        not filename
        or filename != filename.strip()
        or any(char in '/\\:<>|"?*' for char in filename)
        or any(unicodedata.category(char).startswith("C") for char in filename)
        or len(filename.encode("utf-8")) > 255
    ):
        raise EvaluationAPIError(422, "文件名不合法，请选择本机文件。")
    suffix = Path(filename).suffix.lower()
    if suffix not in _EXTENSIONS[kind]:
        raise EvaluationAPIError(422, "文件类型不支持。")
    return suffix


def _upload_directory(project_root: Path | None) -> tuple[Path, Path]:
    root = (_PROJECT_ROOT if project_root is None else Path(project_root)).resolve()
    if not root.is_dir():
        raise EvaluationAPIError(500, "资料导入目录不可用。")
    directory = root
    for name in ("work", "uploads"):
        directory = directory / name
        # Reject existing links, including Windows junctions where supported.
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise EvaluationAPIError(403, "资料导入目录超出允许范围。")
        if not directory.resolve().is_relative_to(root):
            raise EvaluationAPIError(403, "资料导入目录超出允许范围。")
        directory.mkdir(exist_ok=True)
        if not directory.resolve().is_relative_to(root):
            raise EvaluationAPIError(403, "资料导入目录超出允许范围。")
    return root, directory.resolve()


async def import_input_file(
    request: Request,
    *,
    kind: str,
    filename: str,
    project_root: Path | None = None,
) -> dict[str, str | int]:
    """Save a single original byte stream, deleting any incomplete import."""
    suffix = _validate_filename(kind, filename)
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/octet-stream":
        raise EvaluationAPIError(415, "请选择文件后以原始文件格式导入。")
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            length = int(raw_length)
        except ValueError:
            raise EvaluationAPIError(422, "文件长度不合法。") from None
        if length < 0:
            raise EvaluationAPIError(422, "文件长度不合法。")
        if length > MAX_INPUT_BYTES:
            raise EvaluationAPIError(413, "单个文件不能超过 10 MB。")
    destination: Path | None = None
    created = False
    completed = False
    try:
        root, directory = _upload_directory(project_root)
        destination = directory / (uuid4().hex + suffix)
        if not destination.resolve().is_relative_to(root):
            raise EvaluationAPIError(403, "资料导入目录超出允许范围。")
        size_bytes = 0
        with destination.open("xb") as imported:
            created = True
            async for chunk in request.stream():
                size_bytes += len(chunk)
                if size_bytes > MAX_INPUT_BYTES:
                    raise EvaluationAPIError(413, "单个文件不能超过 10 MB。")
                imported.write(chunk)
        if size_bytes == 0:
            raise EvaluationAPIError(422, "文件为空，请选择包含资料的文件。")
        if raw_length is not None and size_bytes != length:
            raise EvaluationAPIError(422, "文件传输未完成，请重新导入。")
        completed = True
        return {
            "kind": kind,
            "file_name": filename,
            "relative_path": destination.relative_to(root).as_posix(),
            "size_bytes": size_bytes,
        }
    except EvaluationAPIError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise EvaluationAPIError(500, "文件未能导入，请检查本地服务后重试。") from None
    except Exception:
        raise EvaluationAPIError(400, "文件传输中断，请重新导入。") from None
    finally:
        # Also runs for ASGI cancellation and client disconnects.
        if destination is not None and created and not completed:
            destination.unlink(missing_ok=True)
