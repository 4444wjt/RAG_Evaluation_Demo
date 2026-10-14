"""本地登记的自定义模型：读写项目根目录的 models.local.json（不入库）。

内置模型仍在 backend.config.MODELS_CONFIG；这里只增加用户在本机登记的 OpenAI 兼容模型。
密钥永远不进这个文件：只记录密钥所在的环境变量名，值仍从本地 .env 或进程环境读取。

自定义模型的密钥变量名必须形如 VERISCOPE_<名称>_API_KEY：
不能借用内置模型的密钥变量，避免把已有密钥发往一个新登记的地址。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

FILE_NAME = "models.local.json"
SCHEMA_VERSION = 1
MAX_MODELS = 20
KEY_ENV_PATTERN = re.compile(r"^VERISCOPE_[A-Z0-9_]{1,48}_API_KEY$")
# 模型名会拼进报告文件名（"/" 换成 "_"），不允许冒号、反斜杠等在 Windows 上有特殊含义的字符。
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
FIELDS = ("name", "display_name", "base_url", "api_key_env")


class ModelConfigError(ValueError):
    """登记内容不合法；消息可直接展示给用户，不含输入原文以外的信息。"""


def _text(value, field: str, limit: int, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ModelConfigError(f"{field} 必须是文本。")
    value = value.strip()
    if required and not value:
        raise ModelConfigError(f"{field} 不能为空。")
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise ModelConfigError(f"{field} 过长或含控制字符。")
    return value


def _base_url(value) -> str:
    url = _text(value, "base_url", 512)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ModelConfigError("base_url 必须是 http 或 https 地址。")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ModelConfigError("base_url 不能包含用户名、密码、查询参数或片段；密钥请写在 .env 中。")
    return url


def validate(models, builtin_names=()) -> list[dict]:
    """校验并规范化登记列表；出现密钥字段或未知字段一律拒绝。"""
    if not isinstance(models, list):
        raise ModelConfigError("models 必须是列表。")
    if len(models) > MAX_MODELS:
        raise ModelConfigError(f"最多登记 {MAX_MODELS} 个自定义模型。")
    result, names = [], set(builtin_names)
    for index, entry in enumerate(models, 1):
        if not isinstance(entry, dict):
            raise ModelConfigError(f"第 {index} 项必须是对象。")
        unknown = set(entry) - set(FIELDS)
        if unknown:
            raise ModelConfigError(f"第 {index} 项含不支持的字段；密钥不能写入登记，只填密钥所在的环境变量名。")
        name = _text(entry.get("name"), "name", 128)
        if not NAME_PATTERN.fullmatch(name):
            raise ModelConfigError("name 只能含字母、数字和 . _ / -，且以字母或数字开头。")
        if name in names:
            raise ModelConfigError(f"模型名 {name} 已存在。")
        key_env = _text(entry.get("api_key_env"), "api_key_env", 64)
        if not KEY_ENV_PATTERN.fullmatch(key_env):
            raise ModelConfigError("api_key_env 必须形如 VERISCOPE_<名称>_API_KEY（大写字母、数字、下划线）。")
        names.add(name)
        result.append({"name": name,
                       "display_name": _text(entry.get("display_name"), "display_name", 64, required=False) or name,
                       "base_url": _base_url(entry.get("base_url")),
                       "api_key_env": key_env})
    return result


def path(root) -> Path:
    return Path(root) / FILE_NAME


def load(root, builtin_names=()) -> list[dict]:
    """读取本地登记；文件不存在时为空。文件损坏时报错，不静默忽略。"""
    file = path(root)
    if not file.exists():
        return []
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ModelConfigError(f"{FILE_NAME} 无法解析，请检查文件内容。") from None
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise ModelConfigError(f"{FILE_NAME} 的格式版本不受支持。")
    return [dict(model, source="local") for model in validate(data.get("models"), builtin_names)]


def save(root, models, builtin_names=()) -> list[dict]:
    """校验后原子写入；先写临时文件再替换，中途失败不留下半个文件。"""
    clean = validate(models, builtin_names)
    file = path(root)
    payload = json.dumps({"schemaVersion": SCHEMA_VERSION, "models": clean}, ensure_ascii=False, indent=2) + "\n"
    handle, temp = tempfile.mkstemp(dir=file.parent, prefix=".models.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temp, file)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return [dict(model, source="local") for model in clean]


def describe(model: dict, source: str) -> dict:
    """对外展示用：只给出密钥是否已设置，不返回密钥值。"""
    return {"name": model["name"], "displayName": model.get("display_name") or model["name"],
            "baseUrl": model["base_url"], "apiKeyEnv": model["api_key_env"],
            "keyConfigured": bool(os.environ.get(model["api_key_env"])), "source": source}
