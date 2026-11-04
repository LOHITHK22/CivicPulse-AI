# Project status

Last updated: 2026-11-04. Current milestone: **1, implemented and awaiting review.**

## Milestone 1: Foundation and real analytics

| Capability | Status | Evidence |
|---|---|---|
| Research (dataset, API, biases, versions, MCP SDK) | Done | `docs/research/SOURCES.md` |
| Architecture decision record | Done | `docs/architecture/SYSTEM_DESIGN.md` (ADR-001) |
| Ingestion: pagination, filters, retries/backoff/jitter, 429/Retry-After, timeouts, schema validation, checkpoints/resume, watermark + lookback, failed-batch log, lineage | Done | 23 ingestion tests; real backfill (772,357 rows) and real incremental run (214,264 rows) |
| Warehouse (DuckDB + Parquet): fact, dims, aggregates, rejects, quality history, model tables (empty) | Done | 6 transform tests; deterministic-rebuild test |
| Data-quality rules + report | Done | 30 rules; `docs/data/QUALITY_RULES.md` |
| Analytics service + FastAPI endpoints | Done | 30 analytics tests (vs independent Polars), 31 API tests |
| Dashboard: Overview, Explorer, Data Quality, About | Done | 18 Playwright tests (desktop + mobile, axe accessibility) |
| Pages for Milestones 2–4 | Placeholders, explicitly labelled "Planned", no fake data | E2E test asserts the label |
| CI workflows (lint, types, tests, build, E2E, secret scan, dependency audits) | Written; **not yet run on GitHub** (no remote) | `.github/workflows/` (E2E job reproduced locally against the sample warehouse) |
| Docker / compose | Written; **not built or verified** (Docker daemon unavailable) | `services/api/Dockerfile`, `apps/web/Dockerfile` |
| Secret scan | **Not run locally** (gitleaks not installed). Runs in CI once pushed. Manual review: no secrets in the repo. | |

### Known limitations
- The data is a **snapshot** (newest request 2026-11-01 02:04 NYC time). It is not live.
- Only 12 months are loaded, so seasonality can't be separated from trend.
- The "categories with unusual increases" KPI, historical-vs-predicted charts and anomaly overlays
  are **not computed** until Milestone 2.
- Without trusted proxy configuration, rate limiting is effectively global behind the web proxy.
- The source dataset was **51.7 h stale** at the last extraction check (rule DQ041 warns). This is
  an upstream issue.
- Licensing terms page could not be retrieved during research. Attribution is applied
  conservatively, and the terms need re-verifying before public launch.

## Milestones 2–5: not started
- **M2:** forecasting baselines and GBM, closure-delay risk, anomaly detection, evaluation UI.
- **M3:** MCP server (`mcp` 2.x), MCP client, analyst agent, 40+ scenario eval suite.
- **M4:** MapLibre geographic explorer, investigation reports, performance polish.
- **M5:** hosting research, deployment, case study, evidence-based resume bullets.

Docs owned by later milestones (`docs/modeling/*`, `docs/mcp/TOOLS.md`,
`docs/evaluation/AGENT_EVAL.md`) are intentionally **not created yet**.
