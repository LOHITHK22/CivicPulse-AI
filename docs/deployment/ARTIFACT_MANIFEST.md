# Data and model artifacts (Milestone 5)

The repository excludes the warehouse and the trained models. Deployments get them as one immutable
**snapshot archive** built and verified by `civicpulse_shared.artifacts`.

## Contents (what the API and MCP server read)

| Part | Path in snapshot | Why |
|---|---|---|
| Warehouse | `processed/*.parquet`, `processed/data_quality_runs/*.parquet` | analytics, geo, explorer, quality |
| Build state | `state/last_build.json`, `watermark.json`, `source_metadata.json`, `manifest.json`, `runs/*.json` | readiness, freshness, quality history |
| Model pointer | `models/current.json` | selects the active run |
| Active model run | `models/runs/<run_id>/**` | forecasts, backtests, delay evaluation (research-only), anomalies, evaluations |

Excluded: `raw/` ingestion pages (111 MB; not read at runtime) and inactive model runs. Data and models travel
together: the snapshot id is `<build_id>__<model_run_id>`.

## Current snapshot (re-packed 2026-11-06 with redaction and notice; supersedes the first 85.7 MB pack)

| Field | Value |
|---|---|
| snapshot_id | `20261104T194022Z-5da243__20261104T195715Z-27ea6f` |
| archive | `civicpulse-snapshot-20261104T194022Z-5da243__20261104T195715Z-27ea6f.tar.gz` |
| archive_bytes | 68,952,849 |
| archive_sha256 | `b18623fa88e4a327ddab90e9c9f5893e7aeebb545f6de139be451c7254c88238` (two independent packs gave identical bytes) |
| files / unpacked | 40 files / 72,880,142 bytes, each with SHA-256 in the embedded `ARTIFACT_MANIFEST.json`, plus `DATA_NOTICE.md` |
| fact_rows | 2,511,293 |
| dataset range | 2023-01-01 00:00:42 → **2026-11-01 02:04:40** (cutoff; snapshot, not live) |
| models | forecast, delay_risk, anomaly: all `*-20261104T195715Z-27ea6f` |
| model ↔ data compatibility | model run manifest `data.build_id` = `20261104T194022Z-5da243` = warehouse build; `data.fact_sha256` = `5dd5799e…` = `redactions[0].source_sha256` (the models were trained on exactly this warehouse before redaction) |

The archive is **not published** anywhere. Uploading it needs your approval.

## Content audit (2026-11-06)

| Check | Result |
|---|---|
| Secrets | gitleaks over the extracted snapshot: no leaks. JSON state contains no tokens, e-mail addresses, user names or local paths. `raw_file` holds only `run=<id>/page-NNNNN.parquet`. |
| Personal data | The source has no names, phone numbers or e-mail addresses. The ingestion never stored the incident address, street or BBL. |
| Precise locations | `latitude`/`longitude` (building-level, 2.5 M residential complaints) were **in** the first pack. Nothing at runtime reads them, so they are now **removed** at pack time (`REDACTED_COLUMNS`). The remaining location fields are ZIP, community board, council district, precinct and borough. |
| Free text | `descriptor` and `location_type` take 8 and 4 fixed city-defined values respectively; no resolution text or comments are stored. |
| Prohibited/confidential data | None found. All rows derive from the public NYC Open Data release. Model outputs are aggregates or scores keyed by the public `unique_key`. |
| Runtime after redaction | Health (2,511,293 rows), all 14 data endpoints, request detail, CSV export and an MCP investigation (`compare_boroughs`, 11 validated claims) all answer correctly on the restored redacted snapshot. |

## Licence and attribution

* **Terms.** The 311 data set (`erm2-nwe9`) carries no licence field in its portal metadata (`license: null`,
  `attribution: "311"`, provenance official). Its terms come from NYC's Open Data Law. NYC Admin Code § 23-502(d)
  requires public data sets to be available without registration, licence requirements or restrictions on use.
  The City *may* require anyone republishing a data set to identify its source and version and describe any
  modifications. Use is also subject to the NYC.gov Terms of Use, which provide the data as is.
* **How we comply.** Every archive embeds `DATA_NOTICE.md`. It names the source data set and publisher, the
  version (source update time, snapshot time and record range), every modification (category filter, window,
  deduplication, derived columns, removed columns) and a no-warranty/no-endorsement statement. The UI already
  attributes NYC Open Data `erm2-nwe9` (sidebar and About page) and DCP `gthc-hcne` (Geo explorer).
* Not legal advice. Before a public launch, re-read the current NYC.gov Terms of Use page. Its text could not
  be retrieved automatically.

## Workflow

```bash
# 1. Package (local; writes the .tar.gz and a sidecar .manifest.json)
python -m civicpulse_shared.artifacts pack --data-dir data --out dist/artifacts
# 2. Publish (after approval), e.g. a GitHub Release asset:
#    gh release create snapshot-<build_id> dist/artifacts/civicpulse-snapshot-*.tar.gz dist/artifacts/*.manifest.json
# 3. Pin in the deployment: CIVICPULSE_ARTIFACT_URL=<https url>, CIVICPULSE_ARTIFACT_SHA256=<archive_sha256>
# 4. The container restores at start (services/api/start.py); manual equivalent:
python -m civicpulse_shared.artifacts restore --source <url|path> --sha256 <hex> --root /app/artifacts
python -m civicpulse_shared.artifacts verify --dir /app/artifacts/snapshots/<snapshot_id>
```

## Guarantees (each covered by `tests/test_artifacts.py` or `services/api/tests/test_start.py`)

* **Integrity.** The archive SHA-256 must equal the pinned value, and every file must match its manifest
  SHA-256. Otherwise nothing is installed and the container exits with code 2.
* **Safety.** HTTPS only; size cap of 2 GiB. Entries with absolute paths, `..`, links or names not in the
  manifest are refused. Download errors report the URL without its query string, since pre-signed URLs carry
  credentials there.
* **No partial activation.** The snapshot is extracted to `<id>.partial`, deep-verified, then renamed into
  place. A failed verification deletes the partial directory. An installed snapshot directory is never modified.
* **No repeated downloads.** A marker maps the archive SHA-256 to the installed snapshot. Restarts reuse it after
  a size check (0.0 s).
* **Fail clearly.** With `CIVICPULSE_REQUIRE_ARTIFACT=true`, a missing URL, a missing pin, a mismatch or an
  incomplete snapshot stops the container with a JSON `startup_failed` log line. It never falls back silently
  to the sample.
* **Secrets.** `CIVICPULSE_ARTIFACT_TOKEN` (private URLs only) is removed from the environment before uvicorn
  starts, so neither the API nor the MCP child can read it.

A new snapshot reaches users only by a deliberate redeploy with a new URL and pin. Rollback is the same operation
with the previous pin (`ROLLBACK.md`).
