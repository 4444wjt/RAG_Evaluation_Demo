"""Local sidecar records; original evaluations and gate verdicts stay untouched."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api import evaluation_records


class ImprovementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_name: str = Field(min_length=1, max_length=128)


def _allowed() -> None:
    if os.environ.get("VERISCOPE_READ_ONLY") == "1":
        raise HTTPException(423, "公开演示站点未开放本机改进候选记录。")


def _path(root: Path, ident: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", ident) is None:
        raise FileNotFoundError
    folder = (root / "work" / "improvements").resolve()
    path = folder / f"{ident}.json"
    if path.resolve().parent != folder:
        raise FileNotFoundError
    return path


def _parent(root: Path, ident: str):
    try:
        record = evaluation_records.read_record(root, ident)
        raw = evaluation_records.record_path(root, ident).read_bytes()
        return record, hashlib.sha256(raw).hexdigest()
    except FileNotFoundError:
        raise HTTPException(404, "没有找到原评测记录，请先保存或导入完整记录。") from None
    except (OSError, ValueError):
        raise HTTPException(500, "原评测记录无法读取，请保留原文件并检查本地存储。") from None


def _read(root: Path, ident: str) -> tuple[dict, bytes]:
    try:
        raw = _path(root, ident).read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("id") != ident:
            raise ValueError
        return value, raw
    except FileNotFoundError:
        raise HTTPException(404, "没有找到这份改进候选记录。") from None
    except (OSError, ValueError):
        raise HTTPException(500, "改进候选记录无法读取，请保留原文件并检查本地存储。") from None


def write_improvement(root: Path, record: dict, *, before_write: Callable[[int], None] | None = None) -> None:
    data = json.dumps(record, ensure_ascii=True, indent=2, allow_nan=False).encode("utf-8")
    if before_write is not None:
        before_write(len(data))
    destination = _path(root, record["id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def make_router(root: Path, *, request_root: Callable[[Request], Path] | None = None,
                persist: Callable[[Path, dict], None] | None = None) -> APIRouter:
    router = APIRouter(tags=["improvements"])

    @router.post("/evaluations/{record_id}/improvements", status_code=201)
    def create_improvement(record_id: str, body: ImprovementRequest, request: Request) -> dict:
        _allowed()
        scoped_root = root if request_root is None else request_root(request)
        from backend.core import answer_improvement as core
        parent, parent_sha = _parent(scoped_root, record_id)
        if body.model_name not in parent.result.model_dfs:
            raise HTTPException(422, "原评测记录中没有这个模型，请选择已保存的模型。")
        record = core.build_record(parent.id, parent.result.model_dump(mode="json"), body.model_name,
                                   parent_record_sha256=parent_sha)
        try:
            (write_improvement if persist is None else persist)(scoped_root, record)
        except (OSError, ValueError):
            raise HTTPException(500, "候选未能保存，原评测记录保持不变；请检查本地存储后重试。") from None
        return record

    @router.get("/improvements/{ident}")
    def get_improvement(ident: str, request: Request) -> Response:
        _allowed()
        scoped_root = root if request_root is None else request_root(request)
        _record, raw = _read(scoped_root, ident)
        return Response(raw, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="veriscope-improvement-{ident}.json"',
                                 "X-Content-SHA256": hashlib.sha256(raw).hexdigest(),
                                 "Cache-Control": "no-store"})

    @router.post("/improvements/{ident}/verify")
    def verify_improvement(ident: str, request: Request) -> dict:
        _allowed()
        scoped_root = root if request_root is None else request_root(request)
        from backend.core import answer_improvement as core
        record, _raw = _read(scoped_root, ident)
        parent_id = record.get("parent_record_id")
        if not isinstance(parent_id, str):
            raise HTTPException(422, "候选记录缺少原评测绑定，不能完成复核。")
        parent, parent_sha = _parent(scoped_root, parent_id)
        try:
            return core.verify_record(record, parent.result.model_dump(mode="json"), parent_record_sha256=parent_sha)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, "候选记录结构已损坏，不能完成字面与补丁复核。") from None

    return router
