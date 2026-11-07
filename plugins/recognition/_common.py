"""Small shared helpers for the two isolated local recognition commands."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import platform
import sys


class BadInput(ValueError):
    """The submitted file cannot be decoded by this plugin."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, f"{self.prog}: {message}\n")


def parser(description):
    result = Parser(description=description)
    result.add_argument("--input", type=Path, required=True)
    result.add_argument("--threads", type=int, default=1)
    return result


def configure(threads):
    if not 1 <= threads <= 64:
        raise ValueError("--threads must be between 1 and 64")
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = str(threads)
    for name in ("HF_HUB_OFFLINE", "MODELSCOPE_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ[name] = "1"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def input_file(path, suffixes):
    try:
        if path.suffix.lower() not in suffixes or not path.is_file() or path.stat().st_size == 0:
            raise BadInput("Input is missing, empty, or has an unsupported suffix")
        return sha256(path)
    except OSError as error:
        raise BadInput("Input cannot be read") from error


def model_records(paths):
    records = []
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Explicit local model file is missing or empty: {path.name}")
        records.append({"name": path.name, "sha256": sha256(path)})
    return records


def payload(kind, version, models, path, source_sha, threads, load_ms, infer_ms,
            *, width=None, height=None, duration_ms=None, pages=None, segments=None, warnings=None):
    return {
        "schema": "veriscope.recognition.v1", "kind": kind,
        "engine": {"name": "rapidocr" if kind == "ocr" else "sherpa-onnx",
                   "version": version, "backend": "onnxruntime-cpu", "models": models},
        "input": {"id": path.stem, "sha256": source_sha,
                  "width": width, "height": height, "duration_ms": duration_ms},
        "platform": {"machine": platform.machine(), "python": platform.python_version(), "threads": threads},
        "pages": pages if pages is not None else [],
        "segments": segments if segments is not None else [],
        "timing_ms": {"load": load_ms, "infer": infer_ms},
        "warnings": warnings if warnings is not None else [],
    }


def command(recognize, argv=None):
    try:
        # Engine messages belong on stderr; stdout contains exactly one JSON document.
        with redirect_stdout(sys.stderr):
            result = recognize(argv)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        sys.stdout.buffer.write(encoded + b"\n")
        return 0
    except BadInput as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
