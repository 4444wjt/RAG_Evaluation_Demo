"""API data contracts, without importing configuration or the engine."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class EvaluateRequest(BaseModel):
    """Public evaluation parameters; path/model semantics belong to service."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    doc_path: str | None = None
    testset_path: str | None = None
    benchmark: Literal["truthful_qa"] | None = None
    benchmark_limit: int = Field(default=20, gt=0, le=50)
    model_names: list[str] | None = Field(default=None, min_length=1)
    judge_model_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
    faith_threshold: float | None = Field(default=None, ge=0, le=1)
    ctx_prec_threshold: float | None = Field(default=None, ge=0, le=1)
    version_label: str | None = None


class EvaluateResponse(BaseModel):
    """The engine's eight result fields, represented as JSON data."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    summary: list[dict[str, JsonValue]]
    summary_df: list[dict[str, JsonValue]]
    gate_passed: bool
    gate_message: str
    chart_path: str | None
    html_path: str | None
    model_dfs: dict[str, list[dict[str, JsonValue]]]
    total_elapsed_seconds: float | None
