"""Small, bounded visitor workspaces and synchronous-engine background jobs.

The bearer session is never stored. Credentials exist only in the submission
handler and running job, outside the durable EvaluateRequest contract.
"""
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from threading import Lock, Thread
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from backend.api import evaluation_records
from backend.api.input_files import import_input_file, MAX_INPUT_BYTES
from backend.api.schemas import EvaluateRequest, EvaluateResponse
from backend.api.serialization import serialize_evaluation_result
from backend.api.service import EvaluationAPIError, _checked_path, _default_evaluator, _EVALUATION_LOCK

SESSION_HEADER = "X-VeriScope-Session"
MAX_SESSIONS = 50
MAX_SESSION_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_FILES = 32
MAX_JOBS_PER_SESSION = 10
MAX_TEST_CASES = 50
SESSION_IDLE_SECONDS = 24 * 60 * 60
MIN_FREE_BYTES = 512 * 1024 * 1024
_MODEL_NAME = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_BUILTIN_URLS = {
    "https://api.deepseek.com", "https://api.deepseek.com/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1", "https://api.openai.com/v1",
    "https://api.siliconflow.cn/v1", "https://openrouter.ai/api/v1", "https://api.moonshot.cn/v1",
}
_EXAMPLE_PATHS = {
    "demo.txt", "testset_example.csv", "testset_example.json",
    "scenarios/deposit_insurance.txt", "scenarios/information_disclosure.txt",
    "scenarios/competition_25/testset.json", "scenarios/competition_25/pilot.json",
    "scenarios/heldout_fp/cases.json",
}


def enabled() -> bool:
    return os.environ.get("VERISCOPE_BYOK") == "1"


class _ModelCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=_MODEL_NAME)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: SecretStr


def _url(value: str) -> str:
    """Exact administrator-controlled origin/path allowlist, with no redirects."""
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or parts.port not in (None, 443)
                or "\\" in value or any(ord(char) < 33 or ord(char) > 126 for char in value)):
            raise ValueError
        canonical = f"https://{parts.hostname.lower()}{parts.path.rstrip('/')}"
        allowed = _BUILTIN_URLS | {item.strip().rstrip('/') for item in
                                 os.environ.get("VERISCOPE_BYOK_BASE_URLS", "").split(",") if item.strip()}
        if canonical not in allowed:
            raise ValueError
        return canonical
    except (ValueError, TypeError):
        raise EvaluationAPIError(422, "模型地址不在本站允许的 HTTPS 服务列表中。") from None


def parse_submission(body: Any) -> tuple[EvaluateRequest, dict[str, dict[str, str]]]:
    try:
        if not isinstance(body, dict):
            raise ValueError
        safe = {key: value for key, value in body.items() if key != "byok_models"}
        request = EvaluateRequest.model_validate(safe)
        supplied = body.get("byok_models")
        if not isinstance(supplied, list) or not 1 <= len(supplied) <= 3 or request.benchmark:
            raise ValueError
        credentials = {}
        for item in supplied:
            model = _ModelCredentials.model_validate(item)
            key = model.api_key.get_secret_value()
            if (not 8 <= len(key) <= 4096 or not key.isascii()
                    or any(ord(char) < 33 or ord(char) > 126 for char in key)
                    or model.name in credentials):
                raise ValueError
            credentials[model.name] = {"name": model.name, "base_url": _url(model.base_url), "api_key": key}
        if (not request.model_names or len(request.model_names) > 3
                or len(set(request.model_names)) != len(request.model_names)
                or any(name not in credentials for name in request.model_names)
                or (request.judge_model_name is not None and request.judge_model_name not in credentials)):
            raise ValueError
        return request, credentials
    except (ValidationError, ValueError, TypeError):
        raise EvaluationAPIError(422, "请提供有效的访客模型、API Key 和评测输入；在线模式不支持基准下载。") from None


def _redact(value: Any, keys: list[str]) -> Any:
    if isinstance(value, str):
        for key in keys:
            value = value.replace(key, "[API Key 已隐藏]")
        return value
    if isinstance(value, dict):
        return {_redact(key, keys): _redact(item, keys) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, keys) for item in value]
    return value


class VisitorWorkspaces:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.data_root = Path(os.environ.get("VERISCOPE_BYOK_DATA_DIR", str(project_root / "work" / "visitors"))).resolve()
        self.jobs: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = Lock()
        self._storage_lock = Lock()
        self._upload_lock = asyncio.Lock()

    def _expire_idle(self):
        if not self.data_root.exists():
            return
        active = {owner for (owner, _), job in self.jobs.items() if job["status"] == "running"}
        cutoff = time.time() - SESSION_IDLE_SECONDS
        for folder in self.data_root.iterdir():
            if (re.fullmatch(r"[0-9a-f]{64}", folder.name) is None or folder.name in active
                    or folder.is_symlink() or getattr(folder, "is_junction", lambda: False)()
                    or not folder.is_dir() or folder.resolve().parent != self.data_root
                    or folder.stat().st_mtime >= cutoff):
                continue
            # Resolved direct child, validated hex name, configured data root.
            shutil.rmtree(folder)
            for key in [key for key in self.jobs if key[0] == folder.name]:
                self.jobs.pop(key)

    def scope(self, request: Request) -> Path:
        token = request.headers.get(SESSION_HEADER, "")
        if re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise EvaluationAPIError(422, "访客会话缺失或格式不正确，请刷新后重新建立会话。")
        name = sha256(token.encode("ascii")).hexdigest()
        root = self.data_root / name
        try:
            with self._lock:
                self._expire_idle()
                if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
                    raise EvaluationAPIError(403, "访客资料目录不可用。")
                if not root.exists():
                    existing = sum(1 for folder in self.data_root.iterdir() if folder.is_dir()) if self.data_root.exists() else 0
                    if existing >= MAX_SESSIONS:
                        raise EvaluationAPIError(503, "访客空间已满，请稍后再试或下载项目在本地使用。")
                    root.mkdir(parents=True)
                os.utime(root, None)
        except OSError:
            raise EvaluationAPIError(503, "访客资料目录暂时不可用，请稍后重试。") from None
        return root

    def _quota(self, root: Path, *, upload: bool = False, additional_bytes: int = 0) -> None:
        try:
            paths = [path for path in root.rglob("*") if path.is_file()]
            used = sum(path.stat().st_size for path in paths)
            if shutil.disk_usage(root).free < MIN_FREE_BYTES:
                raise EvaluationAPIError(503, "服务器暂存空间不足，请下载项目在本地继续。")
        except OSError:
            raise EvaluationAPIError(503, "访客资料目录暂时不可用，请稍后重试。") from None
        reserve = MAX_INPUT_BYTES if upload else 0
        if used + reserve + additional_bytes > MAX_SESSION_BYTES:
            raise EvaluationAPIError(413, "本会话资料与结果已达到 100 MiB 限额，请下载后在本地继续。")
        if upload and sum(path.parent.name == "uploads" for path in paths) >= MAX_UPLOAD_FILES:
            raise EvaluationAPIError(413, "本会话最多导入 32 个文件，请在本地继续。")

    async def upload(self, request: Request, *, kind: str, filename: str) -> dict:
        root = self.scope(request)
        async with self._upload_lock:
            self._quota(root, upload=True)
            uploaded = await import_input_file(request, kind=kind, filename=filename, project_root=root)
            # A background result may have consumed the reservation while the
            # request stream was suspended. Serialize the final check with
            # durable record writes; reject only this newly generated file.
            with self._storage_lock:
                try:
                    self._quota(root)
                except EvaluationAPIError:
                    (root / uploaded["relative_path"]).unlink(missing_ok=True)
                    raise
            return uploaded

    async def import_record(self, request: Request) -> dict:
        root = self.scope(request)
        async with self._upload_lock:
            self._quota(root)
            body = await read_json(request, max_bytes=MAX_INPUT_BYTES)
            try:
                # Imported paths are historical claims, not server file handles.
                if isinstance(body, dict) and isinstance(body.get("result"), dict):
                    body = {**body, "result": {**body["result"], "chart_path": None, "html_path": None}}
                elif isinstance(body, dict):
                    body = {**body, "chart_path": None, "html_path": None}
                with self._storage_lock:
                    return evaluation_records.import_record(root, body,
                        before_write=lambda size: self._quota(root, additional_bytes=size)).model_dump(mode="json")
            except (ValueError, TypeError):
                raise EvaluationAPIError(422, "记录格式不完整或版本不支持，请选择导出的 JSON 记录。") from None

    def _input_path(self, raw: str, root: Path, *, testset: bool = False) -> str:
        if raw in _EXAMPLE_PATHS:
            return _checked_path(raw, self.project_root, testset=testset)
        # The unchanged relative path shape is resolved under this visitor only.
        if not raw.startswith("work/uploads/"):
            raise EvaluationAPIError(403, "仅允许本会话上传的文件或本站示例。")
        return _checked_path(raw, root, testset=testset)

    def submit(self, root: Path, body: Any, evaluator=None) -> dict[str, str]:
        request, credentials = parse_submission(body)
        engine_request = request.model_dump()
        engine_request["doc_path"] = self._input_path(request.doc_path or "demo.txt", root)
        if request.testset_path:
            engine_request["testset_path"] = self._input_path(request.testset_path, root, testset=True)
            from backend.services.testsets import load_custom_testset
            try:
                cases = load_custom_testset(engine_request["testset_path"])
                if len(cases) > MAX_TEST_CASES:
                    raise ValueError
            except Exception:
                raise EvaluationAPIError(422, "题集无法读取或内容不合法；本次最多评测 50 题。") from None
        self._quota(root)
        with self._lock:
            count = sum(owner == root.name for owner, _ in self.jobs)
            if count >= MAX_JOBS_PER_SESSION:
                raise EvaluationAPIError(429, "本会话已达到 10 次评测上限，请下载结果后在本地继续。")
            if not _EVALUATION_LOCK.acquire(blocking=False):
                raise EvaluationAPIError(409, "已有评测正在运行，请完成后再试。")
            ident = uuid4().hex
            self.jobs[(root.name, ident)] = {"status": "running", "step": 0, "message": "正在准备评测。"}
        try:
            thread = Thread(target=self._run, args=(root, ident, request, engine_request, credentials, evaluator), daemon=True)
            thread.start()
        except Exception:
            with self._lock:
                self.jobs.pop((root.name, ident), None)
            credentials.clear()
            _EVALUATION_LOCK.release()
            raise EvaluationAPIError(503, "评测暂时无法启动，请稍后重试。") from None
        return {"job_id": ident}

    def job(self, root: Path, ident: str) -> dict:
        with self._lock:
            row = self.jobs.get((root.name, ident))
            if row is None:
                raise EvaluationAPIError(404, "没有找到这次评测任务。")
            response = dict(row)
        record_id = response.pop("record_id", None)
        if record_id:
            try:
                record = evaluation_records.read_record(root, record_id)
                response["result"] = {**record.result.model_dump(mode="json"), "record_id": record.id,
                    "saved_at": record.created_at.isoformat(), "save_error": "".join(record.artifact_warnings) or None}
            except (OSError, ValueError):
                raise EvaluationAPIError(500, "评测结果暂时无法读取，请保留已下载备份。") from None
        return response

    def _discard_run(self, root: Path, run_dir: Path) -> None:
        # Only this freshly generated UUID run belongs to this job.
        expected = (root / "work" / "runs").resolve()
        if (run_dir.parent.resolve() == expected and run_dir.resolve().parent == expected
                and not run_dir.is_symlink() and re.fullmatch(r"[0-9a-f]{32}", run_dir.name)):
            try:
                shutil.rmtree(run_dir)
            except OSError:
                pass

    def _run(self, root, ident, request, engine_request, credentials, evaluator):
        clients = []
        keys = [entry["api_key"] for entry in credentials.values()]
        run_dir = root / "work" / "runs" / ident

        def progress(step, message):
            with self._lock:
                self.jobs[(root.name, ident)].update(step=step, message=message)

        def factory(config):
            # Every attempted model must have come from this request. Never
            # delegate to registry/build_llm or mutate process environment.
            from backend.adapters.models.openai_compatible import build_visitor_llm
            entry = credentials.get(config["name"])
            if entry is None or entry["base_url"] != config["base_url"]:
                raise ValueError("访客模型配置不一致。")
            llm = build_visitor_llm(entry)
            clients.append(llm)
            return llm

        try:
            run_dir.mkdir(parents=True)
            configs = [{"name": entry["name"], "base_url": entry["base_url"]} for entry in credentials.values()]
            runtime = {"output_dir": str(run_dir), "model_configs": configs,
                       "testset_model": request.model_names[0], "safe_errors": True}
            execute = _default_evaluator if evaluator is None else evaluator
            raw = execute(engine_request, resources=None, model_factory=factory, progress_cb=progress, runtime=runtime)
            # Chroma and report libraries write directly during execution. This
            # is a post-generation limit, not a hard transient disk ceiling.
            self._quota(root)
            payload = _redact(serialize_evaluation_result(raw), keys)
            for field in ("chart_path", "html_path"):
                value = payload[field]
                if value is not None:
                    artifact = Path(value)
                    artifact = (run_dir / artifact).resolve() if not artifact.is_absolute() else artifact.resolve()
                    payload[field] = artifact.relative_to(root).as_posix()
            for path in run_dir.rglob("*"):
                if path.is_file() and path.suffix in (".html", ".csv", ".json"):
                    text = path.read_text(encoding="utf-8-sig")
                    redacted = _redact(text, keys)
                    if redacted != text:
                        path.write_text(redacted, encoding="utf-8")
            payload = EvaluateResponse.model_validate(payload).model_dump(mode="json")
            # The durable request never held byok_models or raw credentials.
            try:
                with self._storage_lock:
                    record = evaluation_records.capture_record(root, request, payload,
                        before_write=lambda size: self._quota(root, additional_bytes=size))
                completed = {"record_id": record.id}
            except (OSError, ValueError):
                completed = {"result": {**payload, "record_id": None, "saved_at": None,
                    "save_error": "评测已完成但记录未保存，请立即下载备份；不要为保存记录重复评测。"}}
            with self._lock:
                # Successful jobs retain only a disk-record pointer. At most two
                # disk-failure fallbacks retain full results in memory.
                fallbacks = [key for key, row in self.jobs.items() if "result" in row]
                if "result" in completed and len(fallbacks) >= 2:
                    self.jobs[fallbacks[0]] = {"status": "failed", "step": 6,
                        "message": "临时结果已过期。", "error": "请从已下载备份恢复；不要为保存记录重复评测。"}
                self.jobs[(root.name, ident)] = {"status": "completed", "step": 6, "message": "评测已完成。", **completed}
        except EvaluationAPIError as error:
            if error.status_code == 413:
                self._discard_run(root, run_dir)
            with self._lock:
                self.jobs[(root.name, ident)] = {"status": "failed", "step": 0,
                    "message": "评测结果未保存。", "error": error.detail}
        except Exception:
            # Provider errors may contain HTTP headers or a reflected key.
            error_message = "评测未能完成，请检查访客 API Key、模型、资料及服务可用性。"
            try:
                self._quota(root)
            except EvaluationAPIError as quota_error:
                if quota_error.status_code == 413:
                    error_message = quota_error.detail
            self._discard_run(root, run_dir)
            with self._lock:
                self.jobs[(root.name, ident)] = {"status": "failed", "step": 0,
                    "message": "评测未完成。", "error": error_message}
        finally:
            for llm in clients:
                try:
                    llm.http_client.close()
                    asyncio.run(llm.http_async_client.aclose())
                except Exception:
                    pass
            credentials.clear()
            keys.clear()
            _EVALUATION_LOCK.release()


async def read_json(request: Request, *, max_bytes: int = 1024 * 1024) -> Any:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise EvaluationAPIError(413, "请求内容超过大小限制。")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (ValueError, TypeError, RecursionError):
        raise EvaluationAPIError(422, "请求参数不合法，请检查 API 合同。") from None
