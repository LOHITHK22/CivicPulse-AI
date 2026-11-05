"""Typed, bounded structured output shared by every CivicPulse MCP tool.

Each tool returns a ``ToolResult``. Its JSON Schema is published as the tool's ``outputSchema``
during discovery, and the MCP SDK validates every result against it.
"""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from civicpulse_mcp.capabilities import Capability

MAX_RESULT_BYTES = 96_000  # hard ceiling on one serialized tool result


class ToolOutputTooLargeError(RuntimeError):
    pass


class Measurement(BaseModel):
    """A headline number with its definition. Agents cite these; validators check claims against them."""

    name: str = Field(max_length=120)
    value: float | int | str | None
    unit: str = Field(default="", max_length=40)
    definition: str = Field(default="", max_length=400)


class Provenance(BaseModel):
    dataset_id: str
    dataset_name: str
    data_build_id: str | None
    snapshot_at_local: str | None = Field(description="When the warehouse snapshot was built (NYC local)")
    data_cutoff: str | None = Field(description="Newest request created_at in the warehouse (NYC local)")
    last_complete_date: str | None = Field(description="Latest calendar day fully covered by the snapshot")
    source_rows_updated_at: str | None = Field(description="Source dataset's last update time (UTC)")
    live: bool = Field(default=False, description="Always false: a versioned snapshot, not a live stream")
    model_run_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    artifact: str | None = Field(default=None, description="Saved artifact the numbers were read from")


class Coverage(BaseModel):
    requested_start: str | None
    requested_end: str | None
    available_start: str | None
    available_end: str | None = Field(description="Last complete day in the snapshot")
    effective_start: str | None
    effective_end: str | None
    partial: bool = Field(description="True if part of the requested period is outside the data")
    note: str | None = None


class QueryInfo(BaseModel):
    elapsed_ms: float
    rows: int
    truncated: bool = False
    row_limit: int | None = None
    calculation: str = Field(default="", max_length=600, description="How the numbers were computed")


class ToolResult(BaseModel):
    tool: str
    status: Literal["ok", "no_data", "restricted"]
    capability: Capability
    summary: str = Field(max_length=600, description="Deterministic one-line description of the result")
    key_measurements: list[Measurement] = Field(default_factory=list, max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)
    metric_definitions: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance
    coverage: Coverage | None = None
    uncertainty: list[str] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=16)
    query: QueryInfo


def clean_json(v: Any) -> Any:
    """JSON-safe copy: non-finite floats become None, dates become ISO strings."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {str(k): clean_json(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [clean_json(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, BaseModel):
        return clean_json(v.model_dump(mode="json"))
    return v


def enforce_size(result: ToolResult) -> ToolResult:
    size = len(json.dumps(result.model_dump(mode="json"), default=str))
    if size > MAX_RESULT_BYTES:
        raise ToolOutputTooLargeError(f"tool result is {size} bytes, above the {MAX_RESULT_BYTES}-byte limit")
    return result
