# CivicPulse AI: predictive city operations intelligence

A personal engineering project. It is an end-to-end analytics platform on **real NYC 311 service
requests** (residential noise, heat/hot water). It is built in milestones, and only what is
listed as done below has been implemented and verified.

> **Status: Milestone 1 (foundation and real analytics) complete.** Forecasting, delay-risk and
> anomaly models (M2), the MCP server and AI analyst (M3), and maps/reports (M4) are **not built
> yet**. Their pages say so and show no simulated data. See [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md).

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

### Real data loaded (local snapshot)
| | |
|---|---|
| Source | NYC Open Data `erm2-nwe9`, *311 Service Requests from 2020 to Present* |
| Scope | `Noise - Residential` (NYPD), `HEAT/HOT WATER` (HPD) |
| Window | created 2025-11-01 → 2026-11-01 02:04 (NYC local) |
| Records | **773,152** accepted, 0 rejected (986,621 raw rows incl. 213,469 superseded versions) |
| Quality | 29 of 30 rules pass or are informational. 1 warning: the source itself was 51.7 h stale at extraction. |
| Freshness | Snapshot, **not live**. Timestamps are shown in the UI. |

### Measured performance (local Windows laptop, 773k rows, via the web proxy)
KPI query 72–80 ms cold / ~5 ms cached. Uncached explorer search ~52 ms. 10,000-row CSV export
~220 ms. Warehouse rebuild ~4 s. Full 12-month extraction ~5.5 min (no app token).

## Architecture
```text
Socrata API → ingestion (raw Parquet + run state) → DuckDB transform → Parquet warehouse
            → analytics service (shared; MCP tools will reuse it in M3) → FastAPI → Next.js
```
Details and the architecture decision record: [`docs/architecture/SYSTEM_DESIGN.md`](docs/architecture/SYSTEM_DESIGN.md).

## Quick start
```bash
py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"   # Python 3.11+
cd apps/web && npm ci && cd ../..

.venv/Scripts/civicpulse extract --mode backfill   # real data, ~5 min (or see offline option below)
.venv/Scripts/civicpulse build
.venv/Scripts/python -m uvicorn civicpulse_api.main:app --port 8000
cd apps/web && npx next build && npx next start -p 3000          # open http://localhost:3000
```
Offline: `python scripts/build_sample_warehouse.py --data-dir .sample-data` and set
`CIVICPULSE_DATA_DIR=.sample-data`. This uses 969 committed real records, see
[`data/samples/README.md`](data/samples/README.md).

Other commands: `civicpulse extract --mode incremental`, `civicpulse status`,
`civicpulse duckdb` (writes a `.duckdb` file of views for ad-hoc SQL), and `make help`-style
targets in the `Makefile`.

## Tests
```bash
.venv/Scripts/python -m pytest                 # 81 offline tests (ingestion, transform, analytics, API)
.venv/Scripts/python -m pytest -m live         # opt-in checks against the real API
cd apps/web && npx playwright test             # 18 browser tests incl. axe accessibility + mobile
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

## Documentation
[Sources and research](docs/research/SOURCES.md) · [System design and ADR](docs/architecture/SYSTEM_DESIGN.md) ·
[Data dictionary](docs/data/DATA_DICTIONARY.md) · [Quality rules](docs/data/QUALITY_RULES.md) ·
[Deployment](docs/deployment/DEPLOYMENT.md) · [Project status](docs/PROJECT_STATUS.md)
