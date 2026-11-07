"""Run one recognition plugin in its own interpreter and bind its output.

The plugin is configured per machine in ``recognition.local.json`` at the
project root (ignored by git through ``*.local.json``)::

    {"schema_version": 1, "plugins": [{
        "kind": "ocr",
        "python": "D:/envs/ocr/Scripts/python.exe",
        "script": "plugins/recognition/rapidocr_plugin.py",
        "args": ["--model-dir", "D:/models/rapidocr"],
        "engine": {"name": "rapidocr", "version": "3.10.0"},
        "models": [{"name": "det.onnx", "sha256": "..."}],
        "suffixes": [".png", ".jpg", ".jpeg"],
        "timeout_s": 120, "threads": 4}]}

Every outcome has an explicit status. Only ``ok`` carries a result, and only
after the output passed the schema and matched the configured engine version,
model hashes and the actual input bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

from backend.recognition.schema import KINDS, RecognitionOutputError, validate

CONFIG_FILE = "recognition.local.json"
PLUGIN_DIR = ("plugins", "recognition")
MAX_STDOUT = 32 * 1024 * 1024
EXIT_BAD_INPUT = 2
_ENV_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL")

STATUSES = ("ok", "plugin_missing", "bad_input", "timeout", "plugin_error",
            "bad_output", "binding_mismatch", "input_mismatch")
MESSAGES = {
    "ok": "识别完成。",
    "plugin_missing": "本机未配置可用的识别插件，请先按说明安装并登记。",
    "bad_input": "文件无法识别：格式不受支持、为空或已损坏。",
    "timeout": "识别超时，已停止插件进程。",
    "plugin_error": "识别插件运行失败，请查看本地服务日志。",
    "bad_output": "识别插件的输出不符合约定格式，结果未采用。",
    "binding_mismatch": "识别插件的引擎版本或模型文件与登记不一致，结果未采用。",
    "input_mismatch": "识别结果对应的文件与提交的文件不一致，结果未采用。",
}


class PluginConfigError(ValueError):
    """The local plugin registration cannot be used."""


@dataclass
class Plugin:
    kind: str
    python: Path
    script: Path
    args: list[str]
    engine: dict[str, str]
    models: list[dict[str, str]]
    suffixes: frozenset[str]
    timeout_s: float
    threads: int


@dataclass
class Outcome:
    status: str
    message: str
    result: dict | None = None
    input_sha256: str | None = None
    elapsed_ms: float | None = None
    # Diagnostic detail for the local log only; never sent to clients.
    diagnostic: str = ""
    plugin: dict[str, Any] = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _plugin(entry: Any, root: Path) -> Plugin:
    required = {"kind", "python", "script", "engine", "models", "suffixes"}
    if not isinstance(entry, dict) or not required <= set(entry):
        raise PluginConfigError("插件登记缺少必要字段。")
    if set(entry) - required - {"args", "timeout_s", "threads"}:
        raise PluginConfigError("插件登记含有未知字段。")
    if entry["kind"] not in KINDS:
        raise PluginConfigError("插件类型只能是 ocr 或 asr。")
    script = (root / str(entry["script"])).resolve()
    plugin_root = root.joinpath(*PLUGIN_DIR).resolve()
    if not script.is_relative_to(plugin_root) or script.suffix != ".py":
        raise PluginConfigError("插件脚本必须位于 plugins/recognition/ 下。")
    engine = entry["engine"]
    if (not isinstance(engine, dict) or set(engine) != {"name", "version"}
            or not all(isinstance(value, str) and value for value in engine.values())):
        raise PluginConfigError("插件登记的引擎名称和版本不完整。")
    models = entry["models"]
    if (not isinstance(models, list) or not models
            or not all(isinstance(model, dict) and set(model) == {"name", "sha256"} for model in models)):
        raise PluginConfigError("插件登记必须列出模型文件及其 SHA256。")
    args = entry.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise PluginConfigError("插件参数必须是字符串列表。")
    suffixes = entry["suffixes"]
    if not isinstance(suffixes, list) or not suffixes or not all(
            isinstance(suffix, str) and suffix.startswith(".") for suffix in suffixes):
        raise PluginConfigError("插件登记必须列出支持的文件后缀。")
    timeout = entry.get("timeout_s", 120)
    threads = entry.get("threads", 1)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise PluginConfigError("插件超时须在 0 到 3600 秒之间。")
    if isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= 64:
        raise PluginConfigError("插件线程数须在 1 到 64 之间。")
    return Plugin(entry["kind"], Path(str(entry["python"])), script, list(args),
                  dict(engine), [dict(model) for model in models],
                  frozenset(suffix.lower() for suffix in suffixes), float(timeout), threads)


def load_plugins(root: Path) -> dict[str, Plugin]:
    """Read the local registration; a missing file means no plugins."""
    path = Path(root) / CONFIG_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise PluginConfigError(f"{CONFIG_FILE} 无法解析。") from None
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("plugins"), list):
        raise PluginConfigError(f"{CONFIG_FILE} 的格式版本不受支持。")
    plugins: dict[str, Plugin] = {}
    for entry in data["plugins"]:
        plugin = _plugin(entry, Path(root).resolve())
        if plugin.kind in plugins:
            raise PluginConfigError("每种识别类型只能登记一个插件。")
        plugins[plugin.kind] = plugin
    return plugins


def _environment(threads: int) -> dict[str, str]:
    # Whitelist only: model keys and other credentials never reach a plugin.
    env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", OMP_NUM_THREADS=str(threads),
               HF_HUB_OFFLINE="1", MODELSCOPE_OFFLINE="1")
    return env


def _binding_errors(result: dict, plugin: Plugin, input_sha256: str) -> tuple[str, str] | None:
    if result["kind"] != plugin.kind:
        return "bad_output", f"kind {result['kind']} != {plugin.kind}"
    engine = result["engine"]
    if engine["name"] != plugin.engine["name"] or engine["version"] != plugin.engine["version"]:
        return "binding_mismatch", f"engine {engine['name']} {engine['version']}"
    actual = sorted((model["name"], model["sha256"]) for model in engine["models"])
    expected = sorted((model["name"], model["sha256"]) for model in plugin.models)
    if actual != expected:
        return "binding_mismatch", "model hashes differ from registration"
    if result["input"]["sha256"] != input_sha256:
        return "input_mismatch", "input sha256 differs from submitted bytes"
    return None


def run(kind: str, input_path: Path, root: Path, *, plugins: dict[str, Plugin] | None = None) -> Outcome:
    """Recognize one file. Never raises for plugin or input faults."""
    def outcome(status: str, **values: Any) -> Outcome:
        return Outcome(status, MESSAGES[status], **values)

    try:
        registry = load_plugins(root) if plugins is None else plugins
    except PluginConfigError as error:
        return outcome("plugin_missing", diagnostic=str(error))
    plugin = registry.get(kind)
    if plugin is None or not plugin.python.is_file() or not plugin.script.is_file():
        return outcome("plugin_missing", diagnostic=f"no usable {kind} plugin")
    summary = {"engine": plugin.engine, "models": plugin.models, "script": plugin.script.name}

    source = Path(input_path)
    if source.suffix.lower() not in plugin.suffixes or not source.is_file() or source.stat().st_size == 0:
        return outcome("bad_input", diagnostic="unsupported suffix, missing or empty file", plugin=summary)
    input_sha256 = sha256_file(source)

    command = [str(plugin.python), "-I", "-X", "utf8", str(plugin.script),
               "--input", str(source.resolve()), "--threads", str(plugin.threads), *plugin.args]
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="veriscope-rec-") as workdir:
        try:
            completed = subprocess.run(command, cwd=workdir, env=_environment(plugin.threads),
                                       capture_output=True, timeout=plugin.timeout_s, check=False)
        except subprocess.TimeoutExpired:
            return outcome("timeout", input_sha256=input_sha256, plugin=summary,
                           elapsed_ms=(time.perf_counter() - started) * 1000,
                           diagnostic=f"exceeded {plugin.timeout_s:g} s")
        except OSError as error:
            return outcome("plugin_missing", plugin=summary, diagnostic=type(error).__name__)
    elapsed = (time.perf_counter() - started) * 1000
    stderr_tail = completed.stderr[-2000:].decode("utf-8", "replace")
    common = {"input_sha256": input_sha256, "elapsed_ms": elapsed, "plugin": summary}
    if completed.returncode == EXIT_BAD_INPUT:
        return outcome("bad_input", diagnostic=stderr_tail, **common)
    if completed.returncode != 0:
        return outcome("plugin_error", diagnostic=f"exit {completed.returncode}: {stderr_tail}", **common)
    if len(completed.stdout) > MAX_STDOUT:
        return outcome("bad_output", diagnostic="stdout too large", **common)
    try:
        result = validate(json.loads(completed.stdout.decode("utf-8")))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        detail = str(error) if isinstance(error, RecognitionOutputError) else type(error).__name__
        return outcome("bad_output", diagnostic=detail, **common)
    problem = _binding_errors(result, plugin, input_sha256)
    if problem is not None:
        status, detail = problem
        return outcome(status, diagnostic=detail, **common)
    return outcome("ok", result=result, **common)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m backend.recognition.runner ocr <file>`` prints a status JSON."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in KINDS:
        print("usage: python -m backend.recognition.runner {ocr|asr} <file>", file=sys.stderr)
        return 64
    root = Path(__file__).resolve().parents[2]
    result = run(args[0], Path(args[1]), root)
    print(json.dumps({"status": result.status, "message": result.message,
                      "input_sha256": result.input_sha256, "elapsed_ms": result.elapsed_ms,
                      "diagnostic": result.diagnostic, "result": result.result},
                     ensure_ascii=False, indent=2))
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
