"""Read approved sources and fixed changedetection.io text snapshots only."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

import httpx

ROOT = Path(__file__).resolve().parents[2]
TIMESTAMP = re.compile(r"[0-9]{1,12}")
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class SourceError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def _unavailable() -> SourceError:
    return SourceError(503, "COLLECTOR_UNAVAILABLE", "采集服务尚未接入。")


def _upstream_error() -> SourceError:
    return SourceError(502, "COLLECTOR_ERROR", "采集服务读取失败，请稍后重试。")


def _captured_at(timestamp: str) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat().replace("+00:00", "Z")


class KnowledgeSources:
    """Configuration stays server-side; an injectable transport supports HTTP tests."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.transport = transport
        self.base = os.environ.get("VERISCOPE_CHANGEDETECTION_URL", "").rstrip("/")
        self.key = os.environ.get("VERISCOPE_CHANGEDETECTION_API_KEY", "")
        path = Path(os.environ.get("VERISCOPE_KNOWLEDGE_SOURCES", str(ROOT / "deploy/knowledge-sources.json")))
        try:
            config = json.loads(path.read_bytes())
            assert config["schema_version"] == "knowledge-sources/v1"
            self.sources = config["sources"]
            assert isinstance(self.sources, list)
            ids = set()
            for source in self.sources:
                assert all(isinstance(source[field], str) and source[field]
                           for field in ("source_id", "title", "url"))
                assert isinstance(source["extraction"], dict)
                assert source["watch_uuid"] is None or (
                    isinstance(source["watch_uuid"], str) and
                    re.fullmatch(r"[A-Za-z0-9_-]+", source["watch_uuid"]))
                assert source["source_id"] not in ids
                ids.add(source["source_id"])
        except (OSError, ValueError, TypeError, KeyError, AssertionError):
            raise _unavailable() from None

    def public_sources(self) -> dict:
        return {"sources": [{**{field: source[field] for field in ("source_id", "title", "url", "extraction")},
                             "collector_ready": bool(self.base and self.key and source["watch_uuid"])}
                            for source in self.sources]}

    def _source(self, source_id: str) -> dict:
        source = next((row for row in self.sources if row["source_id"] == source_id), None)
        if source is None:
            raise SourceError(404, "UNKNOWN_SOURCE", "未知来源。")
        return source

    def _client(self, source: dict) -> httpx.Client:
        if not (self.base and self.key and source["watch_uuid"]):
            raise _unavailable()
        try:
            url = httpx.URL(self.base)
            # An empty "?" or "#" parses as no query/fragment but would still shift the request path.
            if (url.scheme not in {"http", "https"} or not url.host or url.query or url.fragment or url.userinfo
                    or "?" in self.base or "#" in self.base):
                raise ValueError
            return httpx.Client(timeout=TIMEOUT, transport=self.transport, follow_redirects=False,
                                headers={"x-api-key": self.key})
        except (ValueError, httpx.InvalidURL):
            raise _unavailable() from None

    def _get(self, client: httpx.Client, source: dict, timestamp: str | None = None) -> httpx.Response:
        # Never use the server-side history paths, a caller URL, or query params.
        url = f"{self.base}/api/v1/watch/{source['watch_uuid']}/history"
        if timestamp is not None:
            url += "/" + timestamp
        try:
            response = client.get(url)
        except httpx.RequestError:
            raise _upstream_error() from None
        if response.status_code != 200:
            raise _upstream_error()
        return response

    def _history(self, client: httpx.Client, source: dict) -> list[dict]:
        response = self._get(client, source)
        try:
            history = response.json()
            if not isinstance(history, dict) or any(
                not TIMESTAMP.fullmatch(timestamp) or not isinstance(path, str)
                for timestamp, path in history.items()
            ):
                raise ValueError
            return [{"timestamp": timestamp, "captured_at": _captured_at(timestamp)}
                    for timestamp in sorted(history, key=int)]
        except (ValueError, OverflowError, OSError):
            raise _upstream_error() from None

    def versions(self, source_id: str) -> dict:
        source = self._source(source_id)
        with self._client(source) as client:
            return {"source_id": source_id, "versions": self._history(client, source)}

    def snapshot(self, source_id: str, timestamp: str) -> dict:
        if not TIMESTAMP.fullmatch(timestamp):
            raise SourceError(422, "INVALID_TIMESTAMP", "请指定 1 至 12 位数字时间戳，不能使用 latest。")
        source = self._source(source_id)
        with self._client(source) as client:
            history = self._history(client, source)
            version = next((row for row in history if row["timestamp"] == timestamp), None)
            if version is None:
                raise SourceError(404, "UNKNOWN_VERSION", "该采集版本不存在。")
            raw = self._get(client, source, timestamp).content
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise SourceError(422, "INVALID_UTF8", "快照不是严格 UTF-8 文本。") from None
        if not text.strip():
            raise SourceError(422, "EMPTY_SNAPSHOT", "采集快照为空；不能据此认定来源已删除。")
        return {"source_id": source_id, **version, "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw), "text": text}
