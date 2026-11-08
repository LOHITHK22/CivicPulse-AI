# MCP and AI analyst architecture (Milestone 3)

## Components

```text
Browser (Next.js /analyst)
   │  POST /api/v1/ai/investigations   (same-origin via the Next.js server proxy; JSON, ≤ 8 KB)
   ▼
FastAPI (services/api)  ── guard: POST allowed on this one path only, Origin check, Content-Type,
   │                        Content-Length, optional bearer token, per-client + daily + cost limits
   ▼
Orchestrator (packages/agent/civicpulse_agent)
   ├─ input screening (secrets / SQL / shell / file / prompt-injection patterns)   → refuse, no tools
   ├─ relative-date resolution against TODAY and the dataset's coverage (dates.py)
   ├─ provider (fake | OpenAI-compatible) with a bounded tool loop
   ├─ tool policy: allow-list + JSON-schema validation of every argument object (policy.py)
   ├─ evidence objects E1..En (evidence.py)
   ├─ deterministic grounding validation + ≤ N regenerations + verified fallback (grounding.py)
   └─ trace, warnings, charts, freshness
   │  MCP client (official `mcp` 2.3 SDK): one session per investigation
   ▼
MCP server `civicpulse-mcp` (services/mcp) — stdio subprocess of the API (default)
   ├─ 10 typed, read-only tools + 3 JSON resources
   ├─ ProtocolAuditMiddleware (audits every tools/call, including rejected ones)
   └─ CivicPulseTools backend → AnalyticsService / PredictionService (DuckDB over Parquet, saved model artifacts)
```

## Key decisions

| Decision | Why |
|---|---|
| Official Python SDK (`mcp>=2.3,<3`), `MCPServer` + `mcp.Client` | The spec requires a real MCP server; the SDK handles the initialize/discover handshake, schema generation from type hints and `isError` results. |
| **stdio transport by default**, spawned per investigation | Nothing listens on a network port, so anonymous internet clients cannot reach MCP. The child process receives only the SDK's safe environment plus `CIVICPULSE_DATA_DIR` and `PYTHONIOENCODING`; no API key is inherited. Cost: ~1.5–2 s of process start per investigation. |
| Streamable HTTP available but locked down | Refuses to start without a ≥ 32-character bearer token, binds to loopback unless `--allow-non-loopback`, constant-time token compare, DNS-rebinding protection (allowed Host headers). |
| Tools call the **same** `AnalyticsService` / `PredictionService` as the REST API | One source of metric definitions. `services/mcp/tests/test_consistency.py` checks MCP results against the API and against independent Polars computations. |
| No SQL, filesystem, shell, write or network tools | Tool arguments are enums, dates and bounded integers. Queries are bounded (range limits, row limits, ≤ 96 KB results). |
| Closure-delay tool returns research metrics only | The AI readiness audit found that the historical status needed for the target cannot be reliably reconstructed (`docs/evaluation/AI_READINESS_AUDIT.md`). `DELAY_NUMERIC_PREDICTIONS_ENABLED = False`. |
| Grounding is checked in code, not by an LLM judge | Every number in an answer must appear (within shown rounding) in the evidence its sentence cites; failures trigger a bounded regeneration and then a deterministic fallback built from tool summaries. |
| Fake provider is the default | Lets the full pipeline run and be tested with no key and no cost. It is labelled "deterministic test provider, not a language model" in the API and UI. |

## Request lifecycle

1. The API guard checks method/path, Origin, content type and size; FastAPI validates the body
   strictly (`extra="forbid"`, strict types, ≤ 3 history items).
2. Screening refuses secret/SQL/injection requests **before** any budget is charged or tool called.
3. `UsageGovernor.admit` enforces the per-client sliding-window rate, the daily request limit and
   the daily cost ceiling.
4. One MCP session: `list_tools` (schemas are the validation source of truth) and the
   `civicpulse://dataset` resource (first date, last complete date, cutoff).
5. Relative dates are resolved; a planning context (JSON) is given to the provider together with the
   system prompt. Earlier questions are passed as a *user* message labelled "context only".
6. Tool loop: at most `CIVICPULSE_AI_MAX_TOOL_CALLS` executed calls; each call is allow-listed and
   schema-validated before it reaches MCP; failures become structured tool messages and trace steps.
   The input-token budget and cost ceiling are checked before every provider call.
7. Grounding validation → regenerate (≤ `CIVICPULSE_AI_MAX_REGENERATIONS`) → fallback.
8. The whole investigation is bounded by `CIVICPULSE_AI_REQUEST_TIMEOUT`.
9. The response carries the answer, evidence (without raw result arrays), trace, validation,
   warnings, resolved periods, freshness (`live: false`), charts, provider info and usage.

## Status taxonomy

`answered` (grounding passed) · `clarification` (no tools, asks a question) · `refused` (input policy) ·
`fallback` (generated answer failed checks; verified tool summaries shown instead) · `error`
(provider failure, MCP unavailable, timeout, token or cost budget).

## What is not verified

- No real LLM has been run through this pipeline (no paid API call was authorized). The
  OpenAI-compatible provider is tested only against a mocked HTTP transport.
- Rate limits and the cost ceiling are in-process; a multi-instance deployment needs a shared store.
