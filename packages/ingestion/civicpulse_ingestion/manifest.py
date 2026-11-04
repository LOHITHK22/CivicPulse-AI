"""Extraction manifest: what was extracted, when, from which source version, and its checksums.

One entry per extraction run (each backfill chunk is its own run), plus the warehouse build that
consumed them. The manifest is derived from run-state files and the raw pages on disk, so it can be
regenerated at any time and compared with an earlier copy to prove the raw data was not changed.

"Source version": Socrata publishes no version number, so the dataset's ``rowsUpdatedAt`` at
extraction time is used as the version marker (recorded per run).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb

from civicpulse_ingestion.extract import list_runs
from civicpulse_ingestion.socrata import SocrataClient
from civicpulse_shared.paths import DataPaths, read_json


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _run_files_summary(files: list[Path]) -> dict[str, Any]:
    if not files:
        return {
            "files": 0,
            "bytes": 0,
            "rows_on_disk": 0,
            "checksum_sha256": None,
            "created_min": None,
            "created_max": None,
        }
    hashes = [f"{f.name}:{_sha256(f)}" for f in sorted(files)]
    with duckdb.connect(":memory:") as con:
        row = con.execute(
            "SELECT count(*), min(TRY_CAST(created_date AS TIMESTAMP))::VARCHAR, "
            "max(TRY_CAST(created_date AS TIMESTAMP))::VARCHAR FROM read_parquet(?, union_by_name=true)",
            [[f.as_posix() for f in files]],
        ).fetchone()
    assert row is not None
    return {
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files),
        "rows_on_disk": int(row[0]),
        # Checksum over the ordered list of "<file>:<sha256>" so any changed/added/removed page shows up.
        "checksum_sha256": hashlib.sha256("\n".join(hashes).encode()).hexdigest(),
        "created_min": row[1],
        "created_max": row[2],
    }


def build_manifest(paths: DataPaths, client: SocrataClient | None = None) -> dict[str, Any]:
    """Build the manifest. If ``client`` is given, each backfill's server-side count is re-checked."""
    runs = []
    for r in list_runs(paths):
        files = sorted((paths.raw / f"run={r.run_id}").glob("page-*.parquet"))
        entry: dict[str, Any] = {
            "run_id": r.run_id,
            "mode": r.mode,
            "status": r.status,
            "where": r.where,
            "window_start": r.params.get("start"),
            "window_end_exclusive": r.params.get("end"),
            "updated_after": r.params.get("updated_after"),
            "extracted_at_start": r.started_at,
            "extracted_at_end": r.finished_at,
            "source_version_rows_updated_at": r.source_rows_updated_at,
            "rows_fetched": r.rows_fetched,
            "pages": r.pages_written,
            **_run_files_summary(files),
        }
        entry["rows_fetched_match_disk"] = entry["rows_fetched"] == entry["rows_on_disk"]
        if client is not None and r.mode == "backfill" and r.status == "succeeded":
            server = client.count(r.where)
            entry["source_count_now"] = server
            entry["source_count_checked_at"] = dt.datetime.now(dt.UTC).isoformat()
            entry["source_count_matches"] = server == r.rows_fetched
        runs.append(entry)

    build = read_json(paths.state / "last_build.json") if (paths.state / "last_build.json").exists() else {}
    fact = paths.table("fact_service_requests")
    backfills = [r for r in runs if r["mode"] == "backfill" and r["status"] == "succeeded"]
    windows = sorted((r["window_start"], r["window_end_exclusive"]) for r in backfills)
    gaps = [{"from": a[1], "to": b[0]} for a, b in pairwise(windows) if a[1] is not None and a[1] < b[0]]
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "source_metadata": read_json(paths.state / "source_metadata.json")
        if (paths.state / "source_metadata.json").exists()
        else None,
        "runs": runs,
        "backfill_coverage": {
            "windows": [{"start": a, "end_exclusive": b} for a, b in windows],
            "start": windows[0][0] if windows else None,
            "end_exclusive": max(b for _, b in windows) if windows else None,
            "gaps_between_windows": gaps,
            "overlapping_windows": any(a[1] is not None and a[1] > b[0] for a, b in pairwise(windows)),
        },
        "totals": {
            "raw_rows": sum(r["rows_on_disk"] for r in runs),
            "raw_files": sum(r["files"] for r in runs),
            "raw_bytes": sum(r["bytes"] for r in runs),
        },
        "warehouse": {
            "build_id": build.get("build_id"),
            "built_at_utc": build.get("built_at_utc"),
            "accepted_rows": build.get("accepted_rows"),
            "rejected_rows": build.get("rejected_rows"),
            "duplicate_raw_rows": build.get("duplicate_raw_rows"),
            "min_created_at": build.get("min_created_at"),
            "max_created_at": build.get("max_created_at"),
            "source_rows_updated_at": build.get("source_rows_updated_at"),
            "fact_sha256": _sha256(fact) if fact.exists() else None,
        },
    }
