"""CivicPulse MCP server (official MCP Python SDK, ``mcp`` 2.x).

Exposes ten typed, read-only analytical tools over stdio (default) or token-protected Streamable HTTP.

    civicpulse-mcp                                   # stdio: launched as a subprocess by an MCP client
    CIVICPULSE_MCP_HTTP_TOKEN=... civicpulse-mcp --transport streamable-http --port 8765

There is no SQL, filesystem, shell or write tool. Every tool runs in a worker thread with a timeout,
returns a bounded ``ToolResult`` and emits an audit event.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hmac
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from civicpulse_analytics.service import DEFINITIONS, DataUnavailableError
from civicpulse_mcp import __version__
from civicpulse_mcp import capabilities as cap
from civicpulse_mcp.audit import audit_event
from civicpulse_mcp.backend import CivicPulseTools, ToolInputError, sanitize_text
from civicpulse_mcp.capabilities import Capability
from civicpulse_mcp.envelope import Measurement, QueryInfo, ToolOutputTooLargeError, ToolResult, enforce_size

logger = logging.getLogger("civicpulse_mcp")

DEFAULT_TOOL_TIMEOUT = 20.0
MAX_CONCURRENT_TOOLS = 4

Category = Literal["noise_residential", "heat_hot_water"]
CategoryOrAll = Literal["all", "noise_residential", "heat_hot_water"]
Borough = Literal["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]
BoroughOrAll = Literal["ALL", "BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "UNSPECIFIED"]
Geography = Literal["ALL_SERIES", "CITYWIDE", "BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]
ModelFamily = Literal["forecast", "delay_risk", "anomaly"]

StartDate = Annotated[dt.date, Field(description="Inclusive start date (YYYY-MM-DD), NYC local created date.")]
EndDate = Annotated[dt.date, Field(description="Inclusive end date (YYYY-MM-DD).")]

REPORTABLE_TOOLS = (
    "get_service_request_trends",
    "compare_boroughs",
    "get_category_breakdown",
    "forecast_request_volume",
    "predict_closure_delay",
    "detect_unusual_spikes",
    "explain_model_prediction",
    "get_dataset_quality",
    "get_model_evaluation",
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = (
    "CivicPulse exposes read-only analytics over a historical snapshot of NYC 311 service requests "
    "(Noise - Residential and HEAT/HOT WATER only). Every result carries provenance, a capability class "
    "(VALIDATED / EXPERIMENTAL / DISABLED / NOT_VERIFIED) and limitations that must be relayed. The data is "
    "not live. Anomaly flags are unverified statistical flags, never confirmed incidents. Closure-delay "
    "numerical predictions are disabled. No tool provides causal explanations."
)


class ReportStep(BaseModel):
    """One permitted tool call to (re-)execute for an investigation report."""

    model_config = ConfigDict(extra="forbid")
    tool: Literal[
        "get_service_request_trends",
        "compare_boroughs",
        "get_category_breakdown",
        "forecast_request_volume",
        "predict_closure_delay",
        "detect_unusual_spikes",
        "explain_model_prediction",
        "get_dataset_quality",
        "get_model_evaluation",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments for that tool (validated by it)")


class ProtocolAuditMiddleware:
    """Audits every ``tools/call`` request at the protocol layer, including calls rejected before a tool
    runs (unknown tool, schema-invalid arguments). Execution details are audited separately (``tool_call``)."""

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if getattr(ctx, "method", None) != "tools/call":
            return await call_next(ctx)
        t0 = time.perf_counter()
        params = ctx.params if isinstance(ctx.params, dict) else {}
        name = str(params.get("name", ""))[:80]
        outcome = "error"
        try:
            result = await call_next(ctx)
            is_error = getattr(result, "is_error", None)
            if is_error is None and isinstance(result, dict):
                is_error = result.get("isError")
            outcome = "is_error" if is_error else "ok"
            return result
        except Exception as exc:
            outcome = f"protocol_error:{type(exc).__name__}"
            raise
        finally:
            audit_event(
                "tool_request",
                tool=name,
                arguments=params.get("arguments") or {},
                outcome=outcome,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            )


def build_server(data_dir: Path, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT) -> MCPServer:
    tools = CivicPulseTools(data_dir)
    mcp = MCPServer(
        "civicpulse-mcp",
        title="CivicPulse AI analytics",
        description="Read-only NYC 311 analytics, forecasts and model evaluations with provenance.",
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level="WARNING",
        middleware=[ProtocolAuditMiddleware()],
    )
    limiter = anyio.CapacityLimiter(MAX_CONCURRENT_TOOLS)

    async def run(name: str, args: dict[str, Any], fn: Callable[[], ToolResult]) -> ToolResult:
        t0 = time.perf_counter()
        status, error = "ok", None
        try:
            with anyio.fail_after(tool_timeout):
                result = await anyio.to_thread.run_sync(fn, limiter=limiter, abandon_on_cancel=True)
            status = result.status
            return result
        except TimeoutError:
            status, error = "timeout", f"{name} timed out after {tool_timeout:g}s"
            raise ToolError(error) from None
        except ToolInputError as exc:
            status, error = "invalid_arguments", str(exc)
            raise ToolError(f"invalid arguments: {exc}") from None
        except DataUnavailableError as exc:
            status, error = "data_unavailable", str(exc)
            raise ToolError(f"data unavailable: {exc}") from None
        except ToolOutputTooLargeError as exc:
            status, error = "output_too_large", str(exc)
            raise ToolError("result exceeded the response size limit; narrow the request") from None
        except ValueError as exc:  # validation raised by the shared services (allow-lists, ranges)
            status, error = "invalid_arguments", str(exc)
            raise ToolError(f"invalid arguments: {exc}") from None
        except Exception as exc:  # never leak internals to the client
            status, error = "internal_error", type(exc).__name__
            logger.exception("tool %s failed", name)
            raise ToolError(f"{name} failed with an internal error") from None
        finally:
            audit_event(
                "tool_call",
                tool=name,
                arguments=args,
                status=status,
                error=error,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            )

    # ---------------------------------------------------------------- 1
    @mcp.tool(
        title="Service request trends",
        description="Time series and totals of 311 requests created per day/week/month for a date range, "
        "category and borough, with the prior equal-length period for comparison. Daily granularity is limited "
        "to 400 days.",
        annotations=READ_ONLY,
    )
    async def get_service_request_trends(
        start_date: StartDate,
        end_date: EndDate,
        category: Annotated[CategoryOrAll, Field(description="Complaint category, or 'all'")] = "all",
        borough: Annotated[BoroughOrAll, Field(description="Borough, or 'ALL'")] = "ALL",
        granularity: Annotated[Literal["day", "week", "month"], Field(description="Bucket size")] = "day",
    ) -> ToolResult:
        args = {
            "start_date": start_date,
            "end_date": end_date,
            "category": category,
            "borough": borough,
            "granularity": granularity,
        }
        return await run(
            "get_service_request_trends",
            args,
            lambda: tools.get_service_request_trends(start_date, end_date, category, borough, granularity),
        )

    # ---------------------------------------------------------------- 2
    @mcp.tool(
        title="Compare boroughs",
        description="Request counts, share of the selected boroughs, pairwise differences, median hours to close "
        "and share open beyond 7 days for 2-5 boroughs over the same window and category. Raw counts, not per capita.",
        annotations=READ_ONLY,
    )
    async def compare_boroughs(
        start_date: StartDate,
        end_date: EndDate,
        boroughs: Annotated[list[Borough], Field(min_length=2, max_length=5, description="2-5 distinct boroughs")],
        category: Annotated[CategoryOrAll, Field(description="Complaint category, or 'all'")] = "all",
    ) -> ToolResult:
        args = {"start_date": start_date, "end_date": end_date, "boroughs": boroughs, "category": category}
        return await run(
            "compare_boroughs", args, lambda: tools.compare_boroughs(start_date, end_date, list(boroughs), category)
        )

    # ---------------------------------------------------------------- 3
    @mcp.tool(
        title="Category breakdown",
        description="Counts and shares by complaint category (level='category', no category filter allowed) or by "
        "descriptor within one category (level='descriptor', category required).",
        annotations=READ_ONLY,
    )
    async def get_category_breakdown(
        start_date: StartDate,
        end_date: EndDate,
        borough: Annotated[BoroughOrAll, Field(description="Borough, or 'ALL'")] = "ALL",
        level: Annotated[Literal["category", "descriptor"], Field(description="Grouping level")] = "category",
        category: Annotated[Category | None, Field(description="Required for level='descriptor' only")] = None,
    ) -> ToolResult:
        args = {
            "start_date": start_date,
            "end_date": end_date,
            "borough": borough,
            "level": level,
            "category": category,
        }
        return await run(
            "get_category_breakdown",
            args,
            lambda: tools.get_category_breakdown(start_date, end_date, borough, level, category),
        )

    # ---------------------------------------------------------------- 4
    @mcp.tool(
        title="Forecast request volume",
        description="Precomputed 7-day daily forecast from the selected model (made at the dataset cutoff), with "
        "nominal 90% intervals, model version, training cutoff, test evaluation, baseline comparisons with "
        "significance, and known limitations. borough='ALL' returns all five series plus a summed point forecast "
        "without interval.",
        annotations=READ_ONLY,
    )
    async def forecast_request_volume(
        category: Annotated[Category, Field(description="Complaint category")],
        borough: Annotated[
            Literal["ALL", "BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"],
            Field(description="Borough, or 'ALL' for all five"),
        ] = "ALL",
    ) -> ToolResult:
        args = {"category": category, "borough": borough}
        return await run("forecast_request_volume", args, lambda: tools.forecast_request_volume(category, borough))

    # ---------------------------------------------------------------- 5
    @mcp.tool(
        title="Closure-delay model (restricted)",
        description="RESTRICTED. Returns the closure-delay research methodology, measured evaluation and "
        "limitations. Numerical predictions, rankings and per-request explanations are disabled by the AI "
        "readiness audit; requesting them returns a restricted result explaining why.",
        annotations=READ_ONLY,
    )
    async def predict_closure_delay(
        request: Annotated[
            Literal["research_summary", "predict_request", "rank_requests"],
            Field(description="research_summary is the only request that returns substantive content"),
        ] = "research_summary",
        unique_key: Annotated[str | None, Field(pattern=r"^[0-9]{1,12}$", description="311 request id")] = None,
    ) -> ToolResult:
        args = {"request": request, "unique_key": unique_key}
        return await run("predict_closure_delay", args, lambda: tools.predict_closure_delay(request, unique_key))

    # ---------------------------------------------------------------- 6
    @mcp.tool(
        title="Detect unusual spikes",
        description="Statistical anomaly flags (robust z-score vs the same weekday over the previous 8 weeks) with "
        "actual and expected counts, scores, thresholds and the detector's synthetic evaluation. Flags are "
        "unverified and carry no cause. Ordered by |score|.",
        annotations=READ_ONLY,
    )
    async def detect_unusual_spikes(
        start_date: StartDate,
        end_date: EndDate,
        category: Annotated[CategoryOrAll, Field(description="Complaint category, or 'all'")] = "all",
        geography: Annotated[Geography, Field(description="CITYWIDE, a borough, or ALL_SERIES")] = "ALL_SERIES",
        direction: Annotated[Literal["any", "spike", "drop"], Field(description="Flag direction")] = "any",
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum flags returned")] = 20,
    ) -> ToolResult:
        args = {
            "start_date": start_date,
            "end_date": end_date,
            "category": category,
            "geography": geography,
            "direction": direction,
            "limit": limit,
        }
        return await run(
            "detect_unusual_spikes",
            args,
            lambda: tools.detect_unusual_spikes(start_date, end_date, category, geography, direction, limit),
        )

    # ---------------------------------------------------------------- 7
    @mcp.tool(
        title="Explain model output",
        description="Deterministic explanations: global feature importance for the forecast or delay-risk model, "
        "or the expected/residual/scale decomposition of one anomaly score (requires category, geography, date). "
        "Attributions are associations, not causes.",
        annotations=READ_ONLY,
    )
    async def explain_model_prediction(
        model: Annotated[ModelFamily, Field(description="Model family")],
        category: Annotated[Category | None, Field(description="For anomaly explanations")] = None,
        geography: Annotated[
            Literal["CITYWIDE", "BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"] | None,
            Field(description="For anomaly explanations"),
        ] = None,
        date: Annotated[dt.date | None, Field(description="For anomaly explanations")] = None,
        top_k: Annotated[int, Field(ge=1, le=15)] = 8,
    ) -> ToolResult:
        args = {"model": model, "category": category, "geography": geography, "date": date, "top_k": top_k}
        return await run(
            "explain_model_prediction",
            args,
            lambda: tools.explain_model_prediction(model, category, geography, date, top_k),
        )

    # ---------------------------------------------------------------- 8
    @mcp.tool(
        title="Dataset quality and freshness",
        description="Latest ingestion build, dataset cutoff, record and duplicate counts, quality-rule warnings, "
        "freshness and the source manifest reference.",
        annotations=READ_ONLY,
    )
    async def get_dataset_quality() -> ToolResult:
        return await run("get_dataset_quality", {}, tools.get_dataset_quality)

    # ---------------------------------------------------------------- 9
    @mcp.tool(
        title="Model evaluation",
        description="Exact saved evaluation metrics for one model family (forecast, delay_risk, anomaly), read "
        "from the run artifacts.",
        annotations=READ_ONLY,
    )
    async def get_model_evaluation(model: Annotated[ModelFamily, Field(description="Model family")]) -> ToolResult:
        return await run("get_model_evaluation", {"model": model}, lambda: tools.get_model_evaluation(model))

    # ---------------------------------------------------------------- 10
    @mcp.tool(
        title="Investigation report",
        description="Re-executes 1-6 permitted tool calls server-side and assembles a structured, evidence-backed "
        "report (question, filters, evidence, dataset cutoff, key measurements, model provenance, findings, "
        "uncertainty, limitations). Evidence is never accepted from the caller. Writes nothing.",
        annotations=READ_ONLY,
    )
    async def generate_investigation_report(
        question: Annotated[str, Field(min_length=3, max_length=500, description="The question being investigated")],
        steps: Annotated[list[ReportStep], Field(min_length=1, max_length=6)],
    ) -> ToolResult:
        t0 = time.perf_counter()
        evidence: list[dict[str, Any]] = []
        measurements: list[Measurement] = []
        findings, uncertainty, limitations = [], [], []
        models: dict[str, dict[str, Any]] = {}
        cutoffs: set[str] = set()
        classes: dict[str, str] = {}
        with anyio.fail_after(tool_timeout * 3):
            for i, step in enumerate(steps, 1):
                rid = f"R{i}"
                res = await mcp.call_tool(step.tool, step.arguments)
                structured = getattr(res, "structured_content", None)
                if getattr(res, "is_error", False) or not structured:
                    text = " ".join(getattr(c, "text", "") for c in getattr(res, "content", []))[:300]
                    evidence.append(
                        {
                            "evidence_id": rid,
                            "tool": step.tool,
                            "arguments": step.arguments,
                            "status": "error",
                            "error": text,
                        }
                    )
                    findings.append(f"[{rid}] {step.tool} failed: {text}")
                    continue
                r = ToolResult.model_validate(structured)
                p = r.provenance
                evidence.append(
                    {
                        "evidence_id": rid,
                        "tool": r.tool,
                        "arguments": step.arguments,
                        "status": r.status,
                        "capability": r.capability,
                        "summary": r.summary,
                        "data_build_id": p.data_build_id,
                        "data_cutoff": p.data_cutoff,
                        "model_version": p.model_version,
                        "artifact": p.artifact,
                    }
                )
                classes[r.tool] = r.capability
                if p.data_cutoff:
                    cutoffs.add(p.data_cutoff)
                if p.model_version:
                    models[p.model_version] = {"model": p.model_name, "run_id": p.model_run_id, "artifact": p.artifact}
                findings.append(f"[{rid}] {r.summary}")
                for km in r.key_measurements[:8]:
                    if len(measurements) < 40:
                        measurements.append(
                            Measurement(name=f"{rid}.{km.name}", value=km.value, unit=km.unit, definition=km.definition)
                        )
                uncertainty += [u for u in r.uncertainty if u not in uncertainty]
                limitations += [lim for lim in r.limitations if lim not in limitations]
        ok = [e for e in evidence if e["status"] != "error"]
        result = ToolResult(
            tool="generate_investigation_report",
            status="ok" if ok else "no_data",
            capability=Capability.VALIDATED
            if all(c == Capability.VALIDATED for c in classes.values())
            else Capability.EXPERIMENTAL,
            summary=f"Investigation report from {len(ok)} of {len(steps)} tool calls.",
            key_measurements=measurements,
            data={
                "question": sanitize_text(question, 500),
                "filters": [
                    {"evidence_id": e["evidence_id"], "tool": e["tool"], "arguments": e["arguments"]} for e in evidence
                ],
                "evidence": evidence,
                "dataset_cutoff": sorted(cutoffs),
                "model_provenance": models,
                "findings": findings,
                "capability_classes": classes,
                "note": "Findings are the tools' deterministic summaries; no language model wrote this report.",
            },
            metric_definitions={"evidence_id": "R<n> refers to the n-th re-executed tool call in this report."},
            provenance=tools._provenance(),
            uncertainty=uncertainty[:12],
            limitations=limitations[:16],
            query=QueryInfo(
                elapsed_ms=round((time.perf_counter() - t0) * 1000, 2),
                rows=len(evidence),
                calculation="Re-executed the listed tools through this server's own validation path.",
            ),
        )
        audit_event(
            "tool_call",
            tool="generate_investigation_report",
            arguments={"steps": [s.tool for s in steps]},
            status=result.status,
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return enforce_size(result)

    # ---------------------------------------------------------------- resources (static metadata)
    @mcp.resource("civicpulse://capabilities", title="Capability classification", mime_type="application/json")
    def capabilities_resource() -> dict[str, Any]:
        return {
            "audit": cap.AUDIT_DOC,
            "tools": {k: str(v) for k, v in cap.TOOL_CAPABILITY.items()},
            "capabilities": cap.CAPABILITY_TABLE,
            "delay_numeric_predictions_enabled": cap.DELAY_NUMERIC_PREDICTIONS_ENABLED,
            "prohibited_delay_uses": list(cap.PROHIBITED_DELAY_USES),
        }

    @mcp.resource("civicpulse://metric-definitions", title="Metric definitions", mime_type="application/json")
    def definitions_resource() -> dict[str, str]:
        return dict(DEFINITIONS)

    @mcp.resource("civicpulse://dataset", title="Dataset summary", mime_type="application/json")
    def dataset_resource() -> dict[str, Any]:
        opts = tools.svc.filter_options()
        build = tools.svc.build_info() or {}
        try:
            reg = tools.pred.registry()
            model_versions = {name: str(m.get("version")) for name, m in (reg.get("models") or {}).items()}
            model_run_id = reg.get("run_id")
        except Exception:  # no saved model run: the dataset is still usable for analytics tools
            model_versions, model_run_id = {}, None
        return {
            "model_versions": model_versions,
            "model_run_id": model_run_id,
            "dataset_id": cap.DATASET_ID,
            "dataset_name": cap.DATASET_NAME,
            "build_id": build.get("build_id"),
            "records": build.get("accepted_rows"),
            "first_date": str(opts.min_date),
            "last_complete_date": str(opts.last_complete_date),
            "data_cutoff": build.get("max_created_at"),
            "categories": [c["key"] for c in opts.categories],
            "live": False,
            "manifest": cap.MANIFEST_DOC,
        }

    return mcp


# ------------------------------------------------------------------------- HTTP transport security
class BearerTokenMiddleware:
    """Pure-ASGI guard: every HTTP request must carry ``Authorization: Bearer <token>``."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            supplied = headers.get(b"authorization", b"")
            expected = b"Bearer " + self.token
            if not hmac.compare_digest(supplied, expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def _serve_http(mcp: MCPServer, host: str, port: int, token: str) -> None:
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    allowed = [f"{h}:{port}" for h in ("127.0.0.1", "localhost", host)]
    app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=allowed, allowed_origins=[]
        ),
        host=host,
        max_request_body_size=256 * 1024,
        max_sessions=32,
    )
    uvicorn.run(BearerTokenMiddleware(app, token), host=host, port=port, log_level="warning")


def main(argv: list[str] | None = None) -> int:
    from civicpulse_shared.config import get_settings

    ap = argparse.ArgumentParser(prog="civicpulse-mcp", description=__doc__.splitlines()[0])
    ap.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="permit binding a non-loopback host (private container networks only)",
    )
    ap.add_argument("--tool-timeout", type=float, default=float(os.environ.get("CIVICPULSE_MCP_TOOL_TIMEOUT", 20)))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    data_dir = Path(os.environ.get("CIVICPULSE_DATA_DIR") or get_settings().data_dir)
    mcp = build_server(data_dir, tool_timeout=max(1.0, min(a.tool_timeout, 120.0)))
    audit_event("server_start", transport=a.transport, version=__version__, data_dir_name=data_dir.name)
    if a.transport == "stdio":
        mcp.run("stdio")  # returns when the client closes stdin
        audit_event("server_stop", transport="stdio")
        return 0
    token = os.environ.get("CIVICPULSE_MCP_HTTP_TOKEN", "")
    if len(token) < 32:
        print("refusing to start HTTP transport: set CIVICPULSE_MCP_HTTP_TOKEN (>= 32 chars)", file=sys.stderr)
        return 2
    if a.host not in ("127.0.0.1", "localhost", "::1") and not a.allow_non_loopback:
        print("refusing to bind a non-loopback host without --allow-non-loopback", file=sys.stderr)
        return 2
    _serve_http(mcp, a.host, a.port, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
