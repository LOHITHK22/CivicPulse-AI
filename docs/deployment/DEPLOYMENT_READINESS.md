# Deployment readiness (Milestone 4)

Status: **not deployed; not ready for a public deployment without the items under "Required before public use".**
Local and container verification only (2026-11-05/06).

## Verified locally

| Item | Result |
|---|---|
| Docker Compose (Docker 27.5.1) | `api` and `web` images build; API healthy; bundled sample warehouse ready (3,606 rows); geo endpoint, AI status, an investigation (`compare_boroughs` over stdio MCP, protocol 2025-11-25, 11 validated claims) and Markdown/HTML/JSON reports through the web proxy; downloaded report re-verified offline; stack shut down |
| Production Next.js build | passes; MapLibre worker copied to `public/vendor/` at prebuild |
| Browser regression | Playwright 48/48 (desktop + mobile, 1 worker, real API + production build, fake provider) |
| Secret scan | gitleaks 8.30.1: 0 findings in branch commits and full history (15 commits) and 0 in tracked files |

## Controls in place

* Paid-AI kill switch `CIVICPULSE_AI_PAID_ENABLED` (default false). A paid provider additionally requires an API
  key, `CIVICPULSE_AI_ACCESS_TOKEN`, a positive daily budget and both token prices; anything missing fails closed.
* Access token on the investigation and report routes when configured; Origin check, strict JSON, body/URL size
  limits, per-client and daily limits, cost ceiling, concurrency caps (overall and per client).
* MCP: allow-listed read-only tools, schema validation, per-call timeouts; stdio child gets no API key.
* Report download: bounded, expiring, client-scoped in-memory store; strict CSP on HTML reports.

## Required before public use

1. Set `CIVICPULSE_AI_ACCESS_TOKEN` and inject it **server-side** (a proxy or route handler). The browser bundle
   has no token, so with a token set the public UI cannot call the analyst until that injection exists.
2. Shared state for multi-instance deployments: rate limits, daily budget, concurrency gate and the report store
   are **process-local**. Behind more than one instance each instance enforces its own limits, so global limits are
   not guaranteed. Use one instance, or move these to a shared store (e.g. Redis) first.
3. Trusted proxy configuration (`CIVICPULSE_API_TRUSTED_PROXY_HOPS`): behind the Next.js proxy every browser
   shares the proxy's client key unless forwarded addresses are trusted, which also shares per-client limits and
   report ownership between users.
4. Re-verify NYC Open Data terms and attribution (DCP boundaries and 311 data) before launch.
5. A live-LLM smoke test (`scripts/llm_smoke_test.py`) only after explicit authorization of that specific run.

## Not verified

* Hosting platform, TLS, reverse proxy, autoscaling, log retention, backups (Milestone 5).
* Live LLM behaviour and cost (blocked: no authorization or credentials).
* MCP session pooling (not implemented; see `docs/performance/MCP_BENCHMARKS.md`).
