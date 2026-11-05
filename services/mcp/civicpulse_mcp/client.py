"""MCP client gateway used by the AI orchestrator.

Wraps the official ``mcp.Client``: one MCP session per investigation (connect, initialize, discover,
call, close). Transport is stdio (spawns ``python -m civicpulse_mcp``; the child inherits only the
SDK's safe environment plus the data directory, so no API key reaches it) or Streamable HTTP with a
bearer token. Every call is time-limited and returns a ``ToolCallOutcome`` instead of raising, so a
failed tool is reported, never hidden.
"""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anyio
from mcp import Client, StdioServerParameters
from mcp.types import Implementation


@dataclass(frozen=True)
class McpSettings:
    transport: Literal["stdio", "http"] = "stdio"
    data_dir: Path | None = None
    url: str | None = None
    http_token: str | None = None
    protocol_mode: Literal["legacy", "auto"] = "legacy"
    connect_timeout: float = 30.0
    call_timeout: float = 25.0
    command: str = sys.executable
    args: tuple[str, ...] = ("-m", "civicpulse_mcp")


@dataclass
class ToolCallOutcome:
    tool: str
    arguments: dict[str, Any]
    ok: bool
    structured: dict[str, Any] | None
    error: str | None
    error_kind: Literal["tool_error", "timeout", "transport", "none"]
    duration_ms: float


@dataclass
class ToolInfo:
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    read_only: bool | None


@dataclass
class McpSession:
    client: Client
    settings: McpSettings
    tools: list[ToolInfo] = field(default_factory=list)

    @property
    def protocol_version(self) -> str:
        return self.client.protocol_version

    @property
    def server_info(self) -> Implementation | None:
        return self.client.server_info

    async def list_tools(self) -> list[ToolInfo]:
        res = await self.client.list_tools()
        self.tools = [
            ToolInfo(
                name=t.name,
                title=t.title,
                description=t.description or "",
                input_schema=dict(t.input_schema),
                output_schema=dict(t.output_schema) if t.output_schema else None,
                read_only=t.annotations.read_only_hint if t.annotations else None,
            )
            for t in res.tools
        ]
        return self.tools

    async def read_json_resource(self, uri: str) -> Any:
        import json

        res = await self.client.read_resource(uri)
        for c in res.contents:
            text = getattr(c, "text", None)
            if text:
                return json.loads(text)
        return None

    async def call(self, tool: str, arguments: dict[str, Any], timeout: float | None = None) -> ToolCallOutcome:
        t0 = time.perf_counter()
        limit = timeout or self.settings.call_timeout

        def outcome(ok: bool, structured: Any, error: str | None, kind: Any) -> ToolCallOutcome:
            return ToolCallOutcome(
                tool=tool,
                arguments=arguments,
                ok=ok,
                structured=structured,
                error=error,
                error_kind=kind,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            )

        try:
            with anyio.fail_after(limit):
                res = await self.client.call_tool(tool, arguments, read_timeout_seconds=limit)
        except TimeoutError:
            return outcome(False, None, f"MCP call to {tool} timed out after {limit:g}s", "timeout")
        except Exception as exc:  # protocol / transport failure: report, never hide
            return outcome(False, None, f"MCP transport error: {type(exc).__name__}: {str(exc)[:200]}", "transport")
        if res.is_error:
            text = " ".join(getattr(c, "text", "") for c in res.content)[:600]
            return outcome(False, None, text or "tool returned an error", "tool_error")
        if not isinstance(res.structured_content, dict):
            return outcome(False, None, "tool returned no structured content", "tool_error")
        return outcome(True, res.structured_content, None, "none")


class McpGateway:
    def __init__(self, settings: McpSettings) -> None:
        self.settings = settings

    def _server(self) -> Any:
        s = self.settings
        if s.transport == "stdio":
            env = {"PYTHONIOENCODING": "utf-8"}
            if s.data_dir is not None:
                env["CIVICPULSE_DATA_DIR"] = str(s.data_dir)
            return StdioServerParameters(command=s.command, args=list(s.args), env=env)
        if not s.url:
            raise ValueError("http transport requires an MCP url")
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {s.http_token}"} if s.http_token else {}
        http = httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(s.call_timeout, connect=s.connect_timeout))
        return streamable_http_client(s.url, http_client=http)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[McpSession]:
        # The initialize/discover handshake is bounded by read_timeout_seconds. The context manager is not
        # wrapped in an outer cancel scope because the SDK opens task groups that must close in LIFO order.
        client = Client(
            self._server(),
            mode=self.settings.protocol_mode,
            read_timeout_seconds=self.settings.connect_timeout,
            client_info=Implementation(name="civicpulse-analyst", version="0.3.0"),
        )
        async with client:
            yield McpSession(client=client, settings=self.settings)
