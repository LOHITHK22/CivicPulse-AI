# Data dictionary

All tables are Parquet files in `data/processed/`, rebuilt by `civicpulse build`. Times are NYC
local wall-clock (`TIMESTAMP` without zone) unless marked UTC.

## `fact_service_requests`: one row per service request (latest version)

| Column | Type | Definition |
|---|---|---|
| `unique_key` | VARCHAR | Source request ID (primary key, unique, enforced by rule DQ001) |
| `created_at` | TIMESTAMP | Source `created_date` |
| `closed_at` | TIMESTAMP | Source `closed_date`. **NULL** if open, or if the closure is invalid (before creation or in the future) |
| `created_date` | DATE | `created_at` date. All date filters use this. |
| `created_hour` / `created_dow` / `created_month` | TINYINT | Hour 0–23, ISO weekday 1=Mon…7=Sun, month 1–12 |
| `category_key` / `category_label` | VARCHAR | Configured category (`configs/ingestion.yaml`) |
| `complaint_type`, `descriptor` | VARCHAR | Source values |
| `agency`, `agency_name` | VARCHAR | Responding agency |
| `status` | VARCHAR | Source status at extraction ("Unspecified" if missing) |
| `status_group` | VARCHAR | `closed` (Closed), `open` (Open/In Progress/Assigned/Pending/Started), `unknown` (anything else) |
| `borough` | VARCHAR | One of five boroughs, or `UNSPECIFIED` |
| `community_board`, `council_district`, `police_precinct` | VARCHAR | Source geovalidation fields |
| `incident_zip` | VARCHAR | 5-digit ZIP, NULL if invalid |
| `latitude`, `longitude` | DOUBLE | NULL if missing or outside the NYC bounding box. **Never served by the API.** |
| `location_type`, `channel` | VARCHAR | Source `location_type`, `open_data_channel_type` |
| `due_at`, `resolution_updated_at` | TIMESTAMP | Source `due_date`, `resolution_action_updated_date` (post-creation info: **not** usable as M2 features) |
| `hours_to_close` | DOUBLE | `(closed_at − created_at)` in hours, only for valid closures (never negative, DQ026) |
| `dq_*` | BOOLEAN | Quality flags (see QUALITY_RULES.md) |
| `source_updated_at` | TIMESTAMPTZ (UTC) | Socrata `:updated_at` of the kept version |
| `ingest_run_id`, `raw_file` | VARCHAR | Lineage: extraction run and raw page that supplied this version |
| `snapshot_at_local` | TIMESTAMP | Build time in NYC local time |

## Other tables

| Table | Grain | Purpose |
|---|---|---|
| `rejected_records` | one row per quarantined record | `unique_key`, `created_date`, `complaint_type`, `reject_reason`, lineage |
| `dim_complaint_category` | complaint type | category key/label, agencies observed, request count |
| `dim_geography` | borough × community board | `geo_key`, counts, geocoded count |
| `agg_daily_requests` | created_date × category × borough × agency × status_group | `request_count`, `closed_count` (additive; used for time series) |
| `agg_resolution_metrics` | created_date × category × borough | counts, open count, median/p90 hours to close, closed within 1 d / 7 d |
| `data_quality_runs/build=<id>.parquet` | build × rule | rule results history |
| `model_predictions`, `model_evaluations` | — | **Explicit schemas, intentionally empty in Milestone 1** (populated in Milestone 2) |

## Metric definitions (implemented in `packages/analytics/civicpulse_analytics/service.py`)

| Metric | Definition |
|---|---|
| Total requests | Count of requests with `created_date` in [start, end] (inclusive) matching all filters |
| Change vs prior period | (current − prior) / prior, where prior is the equal-length window ending the day before `start`. **NULL** if the prior window starts before the loaded data, or if prior = 0. |
| Open / closed / unknown | Count by `status_group` (status as of extraction, not as of the period end) |
| Median time to recorded closure | Median `hours_to_close` over valid closures. Excludes open requests, so it is **biased low under censoring**. |
| Not closed within N days | Denominator: requests created ≥ N days before the snapshot, excluding closure-quality flags. Numerator: those with no recorded closure, or a closure after N days. |
| Data freshness | Latest `created_at` in the snapshot, the build time, and the source `rowsUpdatedAt`. Always labelled "not live". |
| Last complete date | Latest `created_date` whose data reaches 23:00. The dashboard's default 28-day window ends here so a truncated final day isn't shown as a drop. |
| Categories with unusual increases | **Not computed** in Milestone 1 (needs anomaly detection) |
