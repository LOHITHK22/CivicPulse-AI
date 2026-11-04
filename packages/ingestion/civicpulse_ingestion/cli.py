"""Command line entry point: ``civicpulse <command>`` (or ``python -m civicpulse_ingestion.cli``)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

import duckdb
import polars as pl

from civicpulse_ingestion.extract import list_runs, read_watermark, run_extraction
from civicpulse_ingestion.socrata import SocrataClient
from civicpulse_ingestion.transform import TABLES, build_warehouse
from civicpulse_shared.config import REPO_ROOT, get_settings, load_ingestion_config
from civicpulse_shared.logging import configure_logging
from civicpulse_shared.paths import DataPaths

SAMPLE_PATH = REPO_ROOT / "data" / "samples" / "nyc311_raw_sample.parquet"


def _paths(args: argparse.Namespace) -> DataPaths:
    return DataPaths(Path(args.data_dir) if args.data_dir else get_settings().data_dir)


def cmd_extract(args: argparse.Namespace) -> int:
    cfg = load_ingestion_config(Path(args.config) if args.config else None)
    paths = _paths(args)
    with SocrataClient(cfg.source, cfg.http, app_token=get_settings().socrata_app_token) as client:
        state = run_extraction(
            cfg,
            paths,
            client,
            mode=args.mode,
            start=dt.date.fromisoformat(args.start) if args.start else None,
            end=dt.date.fromisoformat(args.end) if args.end else None,
            resume=not args.no_resume,
            max_pages=args.max_pages,
        )
    print(
        json.dumps(
            {k: v for k, v in asdict(state).items() if k != "page_warnings"}
            | {"page_warning_count": len(state.page_warnings)},
            indent=2,
        )
    )
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    cfg = load_ingestion_config(Path(args.config) if args.config else None)
    s = build_warehouse(cfg, _paths(args))
    print(json.dumps({k: v for k, v in s.__dict__.items() if k not in ("quality", "descriptor_values")}, indent=2))
    bad = [q for q in s.quality if q["status"] in ("warn", "fail")]
    for q in bad:
        print(
            f"  [{q['status'].upper()}] {q['rule_id']} {q['description']}: "
            f"{q['records_failed']}/{q['records_checked']}",
            file=sys.stderr,
        )
    return 1 if any(q["status"] == "fail" for q in s.quality) else 0


def cmd_status(args: argparse.Namespace) -> int:
    paths = _paths(args)
    wm = read_watermark(paths)
    runs = [
        {
            "run_id": r.run_id,
            "mode": r.mode,
            "status": r.status,
            "rows": r.rows_fetched,
            "pages": r.pages_written,
            "started_at": r.started_at,
            "error": r.error,
        }
        for r in list_runs(paths)
    ]
    print(json.dumps({"watermark": wm.isoformat() if wm else None, "runs": runs}, indent=2))
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    """Create the small committed sample from local raw pages (stratified, coordinates coarsened)."""
    paths = _paths(args)
    files = [p.as_posix() for p in sorted(paths.raw.rglob("*.parquet"))]
    if not files:
        print("no raw pages; run `civicpulse extract` first", file=sys.stderr)
        return 2
    df = pl.read_parquet(files)
    # Deterministic stratified sample: per (complaint_type, borough, month) take up to N rows,
    # chosen by a hash of unique_key so the selection is reproducible.
    df = df.with_columns(
        pl.col("created_date").str.slice(0, 7).alias("_m"),
        pl.col("unique_key").hash(seed=42).alias("_h"),
    )
    sample = (
        df.sort("_h")
        .group_by(["complaint_type", "borough", "_m"], maintain_order=True)
        .head(args.per_stratum)
        .drop(["_m", "_h"])
        .sort("unique_key")
    )
    # Coarsen coordinates to 3 decimals (~100 m) in the committed sample.
    sample = sample.with_columns(
        pl.col("latitude").cast(pl.Float64, strict=False).round(3).cast(pl.Utf8),
        pl.col("longitude").cast(pl.Float64, strict=False).round(3).cast(pl.Utf8),
    )
    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    sample.write_parquet(SAMPLE_PATH, compression="zstd")
    print(f"wrote {sample.height} rows to {SAMPLE_PATH}")
    return 0


def cmd_duckdb(args: argparse.Namespace) -> int:
    """Create a DuckDB file with views over the Parquet tables (for ad-hoc SQL exploration)."""
    paths = _paths(args)
    target = paths.root / "warehouse" / "civicpulse.duckdb"
    target.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(target)) as con:
        for t in TABLES:
            con.execute(f"CREATE OR REPLACE VIEW {t} AS SELECT * FROM read_parquet('{paths.table(t).as_posix()}')")
        dq = (paths.processed / "data_quality_runs" / "*.parquet").as_posix()
        con.execute(f"CREATE OR REPLACE VIEW data_quality_runs AS SELECT * FROM read_parquet('{dq}')")
    print(f"wrote {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging(get_settings().log_level)
    p = argparse.ArgumentParser(prog="civicpulse", description="CivicPulse AI data pipeline")
    p.add_argument("--data-dir", help="override CIVICPULSE_DATA_DIR")
    p.add_argument("--config", help="ingestion YAML (default configs/ingestion.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="extract NYC 311 pages into data/raw")
    e.add_argument("--mode", choices=["backfill", "incremental"], default="backfill")
    e.add_argument("--start", help="created_date start (YYYY-MM-DD), default from config")
    e.add_argument("--end", help="created_date end, exclusive (backfill only)")
    e.add_argument("--max-pages", type=int, help="stop after N pages (run is left 'paused')")
    e.add_argument("--no-resume", action="store_true", help="start a new run even if one is resumable")
    e.set_defaults(func=cmd_extract)

    sub.add_parser("build", help="rebuild warehouse tables + quality report").set_defaults(func=cmd_build)
    sub.add_parser("status", help="show runs and watermark").set_defaults(func=cmd_status)
    s = sub.add_parser("sample", help="write the committed real-data sample from local raw pages")
    s.add_argument("--per-stratum", type=int, default=8)
    s.set_defaults(func=cmd_sample)
    sub.add_parser("duckdb", help="create data/warehouse/civicpulse.duckdb views").set_defaults(func=cmd_duckdb)

    args = p.parse_args(argv)
    rc: int = args.func(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
