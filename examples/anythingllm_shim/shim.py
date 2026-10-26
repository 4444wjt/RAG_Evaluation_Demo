"""Loopback-only veriscope.e2e.v1 adapter for AnythingLLM; standard library only.

Starting this program reads workspace settings once. Each POST /rag makes exactly
one query-mode chat request, with a fresh session ID. No retry or model fallback.
API sources are preserved; equivalence to final model context must be verified
separately (AnythingLLM may compress its prompt after constructing sources).
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

SCHEMA = "veriscope.e2e.v1"
MAX_BYTES = 2 * 1024 * 1024
CHAT_TIMEOUT = 120
SETTINGS_FIELDS = ("topN", "similarityThreshold", "vectorSearchMode")
# Audited against AnythingLLM feb0ebe8: prisma/schema.prisma workspaces/documents.
DETAIL_TYPES = {
    "chatProvider": str, "chatModel": str, "chatMode": str,
    "openAiTemp": (int, float), "openAiHistory": int,
    "openAiPrompt": str, "queryRefusalResponse": str,
}
# deepseek/index.js getChatCompletion; duration is seconds, outputTps is tokens/s.
METRIC_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens", "duration", "outputTps")
# GET /api/v1/system returns SystemSettings.currentSettings() (v1.18.0
# endpoints/api/system/index.js:37-67). Only these scalar fields are kept; key
# fields there are booleans, and StorageDir is used locally but never written out.
SYSTEM_FIELDS = (
    "MemoryEnabled", "MemoryAutoExtraction", "MultiUserMode", "LLMProvider", "LLMModel",
    "ModelRouterId", "DeepSeekModelPref", "EmbeddingEngine", "EmbeddingModelPref",
    "EmbeddingOutputDimensions", "EmbeddingModelMaxChunkLength",
    "GenericOpenAiEmbeddingPassagePrefix", "GenericOpenAiEmbeddingQueryPrefix",
    "GenericOpenAiEmbeddingMaxConcurrentChunks", "VectorDB",
)
SNAPSHOT_SCHEMA = "anythingllm-runtime-snapshot.v1"
# modelMap/index.js: cache at STORAGE_DIR/models/context-windows; get() re-reads it
# on every call and returns null when provider/model is absent or falsy;
# deepseek/index.js:63-68 then falls back to 8192.
CACHE_PARTS = ("models", "context-windows")
FALLBACK_WINDOW = 8192


class ShimError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-finite number")


def parse_json(raw: bytes):
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    # Reject overflow floats and lone surrogate escapes before serializing downstream.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return value


def split_reasoning(answer: str) -> tuple[str, dict]:
    """Split only the preregistered leading think block; preserve all other text."""
    raw = answer.encode("utf-8")
    block = {"status": "absent", "rawAnswerUtf8Bytes": len(raw),
             "rawAnswerSha256": sha256(raw).hexdigest(),
             "reasoningUtf8Bytes": None, "reasoningSha256": None}
    if answer.startswith("<think>"):
        if answer.count("</think>") == 1:
            reasoning, answer = answer[len("<think>"):].split("</think>", 1)
            raw_reasoning = reasoning.encode("utf-8")
            block.update(status="stripped", reasoningUtf8Bytes=len(raw_reasoning),
                         reasoningSha256=sha256(raw_reasoning).hexdigest())
        else:
            block["status"] = "ambiguous_not_stripped"
    return answer, block


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnythingLLM:
    def __init__(self, base: str, slug: str, api_key: str, refusal_text: str | None,
                 *, timeout: float = CHAT_TIMEOUT):
        try:
            url = urlsplit(base)
            if (url.scheme not in ("http", "https") or not url.hostname or url.username
                    or url.password or url.query or url.fragment
                    or any(c.isspace() for c in base)):
                raise ValueError
            _ = url.port
        except ValueError:
            raise ValueError("AnythingLLM 地址必须是不含凭据和查询参数的 HTTP(S) 地址。") from None
        if not slug or not api_key or any(c in api_key for c in "\r\n"):
            raise ValueError("工作区 slug 或 API key 环境变量未设置或格式不合法。")
        try:
            api_key.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError("API key 格式不合法。") from None
        self.base = base.rstrip("/")
        self.url = self.base + "/api/v1/workspace/" + quote(slug, safe="")
        self._key = api_key
        self.refusal_text = refusal_text if refusal_text else None
        self.timeout = timeout
        # Do not send local API credentials through proxy environment variables.
        self._opener = build_opener(ProxyHandler({}), NoRedirect())
        self.settings = {name: None for name in SETTINGS_FIELDS}
        self.settings_status = "not_read"
        self.details = {name: None for name in DETAIL_TYPES}
        self.details.update(openAiPromptUtf8Bytes=None, openAiPromptSha256=None,
                            documentCount=None, pinnedDocumentCount=None, documentNames=None)
        self.detail_status = {name: "not_read" for name in (*DETAIL_TYPES, "documents")}

    def _json(self, method: str, suffix: str = "", body=None, *, timeout=None, url=None):
        raw = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = Request(url or self.url + suffix, data=raw, method=method,
                          headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise ShimError("UPSTREAM_HTTP", "AnythingLLM 返回非成功 HTTP 状态。")
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        declared_length = int(declared_length)
                        if declared_length < 0:
                            raise ValueError
                    except ValueError:
                        raise ShimError("UPSTREAM_CONNECTION", "AnythingLLM HTTP 长度声明不合法。") from None
                    if declared_length > MAX_BYTES:
                        raise ShimError("RESPONSE_TOO_LARGE", "AnythingLLM 响应超过 2 MiB；未截断。")
                data = response.read(MAX_BYTES + 1)
                if declared_length is not None and len(data) != declared_length:
                    raise ShimError("UPSTREAM_CONNECTION", "AnythingLLM HTTP 响应未完整接收；未重试。")
        except HTTPError:
            raise ShimError("UPSTREAM_HTTP", "AnythingLLM 返回非成功 HTTP 状态；未重试。") from None
        except (TimeoutError, socket.timeout):
            raise ShimError("UPSTREAM_TIMEOUT", "AnythingLLM 请求超时；未重试。") from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise ShimError("UPSTREAM_TIMEOUT", "AnythingLLM 请求超时；未重试。") from None
            raise ShimError("UPSTREAM_CONNECTION", "无法连接 AnythingLLM；未重试。") from None
        except OSError:
            raise ShimError("UPSTREAM_CONNECTION", "AnythingLLM 连接失败；未重试。") from None
        except HTTPException:
            raise ShimError("UPSTREAM_CONNECTION", "AnythingLLM HTTP 响应不完整或不合法；未重试。") from None
        if len(data) > MAX_BYTES:
            raise ShimError("RESPONSE_TOO_LARGE", "AnythingLLM 响应超过 2 MiB；未截断。")
        try:
            parsed = parse_json(data)
        except (ValueError, UnicodeError, RecursionError):
            raise ShimError("UPSTREAM_JSON", "AnythingLLM 响应不是有效的 UTF-8 JSON 对象。") from None
        if not isinstance(parsed, dict):
            raise ShimError("UPSTREAM_JSON", "AnythingLLM 响应必须为 JSON 对象。")
        if self._key in json.dumps(parsed, ensure_ascii=False):
            raise ShimError("CREDENTIAL_IN_RESPONSE", "响应含凭据信息，已拒绝转发原文。")
        return parsed

    def read_settings(self):
        """One startup GET; unavailable fields remain null. Never copy arbitrary metadata."""
        try:
            value = self._json("GET", timeout=min(self.timeout, 15))
            workspaces = value.get("workspace")
            if not isinstance(workspaces, list) or len(workspaces) != 1 or not isinstance(workspaces[0], dict):
                raise ShimError("WORKSPACE_SHAPE", "工作区设置响应结构不符。")
            workspace = workspaces[0]
            for name in SETTINGS_FIELDS:
                field = workspace.get(name)
                if field is None or type(field) in (int, float, bool, str):
                    if not isinstance(field, float) or math.isfinite(field):
                        self.settings[name] = field
            for name, expected in DETAIL_TYPES.items():
                field = workspace.get(name)
                if name not in workspace:
                    self.detail_status[name] = "missing"
                elif field is None:
                    self.detail_status[name] = "null"
                elif (type(field) in (expected if isinstance(expected, tuple) else (expected,))
                      and (not isinstance(field, float) or math.isfinite(field))):
                    self.details[name] = field
                    self.detail_status[name] = "recorded"
                else:
                    self.detail_status[name] = "invalid_type"
            prompt = self.details["openAiPrompt"]
            if prompt is not None:
                raw_prompt = prompt.encode("utf-8")
                self.details.update(openAiPromptUtf8Bytes=len(raw_prompt), openAiPromptSha256=sha256(raw_prompt).hexdigest())
            documents = workspace.get("documents")
            if isinstance(documents, list):
                self.details["documentCount"] = len(documents)
                names_valid = all(isinstance(doc, dict) and isinstance(doc.get("filename"), str) for doc in documents)
                pins_valid = all(isinstance(doc, dict) and type(doc.get("pinned")) is bool for doc in documents)
                if names_valid:
                    self.details["documentNames"] = [doc["filename"] for doc in documents]
                if pins_valid:
                    self.details["pinnedDocumentCount"] = sum(doc["pinned"] for doc in documents)
                self.detail_status["documents"] = "recorded" if names_valid and pins_valid else "incomplete_or_invalid"
            else:
                self.detail_status["documents"] = "missing" if "documents" not in workspace else "invalid_type"
            self.settings_status = "available"
        except ShimError as error:
            self.settings_status = error.code

    def read_system(self):
        """One GET of system settings; returns (whitelisted fields, per-field status, storage dir)."""
        fields = {name: None for name in SYSTEM_FIELDS}
        status = {name: "not_read" for name in SYSTEM_FIELDS}
        try:
            value = self._json("GET", url=self.base + "/api/v1/system", timeout=min(self.timeout, 15))
        except ShimError as error:
            return fields, {name: error.code for name in SYSTEM_FIELDS}, None
        settings = value.get("settings")
        if not isinstance(settings, dict):
            return fields, {name: "shape_invalid" for name in SYSTEM_FIELDS}, None
        for name in SYSTEM_FIELDS:
            field = settings.get(name)
            if name not in settings:
                status[name] = "missing"
            elif field is None or (type(field) in (str, int, bool) or (type(field) is float and math.isfinite(field))):
                fields[name], status[name] = field, "recorded" if field is not None else "null"
            else:
                status[name] = "invalid_type"
        storage = settings.get("StorageDir")
        return fields, status, storage if isinstance(storage, str) and storage.strip() else None

    def answer(self, body: dict):
        item_id = body.get("id") if isinstance(body, dict) else None
        safe_id = item_id if isinstance(item_id, str) and 1 <= len(item_id) <= 128 and self._key not in item_id else "invalid-request"
        meta = {"system": "AnythingLLM", "mode": "query", "sessionId": None,
                "rawAnswer": None, "reasoningBlock": None,
                "workspaceSettings": dict(self.settings), "workspaceSettingsStatus": self.settings_status,
                # Additive: keep the original three-field snapshot compatible.
                "workspaceDetails": deepcopy(self.details), "workspaceDetailsFieldStatus": dict(self.detail_status),
                "refusalTextMismatch": (self.details["queryRefusalResponse"] != self.refusal_text
                                        if self.detail_status["queryRefusalResponse"] == "recorded"
                                        else False if self.detail_status["queryRefusalResponse"] == "null" else None),
                "upstreamMetrics": {}, "upstreamMetricsStatus": "not_received", "upstreamMetricsInvalidFields": [],
                "upstreamMetricsProvenance": "AnythingLLM_self_reported; token_zero_may_be_upstream_default",
                "refusalDetection": "exact_configured_text" if self.refusal_text else "not_configured_all_answered",
                "contextProvenance": "api_sources_final_prompt_equivalence_unverified",
                "latencyMs": None}
        result = {"schemaVersion": SCHEMA, "id": safe_id, "status": "error", "meta": meta}
        started = None
        try:
            question = body.get("question") if isinstance(body, dict) else None
            if (not isinstance(body, dict) or body.get("schemaVersion") != SCHEMA or safe_id != item_id
                    or any(ord(c) < 32 for c in safe_id) or not isinstance(question, str)
                    or not question.strip() or len(question) > 8000):
                raise ShimError("INVALID_REQUEST", "请求的协议版本、题号或问题不合法。")
            if self._key in question:
                raise ShimError("CREDENTIAL_IN_REQUEST", "问题含凭据信息，已拒绝转发。")
            meta["sessionId"] = "veriscope-" + item_id + "-" + str(uuid4())
            raw_question = question.encode("utf-8")
            meta["requestEvidence"] = {
                "questionUtf8Bytes": len(raw_question), "questionSha256": sha256(raw_question).hexdigest(),
                "attachments": 0, "freshSession": True,
                "hasCommandSyntax": "/" in question or "@agent" in question.lower(),
            }
            started = time.monotonic()
            value = self._json("POST", "/chat", {"message": question, "mode": "query", "sessionId": meta["sessionId"]})
            meta["latencyMs"] = round((time.monotonic() - started) * 1000, 3)
            metrics = value.get("metrics")
            meta["upstreamMetricsStatus"] = "recorded" if isinstance(metrics, dict) else "missing_or_invalid"
            if isinstance(metrics, dict):
                for name in METRIC_FIELDS:
                    if name not in metrics:
                        continue
                    metric = metrics[name]
                    if (type(metric) in (int, float) and metric >= 0
                            and (type(metric) is int or math.isfinite(metric))):
                        meta["upstreamMetrics"][name] = metric
                    else:
                        meta["upstreamMetricsInvalidFields"].append(name)
            if value.get("type") == "abort":
                raise ShimError("UPSTREAM_ABORT", "AnythingLLM 中止作答；未重试。")
            answer, sources = value.get("textResponse"), value.get("sources")
            if not isinstance(answer, str) or not answer.strip() or not isinstance(sources, list):
                raise ShimError("UPSTREAM_SHAPE", "AnythingLLM 未提供有效的原文回答与 sources 数组。")
            if len(answer) > 32000 or len(sources) > 20:
                raise ShimError("LIMIT_EXCEEDED", "回答或片段数量超出契约限制；未截断。")
            meta["rawAnswer"] = answer
            answer, meta["reasoningBlock"] = split_reasoning(answer)
            if not answer.strip():
                raise ShimError("UPSTREAM_SHAPE", "AnythingLLM 剥离推理块后未提供有效回答；未重试。")
            contexts = []
            for source in sources:
                if not isinstance(source, dict) or not isinstance(source.get("text"), str) or not source["text"]:
                    raise ShimError("UPSTREAM_SHAPE", "source 缺少非空原文 text。")
                if len(source["text"]) > 8000:
                    raise ShimError("LIMIT_EXCEEDED", "片段超过 8000 字；未截断。")
                context = {"text": source["text"]}
                if source.get("title") is not None:
                    if not isinstance(source["title"], str) or len(source["title"]) > 512:
                        raise ShimError("UPSTREAM_SHAPE", "source title 不符合契约。")
                    context["sourceTitle"] = source["title"]
                if source.get("score") is not None:
                    score = source["score"]
                    if type(score) not in (int, float) or not math.isfinite(score):
                        raise ShimError("UPSTREAM_SHAPE", "source score 必须为有限数值。")
                    context["score"] = score
                contexts.append(context)
            status = "refused" if not sources and self.refusal_text is not None and answer == self.refusal_text else "answered"
            result.update(status=status, answer=answer, contexts=contexts, error=None)
            if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
                raise ShimError("RESPONSE_TOO_LARGE", "转换后的响应超过 2 MiB；未截断。")
        except ShimError as error:
            result.update(status="error", error={"code": error.code, "message": error.message})
            result.pop("answer", None)
            result.pop("contexts", None)
        finally:
            if started is not None and meta["latencyMs"] is None:
                meta["latencyMs"] = round((time.monotonic() - started) * 1000, 3)
        return result


def effective_provider_model(details: dict, system: dict):
    """Mirror resolveProviderConnector and DeepSeekLLM's constructor (both use `||`)."""
    provider = details.get("chatProvider") or system.get("LLMProvider") or None
    if provider != "deepseek":
        return provider, None, "provider_not_deepseek_or_unknown"
    if details.get("chatModel"):
        return provider, details["chatModel"], "workspace.chatModel"
    if system.get("DeepSeekModelPref"):
        return provider, system["DeepSeekModelPref"], "system.DeepSeekModelPref"
    return provider, "deepseek-chat", "deepseek_adapter_default"


def _file_facts(path):
    try:
        data = path.read_bytes()
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False}, None
    except OSError:
        return {"exists": None, "error": "unreadable"}, None
    return {"exists": True, "sizeBytes": len(data), "sha256": sha256(data).hexdigest(),
            "mtimeNs": stat.st_mtime_ns}, data


def snapshot_window_cache(storage_dir, provider, model):
    """Facts about the context-window cache plus the single value AnythingLLM would read."""
    if storage_dir is None:
        return {"status": "storage_dir_unknown", "effectiveWindow": None}
    folder = Path(storage_dir, *CACHE_PARTS)
    cache, data = _file_facts(folder / "context-windows.json")
    cached_at, _ = _file_facts(folder / ".cached_at")
    result = {"cacheFile": cache, "cachedAtFile": cached_at, "provider": provider, "model": model,
              "lookupValue": None, "effectiveWindow": None}
    if provider != "deepseek" or not model:
        result["status"] = "provider_not_deepseek_or_unknown"
    elif cache.get("exists") is False:
        result.update(status="cache_file_missing_fallback", effectiveWindow=FALLBACK_WINDOW)
    elif data is None:
        result["status"] = "cache_file_unreadable"
    else:
        try:
            table = parse_json(data)
        except (ValueError, UnicodeError, RecursionError):
            table = None
        providers = table if isinstance(table, dict) else None
        models = providers.get(provider) if providers is not None else None
        value = models.get(model) if isinstance(models, dict) else None
        if providers is None:
            result["status"] = "cache_file_invalid_json"
        elif not isinstance(models, dict) or not models:
            # get(): `!this.cachedModelMap[provider]` returns null -> 8192.
            result.update(status="provider_missing_fallback", effectiveWindow=FALLBACK_WINDOW)
        elif value is None or value is False or value == "" or (type(value) in (int, float) and value == 0):
            # JavaScript-falsy only: `!{}` and `![]` are false there, and Number() of them is not 8192.
            result.update(status="model_missing_fallback", effectiveWindow=FALLBACK_WINDOW, lookupValue=value)
        elif type(value) in (int, float) and math.isfinite(value) and value > 0:
            result.update(status="cached_value", lookupValue=value, effectiveWindow=int(value) if value == int(value) else value)
        else:
            # Number(value) could be NaN or fractional; do not guess the compressor's behaviour.
            result.update(status="cached_value_not_a_positive_number", lookupValue=value)
    return result


def runtime_snapshot(client: AnythingLLM, shim_path=None):
    client.read_settings()
    system, system_status, storage = client.read_system()
    provider, model, basis = effective_provider_model(client.details, system)
    try:
        shim_hash = sha256(Path(shim_path or __file__).read_bytes()).hexdigest()
    except OSError:
        shim_hash = None
    return {
        "schemaVersion": SNAPSHOT_SCHEMA,
        "takenAtUtc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "shimSha256": shim_hash,
        "workspaceSettings": dict(client.settings), "workspaceSettingsStatus": client.settings_status,
        "workspaceDetails": deepcopy(client.details), "workspaceDetailsFieldStatus": dict(client.detail_status),
        "system": system, "systemFieldStatus": system_status,
        "storageDirReported": storage is not None,  # the path itself is never written
        "effective": {"provider": provider, "model": model, "modelBasis": basis},
        "contextWindowCache": snapshot_window_cache(storage, provider, model),
        "note": "只读快照：未发送聊天请求。不含 API key、系统设置中的其他字段或存储目录路径。",
    }


def make_handler(client: AnythingLLM):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, value: dict):
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass  # Disconnected caller; never retry a potentially paid upstream call.

        def do_POST(self):  # noqa: N802
            if self.path != "/rag":
                return self._send(404, {"error": "not found"})
            self.connection.settimeout(10)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BYTES or self.headers.get("Transfer-Encoding"):
                    raise ValueError
                body = parse_json(self.rfile.read(length))
            except (ValueError, UnicodeError, OSError, RecursionError):
                self.close_connection = True
                return self._send(400, {"schemaVersion": SCHEMA, "id": "invalid-request", "status": "error",
                                        "error": {"code": "INVALID_REQUEST", "message": "请求不是合法的有限长度 UTF-8 JSON。"}})
            self._send(200, client.answer(body))

        def log_message(self, _format, *_args):
            pass  # No request bodies, upstream messages, credentials or URL paths in logs.

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--anythingllm-base", default="http://localhost:3001")
    parser.add_argument("--workspace-slug", required=True)
    parser.add_argument("--api-key-env", default="ANYTHINGLLM_API_KEY")
    parser.add_argument("--refusal-text-env", default="ANYTHINGLLM_REFUSAL_TEXT")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--dump-settings", metavar="OUTPUT_JSON",
                        help="只读快照工作区、系统白名单字段与窗口缓存后退出；不发聊天请求，不覆盖已有文件。")
    args = parser.parse_args(argv)
    try:
        for name in (args.api_key_env, args.refusal_text_env):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
                raise ValueError("请传环境变量名，不要把 key 或拒答文本直接填进启动参数。")
        if not 1 <= args.port <= 65535:
            raise ValueError("端口必须在 1 到 65535 之间。")
        client = AnythingLLM(args.anythingllm_base, args.workspace_slug,
                             os.environ.get(args.api_key_env, ""), os.environ.get(args.refusal_text_env))
        if args.dump_settings:
            snapshot = runtime_snapshot(client)
            with open(args.dump_settings, "x", encoding="utf-8") as handle:  # never overwrite "before"
                handle.write(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
            cache = snapshot["contextWindowCache"]
            print(f"快照已写入；工作区设置：{snapshot['workspaceSettingsStatus']}；"
                  f"生效提供方：{snapshot['effective']['provider']}；窗口：{cache.get('effectiveWindow')}（{cache.get('status')}）。",
                  flush=True)
            return 0
        client.read_settings()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(client))
    except (ValueError, OSError):
        print("启动失败：请检查地址、工作区、环境变量及监听端口；未重试。", file=sys.stderr)
        return 2
    print(f"转换脚本监听 http://127.0.0.1:{args.port}/rag；尚未发送聊天请求。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
