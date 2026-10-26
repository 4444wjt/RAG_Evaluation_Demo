"""veriscope.e2e.v1: retrieve in the application, validate its actual generation contexts."""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
import time
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

SCHEMA_VERSION = "veriscope.e2e.v1"
HTTP_TIMEOUT = 180
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CONTEXTS = 20
MAX_CONTEXT_CHARS = 8000


class ApplicationConfigError(ValueError):
    """Client-safe configuration failure; messages never contain credential values."""


class ContextPassage(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    text: StrictStr = Field(min_length=1, max_length=MAX_CONTEXT_CHARS)
    sourceId: StrictStr | None = Field(default=None, max_length=512)
    sourceTitle: StrictStr | None = Field(default=None, max_length=512)
    location: StrictStr | None = Field(default=None, max_length=1024)
    rank: int | None = Field(default=None, ge=1, strict=True)
    score: float | None = Field(default=None, strict=True)


class ApplicationError(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: StrictStr = Field(min_length=1, max_length=256)
    message: StrictStr = Field(max_length=32000)


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schemaVersion: Literal["veriscope.e2e.v1"]
    id: StrictStr = Field(min_length=1, max_length=128)
    status: Literal["answered", "refused", "error"]
    answer: StrictStr | None = Field(default=None, max_length=32000)
    contexts: list[ContextPassage] | None = Field(default=None, max_length=MAX_CONTEXTS)
    error: ApplicationError | None = None
    meta: dict[str, Any] | None = None

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "error":
            if self.error is None:
                raise ValueError("error status requires error details")
        elif self.answer is None or not self.answer.strip() or self.contexts is None or self.error is not None:
            raise ValueError("answered/refused require an answer, contexts and no error")
        return self


def checked_endpoint(value: str) -> str:
    try:
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(char.isspace() for char in value)):
            raise ValueError
        _ = url.port
    except (ValueError, TypeError):
        raise ApplicationConfigError("应用地址必须是无用户名、密码、查询参数或片段的 HTTP(S) 地址。") from None
    return value


def request_body(item: dict) -> dict:
    item_id, question = item.get("id"), item.get("question")
    if (not isinstance(item_id, str) or not 1 <= len(item_id) <= 128
            or any(ord(char) < 32 for char in item_id)
            or not isinstance(question, str) or not question.strip() or len(question) > 8000):
        raise ApplicationConfigError("题号或问题不合法。")
    # Labels, references, expected behavior and platform contexts must never leave here.
    return {"schemaVersion": SCHEMA_VERSION, "id": item_id, "question": question}


def access_token(token_env: str | None) -> str | None:
    if token_env is None:
        return None
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", token_env):
        raise ApplicationConfigError("tokenEnv 必须是环境变量名，不能填写令牌。")
    token = os.environ.get(token_env)
    if not token or any(char in token for char in "\r\n"):
        raise ApplicationConfigError("应用令牌环境变量未设置或格式不合法。")
    return token


def _no_constant(_value):
    raise ValueError("Non-finite JSON numbers are not allowed")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON fields are not allowed")
        result[key] = value
    return result


@dataclass
class ApplicationCall:
    request: dict
    response: dict | None
    response_text: str | None
    http_status: int | None
    seconds: float
    outcome: str
    error_code: str | None = None
    response_sha256: str | None = None
    response_bytes: int | None = None

    @property
    def usable(self):
        return self.outcome == "completed"


def call_application(endpoint: str, item: dict, *, token_env: str | None = None,
                     timeout: float = HTTP_TIMEOUT, client: httpx.Client | None = None) -> ApplicationCall:
    """Exactly one HTTP request. Never retry, follow redirects or replace supplied contexts."""
    endpoint = checked_endpoint(endpoint)
    body = request_body(item)
    if not 0 < timeout <= HTTP_TIMEOUT:
        raise ApplicationConfigError("应用超时必须大于 0 且不超过 180 秒。")
    token = access_token(token_env)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    started = time.monotonic()
    owned = client is None
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=False)
    payload, text, status, outcome, code = None, None, None, "call_error", None
    response_hash, size = None, None
    try:
        with client.stream("POST", endpoint, json=body, headers=headers, timeout=timeout,
                           follow_redirects=False) as response:
            status = response.status_code
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    outcome = "response_too_large"
                    break
                chunks.append(chunk)
            else:
                raw_bytes = b"".join(chunks)
                response_hash = sha256(raw_bytes).hexdigest()
                try:
                    text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    outcome = "encoding_error"
                if text is None:
                    pass
                elif not 200 <= status < 300:
                    outcome = "http_error"
                else:
                    try:
                        value = json.loads(text, parse_constant=_no_constant, object_pairs_hook=_unique_object)
                    except ValueError:
                        outcome = "parse_error"
                    else:
                        if isinstance(value, dict):
                            payload = value
                        if not isinstance(value, dict):
                            outcome = "invalid_response"
                        elif value.get("schemaVersion") != SCHEMA_VERSION:
                            outcome = "version_mismatch"
                        elif value.get("id") != body["id"]:
                            outcome = "id_mismatch"
                        else:
                            try:
                                parsed = ApplicationResponse.model_validate(value)
                            except ValidationError:
                                outcome = "invalid_response"
                            else:
                                outcome = "application_error" if parsed.status == "error" else "completed"
                                code = parsed.error.code if parsed.error is not None else None
    except httpx.TimeoutException:
        outcome = "timeout"
    except httpx.HTTPError:
        outcome = "call_error"
    finally:
        if owned:
            client.close()
    return ApplicationCall(body, payload, text, status, round(time.monotonic() - started, 4), outcome,
                           code, response_hash, size)
