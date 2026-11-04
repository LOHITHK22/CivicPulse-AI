"""Warehouse build: dedup, validation rules, rejections, flags, aggregates, determinism."""

from __future__ import annotations

import datetime as dt

import duckdb
import polars as pl

from civicpulse_ingestion.extract import run_extraction
from civicpulse_ingestion.socrata import SocrataClient
from civicpulse_ingestion.transform import build_warehouse
from conftest import FakeSocrata, make_record

SNAP = dt.datetime(2026, 3, 15, 12, 0, tzinfo=dt.UTC)


def extract(cfg, paths, records):
    fake = FakeSocrata(records)
    client = SocrataClient(cfg.source, cfg.http, transport=fake.transport, sleep=lambda s: None)
    return run_extraction(cfg, paths, client, mode="backfill")


def table(paths, name) -> pl.DataFrame:
    return pl.read_parquet(paths.table(name))


def rule(summary, rule_id):
    return next(q for q in summary.quality if q["rule_id"] == rule_id)


def test_dirty_records_are_rejected_or_flagged(cfg, paths):
    recs = [make_record(i) for i in range(10)]
    recs.append(make_record(20, unique_key=None))  # reject
    recs.append(make_record(21, created_date="not-a-date"))  # reject
    recs.append(make_record(22, complaint_type="Illegal Parking"))  # reject
    recs.append(make_record(23, created_date="2025-06-01T00:00:00.000"))  # before window
    recs.append(make_record(24, closed_date="2025-12-01T00:00:00.000"))  # closed < created
    recs.append(make_record(25, borough="Unspecified"))  # flag borough
    recs.append(make_record(26, latitude="0", longitude="0"))  # flag coords
    recs.append(make_record(27, latitude=None, longitude=None, incident_zip=None))  # flag coords+zip
    recs.append(make_record(28, closed_date=None))  # Closed w/o date
    extract(cfg, paths, recs)
    s = build_warehouse(cfg, paths, snapshot_at=SNAP)

    rej = table(paths, "rejected_records")
    assert sorted(rej["reject_reason"].to_list()) == sorted(
        ["missing_unique_key", "invalid_created_date", "category_not_configured", "created_before_window"]
    )
    fact = table(paths, "fact_service_requests")
    assert s.accepted_rows == fact.height == 15

    def row(i: int) -> dict:
        return fact.filter(pl.col("unique_key") == str(60_000_000 + i)).row(0, named=True)

    r24 = row(24)
    assert r24["dq_closed_before_created"] is True
    assert r24["closed_at"] is None and r24["hours_to_close"] is None  # no duration for invalid closure
    assert row(25)["borough"] == "UNSPECIFIED" and row(25)["dq_invalid_borough"]
    assert row(26)["dq_invalid_coordinates"] and row(26)["latitude"] is None
    assert row(27)["dq_missing_zip"] and row(27)["incident_zip"] is None
    assert row(28)["dq_closed_status_without_date"]
    assert fact["hours_to_close"].drop_nulls().min() >= 0

    assert rule(s, "DQ001")["status"] == "pass"
    assert rule(s, "DQ020")["records_failed"] == 1
    assert rule(s, "DQ023")["records_failed"] == 1
    assert rule(s, "DQ024")["records_failed"] == 2
    assert rule(s, "DQ026")["status"] == "pass"


def test_dedup_keeps_latest_version_across_runs(cfg, paths):
    recs = [make_record(i) for i in range(5)]
    extract(cfg, paths, recs)
    updated = {**recs[0], ":updated_at": "2026-02-05T00:00:00.000Z", "status": "Open"}
    updated.pop("closed_date")
    extract(cfg, paths, [updated])
    s = build_warehouse(cfg, paths, snapshot_at=SNAP)
    fact = table(paths, "fact_service_requests")
    assert fact.height == 5 and s.duplicate_raw_rows == 1
    row = fact.filter(pl.col("unique_key") == recs[0]["unique_key"]).row(0, named=True)
    assert row["status"] == "Open" and row["status_group"] == "open" and row["closed_at"] is None


def test_aggregates_reconcile_with_fact(cfg, paths):
    extract(cfg, paths, [make_record(i) for i in range(40)])
    build_warehouse(cfg, paths, snapshot_at=SNAP)
    con = duckdb.connect()
    f, a, r = (
        paths.table(n).as_posix() for n in ("fact_service_requests", "agg_daily_requests", "agg_resolution_metrics")
    )
    assert (
        con.execute(f"SELECT count(*) FROM '{f}'").fetchone()[0]
        == con.execute(f"SELECT sum(request_count) FROM '{a}'").fetchone()[0]
        == con.execute(f"SELECT sum(request_count) FROM '{r}'").fetchone()[0]
        == 40
    )
    by_day = con.execute(f"""
        SELECT count(*) FROM (SELECT created_date, count(*) n FROM '{f}' GROUP BY 1) x
        JOIN (SELECT created_date, sum(request_count) n FROM '{a}' GROUP BY 1) y USING (created_date)
        WHERE x.n <> y.n""").fetchone()[0]
    assert by_day == 0


def test_rebuild_is_deterministic(cfg, paths):
    extract(cfg, paths, [make_record(i) for i in range(25)])
    build_warehouse(cfg, paths, snapshot_at=SNAP)
    first = table(paths, "fact_service_requests").drop("snapshot_at_local")
    build_warehouse(cfg, paths, snapshot_at=SNAP)
    second = table(paths, "fact_service_requests").drop("snapshot_at_local")
    assert first.equals(second)


def test_model_tables_exist_with_explicit_empty_schemas(cfg, paths):
    extract(cfg, paths, [make_record(1)])
    build_warehouse(cfg, paths, snapshot_at=SNAP)
    preds = table(paths, "model_predictions")
    evals = table(paths, "model_evaluations")
    assert preds.height == 0 and "model_version" in preds.columns
    assert evals.height == 0 and "metric" in evals.columns


def test_quality_run_history_is_appended(cfg, paths):
    extract(cfg, paths, [make_record(i) for i in range(3)])
    build_warehouse(cfg, paths, snapshot_at=SNAP)
    build_warehouse(cfg, paths, snapshot_at=SNAP + dt.timedelta(hours=1))
    runs = list((paths.processed / "data_quality_runs").glob("*.parquet"))
    assert len(runs) == 2
