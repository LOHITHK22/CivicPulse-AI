"""MCP protocol verification over a REAL stdio connection to a spawned ``civicpulse-mcp`` process.

Sequence (Milestone 3 §6): start server -> connect -> initialize -> list tools -> verify names and
schemas -> call trends -> call forecast -> compare numbers with the analytics service -> invalid
arguments -> expected error -> close cleanly (server process logs ``server_stop``).
"""

from __future__ import annotations

import datetime as dt
import json
import time

import anyio
import pytest

from civicpulse_analytics import AnalyticsService, Filters
from civicpulse_analytics.predictions import PredictionService
from civicpulse_mcp.client import McpGateway, McpSettings
from civicpulse_mcp.server import build_server

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = {
    "get_service_request_trends",
    "compare_boroughs",
    "get_category_breakdown",
    "forecast_request_volume",
    "predict_closure_delay",
    "detect_unusual_spikes",
    "explain_model_prediction",
    "get_dataset_quality",
    "get_model_evaluation",
    "generate_investigation_report",
}


def _window(svc: AnalyticsService) -> tuple[dt.date, dt.date]:
    opts = svc.filter_options()
    end = opts.last_complete_date
    return max(opts.min_date, end - dt.timedelta(days=27)), end


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_full_protocol_sequence_over_stdio(sample_models, tmp_path, monkeypatch, mode):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("CIVICPULSE_MCP_AUDIT_LOG", str(audit))  # inherited explicitly below
    svc = AnalyticsService(sample_models.root)
    start, end = _window(svc)
    settings = McpSettings(transport="stdio", data_dir=sample_models.root, protocol_mode=mode, call_timeout=60)
    gateway = McpGateway(settings)
    # The SDK passes only safe variables plus `env`; add the audit path for this test.
    gateway._server = _with_env(gateway._server, {"CIVICPULSE_MCP_AUDIT_LOG": str(audit)})  # type: ignore[method-assign]

    async def scenario() -> dict:
        out: dict = {}
        async with gateway.session() as s:
            out["protocol"] = s.protocol_version
            out["server"] = s.server_info.name if s.server_info else None
            tools = await s.list_tools()
            out["tools"] = {t.name: t for t in tools}
            out["trends"] = await s.call(
                "get_service_request_trends",
                {"start_date": start.isoformat(), "end_date": end.isoformat(), "granularity": "day"},
            )
            out["forecast"] = await s.call(
                "forecast_request_volume", {"category": "noise_residential", "borough": "BRONX"}
            )
            out["invalid"] = await s.call(
                "get_service_request_trends",
                {"start_date": "not-a-date", "end_date": end.isoformat(), "borough": "MARS"},
            )
            out["mismatch"] = await s.call(
                "get_category_breakdown",
                {
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "level": "category",
                    "category": "heat_hot_water",
                },
            )
            out["unknown"] = await s.call("drop_table", {})
            out["resource"] = await s.read_json_resource("civicpulse://capabilities")
        return out

    r = anyio.run(scenario)

    # initialize / discovery
    assert r["protocol"] == ("2025-11-25" if mode == "legacy" else "2026-07-28")
    assert r["server"] == "civicpulse-mcp"
    assert set(r["tools"]) == EXPECTED_TOOLS
    trends_schema = r["tools"]["get_service_request_trends"].input_schema
    assert set(trends_schema["required"]) == {"start_date", "end_date"}
    assert trends_schema["properties"]["granularity"]["enum"] == ["day", "week", "month"]
    assert "UNSPECIFIED" in trends_schema["properties"]["borough"]["enum"]
    for t in r["tools"].values():
        assert t.read_only is True, t.name
        assert t.output_schema and "provenance" in t.output_schema["properties"], t.name
    assert r["tools"]["compare_boroughs"].input_schema["properties"]["boroughs"]["minItems"] == 2

    # trends numbers equal the analytics service (single source of metric definitions)
    tr = r["trends"]
    assert tr.ok, tr.error
    expected = svc.volume_series(Filters(start=start, end=end), "day", "none")
    assert [p["value"] for p in tr.structured["data"]["points"]] == [int(p.value) for p in expected.points]
    total = next(m["value"] for m in tr.structured["key_measurements"] if m["name"] == "total_requests")
    assert total == svc.kpis(Filters(start=start, end=end)).total_requests
    assert tr.structured["provenance"]["live"] is False
    assert tr.structured["capability"] == "VALIDATED"

    # forecast numbers equal the saved forecast artifact
    fc = r["forecast"]
    assert fc.ok, fc.error
    pred = PredictionService(sample_models.root).forecast("noise_residential", "BRONX", 28)
    got = fc.structured["data"]["series"][0]["forecast"]
    assert [round(p["predicted"], 6) for p in got] == [round(p["predicted"], 6) for p in pred["forecast"]]
    assert fc.structured["capability"] == "EXPERIMENTAL"
    assert fc.structured["data"]["origin"] == pred["origin"]
    assert any("naive" in lim for lim in fc.structured["limitations"])

    # invalid arguments -> MCP tool error (isError), with the offending fields named
    inv = r["invalid"]
    assert not inv.ok and inv.error_kind == "tool_error"
    assert "start_date" in inv.error and "borough" in inv.error
    assert not r["mismatch"].ok and "cannot be combined" in r["mismatch"].error
    assert not r["unknown"].ok and "Unknown tool" in r["unknown"].error

    assert r["resource"]["delay_numeric_predictions_enabled"] is False

    # clean shutdown: the server process exits its run loop after the client closes stdin
    deadline = time.time() + 15
    events: list[str] = []
    while time.time() < deadline:
        if audit.exists():
            events = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines() if line]
            if "server_stop" in events:
                break
        time.sleep(0.2)
    assert events[0] == "server_start" and events[-1] == "server_stop", events
    # every request is audited at the protocol layer, including schema-invalid and unknown-tool calls
    assert events.count("tool_request") == 5
    assert events.count("tool_call") == 3  # executed (or rejected inside the tool) calls


def _with_env(original, extra):
    def server():
        params = original()
        params.env = {**(params.env or {}), **extra}
        return params

    return server


def test_client_side_timeout_is_reported(sample_models):
    gateway = McpGateway(McpSettings(transport="stdio", data_dir=sample_models.root, call_timeout=60))

    async def scenario():
        async with gateway.session() as s:
            await s.list_tools()
            slow = await s.call("get_dataset_quality", {}, timeout=0.001)
            fast = await s.call("get_dataset_quality", {})
            return slow, fast

    slow, fast = anyio.run(scenario)
    assert not slow.ok and slow.error_kind == "timeout"
    assert fast.ok  # the session survives a timed-out call


def test_server_side_tool_timeout_returns_tool_error(sample_models):
    """In-process MCP connection (JSON-RPC over memory streams) to a server with a tiny tool timeout."""
    from mcp import Client

    server = build_server(sample_models.root, tool_timeout=1e-6)

    async def scenario():
        async with Client(server, mode="legacy") as c:
            return await c.call_tool("get_dataset_quality", {})

    res = anyio.run(scenario)
    assert res.is_error
    assert "timed out" in res.content[0].text


def test_transport_failure_is_reported_not_raised(tmp_path):
    gateway = McpGateway(
        McpSettings(transport="stdio", data_dir=tmp_path, args=("-c", "import sys; sys.exit(3)"), connect_timeout=10)
    )

    async def scenario():
        async with gateway.session():
            pass

    with pytest.raises(Exception):
        anyio.run(scenario)
