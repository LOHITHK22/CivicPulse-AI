# System design

## Data and request flow (Milestone 1)

```text
NYC Open Data (Socrata, erm2-nwe9)
   │  HTTPS, $select/$where server-side filters, $order=:id keyset paging,
   │  retries (exp. backoff + jitter, Retry-After), 120 s timeout
   ▼
packages/ingestion ── extract.py ──► data/raw/nyc311/run=<id>/page-NNNNN.parquet   (raw, all strings + lineage)
   │                                  data/state/runs/<id>.json                     (checkpoint, resumable)
   │                                  data/state/watermark.json                     (max :updated_at, forward-only)
   │                                  data/state/failed_batches.jsonl               (failure log)
   ▼
transform.py (DuckDB, in-memory, deterministic)
   dedup by unique_key (latest :updated_at) → type/parse → reject | flag → dims/aggs
   │
   ├─► data/processed/*.parquet           fact, dims, aggs, rejected, model_* (empty until M2)
   ├─► data/processed/data_quality_runs/  one Parquet per build (history)
   └─► data/state/last_build.json         build summary + rule results
   ▼
packages/analytics  AnalyticsService (DuckDB in-memory views over Parquet, read-only, cached per build)
   ▼                     ▲ (Milestone 3: MCP server will call the same service)
services/api  FastAPI  /api/v1/*  (GET only, validation, rate limit, security headers)
   ▼
apps/web  Next.js  — browser calls same-origin /api/v1/*, rewritten server-side to the API
```

## Architecture decision record (ADR-001: Milestone 1 foundation)

**Status:** accepted, 2026-11-04.

**Context.** The project needs a reproducible, testable analytics stack on public 311 data. It
runs on a single developer machine now and on low-cost hosting later, with about 1M rows
per year of the two categories in scope.

**Decisions**

| # | Decision | Alternatives considered | Why |
|---|---|---|---|
| 1 | **Parquet files as the warehouse, queried by DuckDB in-memory views** | DuckDB database file; PostgreSQL | A DuckDB file holds a single-writer lock, so rebuilding while the API runs would fail (worse on Windows). Parquet plus atomic rename lets the pipeline rebuild under a running API. No transactional or multi-user needs exist yet, so PostgreSQL isn't justified. `civicpulse duckdb` still produces a `.duckdb` file of views for ad-hoc SQL. |
| 2 | **Full rebuild from all raw pages each build** (not in-place upserts) | MERGE/upsert into the fact table | 1M rows rebuild in about 4 s. A pure function of raw inputs is deterministic, idempotent and easy to test. Dedup by `unique_key` keeps the latest `:updated_at`, which gives upsert semantics. |
| 3 | **Keyset pagination on `:id`** | `$offset` paging; paging on `created_date` | Offset paging skips or duplicates rows when the source changes mid-run. `created_date` is not unique. `:id` keyset was verified live. |
| 4 | **Incremental = `:updated_at > watermark − 72 h`** | `created_date`-only increments | Catches late closures and status changes. The cost (bulk re-fetches) was measured and documented. |
| 5 | **Reject vs flag**: structural failures are quarantined; plausibility problems are kept and flagged | Drop all suspect rows | Keeps counts honest (39–41 bad closures shouldn't remove the requests themselves) while excluding bad values from the affected measure. |
| 6 | **One analytics service layer** shared by the API (and later MCP) | Separate SQL in each interface | Metric definitions live in one place, which keeps them consistent and grounded. |
| 7 | **Browser → Next.js → API via a server-side proxy** (rewrites until M4; route handlers since M5) | Browser calls API directly with CORS | No API URL or credential in the client bundle. Same-origin. Since M5 the proxy attaches the proxy secret, AI token and client address on the server (`docs/deployment/ARCHITECTURE.md`). |
| 8 | **No Kafka, Airflow, Redis, Kubernetes or vector DB** | | Not needed at this scale. A rate limiter and cache in the process are enough for a single instance. |
| 9 | **Python 3.12, pinned lower bounds; latest releases tested** | Pin older majors | DuckDB 2.0, Polars 2.1 and Next 16.4 were tested end to end in this environment. |

**Consequences / risks**
- The in-process rate limiter and cache are per instance. Scaling out would need a shared store.
- Without trusted proxy configuration, all traffic through the web proxy shares one rate-limit
  bucket (default 600 req/min). See `docs/deployment/DEPLOYMENT.md`.
- Incremental runs can re-download 200k+ rows after platform bulk rewrites, so an app token is
  recommended for scheduled runs.

## Security model (Milestone 1)
- **Read-only API**: non-GET methods return 405. There is no arbitrary SQL. Sort columns are
  allow-listed and all values are bound parameters (an injection test covers search). Page size
  ≤ 100, export ≤ 10,000 rows, URL ≤ 4 KB, date range ≤ 3 years.
- **Privacy**: addresses, cross streets, BBL and resolution text are never ingested.
  Coordinates are stored but never returned by any endpoint. The committed sample rounds them
  to about 100 m.
- **Secrets**: only from the environment (`.env` is git-ignored). The optional Socrata token is
  sent as a header and redacted by the log formatter. There are no client-side secrets.
- **Errors**: unexpected exceptions return a generic 500. Details are logged server-side as
  structured JSON.

## Milestone 4 additions

```
Browser ──► Next.js (/geo, /analyst, proxy /api/v1/*) ──► FastAPI
  /api/v1/geo/boroughs ──► AnalyticsService (agg_daily_requests; prior period of equal length)
  POST /api/v1/ai/investigations ──► paid-AI guard ─► concurrency gate ─► Orchestrator
        Orchestrator ──► MCP client ──stdio──► civicpulse_mcp (one child per investigation)
                     ──► evidence ─► facts ─► claim validation ─► rendered answer
        result ──► InvestigationStore (bounded, expiring, client-scoped)
  GET /api/v1/ai/investigations/{id}/report ──► report.py (JSON schema v1, Markdown, print HTML)
```

* **Geography:** MapLibre GL 6.15 over DCP Borough Boundaries (`gthc-hcne`, EPSG:4326), simplified offline by
  `scripts/build_borough_geojson.py` (provenance in `docs/data/borough_boundaries.json`). No basemap tiles, no
  request coordinates or addresses: counts are joined on the borough recorded in each request. Volume is drawn as
  proportional circles over neutral shapes so land area is not read as volume; change uses a fixed ±50% scale.
* **Semantic grounding:** see `docs/evaluation/SEMANTIC_GROUNDING.md`.
* **Reports:** only validated claims; per-evidence SHA-256 and a timestamp-independent content checksum;
  `python -m civicpulse_agent.report verify` re-validates offline without the LLM.
* **MCP transport:** per-investigation stdio subprocess kept after benchmarking (`docs/performance/MCP_BENCHMARKS.md`).
* **Process-local state:** rate limits, budget, concurrency gate and report store live in one API process;
  multi-instance deployments need a shared store (`docs/deployment/DEPLOYMENT_READINESS.md`).
