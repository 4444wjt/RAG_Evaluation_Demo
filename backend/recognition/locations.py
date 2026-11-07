"""Attach native OCR/ASR locations to copied Trace statements; no new verdicts."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from backend.recognition.textmap import build_text, locate


def locate_statements(statements: list[dict], sidecar: dict) -> list[dict]:
    """Copy statements and add locations for each unchanged evidence value."""
    text, spans = build_text(sidecar["recognition"])
    attached = deepcopy(statements)
    for statement in attached:
        evidence = statement.get("evidence")
        statement["source_locations"] = (
            locate(text, spans, evidence) if isinstance(evidence, str)
            else {"status": "invalid_quote", "matches": []}
        )
    return attached


def locate_result(result: dict[str, Any], sidecar: dict) -> dict[str, list[dict]]:
    """Keep every model/row, distinguishing missing, invalid and empty Trace."""
    models = {}
    for model, rows in result["model_dfs"].items():
        located = []
        for index, row in enumerate(rows):
            trace = row.get("hallucination_trace")
            item = {"row_index": index, "trace_status": "missing", "statements": None}
            if trace is not None and trace != "":
                if isinstance(trace, str):
                    try:
                        trace = json.loads(trace)
                    except ValueError:
                        trace = None
                statements = trace.get("statements") if isinstance(trace, dict) else None
                if isinstance(statements, list) and all(isinstance(value, dict) for value in statements):
                    item.update(trace_status="available", statements=locate_statements(statements, sidecar))
                else:
                    item["trace_status"] = "invalid"
            located.append(item)
        models[model] = located
    return models
