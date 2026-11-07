"""Turn a validated recognition result into evaluation text plus a position map.

The text is what the existing pipeline loads; the spans tie every character
offset back to an OCR page/line/box or an ASR segment time range. Evidence that
the trace quotes (always a contiguous substring of a context chunk, which is a
substring of this text) can then be located in the original file.
"""
from __future__ import annotations

from typing import Any

PAGE_BREAK = "\n\n"
LINE_BREAK = "\n"


def build_text(result: dict) -> tuple[str, list[dict]]:
    """Return (text, spans). Spans are ordered, non-overlapping [start, end)."""
    parts: list[str] = []
    spans: list[dict] = []
    offset = 0

    def add(text: str, span: dict, separator: str) -> None:
        nonlocal offset
        if parts:
            parts.append(separator)
            offset += len(separator)
        parts.append(text)
        spans.append({"start": offset, "end": offset + len(text), **span})
        offset += len(text)

    if result["kind"] == "ocr":
        for page in sorted(result["pages"], key=lambda item: item["page"]):
            first = True
            for line in page["lines"]:
                if not line["text"]:
                    continue
                chars = line["chars"] if "".join(c["text"] for c in line["chars"]) == line["text"] else []
                add(line["text"], {
                    "page": page["page"], "line_id": line["id"], "box": line["box"],
                    "conf": line["conf"],
                    # Per-character confidence only when the engine's characters
                    # spell the line exactly; otherwise offsets would drift.
                    "char_conf": [char["conf"] for char in chars for _ in char["text"]] or None,
                }, PAGE_BREAK if first else LINE_BREAK)
                first = False
    else:
        for segment in result["segments"]:
            if not segment["text"]:
                continue
            add(segment["text"], {
                "segment_id": segment["id"], "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"], "conf": segment["conf"], "char_conf": None,
            }, LINE_BREAK)
    return "".join(parts), spans


def _occurrences(text: str, quote: str) -> list[int]:
    found, start = [], text.find(quote)
    while start != -1:
        found.append(start)
        start = text.find(quote, start + 1)
    return found


def _min_conf(spans: list[dict], start: int, end: int) -> tuple[float | None, bool]:
    """Lowest native confidence over [start, end) and whether any part lacked one."""
    values: list[float] = []
    missing = False
    for span in spans:
        low, high = max(start, span["start"]), min(end, span["end"])
        if low >= high:
            continue
        chars = span.get("char_conf")
        if chars:
            for value in chars[low - span["start"]:high - span["start"]]:
                missing |= value is None
                if value is not None:
                    values.append(value)
        elif span["conf"] is None:
            missing = True
        else:
            values.append(span["conf"])
    return (min(values) if values else None), missing


def locate(text: str, spans: list[dict], quote: str) -> dict[str, Any]:
    """Locate a quote; every occurrence is reported so ambiguity stays visible."""
    if not quote:
        return {"status": "empty_quote", "matches": []}
    matches = []
    for start in _occurrences(text, quote):
        end = start + len(quote)
        refs = [{key: value for key, value in span.items() if key not in ("start", "end", "char_conf")}
                for span in spans if span["start"] < end and start < span["end"]]
        conf, missing = _min_conf(spans, start, end)
        matches.append({"start": start, "end": end, "refs": refs, "min_conf": conf, "conf_missing": missing})
    status = "not_found" if not matches else "ambiguous" if len(matches) > 1 else "located"
    return {"status": status, "matches": matches}
