# CivicPulse AI: predictive city operations intelligence

A personal engineering project. It is an end-to-end analytics platform on **real NYC 311 service
requests** (residential noise, heat/hot water). It is built in milestones, and only what is
listed as done below has been implemented and verified.

> **Status: Milestone 2 (predictive modeling) implemented and awaiting review.** Milestone 1
> analytics, plus daily forecasts, closure-delay risk, anomaly detection, a model API and a
> Prediction Lab UI, all trained and evaluated on real data. The MCP server and AI analyst (M3)
> and maps/reports (M4) are **not built yet**. Their pages say so. See
> [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md).

![Operations overview](docs/screenshots/overview.png)

## The problem
City analysts need to know where service-request volume is rising, how quickly requests get
closed, and whether the data behind those answers can be trusted. 311 data is public but
large, changing daily, and full of quirks: bulk record rewrites, invalid closures, missing
coordinates, and reporting bias. This project builds a pipeline and dashboard that handle
those quirks explicitly.

## What works today (verified)
- **Ingestion** from the Socrata API, verified live:
  - keyset pagination on `:id` and explicit `$select`
  - server-side filters
  - retries with exponential backoff, jitter and `Retry-After`
  - checkpoint/resume, a forward-only watermark with a 72 h lookback, a failed-batch log, and lineage
- **Warehouse:** DuckDB over Parquet.
  - Deterministic rebuild with dedup to the latest version per request.
  - Structural failures are quarantined; plausibility problems are flagged and excluded only
    from the affected measure.
  - 30 data-quality rules with run history.
- **Analytics API** (FastAPI, read-only): KPIs with documented definitions, time series,
  breakdowns, closure distribution, hourly pattern, request search, drill-down and bounded
  CSV export.
- **Dashboard** (Next.js + Recharts): Overview, Service Request Explorer, Data Quality and
  About. It has URL-backed filters shared by every component, a table view for every chart,
  and loading, error and empty states. It works on mobile.

- **Predictive models (Milestone 2)**, trained by `civicpulse-model train` and served read-only
  from precomputed outputs:
  - **7-day daily forecasts** per borough × category: naive, seasonal-naive, 28-day mean and
    LightGBM, with rolling-origin backtests and conformal 90% intervals.
  - **Closure-delay risk** (not closed within 7 days): majority class, logistic regression and
    LightGBM, using creation-time features only, with label-maturity cutoffs.
  - **Anomaly detection**: a seasonal robust z-score, evaluated with synthetic injected anomalies.
  - The **Prediction Lab** pages (Forecasts, Delay Risk, Anomalies, Model Evaluation) show real
    model output and backtest metrics. They show no illustrative numbers.

### Model results (test periods, real data; full detail in [`docs/evaluation/MODEL_EVALUATION.md`](docs/evaluation/MODEL_EVALUATION.md))
| Task | Selected (on validation) | Test result | Honest reading |
|---|---|---|---|
| 7-day forecast, 52 weekly origins, Nov 2025 → Oct 2026 | LightGBM | WAPE 0.318 vs seasonal-naive 0.368, naive 0.347 | Beats seasonal-naive and 28-day mean (bootstrap CIs exclude 0). **Not significantly better than naive.** No gain on heat. 90% intervals cover 86.4%. |
| Delay risk, Jan → Oct 2026, 544,834 requests | LightGBM | PR-AUC 0.062 (base rate 0.027), ROC-AUC 0.79 | Ranks mostly by category and borough. Chance-level within a category. **Poorly calibrated** under 2026 label drift. Not decision-grade. |
| Anomalies, 144 synthetic injections | seasonal MAD | recall 29.9% at a 7.7% alert rate on ordinary days | Catches about half of +100% spikes and few changes under 50%. Real alerts are unverified. |

### Real data loaded (local snapshot)
| | |
|---|---|
| Source | NYC Open Data `erm2-nwe9`, *311 Service Requests from 2020 to Present* |
| Scope | `Noise - Residential` (NYPD), `HEAT/HOT WATER` (HPD) |
| Window | created **2023-01-01** → 2026-11-01 02:04 (NYC local); 7 contiguous backfill windows, each re-counted live ([manifest](docs/data/MANIFEST.md)) |
| Records | **2,511,293** accepted, 0 rejected (2,975,048 raw rows incl. 463,755 superseded versions) |
| Quality | 29 of 31 rules pass or are informational. 2 warnings (DQ040, DQ042): the source has published no row newer than 2026-11-02 02:38 UTC. |
| Freshness | Snapshot, **not live**. Timestamps are shown in the UI. |

### Measured performance (Milestone 1: local Windows laptop, 773k rows, via the web proxy)
KPI query 72–80 ms cold / ~5 ms cached. Uncached explorer search ~52 ms. 10,000-row CSV export
~220 ms. Warehouse rebuild ~4 s. Full 12-month extraction ~5.5 min (no app token).

## Architecture
```text
Socrata API → ingestion (raw Parquet + run state) → DuckDB transform → Parquet warehouse
            → analytics service (shared; MCP tools will reuse it in M3) → FastAPI → Next.js
warehouse → civicpulse-model train → data/models/runs/<id>/ (predictions, backtests, evaluation)
            → prediction service (read-only) → FastAPI /api/v1/predictions, /models, /anomalies
```
Details and the architecture decision record: [`docs/architecture/SYSTEM_DESIGN.md`](docs/architecture/SYSTEM_DESIGN.md).

## Quick start
```bash
py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev,ml]"   # Python 3.11+
cd apps/web && npm ci && cd ../..

.venv/Scripts/civicpulse extract --mode backfill   # last 12 months, ~5 min (or see offline option below)
bash scripts/backfill_history.sh                   # optional: history back to 2023-01-01, ~20 min, resumable
.venv/Scripts/civicpulse build
.venv/Scripts/civicpulse-model train               # forecasts, delay risk, anomalies (~9 min on full history)
.venv/Scripts/python -m uvicorn civicpulse_api.main:app --port 8000
cd apps/web && npx next build && npx next start -p 3000          # open http://localhost:3000
```
Offline: `python scripts/build_sample_warehouse.py --data-dir .sample-data --with-models` and set
`CIVICPULSE_DATA_DIR=.sample-data`. This uses 3,785 committed real records (2023-01 → 2026-11)
and trains smoke-scale models whose metrics are **not** meaningful. See
[`data/samples/README.md`](data/samples/README.md).

Other commands: `civicpulse extract --mode incremental`, `civicpulse status`,
`civicpulse duckdb` (writes a `.duckdb` file of views for ad-hoc SQL), and `make help`-style
targets in the `Makefile`.

## Tests
```bash
.venv/Scripts/python -m pytest                 # 126 offline tests (ingestion, analytics, modeling, API)
.venv/Scripts/python -m pytest -m live         # opt-in checks against the real API
cd apps/web && npx playwright test             # 28 browser tests incl. Prediction Lab, axe accessibility, mobile
```
Analytics tests compare every metric with an **independent Polars computation** over a
warehouse built from the committed real sample. Ingestion tests use a fake Socrata server that
reproduces the real API's quirks: non-lexicographic `:id` order, omitted nulls, 429s and timeouts.

## Data source, attribution and limitations
Data from **NYC Open Data**, published by the NYC Office of Technology and Innovation. This
project filters, cleans and aggregates it; it is not an official City product, and the City
gives no warranty of accuracy.
- 311 records **reported complaints**, not true incident prevalence. Reporting propensity varies
  between neighborhoods ([research](docs/research/SOURCES.md#2-analytical-validity-and-known-biases)).
- "Time to closure" is time to a **recorded** closure. The median excludes open requests.
- Street addresses are never ingested, and coordinates are never served.
- Model scores are associations learned from historical data. They are not causal claims and
  not operational recommendations. The delay-risk model is weak and poorly calibrated on 2026
  data (see its doc).

## Documentation
[Sources and research](docs/research/SOURCES.md) · [System design and ADR](docs/architecture/SYSTEM_DESIGN.md) ·
[Data dictionary](docs/data/DATA_DICTIONARY.md) · [Quality rules](docs/data/QUALITY_RULES.md) ·
[Data manifest](docs/data/MANIFEST.md) · [Deployment](docs/deployment/DEPLOYMENT.md) · [Project status](docs/PROJECT_STATUS.md)

Modeling: [Forecasting](docs/modeling/FORECASTING.md) · [Delay risk](docs/modeling/DELAY_RISK.md) ·
[Anomalies](docs/modeling/ANOMALIES.md) · [Model evaluation](docs/evaluation/MODEL_EVALUATION.md)
