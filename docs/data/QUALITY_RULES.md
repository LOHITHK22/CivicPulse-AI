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

| Rule | Check | Severity | Threshold | Result, build `20261104T062305Z-be6cd7` (773,152 rows) |
|---|---|---|---|---|
| DQ001 | `unique_key` unique in fact | invariant | 0 | pass (0) |
| DQ002 | Raw rows superseded by a newer version (dedup) | info | — | 213,469 of 986,621 raw rows (from the incremental re-fetch) |
| DQ003 | Duplicate `unique_key` within one source page | flag | 0 % | pass (0) |
| DQ010 | `unique_key` present | reject | 0.5 % | pass (0) |
| DQ011 | `created_date` parses | reject | 0.5 % | pass (0) |
| DQ012 | `complaint_type` is a configured category | reject | 0.5 % | pass (0) |
| DQ013 | `created_date` ≥ configured window start | reject | 0.5 % | pass (0) |
| DQ014 | `created_date` not in the future | reject | 0.5 % | pass (0) |
| DQ020 | `closed_date` ≥ `created_date` | flag | 0.1 % | pass (41 = 0.005 %) |
| DQ021 | `closed_date` not in the future | flag | 0 % | pass (0) |
| DQ022 | Closed status has a `closed_date` | flag | 0.5 % | pass (3) |
| DQ023 | Borough is one of the five | flag | 0.1 % | pass (9) |
| DQ024 | Coordinates present and inside the NYC bounding box (lat 40.47–40.93, lon −74.27 to −73.68) | flag | 2 % | pass (641 = 0.083 %) |
| DQ025 | ZIP is 5 digits | flag | 2 % | pass (24) |
| DQ026 | `hours_to_close` never negative | invariant | 0 | pass |
| DQ030–038 | Missingness of `closed_at`, `descriptor`, `agency`, `community_board`, `incident_zip`, `latitude`, `channel`, `location_type`, `council_district` | info | — | `closed_at` 2.16 % (open requests), `incident_zip` 0.003 %, `latitude` 0.083 %, `council_district` 0.083 %, others 0 |
| DQ040 | Latest `created_at` within 72 h of build | flag | — | pass (71.3 h at build time) |
| DQ041 | Source `rowsUpdatedAt` within 48 h of extraction check | flag | — | **warn**: the source had not updated for 51.7 h (genuine upstream staleness, not a pipeline fault) |
| DQ050_* | Each configured category has records in the latest 7 days | flag | — | pass (noise 8,967; heat 6,186) |
| DQ051 | Descriptor values unchanged since previous build | flag | — | pass |
| DQ060 | Source schema matches the committed snapshot | flag | — | pass (no drift) |

Extraction-time checks (recorded on the run, not as build rules):
- Selected fields missing at source **abort** the run before any data is fetched.
- Added, removed or retyped source columns are recorded as `schema_warnings`.
- Pages with missing required fields or unexpected keys are recorded as `page_warnings`.
