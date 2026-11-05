"""Streamable HTTP transport: bearer token required, DNS-rebinding protection, real tool call."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time

import anyio
import httpx
import pytest

from civicpulse_mcp.client import McpGateway, McpSettings

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def http_server(sample_models):
    port = _free_port()
    token = secrets.token_urlsafe(32)
    env = {**os.environ, "CIVICPULSE_DATA_DIR": str(sample_models.root), "CIVICPULSE_MCP_HTTP_TOKEN": token}
    proc = subprocess.Popen(
        [sys.executable, "-m", "civicpulse_mcp", "--transport", "streamable-http", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.3)
    yield url, token
    proc.terminate()
    proc.wait(timeout=10)


def test_http_requires_token(http_server):
    url, _ = http_server
    r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, timeout=5)
    assert r.status_code == 401
    r = httpx.post(url, json={}, headers={"Authorization": "Bearer wrong"}, timeout=5)
    assert r.status_code == 401


def test_http_rejects_foreign_host_header(http_server):
    url, token = http_server
    r = httpx.post(url, json={}, headers={"Authorization": f"Bearer {token}", "Host": "evil.example:80"}, timeout=5)
    assert r.status_code in (400, 403, 421)


def test_http_tool_call_with_token(http_server):
    url, token = http_server
    gw = McpGateway(McpSettings(transport="http", url=url, http_token=token, call_timeout=30))

    async def scenario():
        async with gw.session() as s:
            tools = await s.list_tools()
            q = await s.call("get_dataset_quality", {})
            return [t.name for t in tools], q

    names, q = anyio.run(scenario)
    assert len(names) == 10
    assert q.ok and q.structured["provenance"]["live"] is False


def test_http_refuses_to_start_without_strong_token(sample_models):
    env = {**os.environ, "CIVICPULSE_DATA_DIR": str(sample_models.root), "CIVICPULSE_MCP_HTTP_TOKEN": "short"}
    r = subprocess.run(
        [sys.executable, "-m", "civicpulse_mcp", "--transport", "streamable-http", "--port", str(_free_port())],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 2 and "CIVICPULSE_MCP_HTTP_TOKEN" in r.stderr
    env["CIVICPULSE_MCP_HTTP_TOKEN"] = "x" * 40
    r = subprocess.run(
        [sys.executable, "-m", "civicpulse_mcp", "--transport", "streamable-http", "--host", "0.0.0.0"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 2 and "non-loopback" in r.stderr
