# Data-quality rules

Evaluated on every `civicpulse build` (`packages/ingestion/civicpulse_ingestion/quality.py`).
Results are stored per build in `data/processed/data_quality_runs/` and shown on the Data Quality
page.

**Severity**
- `invariant`: must never fail.
- `reject`: the record is quarantined in `rejected_records`.
- `flag`: the record is kept, a `dq_*` column is set, and the bad value is excluded from the
  affected measure.
- `info`: measured only.

**Status:** `pass`, `warn` (rate above threshold), `fail` (invariant broken), or `info`.

| Rule | Check | Severity | Threshold | Result, build `20261104T194022Z-5da243` (2,511,293 rows, 2023-01-01 → 2026-11-01) |
|---|---|---|---|---|
| DQ001 | `unique_key` unique in fact | invariant | 0 | pass (0) |
| DQ002 | Raw rows superseded by a newer version (dedup) | info | — | 463,755 of 2,975,048 raw rows (M1 windows plus the M2 backfill and incremental re-fetches) |
| DQ003 | Duplicate `unique_key` within one source page | flag | 0 % | pass (0) |
| DQ010 | `unique_key` present | reject | 0.5 % | pass (0) |
| DQ011 | `created_date` parses | reject | 0.5 % | pass (0) |
| DQ012 | `complaint_type` is a configured category | reject | 0.5 % | pass (0) |
| DQ013 | `created_date` ≥ configured window start | reject | 0.5 % | pass (0) |
| DQ014 | `created_date` not in the future | reject | 0.5 % | pass (0) |
| DQ020 | `closed_date` ≥ `created_date` | flag | 0.1 % | pass (126 = 0.005 %) |
| DQ021 | `closed_date` not in the future | flag | 0 % | pass (0) |
| DQ022 | Closed status has a `closed_date` | flag | 0.5 % | pass (3) |
| DQ023 | Borough is one of the five | flag | 0.1 % | pass (297) |
| DQ024 | Coordinates present and inside the NYC bounding box (lat 40.47–40.93, lon −74.27 to −73.68) | flag | 2 % | pass (3,096 = 0.12 %) |
| DQ025 | ZIP is 5 digits | flag | 2 % | pass (404) |
| DQ026 | `hours_to_close` never negative | invariant | 0 | pass |
| DQ030–038 | Missingness of `closed_at`, `descriptor`, `agency`, `community_board`, `incident_zip`, `latitude`, `channel`, `location_type`, `council_district` | info | — | `closed_at` 21,343 (0.85 %, open requests), `incident_zip` 404, `latitude` 3,096, `location_type` 18, `council_district` 3,300, others 0 |
| DQ040 | Latest `created_at` within 72 h of build | flag | — | **warn**: 84.6 h. The newest request is 2026-11-01 02:04 and the source has published nothing newer (see DQ042). |
| DQ041 | Source `rowsUpdatedAt` within 48 h of extraction check | flag | — | pass (2.1 h). The metadata timestamp moved, but no newer rows arrived. |
| DQ042 | Newest record version (`:updated_at`) within 48 h of extraction check | flag | — | **warn**: newest row version is 2026-11-02T02:38:25Z, 64.6 h old. This is genuine upstream staleness, not a pipeline fault. |
| DQ050_* | Each configured category has records in the latest 7 days | flag | — | pass (noise 8,967; heat 6,186) |
| DQ051 | Descriptor values unchanged since previous build | flag | — | pass |
| DQ060 | Source schema matches the committed snapshot | flag | — | pass (no drift) |

**Why DQ042 exists (added in Milestone 2).** On 2026-11-04 the dataset's `rowsUpdatedAt`
metadata advanced, but no rows newer than 2026-11-02 02:38 UTC were returned. DQ041 alone would
have reported the source as fresh. DQ042 checks the newest row version actually received.

Extraction-time checks (recorded on the run, not as build rules):
- Selected fields missing at source **abort** the run before any data is fetched.
- Added, removed or retyped source columns are recorded as `schema_warnings`.
- Pages with missing required fields or unexpected keys are recorded as `page_warnings`.
