"""Structured evidence: one record per successful MCP tool call, cited by ID in answers."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

_NUM_IN_TEXT = re.compile(r"(?<![A-Za-z_])-?\d[\d,]*(?:\.\d+)?")
_DATE_IN_TEXT = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+)?\b")


class Evidence(BaseModel):
    evidence_id: str = Field(pattern=r"^E\d{1,2}$")
    tool: str
    arguments: dict[str, Any]
    status: str
    capability: str
    dataset_id: str | None
    data_build_id: str | None
    artifact: str | None
    data_cutoff: str | None
    last_complete_date: str | None
    metric_definitions: dict[str, str]
    summary: str
    key_measurements: list[dict[str, Any]]
    result: dict[str, Any] = Field(description="Full structured MCP result (bounded by the server)")
    model_version: str | None
    model_run_id: str | None
    query: dict[str, Any]
    limitations: list[str]
    uncertainty: list[str]
    duration_ms: float

    @classmethod
    def from_tool_result(
        cls, evidence_id: str, structured: dict[str, Any], arguments: dict[str, Any], duration_ms: float
    ) -> Evidence:
        p = structured.get("provenance") or {}
        return cls(
            evidence_id=evidence_id,
            tool=structured.get("tool", ""),
            arguments=arguments,
            status=structured.get("status", ""),
            capability=str(structured.get("capability", "")),
            dataset_id=p.get("dataset_id"),
            data_build_id=p.get("data_build_id"),
            artifact=p.get("artifact"),
            data_cutoff=p.get("data_cutoff"),
            last_complete_date=p.get("last_complete_date"),
            metric_definitions=structured.get("metric_definitions") or {},
            summary=structured.get("summary", ""),
            key_measurements=structured.get("key_measurements") or [],
            result=structured,
            model_version=p.get("model_version"),
            model_run_id=p.get("model_run_id"),
            query=structured.get("query") or {},
            limitations=structured.get("limitations") or [],
            uncertainty=structured.get("uncertainty") or [],
            duration_ms=duration_ms,
        )

    def public(self) -> dict[str, Any]:
        """Evidence as returned to the browser: everything except the bulky raw result arrays."""
        d = self.model_dump()
        res = dict(d.pop("result"))
        d["data"] = res.get("data", {})
        d["coverage"] = res.get("coverage")
        return d


def numbers_in(value: Any, out: set[float] | None = None) -> set[float]:
    """All numeric values in a structured result, including numbers written inside strings (dates excluded)."""
    out = set() if out is None else out
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, int | float):
        out.add(float(value))
    elif isinstance(value, str):
        stripped = _DATE_IN_TEXT.sub(" ", value)
        for tok in _NUM_IN_TEXT.findall(stripped):
            try:
                out.add(float(tok.replace(",", "")))
            except ValueError:
                continue
    elif isinstance(value, dict):
        for v in value.values():
            numbers_in(v, out)
    elif isinstance(value, list | tuple):
        for v in value:
            numbers_in(v, out)
    return out
