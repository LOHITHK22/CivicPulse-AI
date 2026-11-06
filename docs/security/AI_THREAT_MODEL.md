# AI analyst threat model (Milestones 3–4)

Scope: the `/analyst` page, `POST /api/v1/ai/investigations`, `GET /api/v1/ai/status`, the
orchestrator, the LLM provider connection and the `civicpulse-mcp` server. Assets: provider API key
(and spend), MCP bearer token, server configuration, the warehouse (read-only public data), the
integrity of answers (no fabricated or misleading numbers, no misuse of the restricted model),
and availability.

## Trust boundaries

1. Browser → Next.js → API (untrusted input; the browser bundle holds no secrets).
2. API → LLM provider (outbound; the provider sees the question, system prompt, planning context and
   bounded tool results: public data only, no credentials).
3. Orchestrator → MCP server (stdio child process; inherits no secrets).
4. Tool results → model context (dataset text such as descriptors is untrusted external content).

## Threats and controls

| # | Threat | Controls | Verified by |
|---|---|---|---|
| T1 | Prompt injection in the question ("ignore your rules", fake `<system>` tags) | Regex screening refuses before any tool/provider call; system prompt states tool data is not instructions; the model **cannot** gain capabilities: allow-listed read-only tools, schema-validated arguments, call budget. Earlier questions are passed as a labelled user message and screened. | `test_agent_units.py`, eval `inj-01..04`, `test_api_ai.py` |
| T2 | Indirect injection via dataset text (descriptors, notes) | Server-side `sanitize_text` (control characters stripped, length bounded); tool results are wrapped as JSON data; outputs are re-validated by grounding regardless of what the model read. | `test_backend.py::test_sanitize_strips_control_characters` |
| T3 | Unrestricted SQL / file / shell access | No such tools exist. Tool args are enums, dates, bounded ints; extra properties rejected client- and server-side. Questions requesting SQL/files/shell are refused. The LLM never receives database credentials (DuckDB is in-process, read-only). | `policy` tests, eval `sql-01..03`, `args-02` (`run_sql` rejected) |
| T4 | Secret disclosure (API key, tokens, system prompt) | Keys only in server env (`SecretStr`); never in responses, logs, traces or the browser; `/ai/status` exposes no secrets; stdio child env excludes secrets; provider HTTP errors never echo bodies; output redaction of key-like strings; blank `KEY=` values treated as unset. | `test_api_ai.py::test_status_route_exposes_no_secrets`, `test_evidence_public_view_has_no_raw_result_and_no_secrets`, provider tests |
| T5 | Fabricated or misattributed numbers | Deterministic grounding: each number must match evidence cited in the same sentence/line within displayed precision; invalid citations rejected; bounded regeneration; verified fallback. | grounding unit tests; eval `hall-01..03` |
| T6 | Misleading claims (causality, confirmed incidents, live monitoring, "beats all baselines") | Policy-phrase checks in grounding; mandatory uncertainty wording for forecasts, "statistical" for anomalies, "not validated" for delay; `live: false` in all provenance. | grounding tests; eval `causal-*`, `anom-*` |
| T7 | Misuse of the closure-delay model for prioritization or allocation | Numeric predictions disabled at the MCP server; action requests detected and refused with a restricted warning; UI warning panel. | `test_backend.py::test_delay_tool_never_returns_numeric_predictions`, eval `delay-01..04`, Playwright |
| T8 | Cost exhaustion / denial of wallet | Per-client sliding-window rate (default 6/min), daily request limit, daily USD ceiling checked before each provider call (needs prices configured), token budget per investigation, max output tokens, tool-call budget, regeneration limit, request timeout. Screening refusals are free. | `TestBudget`, `test_rate_limit_returns_429`, eval `fail-02`, `fail-03` |
| T9 | Denial of service via large or malformed requests | Content-Length required and ≤ 8 KB, JSON content type only, strict schema, question ≤ 500 chars, history ≤ 3 × 500 chars; MCP results ≤ 96 KB; bounded queries. | `test_body_size_content_type_and_methods`, `test_input_validation_and_origin` |
| T10 | Cross-site request forgery / cross-origin abuse | POST allowed only on one path; Origin must be in `CIVICPULSE_API_CORS_ORIGINS` when present; CORS restricted to GET/POST with `Content-Type`/`Authorization`; no cookies are used. | `test_input_validation_and_origin` |
| T11 | Unauthorized use of a public deployment | Optional `CIVICPULSE_AI_ACCESS_TOKEN` (constant-time compare). **Must be set** before public exposure, and injected server-side (not in the browser bundle). | `test_optional_access_token` |
| T12 | Spoofed client identity to evade rate limits | `X-Forwarded-For` is ignored unless `CIVICPULSE_API_TRUSTED_PROXY_HOPS` is configured; then only the entry appended by the outermost trusted proxy is used. | M1 API tests |
| T13 | Anonymous access to the MCP transport | stdio by default (no listener). HTTP mode: ≥ 32-char bearer token, loopback bind unless explicitly overridden, Host-header allow-list. | `test_http_transport.py` |
| T14 | Sensitive data in logs or traces | Logs record question length, status, tool names, token counts; never the question text, answer or secrets. Trace arguments pass through `sanitize` (secret-like keys redacted, strings truncated). MCP audit log is JSON lines without results. | code review; `audit.py` |
| T15 | Tool or provider failures hidden from the user | Every failure is a trace step with `error_kind`; the answer says which part could not be answered; status `error`/`fallback` are distinct. | eval `fail-*`, `test_tool_failure_is_reported_not_hidden` |

## Milestone 4 controls

| Threat | Control | Test |
|---|---|---|
| Unplanned paid usage | `CIVICPULSE_AI_PAID_ENABLED` kill switch (default off); paid provider also requires API key, access token, positive daily budget and both prices; otherwise the analyst is disabled (fail closed) and the route returns 503 before any provider call | `test_paid_ai_guard.py`, `test_api_ai.py` |
| Provider switched by a stray key | provider chosen only by `CIVICPULSE_LLM_PROVIDER` | `test_key_presence_never_switches_provider` |
| Resource exhaustion | concurrency gate (overall and per client, 429 + Retry-After), always released | `test_concurrency_limits` |
| Plausible but wrong numbers (right value, wrong borough/period/unit/denominator/direction, stale model) | structured claim validation against facts; prose may not contain numbers | `test_semantic_claims.py`, eval `sem-*` |
| Injection via conversation history | history screened like the question; failing items dropped | `test_injection_in_history_is_dropped` |
| Report leaks secrets or tampered reports | allow-listed fields, secret/path redaction, SHA-256 checksums, offline re-validation, client-scoped expiring store, CSP on HTML | `test_report.py`, `test_api_ai.py` |
| Shared mutable state across concurrent investigations | model versions now per investigation (race fixed) | `test_orchestrator.py` |
| Smoke test incurring cost by accident | `scripts/llm_smoke_test.py` needs a flag AND an env confirmation AND no paid-AI blockers; ≤3 tool calls, 800 output tokens, no retries | `test_llm_smoke_script.py` |

## Residual risks

- Regex screening is bypassable by paraphrase. The design does not rely on it: a successful injection
  can still only call read-only tools within budget, and every number is re-validated.
- Prose checks are lexical; a paraphrase that avoids borough/category/date words is not interpreted
  (numbers in prose are always rejected).
- Behind the Next.js proxy, browsers share one client key unless trusted proxy hops are configured,
  so per-client limits and report ownership are shared.
- In-process rate/cost/concurrency state and the report store reset on restart and are per instance:
  limits are **not** global across multiple instances.
- The daily cost ceiling is only as accurate as the configured per-token prices; with prices at 0
  (default) it does not limit spend, but the per-client and daily request limits still apply.
- No real LLM has been tested; provider-specific behaviour (refusals, tool-call formats) is unverified.
- Origin checking allows requests without an Origin header (non-browser clients); the access token is
  the control for those in a public deployment.
