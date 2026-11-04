# Research and sources

Findings verified on 2026-11-03/04 before and during Milestone 1. "Verified live" means the
behavior was observed against the real API, not just read in documentation.

## 1. Primary dataset: NYC 311 Service Requests from 2020 to Present

| Item | Finding | How verified |
|---|---|---|
| Dataset | `erm2-nwe9`, "311 Service Requests from 2020 to Present" | Live metadata `/api/views/erm2-nwe9.json` |
| Publisher | NYC Office of Technology and Innovation (OTI); attribution field "311" | Live metadata |
| Update frequency | Daily, automated | Live metadata (`custom_fields.Update`) |
| Last source update at check | `rowsUpdatedAt` = 2026-11-02 02:37:49 UTC; newest record created 2026-11-01 02:06 | Live metadata + query |
| Schema | 44 public columns (+4 computed-region columns); snapshot committed in `configs/nyc311_schema_snapshot.json` | Live metadata |
| Historical split | This dataset covers 2020→present. Earlier years are in a separate dataset (out of scope). | Dataset title and description |
| Time fields | `created_date`, `closed_date` etc. are *floating* timestamps (no zone), which represent NYC local time | Live records (`2026-11-01T02:06:29.000`) |
| Null handling | Fields whose value is null are **omitted** from JSON records | Live records |
| Target category names | `Noise - Residential` (NYPD) and `HEAT/HOT WATER` (HPD), with exact spelling and case | Live `$group=complaint_type` query |
| Volume | 772,357 records for these two types, 2025-11-01 → 2026-10-31 | Live `count(*)`; matched by our extraction |

### API behavior
- **Endpoint**: `https://data.cityofnewyork.us/resource/erm2-nwe9.json` (SODA 2.0).
- **`$limit` max 50,000** on 2.0 endpoints (no maximum on 2.1/3.0). [Socrata: LIMIT](https://dev.socrata.com/docs/queries/limit.html)
- **Throttling**: requests without an app token share an IP-based pool and get HTTP **429** when
  throttled. Token goes in `X-App-Token`. [Socrata: App tokens](https://dev.socrata.com/docs/app-tokens.html)
- **Stable pagination (verified live)**: `$order=:id` with `$where=... AND :id > '<last :id>'`
  continues exactly where the previous page ended. `:id` values are **not in string order**
  (e.g. `row-fjhv~…` is followed by `row-54ni.…`), so the client must never compare or sort
  them locally; it only echoes the last value back to the server. Offset pagination was
  rejected because rows can be inserted or updated mid-extraction.
- **`:updated_at` system field (verified live)**: usable in `$where` for incremental extraction.
  The platform **rewrites this field in bulk**: 251,587 rows of the two target types had
  `:updated_at` after 2026-10-30, and our real incremental run re-fetched 214,264 rows within
  the 72 h lookback. Incremental runs are therefore heavier than "new rows only".
- **Performance (measured)**: 50,000-row pages took 6–80 s, typically 12–25 s. The full
  12-month backfill (16 pages) took about 5.5 minutes without an app token.

## 2. Analytical validity and known biases
- **Reporting bias**: 311 measures *reported* complaints. Reporting propensity varies with
  neighborhood socioeconomic characteristics. Heat/hot-water under-reporting is documented
  specifically. ([Boxer, Hong, Kontokosta & Neill, *Annals of Applied Statistics* 2025 / NYU Marron](https://marroninstitute.nyu.edu/papers/estimating-reporting-bias-in-311-compliant-data);
  [Kontokosta & Hong, equity in 311 reporting, arXiv 1710.02452](https://export.arxiv.org/pdf/1710.02452)).
  Consequence: rates and rankings between areas must not be read as true incident prevalence.
  Any future model that ranks areas risks encoding this bias.
- **Closure status is noisy**: a NYC Council review found closure statuses are not always
  accurate. Some agencies marked complaints closed while the files suggested ongoing work
  ([CBS New York](https://www.cbsnews.com/newyork/news/311-city-council-study)). That study is
  older and may not reflect current practice, but `closed_date` is still treated as "time to
  *recorded* closure", not "time to resolution".
- **Right-censoring**: open requests have no `closed_date`. A median over closed requests is
  biased low, so the dashboard documents this and uses a maturity-aware "not closed within N
  days" metric.
- **Observed data-quality issues (this snapshot)**: 41 closures before creation, 641 missing or
  out-of-bounds coordinates, 9 "Unspecified" boroughs, 3 Closed records without a
  `closed_date`, and 24 invalid ZIPs (see `docs/data/QUALITY_RULES.md`).

## 3. Licensing and attribution
- NYC Open Data is offered under the NYC.gov Terms of Use. Data is provided without warranty of
  accuracy or completeness.
- **Not fully verified**: the official terms page URLs tried (`nyc.gov/html/data/terms.html`,
  `opendata.cityofnewyork.us/overview/`) returned 404 or redirects during research. Older public
  discussions of the terms mention a requirement to state that data was modified from its
  source. The app follows that conservatively: the About page credits NYC Open Data,
  states the data was filtered and aggregated, and repeats the no-warranty disclaimer.
  **Action:** re-verify the current terms text before public deployment (Milestone 5).

## 4. Technology versions (resolved and tested in this environment)

| Component | Version installed | Notes |
|---|---|---|
| Python | 3.12.0 | Several deps (polars, pyarrow, duckdb 2.x) need 3.11+ |
| FastAPI / Pydantic / pydantic-settings | 0.162.0 / 2.14.2 / 2.16.2 | |
| DuckDB | 2.0.0 | Released 2026-10-21. All SQL used was tested on it. |
| Polars / PyArrow | 2.1.0 / 26.0.1 | |
| httpx | 0.28.1 | Starlette warns its TestClient prefers `httpx2` (deprecation only) |
| pytest / ruff / mypy | 9.2.0 / 0.17.3 / 2.4.1 | |
| Next.js / React | 16.4.5 / 19.3.0 | Scaffolded with `create-next-app@16.4.5`. Cache Components and Partial Prefetching are on by default. |
| Tailwind CSS | 4.x (via `@tailwindcss/turbopack`) | |
| Recharts | 3.10.1 | |
| Playwright | 1.64.1 | `@axe-core/playwright` for accessibility checks |
| MCP Python SDK (Milestone 3) | `mcp` 2.3.0 | v2 API: `from mcp.server import MCPServer`, `@mcp.tool()`, `from mcp import Client`. Streamable HTTP + stdio transports. Python ≥3.11. [repo](https://github.com/modelcontextprotocol/python-sdk) |
| LightGBM / scikit-learn (Milestone 2) | 4.7.0 / 1.9.1 (latest on PyPI) | Not installed yet |

Next.js notes from its bundled docs (`node_modules/next/dist/docs`):
- `useSearchParams` client components must sit under a `<Suspense>` boundary when routes are
  prerendered.
- With Cache Components, routes are kept mounted but hidden via React `<Activity>`, so E2E
  selectors must be visibility-aware.
- **Rewrite destinations are fixed at build time.**
- Verified empirically: the Next.js rewrite proxy **forwards a client-supplied
  `X-Forwarded-For` unchanged and does not add one**. The API therefore ignores that header
  unless trusted proxy hops are configured.
