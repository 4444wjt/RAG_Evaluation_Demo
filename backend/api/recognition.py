"""POST /recognitions: run a local OCR/ASR plugin on an imported file.

Writes two files next to the upload: ``<id>.txt`` (the text the evaluation can
load as a document) and ``<id>.recognition.json`` (the validated plugin output,
the position map and SHA256 bindings to both the source and the text).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from backend.api.service import EvaluationAPIError

_UPLOAD = re.compile(r"work/uploads/([0-9a-f]{32})\.([a-z0-9]+)")
_SUFFIXES = {"ocr": {"png", "jpg", "jpeg"}, "asr": {"wav", "flac"}}
SIDECAR_SCHEMA = "veriscope.recognition-sidecar.v1"
LOCATION_SCHEMA = "veriscope.source-locations.v1"
_UPLOAD_MEMBER = re.compile(r"work/uploads/([0-9a-f]{32})\.(png|jpg|jpeg|wav|flac|txt|recognition\.json)")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "wav": "audio/wav", "flac": "audio/flac"}


def _linked(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _upload_file(root: Path, raw: Any, suffixes: set[str]) -> tuple[Path, str]:
    """Read only exact imported filenames, without following upload links."""
    if (not isinstance(raw, str) or (match := _UPLOAD_MEMBER.fullmatch(raw)) is None
            or match.group(2) not in suffixes):
        raise EvaluationAPIError(422, "只能读取通过导入接口保存的指定类型文件。")
    root = root.resolve()
    work = root / "work"
    directory = work / "uploads"
    path = directory / (match.group(1) + "." + match.group(2))
    if any(_linked(part) for part in (work, directory, path)):
        raise EvaluationAPIError(403, "资料读取目录超出允许范围。")
    if directory.resolve().parent != work or path.resolve().parent != directory:
        raise EvaluationAPIError(403, "资料读取目录超出允许范围。")
    if not path.is_file():
        raise EvaluationAPIError(404, "指定范围内的文件不存在。")
    return path, match.group(1)


def _binding(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"relative_path", "sha256"}
            and isinstance(value["relative_path"], str) and isinstance(value["sha256"], str)
            and _SHA256.fullmatch(value["sha256"]) is not None)


def _conflict() -> None:
    raise EvaluationAPIError(409, "记录或识别材料已变化，原定位不能继续使用；请保留材料并重新建立定位。")


def _read_sidecar(root: Path, relative_path: Any) -> tuple[dict, str, str]:
    from backend.recognition.schema import validate
    from backend.recognition.textmap import build_text

    path, ident = _upload_file(root, relative_path, {"recognition.json"})
    try:
        raw = path.read_bytes()
        sidecar = json.loads(raw)
        if (not isinstance(sidecar, dict) or set(sidecar) != {
                "schema", "created_at", "source", "text", "elapsed_ms", "spans", "recognition"}
                or sidecar["schema"] != SIDECAR_SCHEMA or not isinstance(sidecar["created_at"], str)
                or not _binding(sidecar["source"]) or not _binding(sidecar["text"])):
            raise ValueError
        elapsed = sidecar["elapsed_ms"]
        if isinstance(elapsed, bool) or not isinstance(elapsed, (float, int)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError
        recognition = validate(sidecar["recognition"])
        source, source_id = _upload_file(root, sidecar["source"]["relative_path"], _SUFFIXES[recognition["kind"]])
        text_file, text_id = _upload_file(root, sidecar["text"]["relative_path"], {"txt"})
        if source_id != ident or text_id != ident:
            raise ValueError
        source_bytes, text_bytes = source.read_bytes(), text_file.read_bytes()
        text = text_bytes.decode("utf-8")
    except (UnicodeError, ValueError, TypeError, KeyError):
        raise EvaluationAPIError(422, "识别旁挂件结构损坏，请保留原文件并重新识别。") from None
    except OSError:
        raise EvaluationAPIError(500, "识别材料无法读取，请检查本地存储。") from None
    expected_text, expected_spans = build_text(recognition)
    if (hashlib.sha256(source_bytes).hexdigest() != sidecar["source"]["sha256"]
            or hashlib.sha256(text_bytes).hexdigest() != sidecar["text"]["sha256"]
            or recognition["input"]["sha256"] != sidecar["source"]["sha256"]
            or expected_text != text or expected_spans != sidecar["spans"]):
        _conflict()
    return sidecar, text, hashlib.sha256(raw).hexdigest()


def read_recognition(root: Path, relative_path: str) -> Response:
    sidecar, text, _sha = _read_sidecar(Path(root), relative_path)
    data = json.dumps({"sidecar": sidecar, "text": text}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return Response(data, media_type="application/json", headers={"Cache-Control": "no-store"})


def read_media(root: Path, relative_path: str) -> Response:
    path, _ident = _upload_file(Path(root), relative_path, set(_MEDIA_TYPES))
    try:
        raw = path.read_bytes()
    except OSError:
        raise EvaluationAPIError(500, "原件无法读取，请检查本地存储。") from None
    return Response(raw, media_type=_MEDIA_TYPES[path.suffix[1:]], headers={
        "Cache-Control": "no-store", "X-Content-SHA256": hashlib.sha256(raw).hexdigest(),
        "X-Content-Type-Options": "nosniff", "Content-Disposition": f'inline; filename="{path.name}"',
    })


def _location_path(root: Path, ident: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", ident) is None:
        raise EvaluationAPIError(404, "没有找到这份定位旁挂件。")
    work = root.resolve() / "work"
    folder = work / "source-locations"
    path = folder / f"{ident}.json"
    if any(_linked(part) for part in (work, folder, path)) or path.resolve().parent != folder:
        raise EvaluationAPIError(403, "定位旁挂件目录超出允许范围。")
    return path


def _parent(root: Path, ident: str):
    from backend.api import evaluation_records

    try:
        raw = evaluation_records.record_path(root, ident).read_bytes()
        parent = evaluation_records.EvaluationRecord.model_validate_json(raw)
        if parent.id != ident:
            raise ValueError
        return parent, hashlib.sha256(raw).hexdigest()
    except FileNotFoundError:
        raise EvaluationAPIError(404, "没有找到原评测记录，请先保存完整记录。") from None
    except (OSError, ValueError):
        raise EvaluationAPIError(422, "原评测记录无法读取，请保留原文件并检查本地存储。") from None


def _location_values(parent, parent_sha: str, sidecar: dict, relative_path: str, recognition_sha: str) -> dict:
    from backend.recognition.locations import locate_result

    if parent.request is None or parent.request.doc_path != sidecar["text"]["relative_path"]:
        raise EvaluationAPIError(422, "识别文字与原评测的文档路径不一致，不能建立定位绑定。")
    return {"schema": LOCATION_SCHEMA, "parent_record_id": parent.id, "parent_record_sha256": parent_sha,
            "recognition_path": relative_path, "recognition_sha256": recognition_sha,
            "source": sidecar["source"], "text": sidecar["text"],
            "models": locate_result(parent.result.model_dump(mode="json"), sidecar)}


def write_locations(root: Path, record: dict) -> None:
    data = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    path = _location_path(root, record["parent_record_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def source_locations_request(request: Request, root: Path, record_id: str) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise EvaluationAPIError(422, "请求参数不合法，请检查 API 合同。") from None
    if not isinstance(body, dict) or set(body) != {"recognition_path"}:
        raise EvaluationAPIError(422, "请求体必须只包含 recognition_path。")
    parent, parent_sha = _parent(root, record_id)
    sidecar, _text, recognition_sha = _read_sidecar(root, body["recognition_path"])
    record = {"created_at": datetime.now(timezone.utc).isoformat(),
              **_location_values(parent, parent_sha, sidecar, body["recognition_path"], recognition_sha)}
    try:
        write_locations(root, record)
    except OSError:
        raise EvaluationAPIError(500, "定位旁挂件未能保存，原评测记录保持不变；请检查本地存储。") from None
    return record


def read_locations(root: Path, record_id: str) -> Response:
    parent, parent_sha = _parent(root, record_id)
    try:
        raw = _location_path(root, record_id).read_bytes()
        record = json.loads(raw)
        if (not isinstance(record, dict) or set(record) != {
                "schema", "created_at", "parent_record_id", "parent_record_sha256", "recognition_path",
                "recognition_sha256", "source", "text", "models"}
                or not isinstance(record["created_at"], str)):
            raise ValueError
    except FileNotFoundError:
        raise EvaluationAPIError(404, "没有找到这份定位旁挂件，请先建立定位。") from None
    except (ValueError, UnicodeError):
        raise EvaluationAPIError(422, "定位旁挂件结构损坏，请保留原文件并重新建立定位。") from None
    except OSError:
        raise EvaluationAPIError(500, "定位旁挂件无法读取，请检查本地存储。") from None
    if record["parent_record_sha256"] != parent_sha or record["parent_record_id"] != record_id:
        _conflict()
    sidecar, _text, recognition_sha = _read_sidecar(root, record["recognition_path"])
    expected = _location_values(parent, parent_sha, sidecar, record["recognition_path"], recognition_sha)
    if any(record[key] != value for key, value in expected.items()):
        _conflict()
    return Response(raw, media_type="application/json", headers={
        "Cache-Control": "no-store", "X-Content-SHA256": hashlib.sha256(raw).hexdigest(),
    })


def _source(root: Path, raw: Any, kind: str) -> tuple[Path, str]:
    if not isinstance(raw, str) or (match := _UPLOAD.fullmatch(raw)) is None:
        raise EvaluationAPIError(422, "只能识别通过导入接口保存的文件。")
    if match.group(2) not in _SUFFIXES[kind]:
        raise EvaluationAPIError(422, "文件类型与识别类型不匹配。")
    path = (root / raw).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise EvaluationAPIError(404, "指定范围内的文件不存在。")
    return path, match.group(1)


def recognize(root: Path, body: Any) -> dict[str, Any]:
    from backend.recognition import runner
    from backend.recognition.textmap import build_text

    if not isinstance(body, dict) or set(body) != {"kind", "relative_path"} or body["kind"] not in _SUFFIXES:
        raise EvaluationAPIError(422, "请求体必须是 {\"kind\": \"ocr|asr\", \"relative_path\": ...}。")
    kind = body["kind"]
    source, ident = _source(root, body["relative_path"], kind)
    outcome = runner.run(kind, source, root)
    response: dict[str, Any] = {"status": outcome.status, "message": outcome.message,
                                "kind": kind, "source_sha256": outcome.input_sha256}
    if outcome.status != "ok":
        return response
    text, spans = build_text(outcome.result)
    if not text.strip():
        return {**response, "status": "empty_text", "message": "识别完成，但没有识别出文字。"}
    text_bytes = text.encode("utf-8")
    text_path = source.with_name(ident + ".txt")
    sidecar_path = source.with_name(ident + ".recognition.json")
    sidecar = {
        "schema": SIDECAR_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {"relative_path": body["relative_path"], "sha256": outcome.input_sha256},
        "text": {"relative_path": text_path.relative_to(root.resolve()).as_posix(),
                 "sha256": hashlib.sha256(text_bytes).hexdigest()},
        "elapsed_ms": outcome.elapsed_ms,
        "spans": spans,
        "recognition": outcome.result,
    }
    try:
        text_path.write_bytes(text_bytes)
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    except OSError:
        text_path.unlink(missing_ok=True)
        sidecar_path.unlink(missing_ok=True)
        raise EvaluationAPIError(500, "识别结果未能保存，请检查本地存储。") from None
    confs = [span["conf"] for span in spans]
    return {**response, "text_path": sidecar["text"]["relative_path"],
            "recognition_path": sidecar_path.relative_to(root.resolve()).as_posix(),
            "engine": outcome.result["engine"], "units": len(spans),
            "conf_missing": sum(value is None for value in confs),
            "min_conf": min((value for value in confs if value is not None), default=None)}


async def recognition_request(request: Request, root: Path) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise EvaluationAPIError(422, "请求参数不合法，请检查 API 合同。") from None
    return recognize(Path(root), body)
