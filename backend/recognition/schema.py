"""Strict validation of the fixed ``veriscope.recognition.v1`` plugin output.

The contract is shared with the recognition probe (work/recognition-probe-*).
Confidence values are copied from the engine or ``null``; this module never
fills in a missing value. Coordinates are pixels from the top-left corner and
times are milliseconds.
"""
from __future__ import annotations

import math
import re
from typing import Any

SCHEMA = "veriscope.recognition.v1"
KINDS = ("ocr", "asr")
MAX_PAGES = 500
MAX_LINES = 20000
MAX_SEGMENTS = 20000
MAX_TEXT = 2_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOP_KEYS = {"schema", "kind", "engine", "input", "platform", "pages", "segments", "timing_ms", "warnings"}


class RecognitionOutputError(ValueError):
    """The plugin output does not follow the fixed contract."""


def _fail(location: str, reason: str) -> None:
    raise RecognitionOutputError(f"{location}: {reason}")


def _keys(value: Any, location: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        _fail(location, "must be an object")
    missing = required - set(value)
    extra = set(value) - required - set(optional)
    if missing:
        _fail(location, f"missing {sorted(missing)}")
    if extra:
        _fail(location, f"unexpected {sorted(extra)}")
    return value


def _text(value: Any, location: str, *, empty: bool = True) -> str:
    if not isinstance(value, str) or (not empty and not value):
        _fail(location, "must be a string" if empty else "must be a non-empty string")
    return value


def _int(value: Any, location: str, *, minimum: int = 0, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(location, f"must be an integer >= {minimum}")
    return value


def _number(value: Any, location: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(location, "must be a finite number")
    if minimum is not None and value < minimum:
        _fail(location, f"must be >= {minimum}")
    return float(value)


def _conf(value: Any, location: str) -> float | None:
    if value is None:
        return None
    number = _number(value, location)
    if not 0.0 <= number <= 1.0:
        _fail(location, "confidence must be within [0, 1] or null")
    return number


def _box(value: Any, location: str, width: int, height: int) -> None:
    if not isinstance(value, list) or len(value) != 4:
        _fail(location, "box must have four [x, y] points")
    # Detection boxes may extend slightly past a rotated image edge.
    slack_x, slack_y = 2 + width * 0.02, 2 + height * 0.02
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            _fail(f"{location}[{index}]", "point must be [x, y]")
        x = _number(point[0], f"{location}[{index}][0]")
        y = _number(point[1], f"{location}[{index}][1]")
        if not (-slack_x <= x <= width + slack_x and -slack_y <= y <= height + slack_y):
            _fail(f"{location}[{index}]", "point lies outside the image")


def _engine(value: Any) -> None:
    engine = _keys(value, "engine", {"name", "version", "backend", "models"})
    for key in ("name", "version", "backend"):
        _text(engine[key], f"engine.{key}", empty=False)
    if not isinstance(engine["models"], list) or not engine["models"]:
        _fail("engine.models", "must list at least one model")
    for index, model in enumerate(engine["models"]):
        model = _keys(model, f"engine.models[{index}]", {"name", "sha256"})
        _text(model["name"], f"engine.models[{index}].name", empty=False)
        if not isinstance(model["sha256"], str) or not _SHA256.fullmatch(model["sha256"]):
            _fail(f"engine.models[{index}].sha256", "must be 64 lowercase hex characters")


def _pages(pages: Any, width: int, height: int) -> None:
    if not isinstance(pages, list) or len(pages) > MAX_PAGES:
        _fail("pages", f"must be a list of at most {MAX_PAGES} pages")
    ids, numbers, total_lines, total_text = set(), set(), 0, 0
    for p_index, page in enumerate(pages):
        page = _keys(page, f"pages[{p_index}]", {"page", "lines"})
        number = _int(page["page"], f"pages[{p_index}].page", minimum=1)
        if number in numbers:
            _fail(f"pages[{p_index}].page", "duplicate page number")
        numbers.add(number)
        if not isinstance(page["lines"], list):
            _fail(f"pages[{p_index}].lines", "must be a list")
        for l_index, line in enumerate(page["lines"]):
            where = f"pages[{p_index}].lines[{l_index}]"
            line = _keys(line, where, {"id", "text", "conf", "box", "chars"})
            line_id = _text(line["id"], f"{where}.id", empty=False)
            if line_id in ids:
                _fail(f"{where}.id", "duplicate line id")
            ids.add(line_id)
            total_text += len(_text(line["text"], f"{where}.text"))
            _conf(line["conf"], f"{where}.conf")
            _box(line["box"], f"{where}.box", width, height)
            if not isinstance(line["chars"], list):
                _fail(f"{where}.chars", "must be a list")
            for c_index, char in enumerate(line["chars"]):
                char = _keys(char, f"{where}.chars[{c_index}]", {"text", "conf", "box"})
                _text(char["text"], f"{where}.chars[{c_index}].text", empty=False)
                _conf(char["conf"], f"{where}.chars[{c_index}].conf")
                _box(char["box"], f"{where}.chars[{c_index}].box", width, height)
        total_lines += len(page["lines"])
    if total_lines > MAX_LINES or total_text > MAX_TEXT:
        _fail("pages", "recognition output is too large")


def _segments(segments: Any, duration_ms: int) -> None:
    if not isinstance(segments, list) or len(segments) > MAX_SEGMENTS:
        _fail("segments", f"must be a list of at most {MAX_SEGMENTS} segments")
    ids, total_text, previous_start = set(), 0, -1
    limit = duration_ms + 100  # rounding of frame boundaries
    for s_index, segment in enumerate(segments):
        where = f"segments[{s_index}]"
        segment = _keys(segment, where, {"id", "start_ms", "end_ms", "text", "conf", "tokens"})
        segment_id = _text(segment["id"], f"{where}.id", empty=False)
        if segment_id in ids:
            _fail(f"{where}.id", "duplicate segment id")
        ids.add(segment_id)
        start = _int(segment["start_ms"], f"{where}.start_ms")
        end = _int(segment["end_ms"], f"{where}.end_ms")
        if end < start or end > limit:
            _fail(where, "segment time must satisfy start <= end <= duration")
        if start < previous_start:
            _fail(where, "segments must be ordered by start time")
        previous_start = start
        total_text += len(_text(segment["text"], f"{where}.text"))
        _conf(segment["conf"], f"{where}.conf")
        if not isinstance(segment["tokens"], list):
            _fail(f"{where}.tokens", "must be a list")
        for t_index, token in enumerate(segment["tokens"]):
            token = _keys(token, f"{where}.tokens[{t_index}]", {"text", "start_ms", "conf"})
            _text(token["text"], f"{where}.tokens[{t_index}].text", empty=False)
            token_start = _int(token["start_ms"], f"{where}.tokens[{t_index}].start_ms", nullable=True)
            if token_start is not None and token_start > limit:
                _fail(f"{where}.tokens[{t_index}].start_ms", "token time lies outside the audio")
            _conf(token["conf"], f"{where}.tokens[{t_index}].conf")
    if total_text > MAX_TEXT:
        _fail("segments", "recognition output is too large")


def validate(payload: Any) -> dict:
    """Return the payload unchanged when it follows the contract, else raise."""
    data = _keys(payload, "$", _TOP_KEYS)
    if data["schema"] != SCHEMA:
        _fail("schema", f"must be {SCHEMA}")
    if data["kind"] not in KINDS:
        _fail("kind", "must be ocr or asr")
    _engine(data["engine"])
    source = _keys(data["input"], "input", {"id", "sha256", "width", "height", "duration_ms"})
    _text(source["id"], "input.id", empty=False)
    if not isinstance(source["sha256"], str) or not _SHA256.fullmatch(source["sha256"]):
        _fail("input.sha256", "must be 64 lowercase hex characters")
    platform = _keys(data["platform"], "platform", {"machine", "python", "threads"})
    _text(platform["machine"], "platform.machine", empty=False)
    _text(platform["python"], "platform.python", empty=False)
    _int(platform["threads"], "platform.threads", minimum=1)
    timing = _keys(data["timing_ms"], "timing_ms", {"load", "infer"})
    _number(timing["load"], "timing_ms.load", minimum=0)
    _number(timing["infer"], "timing_ms.infer", minimum=0)
    if not isinstance(data["warnings"], list) or not all(isinstance(item, str) for item in data["warnings"]):
        _fail("warnings", "must be a list of strings")

    if data["kind"] == "ocr":
        width = _int(source["width"], "input.width", minimum=1)
        height = _int(source["height"], "input.height", minimum=1)
        if source["duration_ms"] is not None:
            _fail("input.duration_ms", "must be null for OCR")
        if data["segments"] != []:
            _fail("segments", "must be empty for OCR")
        _pages(data["pages"], width, height)
    else:
        duration = _int(source["duration_ms"], "input.duration_ms", minimum=1)
        if source["width"] is not None or source["height"] is not None:
            _fail("input", "width and height must be null for ASR")
        if data["pages"] != []:
            _fail("pages", "must be empty for ASR")
        _segments(data["segments"], duration)
    return data
