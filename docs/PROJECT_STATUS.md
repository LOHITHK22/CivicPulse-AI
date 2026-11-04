# Project status

Last updated: 2026-11-04. Current milestone: **2, implemented and awaiting review.** Milestone 1 was
approved. Milestone 3 has **not** been started.

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

## Milestones 3–5: not started
- **M3:** MCP server (`mcp` 2.x), MCP client, analyst agent, 40+ scenario eval suite.
- **M4:** MapLibre geographic explorer, investigation reports, performance polish.
- **M5:** hosting research, deployment, case study, evidence-based resume bullets.

Docs owned by later milestones (`docs/mcp/TOOLS.md`, `docs/evaluation/AGENT_EVAL.md`) are
intentionally **not created yet**.
