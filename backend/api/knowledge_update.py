"""In-memory HTTP entry for knowledge checks and fixed collector snapshots."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PureWindowsPath
import posixpath
import re
import unicodedata
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

MAX_REQUEST_BYTES = 8 * 1024 * 1024
# Win32 MAX_PATH (260 UTF-16 units), less a drive root and terminating NUL.
MAX_INPUT_MEMBER_UNITS = 256


def _enabled() -> None:
    if os.environ.get("VERISCOPE_KNOWLEDGE_UPDATE") != "1":
        raise HTTPException(404, "知识更新功能未开启。")


router = APIRouter(prefix="/knowledge-update", tags=["knowledge-update"], dependencies=[Depends(_enabled)])


def _collector(operation: str, source_id: str | None = None, timestamp: str | None = None):
    from backend.core.knowledge_sources import KnowledgeSources, SourceError
    try:
        collector = KnowledgeSources()
        if operation == "sources":
            return collector.public_sources()
        if operation == "versions":
            return collector.versions(source_id)
        return collector.snapshot(source_id, timestamp)
    except SourceError as error:
        return JSONResponse(status_code=error.status, content={"code": error.code, "detail": error.detail})


@router.get("/sources")
def sources():
    return _collector("sources")


@router.get("/sources/{source_id}/versions")
def versions(source_id: str):
    return _collector("versions", source_id)


@router.get("/sources/{source_id}/versions/{timestamp}")
def snapshot(source_id: str, timestamp: str):
    return _collector("snapshot", source_id, timestamp)


def _invalid() -> HTTPException:
    return HTTPException(422, "请求必须提供 task 原文、已声明的文本文件及对应模式所需的 revisions 原文。")


async def _inputs(request: Request, operation: str) -> tuple[str, bytes, dict, bytes | None]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "请以 application/json 提交知识更新输入。")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise HTTPException(413, "请求体超过 8 MiB。")
        chunks.append(chunk)
    from backend.core import knowledge_update as core
    errors = []
    body = core._parse(b"".join(chunks), "$request", errors)
    allowed = {"task", "files", "revisions"} | ({"mode"} if operation == "export" else set())
    if errors or not isinstance(body, dict) or set(body) - allowed:
        raise _invalid()
    mode = body.get("mode") if operation == "export" else operation
    if mode not in ("scan", "recheck") or not isinstance(body.get("task"), str) or not isinstance(body.get("files"), dict):
        raise _invalid()
    if (mode == "scan" and "revisions" in body) or (mode == "recheck" and not isinstance(body.get("revisions"), str)):
        raise _invalid()
    files = body["files"]
    if any(not isinstance(text, str) for text in files.values()):
        raise _invalid()
    task_bytes = body["task"].encode("utf-8")
    revisions_bytes = body["revisions"].encode("utf-8") if mode == "recheck" else None
    # Invalid task JSON/structure goes to the core to retain its full diagnostics.
    _, _, task_errors, _ = core.parse_inputs(task_bytes)
    if not task_errors and set(files) - set(core.source_paths(task_bytes)):
        raise _invalid()
    return mode, task_bytes, {path: text.encode("utf-8") for path, text in files.items()}, revisions_bytes


def _json_bytes(value: object) -> bytes:
    # This is the CLI's result serialization, including its final newline.
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _canonical_paths(task_bytes: bytes) -> bool:
    from backend.core import knowledge_update as core
    task = core._parse(task_bytes, "$task", [])
    if not isinstance(task, dict) or not isinstance(task.get("sources"), list):
        return True
    # Inspect declared strings even when the core rejects their path syntax
    # (e.g. NUL or a fixed .json member), so D3 has one HTTP rejection code.
    paths = []
    for source in task["sources"]:
        if not isinstance(source, dict):
            continue
        for side in ("old", "new"):
            version = source.get(side)
            if isinstance(version, dict) and isinstance(version.get("path"), str):
                paths.append(version["path"])
    seen = {}
    for path in paths:
        parts = path.split("/")
        if len(("inputs/" + path).encode("utf-16-le")) // 2 > MAX_INPUT_MEMBER_UNITS:
            return False
        if (posixpath.isabs(path) or posixpath.normpath(path) != path or "\\" in path or
                any(part in {".", ".."} for part in parts)):
            return False
        if parts[0].casefold() in {"task.json", "revisions.json"}:
            return False
        # Each member must be creatable on both Windows and Linux. pathlib
        # also covers Windows device aliases such as CON .txt and COM¹.txt.
        # DOS short-name shapes can alias another member on Windows.
        if any(PureWindowsPath(part).is_reserved() or part.endswith((".", " ")) or
               len(part.encode("utf-8")) > 255 or
               (len(part.partition(".")[0]) <= 8 and
                re.fullmatch(r"[^.]{1,6}~[0-9]+(?:\.[^.]{1,3})?", part)) or
               any(char in ':*?"<>|' or unicodedata.category(char) == "Cc" for char in part)
               for part in parts):
            return False
        folded = path.casefold()
        if folded in seen and seen[folded] != path:
            return False
        seen[folded] = path
    return not any(parent in seen for path in seen
                   for parent in (path[:index] for index, char in enumerate(path) if char == "/"))


def _check(mode: str, task_bytes: bytes, files: dict, revisions_bytes: bytes | None) -> tuple[dict, dict]:
    from backend.core import knowledge_update as core
    implementation = {"core_sha256": core.sha256(Path(core.__file__).read_bytes()),
                      "http_sha256": core.sha256(Path(__file__).read_bytes()),
                      "checker_version": core.CHECKER_VERSION}
    report = core.check(task_bytes, files, revisions_bytes, mode=mode, implementation=implementation)
    handover = core.render_handover(report)
    documents = {"report.json": _json_bytes(report), "handover.md": handover.encode("utf-8")}
    run = {"schema_version": "knowledge-update-run/v2",
           "http_status": 422 if report["run_status"] == "INPUT_ERROR" else 200,
           "bindings": report["bindings"],
           "outputs": {name: {"sha256": core.sha256(raw), "size": len(raw)} for name, raw in documents.items()},
           "cost": None, "human_time_seconds": None, "time_saved_seconds": None}
    documents["run.json"] = _json_bytes(run)
    return {"report": report, "handover": handover, "run": run}, documents


def _zip(mode: str, task_bytes: bytes, files: dict, revisions_bytes: bytes | None, documents: dict) -> bytes:
    from backend.core import knowledge_update as core
    entries = [("inputs/task.json", task_bytes)]
    for path in core.source_paths(task_bytes, revisions_bytes, mode=mode):
        entries.append(("inputs/" + path, files[path]))
    if mode == "recheck":
        entries.append(("inputs/revisions.json", revisions_bytes))
    entries.extend(("results/" + name, data) for name, data in documents.items())
    command = f"python scripts/knowledge_update.py {mode} --input inputs/task.json"
    if mode == "recheck":
        command += " --revisions inputs/revisions.json"
    readme = ("在对应版本的源码仓库根目录解压，使用 CLI 重核，输出目录必须是尚不存在的新目录：\n" +
              command + " --output work/knowledge-cli-recheck-new\n\n" +
              "重核得到相同的业务字段和 task_fingerprint。\n" +
              "run_fingerprint 因入口不同而不同。\n" +
              "这不代表 CLI 认证了 HTTP 运行。\n" +
              "Windows 解压时请选择短目录；目标目录也计入系统路径长度限制。\n" +
              "MATCHED 只表示字面匹配；本工具不批准发布。\n")
    entries.append(("README.txt", readme.encode("utf-8")))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return stream.getvalue()


async def _execute(request: Request, operation: str):
    mode, task_bytes, files, revisions_bytes = await _inputs(request, operation)
    if not _canonical_paths(task_bytes):
        return JSONResponse(status_code=422, content={"code": "NON_CANONICAL_PATH",
                            "detail": "来源路径须为规范 POSIX 相对路径，并与固定输入文件组成 Windows/Linux 均可解压的无冲突文件树。"})
    from backend.core import knowledge_update as core
    if any(not core.valid_source_path(path) for path in files):
        raise _invalid()
    result, documents = _check(mode, task_bytes, files, revisions_bytes)
    status = result["run"]["http_status"]
    if status == 422 or operation != "export":
        return JSONResponse(status_code=status, content=result)
    fingerprint = result["report"]["bindings"]["task_fingerprint"][:12]
    return Response(content=_zip(mode, task_bytes, files, revisions_bytes, documents), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="knowledge-update-{mode}-{fingerprint}.zip"'})


@router.post("/scan")
async def scan(request: Request):
    return await _execute(request, "scan")


@router.post("/recheck")
async def recheck(request: Request):
    return await _execute(request, "recheck")


@router.post("/export")
async def export(request: Request):
    return await _execute(request, "export")
