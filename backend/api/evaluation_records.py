"""Local full-result records. One atomic JSON file per run, without engine imports."""

import base64
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.api.schemas import EvaluateRequest, EvaluateResponse


class EvaluationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: datetime
    source: Literal["evaluation", "imported"]
    request: EvaluateRequest | None
    result: EvaluateResponse
    report_html: str | None
    chart_png_base64: str | None
    artifact_warnings: list[str]


def store_path(root: Path) -> Path:
    return root / "work" / "evaluations"


def record_path(root: Path, ident: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", ident) is None:
        raise FileNotFoundError
    folder = store_path(root).resolve()
    path = folder / f"{ident}.json"
    if path.resolve().parent != folder:
        raise FileNotFoundError
    return path


def write_record(root: Path, record: EvaluationRecord, *, before_write: Callable[[int], None] | None = None) -> None:
    data = record.model_dump_json(indent=2).encode("utf-8")
    if before_write is not None:
        before_write(len(data))
    folder = store_path(root)
    folder.mkdir(parents=True, exist_ok=True)
    # Readers see either a whole record or no record; interrupted .tmp files
    # never appear in the list. UUIDs are generated here, not supplied as paths.
    with tempfile.NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, folder / f"{record.id}.json")
    finally:
        temp.unlink(missing_ok=True)


def read_record(root: Path, ident: str) -> EvaluationRecord:
    record = EvaluationRecord.model_validate_json(record_path(root, ident).read_bytes())
    if record.id != ident:
        raise ValueError("Record identity mismatch")
    return record


def list_records(root: Path) -> dict[str, Any]:
    folder = store_path(root)
    if not folder.exists():
        return {"records": [], "unreadable_count": 0}
    rows = []
    unreadable = 0
    # iterdir deliberately reports an inaccessible store instead of pretending
    # that all previously saved evaluations disappeared.
    for path in folder.iterdir():
        if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
            continue
        try:
            record = read_record(root, path.stem)
        except (OSError, ValueError):
            unreadable += 1
            continue
        req = record.request
        doc = req.doc_path if req else None
        rows.append({
            "id": record.id,
            "created_at": record.created_at.isoformat(),
            "source": record.source,
            "document_name": Path(doc.replace("\\", "/")).name if doc else
                ("demo.txt" if req and not req.benchmark else
                 (req.benchmark if req and req.benchmark else "导入的评测结果")),
            "models": list(record.result.model_dfs),
            "gate_passed": record.result.gate_passed,
            "total_elapsed_seconds": record.result.total_elapsed_seconds,
            "has_report": record.report_html is not None,
            "artifact_warnings": record.artifact_warnings,
        })
    rows.sort(key=lambda row: row["created_at"], reverse=True)
    return {"records": rows, "unreadable_count": unreadable}


def capture_record(root: Path, request: EvaluateRequest, result: dict[str, Any], *,
                   before_write: Callable[[int], None] | None = None) -> EvaluationRecord:
    """Called while the API evaluation lock still owns the legacy output paths."""
    payload = EvaluateResponse.model_validate(result)
    warnings = []

    def artifact(raw: str | None, label: str, text: bool = False) -> str | None:
        if raw is None:
            warnings.append(f"{label}未生成。")
            return None
        try:
            path = Path(raw)
            path = (root / path).resolve() if not path.is_absolute() else path.resolve()
            # Generated artifacts belong to this checkout; never archive an
            # arbitrary external file based solely on a path string.
            path.relative_to(root.resolve())
            data = path.read_bytes()
            return data.decode("utf-8-sig") if text else base64.b64encode(data).decode("ascii")
        except (OSError, ValueError):
            warnings.append(f"{label}未能保存。")
            return None

    record = EvaluationRecord(
        id=uuid4().hex, created_at=datetime.now(timezone.utc), source="evaluation",
        request=request, result=payload,
        report_html=artifact(payload.html_path, "HTML 报告", text=True),
        chart_png_base64=artifact(payload.chart_path, "结果图表"),
        artifact_warnings=warnings,
    )
    write_record(root, record, before_write=before_write)
    return record


def import_record(root: Path, data: Any, *, before_write: Callable[[int], None] | None = None) -> EvaluationRecord:
    """Imports data, never the local paths claimed by an uploaded result."""
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    # Save status belongs to the source server, including browser fallback
    # backups. Preserve the evaluation fields while discarding that status.
    result_data = data.get("result") if "schema_version" in data else data
    if isinstance(result_data, dict):
        result_data = {key: value for key, value in result_data.items()
                       if key not in {"record_id", "saved_at", "save_error"}}
    if "schema_version" in data:
        record = EvaluationRecord.model_validate({**data, "result": result_data})
        if record.chart_png_base64 is not None:
            base64.b64decode(record.chart_png_base64, validate=True)
    else:
        record = EvaluationRecord(
            id=uuid4().hex, created_at=datetime.now(timezone.utc), source="imported",
            request=None, result=EvaluateResponse.model_validate(result_data),
            report_html=None, chart_png_base64=None,
            artifact_warnings=["此文件仅包含评测结果，未附带 HTML 报告与图表。"],
        )
    # Every import is a new entry. The original export/file is never overwritten.
    record = record.model_copy(update={"id": uuid4().hex, "source": "imported",
                                      "created_at": datetime.now(timezone.utc)})
    write_record(root, record, before_write=before_write)
    return record
