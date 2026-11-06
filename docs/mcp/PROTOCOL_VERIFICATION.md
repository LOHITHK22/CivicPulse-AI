# MCP protocol verification

Verified with the official SDK (`mcp` 2.3.0) on 2026-11-04, Windows 11, Python 3.12. All tests listed
here run offline in `pytest` against a warehouse and models built from the committed real-data sample.

## Required sequence (spec §6): `services/mcp/tests/test_protocol.py::test_full_protocol_sequence_over_stdio`

Runs twice: `mode="legacy"` (initialize handshake, negotiated protocol **2025-11-25**) and
`mode="auto"` (server discovery, protocol **2026-07-28**). Over a **real stdio subprocess**
(`python -m civicpulse_mcp`):

| Step | Assertion |
|---|---|
| Start server, connect, initialize | Negotiated protocol version as above; server name `civicpulse-mcp` |
| List tools | Exactly the 10 expected names; every tool `readOnlyHint: true`; every tool has an `outputSchema` with `provenance` |
| Verify schemas | trends: `start_date`, `end_date` required, `granularity` enum day/week/month, borough enum incl. UNSPECIFIED; compare: `boroughs.minItems == 2` |
| Call trends | Daily values equal `AnalyticsService.volume_series`; `total_requests` equals `AnalyticsService.kpis` |
| Call forecast | Predictions equal `PredictionService.forecast` (saved artifact); capability EXPERIMENTAL; naive-baseline caveat present |
| Invalid arguments | `isError` result naming `start_date` and `borough`; mismatched filters rejected; unknown tool → `Unknown tool` |
| Resource | `civicpulse://capabilities` reports `delay_numeric_predictions_enabled: false` |
| Close cleanly | Audit log ends with `server_stop` after the client closes; 5 `tool_request` events (all calls, including schema-rejected and unknown-tool calls) and 3 `tool_call` events |

## Failure handling

| Test | Verifies |
|---|---|
| `test_client_side_timeout_is_reported` | A call exceeding the client timeout returns `error_kind="timeout"` instead of raising |
| `test_server_side_tool_timeout_returns_tool_error` | A tool exceeding the server's tool timeout returns a tool error |
| `test_transport_failure_is_reported_not_raised` | A dead server process yields `error_kind="transport"` |

## HTTP transport (`test_http_transport.py`)

Requests without the bearer token are rejected (401); a foreign `Host` header is rejected
(DNS-rebinding protection); a tool call with the token succeeds; the server refuses to start without
a token of at least 32 characters.

## Consistency with the analytics API (`test_consistency.py`)

Trends, borough comparison, category and descriptor breakdowns, forecasts (single borough and the
interval-less all-borough sum), anomalies, quality and model evaluation are compared with the REST
API and, where applicable, with independent Polars computations.

## End to end through the orchestrator

`packages/agent/tests/test_orchestrator.py::test_real_stdio_transport` runs a full investigation
through a spawned stdio server. The agent evaluation (`scripts/agent_eval.py`, 60 scenarios) spawns
one stdio server per investigation against the **full** snapshot (build `20261104T194022Z-5da243`).

## Real-data smoke check

All ten tools were called on the full snapshot during development, e.g. Brooklyn residential
noise, October 2026: 11,861 requests; citywide heat on 2026-10-16: 2,432 actual vs 119.5 expected.

## Milestone 4 re-verification

* Docker Compose (2026-11-06): the in-container API spawned `civicpulse_mcp` over stdio, negotiated protocol
  2025-11-25 and answered an investigation with `compare_boroughs` (11 validated claims); the report built from it
  re-verified offline.
* Benchmark (`scripts/mcp_benchmark.py`, real stdio subprocesses): session open median 1.93 s, warm tool call
  0.17 s, close 0.39 s (`docs/performance/MCP_BENCHMARKS.md`).
* The dataset resource now also carries `model_versions` and `model_run_id`; claim validation rejects evidence
  whose model version is not the current one.
* Agent evaluation: 70/70 scenarios through real stdio MCP (fake provider).

## Not verified

- Interoperability with third-party MCP hosts (e.g. desktop AI clients) was not tested.
- Streamable HTTP was tested in-process with the SDK client only, not behind a reverse proxy.
