"""Convert evaluation results to JSON without changing engine-owned values."""

from collections.abc import Mapping
import math
from typing import Any

import numpy as np
import pandas as pd


def _json_value(value: Any) -> Any:
    """Build new JSON containers, preserving strings and finite scalar values."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.DataFrame):
        return _json_value(value.to_dict(orient="records"))
    if isinstance(value, np.ndarray):
        return _json_value(value.tolist())
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return _json_value(float(value))
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON result object keys must be strings")
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"Unsupported result value type: {type(value).__name__}")


def serialize_evaluation_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the existing eight fields; absent fields remain an error."""
    return {
        field: _json_value(result[field])
        for field in (
            "summary", "summary_df", "gate_passed", "gate_message",
            "chart_path", "html_path", "model_dfs", "total_elapsed_seconds",
        )
    }
