# Project status

Last updated: 2026-11-04. Current milestone: **3, implemented and awaiting review** (branch
`feature/milestone-3-mcp-analyst`, not merged). Milestones 1 and 2 were approved (M2 with model-safety
restrictions). Milestone 4 has **not** been started.

## Milestone 3: Real MCP server + evidence-grounded AI analyst

| Capability | Status | Evidence |
|---|---|---|
| AI readiness audit before exposing models to an agent | Done. 42/42 artifact checks reproduce; **closure-delay numeric predictions disabled** for the agent (historical status not reliably reconstructable) | `docs/evaluation/AI_READINESS_AUDIT.md` |
| MCP server, official SDK `mcp` 2.3, 10 typed read-only tools, 3 resources, audit log | Done | `docs/mcp/TOOLS.md`, `services/mcp/` |
| MCP client + protocol verification (stdio, both protocol versions; token-protected HTTP) | Done | `docs/mcp/PROTOCOL_VERIFICATION.md` |
| Orchestrator: provider abstraction (fake / OpenAI-compatible), allow-list + schema validation, tool/token/time/cost budgets, evidence IDs, deterministic grounding, bounded regeneration, verified fallback | Done | `docs/mcp/ARCHITECTURE.md`, `packages/agent/` |
| API: `POST /api/v1/ai/investigations` (only non-GET route), `GET /api/v1/ai/status`; Origin, size, strict JSON, rate limits, optional token | Done | `services/api/tests/test_api_ai.py` |
| AI Analyst page: suggestions, answers with clickable evidence, charts, freshness, trace panel, model warnings, copy / retry / new / clear, loading and error states, keyboard and mobile | Done | `apps/web/tests/analyst.spec.ts`, `docs/screenshots/analyst.png` |
| Evaluation: 60 deterministic scenarios on the full snapshot via real stdio MCP | **60/60 with the FAKE provider** (first run 56/60; fixes disclosed). **No real-LLM evaluation run.** | `docs/evaluation/AGENT_EVAL.md` |
| Threat model | Done | `docs/security/AI_THREAT_MODEL.md` |
| Regression | pytest 244 passed (3 live tests deselected); Playwright 37/37 on the full snapshot; ruff, mypy, tsc, eslint, `next build` clean | this file, §Verification |
| Docker / compose | **Verified locally** (Docker 27.5.1): images build; in-container API spawns the MCP server over stdio and answers through the web proxy | §Verification |
| Secret scan | detect-secrets 1.5.0: 0 findings in the 38 changed files | |

### Verification (2026-11-04)
- `pytest`: 244 passed, 3 deselected (`live`). Agent: 51 unit + 20 orchestrator; API AI: 10; MCP: 37.
- Playwright (4 workers, real API + Next production build, fake provider): 37 passed, including axe
  checks on `/analyst` (empty and with an answered, expanded investigation) and mobile overflow.
- `scripts/agent_eval.py`: 60/60, median 2.15 s per investigation.
- Docker compose (bundled sample): `/api/v1/ai/status` 200; an investigation returned `answered`,
  MCP `civicpulse-mcp` 0.3.0, protocol 2025-11-25, stdio; `/analyst` 200.

### Known limitations (Milestone 3)
- **No live LLM has been tested.** The default provider is a deterministic rule-based fake. The
  OpenAI-compatible provider is tested only against a mocked HTTP transport. Paid testing needs
  explicit authorization and a key.
- Grounding verifies that numbers exist in cited evidence, not that prose attributes them correctly.
- Input screening is regex-based and bypassable by paraphrase; containment relies on the read-only
  tool surface, budgets and output validation.
- Rate limits and the cost ceiling are in-process (single instance). The cost ceiling needs
  per-token prices configured to limit spend.
- The analyst endpoint is anonymous unless `CIVICPULSE_AI_ACCESS_TOKEN` is set; it must be set (and
  injected server-side) before any public deployment.
- Each investigation spawns an MCP process (~1.5–2 s overhead).
- Answers describe a historical snapshot (cutoff 2026-11-01 02:04:40 NYC), never live operations.

## Milestone 2: Predictive modeling (branch `feature/milestone-2-predictive`)

| Capability | Status | Evidence |
|---|---|---|
| History expanded to 2023-01-01 (6 resumable half-year backfills plus an incremental) | Done | 1,738,141 new rows. Every window re-counted live and matching. Contiguous coverage. M1 files untouched. `docs/data/MANIFEST.md` |
| Source freshness check on actual rows (DQ042) | Done | Warns: newest row version is 2026-11-02 02:38 UTC |
| Daily 7-day forecasts (naive, seasonal naive, 28-day mean, LightGBM), rolling-origin backtest, conformal intervals | Done | `docs/modeling/FORECASTING.md`, leakage tests |
| Closure-delay risk (majority, logistic regression, LightGBM), maturity cutoff, embargoed chronological splits, calibration | Done, **weak model** | `docs/modeling/DELAY_RISK.md` |
| Anomaly detection (seasonal MAD, rolling-median comparator), synthetic injection evaluation | Done | `docs/modeling/ANOMALIES.md` |
| Explainability (coefficients, gain, permutation, TreeSHAP) with a causality caveat | Done | Model docs, `/api/v1/models/{name}/explanations` |
| Read-only model API (10 endpoints, bounded inputs, no training or execution endpoints) | Done | `services/api/tests/test_api_predictions.py` |
| Prediction Lab UI: Forecasts, Delay Risk, Anomalies, Model Evaluation | Done. 28/28 Playwright on the real snapshot and on the sample | `apps/web/tests/prediction-lab.spec.ts`, screenshots in `docs/screenshots/` |
| Reproducibility | Done | Two full training runs on the same warehouse give identical metrics (`docs/evaluation/MODEL_EVALUATION.md`) |
| Local secret scan | Done | detect-secrets 1.5.0: 0 findings in tracked files and in each commit |
| Docker / compose | **Verified locally** (Docker 27.5.1): API image builds, ML-free runtime, healthy; compose serves all pages | `docs/evaluation/MODEL_EVALUATION.md` §6 |
| CI | Updated (`.[dev,ml]`, sample models, modeling coverage). **Never run on GitHub** (no remote) | `.github/workflows/ci.yml` |

### Known limitations (Milestone 2)
- **Forecasting:**
  - The forecast is not significantly better than the naive baseline (bootstrap CI includes 0).
  - Heat/hot water is not improved, because there are no weather inputs.
  - 90% intervals cover 86.4% on test.
- **Delay risk:**
  - The 2026 label drift (heat not-closed-in-7-days rate 3–14% vs 0.1–0.5% before) leaves
    probabilities under-calibrated.
  - Within-category ranking is at chance.
  - Snapshot fields are used because point-in-time history cannot be reconstructed.
- **Anomalies:**
  - There are no real labels. Recall is about 30% on synthetic injections.
  - The October heating-season onset produces many alerts.
- **Data:** the snapshot is stale upstream (no rows after 2026-11-01 02:06 created time). It is
  not live.

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
| Docker / compose | Written; not verified during M1 (Docker daemon unavailable). **Verified in M2**, see above | `services/api/Dockerfile`, `apps/web/Dockerfile` |
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

## Milestones 4–5: not started
- **M4:** MapLibre geographic explorer, investigation reports, performance polish.
- **M5:** hosting research, deployment, case study, evidence-based resume bullets.
