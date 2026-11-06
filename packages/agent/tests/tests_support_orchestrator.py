"""Shared helpers for tests that run real investigations over the in-memory MCP server."""

from __future__ import annotations

import datetime as dt
from typing import Any

import anyio

from civicpulse_agent.budget import Prices, UsageGovernor
from civicpulse_agent.config import AgentSettings
from civicpulse_agent.orchestrator import InvestigationResult, Orchestrator
from civicpulse_agent.providers import FakeProvider
from civicpulse_analytics import AnalyticsService
from civicpulse_mcp.client import McpGateway, McpSettings
from civicpulse_mcp.server import build_server


class InMemoryGateway(McpGateway):
    def __init__(self, data_dir: Any) -> None:
        super().__init__(McpSettings(transport="stdio", data_dir=data_dir, call_timeout=60))
        self._mcp = build_server(data_dir, tool_timeout=60)

    def _server(self) -> Any:
        return self._mcp


def make_orch(gateway: McpGateway, sample_models: Any, fault: str = "none", **settings: Any) -> Orchestrator:
    last_day = AnalyticsService(sample_models.root).filter_options().last_complete_date
    s = AgentSettings(_env_file=None, data_dir=None, **settings)  # type: ignore[call-arg]
    gov = UsageGovernor(1000, 10_000, 0, Prices(0, 0))
    return Orchestrator(s, FakeProvider(fault=fault), gov, gateway=gateway, today=last_day + dt.timedelta(days=4))


def ask(orch: Orchestrator, q: str, history: list[str] | None = None) -> InvestigationResult:
    async def go() -> InvestigationResult:
        return await orch.investigate(q, "test-client", history)

    return anyio.run(go)
