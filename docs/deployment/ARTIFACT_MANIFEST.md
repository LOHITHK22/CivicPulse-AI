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

## Current snapshot (packed 2026-11-06 from the local full warehouse)

| Field | Value |
|---|---|
| snapshot_id | `20261104T194022Z-5da243__20261104T195715Z-27ea6f` |
| archive | `civicpulse-snapshot-20261104T194022Z-5da243__20261104T195715Z-27ea6f.tar.gz` |
| archive_bytes | 85,702,881 |
| archive_sha256 | `9dc8ef01ecfbefd02b5dffc9139db4f9eda1c6d3f81829ea0178404746b1c9f3` |
| files / unpacked | 39 files / 89,631,678 bytes, each with SHA-256 in the embedded `ARTIFACT_MANIFEST.json` |
| fact_rows | 2,511,293 |
| dataset range | 2023-01-01 00:00:42 → **2026-11-01 02:04:40** (cutoff; snapshot, not live) |
| models | forecast, delay_risk, anomaly: all `*-20261104T195715Z-27ea6f` |

The archive is reproducible: the same inputs produce the same bytes and the same SHA-256 (tested). It is **not**
published anywhere yet; uploading it needs your approval.

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
