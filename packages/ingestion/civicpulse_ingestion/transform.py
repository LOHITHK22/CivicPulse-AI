"""Warehouse build: raw Parquet pages -> cleaned, typed DuckDB/Parquet analytical tables.

The build is a pure function of (all raw pages, ingestion config, snapshot time), so it is
deterministic and idempotent: rebuilding from the same raw pages yields identical tables.

Time semantics: Socrata ``created_date``/``closed_date`` are *floating* timestamps (no zone).
They represent NYC local wall-clock time and are stored as naive ``TIMESTAMP`` values.
``snapshot_at_local`` is the build time converted to America/New_York for comparisons.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from civicpulse_ingestion.quality import QualityResult, run_quality_checks
from civicpulse_shared.config import IngestionConfig
from civicpulse_shared.logging import log
from civicpulse_shared.paths import DataPaths, atomic_write_json, read_json

logger = logging.getLogger(__name__)
NYC = ZoneInfo("America/New_York")

VALID_BOROUGHS = ("BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND")
# Generous NYC bounding box (WGS84). Points outside are treated as invalid coordinates.
NYC_BBOX = {"lat_min": 40.47, "lat_max": 40.93, "lon_min": -74.27, "lon_max": -73.68}
OPEN_STATUSES = ("Open", "In Progress", "Assigned", "Pending", "Started")

TABLES = (
    "fact_service_requests",
    "rejected_records",
    "dim_geography",
    "dim_complaint_category",
    "agg_daily_requests",
    "agg_resolution_metrics",
    "model_predictions",
    "model_evaluations",
)


@dataclass
class BuildSummary:
    build_id: str
    built_at_utc: str
    snapshot_at_local: str
    raw_files: int
    raw_rows: int
    distinct_keys: int
    duplicate_raw_rows: int
    accepted_rows: int
    rejected_rows: int
    min_created_at: str | None
    max_created_at: str | None
    source_rows_updated_at: str | None
    quality: list[dict[str, Any]] = field(default_factory=list)
    descriptor_values: list[str] = field(default_factory=list)


def _category_values_sql(cfg: IngestionConfig) -> tuple[str, list[Any]]:
    rows, params = [], []
    for c in cfg.categories:
        for t in c.complaint_types:
            rows.append("(?, ?, ?)")
            params += [t, c.key, c.label]
    return "VALUES " + ", ".join(rows), params


def build_warehouse(
    cfg: IngestionConfig,
    paths: DataPaths,
    *,
    snapshot_at: dt.datetime | None = None,
) -> BuildSummary:
    """Rebuild every analytical table from all raw pages. Writes Parquet atomically."""
    paths.ensure()
    snapshot_utc = (snapshot_at or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    snapshot_local = snapshot_utc.astimezone(NYC).replace(tzinfo=None)
    raw_files: list[Path] = sorted(paths.raw.rglob("*.parquet"))
    if not raw_files:
        raise FileNotFoundError(f"no raw pages found under {paths.raw}; run extraction first")

    build_id = snapshot_utc.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET TimeZone='UTC'")
        file_list = [f.as_posix() for f in raw_files]
        con.execute(
            "CREATE TABLE raw AS SELECT * EXCLUDE (filename), "
            r"regexp_extract(replace(filename, '\', '/'), 'run=[^/]+/page-[0-9]+\.parquet') AS _raw_file "
            "FROM read_parquet(?, union_by_name=true, filename=true)",
            [file_list],
        )
        values_sql, params = _category_values_sql(cfg)
        con.execute(
            f"CREATE TABLE cat_map(complaint_type VARCHAR, category_key VARCHAR, label VARCHAR); "
            f"INSERT INTO cat_map {values_sql}",
            params,
        )

        # 1) Deduplicate: keep the latest version of each unique_key (by source :updated_at,
        #    then fetch time, then file name for a total, deterministic order).
        con.execute("""
            CREATE TABLE dedup AS
            SELECT * EXCLUDE (rn) FROM (
              SELECT *, row_number() OVER (
                PARTITION BY coalesce(unique_key, '__missing__' || _raw_file || _sys_id)
                ORDER BY TRY_CAST(_sys_updated_at AS TIMESTAMPTZ) DESC NULLS LAST,
                         _fetched_at DESC, _raw_file DESC, _sys_id DESC) AS rn
              FROM raw)
            WHERE rn = 1
        """)

        # 2) Type + classify.
        con.execute("""
            CREATE TABLE typed AS
            SELECT
              d.*,
              TRY_CAST(d.created_date AS TIMESTAMP) AS created_ts,
              TRY_CAST(d.closed_date AS TIMESTAMP) AS closed_ts,
              TRY_CAST(d.due_date AS TIMESTAMP) AS due_ts,
              TRY_CAST(d.resolution_action_updated_date AS TIMESTAMP) AS resolution_ts,
              TRY_CAST(d._sys_updated_at AS TIMESTAMPTZ) AS source_updated_at,
              TRY_CAST(d.latitude AS DOUBLE) AS lat_raw,
              TRY_CAST(d.longitude AS DOUBLE) AS lon_raw,
              m.category_key, m.label AS category_label
            FROM dedup d LEFT JOIN cat_map m USING (complaint_type)
        """)
        window_start = dt.datetime.combine(cfg.backfill.start, dt.time())
        con.execute(
            """
            CREATE TABLE classified AS
            SELECT *,
              CASE
                WHEN unique_key IS NULL OR trim(unique_key) = '' THEN 'missing_unique_key'
                WHEN created_ts IS NULL THEN 'invalid_created_date'
                WHEN category_key IS NULL THEN 'category_not_configured'
                WHEN created_ts < ?::TIMESTAMP THEN 'created_before_window'
                WHEN created_ts > ?::TIMESTAMP + INTERVAL 1 DAY THEN 'created_in_future'
                ELSE NULL
              END AS reject_reason
            FROM typed
        """,
            [window_start, snapshot_local],
        )

        con.execute("""
            CREATE TABLE rejected_records AS
            SELECT unique_key, created_date, complaint_type, reject_reason,
                   _ingest_run_id AS ingest_run_id, _raw_file AS raw_file
            FROM classified WHERE reject_reason IS NOT NULL
        """)

        boroughs = ", ".join(f"'{b}'" for b in VALID_BOROUGHS)
        opens = ", ".join(f"'{s}'" for s in OPEN_STATUSES)
        b = NYC_BBOX
        con.execute(
            f"""
            CREATE TABLE fact_service_requests AS
            WITH base AS (
              SELECT *,
                (closed_ts IS NOT NULL AND closed_ts < created_ts) AS dq_closed_before_created,
                (closed_ts IS NOT NULL AND closed_ts > ?::TIMESTAMP + INTERVAL 1 DAY) AS dq_closed_in_future,
                (upper(trim(coalesce(borough, ''))) NOT IN ({boroughs})) AS dq_invalid_borough,
                (lat_raw IS NULL OR lon_raw IS NULL
                  OR lat_raw NOT BETWEEN {b["lat_min"]} AND {b["lat_max"]}
                  OR lon_raw NOT BETWEEN {b["lon_min"]} AND {b["lon_max"]}) AS dq_invalid_coordinates,
                (incident_zip IS NULL OR NOT regexp_full_match(incident_zip, '\\d{{5}}')) AS dq_missing_zip,
                (status = 'Closed' AND closed_ts IS NULL) AS dq_closed_status_without_date
              FROM classified WHERE reject_reason IS NULL
            )
            SELECT
              unique_key,
              created_ts AS created_at,
              CASE WHEN dq_closed_before_created OR dq_closed_in_future THEN NULL ELSE closed_ts END AS closed_at,
              CAST(created_ts AS DATE) AS created_date,
              CAST(hour(created_ts) AS TINYINT) AS created_hour,
              CAST(isodow(created_ts) AS TINYINT) AS created_dow,
              CAST(month(created_ts) AS TINYINT) AS created_month,
              category_key, category_label, complaint_type, descriptor,
              agency, agency_name,
              coalesce(status, 'Unspecified') AS status,
              CASE WHEN status = 'Closed' THEN 'closed'
                   WHEN status IN ({opens}) THEN 'open'
                   ELSE 'unknown' END AS status_group,
              CASE WHEN dq_invalid_borough THEN 'UNSPECIFIED' ELSE upper(trim(borough)) END AS borough,
              community_board, council_district, police_precinct,
              CASE WHEN dq_missing_zip THEN NULL ELSE incident_zip END AS incident_zip,
              CASE WHEN dq_invalid_coordinates THEN NULL ELSE lat_raw END AS latitude,
              CASE WHEN dq_invalid_coordinates THEN NULL ELSE lon_raw END AS longitude,
              location_type, open_data_channel_type AS channel,
              due_ts AS due_at, resolution_ts AS resolution_updated_at,
              CASE WHEN closed_ts IS NOT NULL AND NOT dq_closed_before_created AND NOT dq_closed_in_future
                   THEN date_diff('second', created_ts, closed_ts) / 3600.0 END AS hours_to_close,
              dq_closed_before_created, dq_closed_in_future, dq_invalid_borough,
              dq_invalid_coordinates, dq_missing_zip, dq_closed_status_without_date,
              source_updated_at,
              _ingest_run_id AS ingest_run_id,
              _raw_file AS raw_file,
              ?::TIMESTAMP AS snapshot_at_local
            FROM base
            ORDER BY created_ts, unique_key
        """,
            [snapshot_local, snapshot_local],
        )

        _build_dims_and_aggs(con, cfg)
        _create_model_tables(con)

        counts = con.execute("""
            SELECT (SELECT count(*) FROM raw), (SELECT count(*) FROM dedup),
                   (SELECT count(*) FROM fact_service_requests), (SELECT count(*) FROM rejected_records),
                   (SELECT min(created_at)::VARCHAR FROM fact_service_requests),
                   (SELECT max(created_at)::VARCHAR FROM fact_service_requests)
        """).fetchone()
        assert counts is not None
        raw_rows, distinct_keys, accepted, rejected, min_c, max_c = counts

        previous = _previous_summary(paths)
        source_meta = read_json(paths.source_metadata_file) if paths.source_metadata_file.exists() else {}
        quality: list[QualityResult] = run_quality_checks(
            con,
            cfg,
            snapshot_local=snapshot_local,
            previous_descriptors=set(previous.get("descriptor_values", [])) if previous else None,
            source_meta=source_meta,
        )
        descriptors = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT descriptor FROM fact_service_requests WHERE descriptor IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]

        summary = BuildSummary(
            build_id=build_id,
            built_at_utc=snapshot_utc.isoformat(),
            snapshot_at_local=snapshot_local.isoformat(),
            raw_files=len(raw_files),
            raw_rows=int(raw_rows),
            distinct_keys=int(distinct_keys),
            duplicate_raw_rows=int(raw_rows) - int(distinct_keys),
            accepted_rows=int(accepted),
            rejected_rows=int(rejected),
            min_created_at=min_c,
            max_created_at=max_c,
            source_rows_updated_at=source_meta.get("rows_updated_at"),
            quality=[q.to_dict() for q in quality],
            descriptor_values=descriptors,
        )
        _write_quality_run(con, paths, summary)
        _export_tables(con, paths)
        atomic_write_json(paths.state / "last_build.json", summary.__dict__)
        log(
            logger,
            logging.INFO,
            "warehouse built",
            build_id=build_id,
            accepted=accepted,
            rejected=rejected,
            raw_rows=raw_rows,
        )
        return summary
    finally:
        con.close()


def _build_dims_and_aggs(con: duckdb.DuckDBPyConnection, cfg: IngestionConfig) -> None:
    con.execute("""
        CREATE TABLE dim_complaint_category AS
        SELECT m.category_key, m.label AS category_label, m.complaint_type,
               (SELECT string_agg(DISTINCT agency, ',' ORDER BY agency) FROM fact_service_requests f
                 WHERE f.complaint_type = m.complaint_type) AS agencies,
               (SELECT count(*) FROM fact_service_requests f WHERE f.complaint_type = m.complaint_type)
                 AS request_count
        FROM cat_map m ORDER BY m.category_key, m.complaint_type
    """)
    con.execute("""
        CREATE TABLE dim_geography AS
        SELECT borough || '|' || coalesce(community_board, 'UNKNOWN') AS geo_key,
               borough, coalesce(community_board, 'UNKNOWN') AS community_board,
               count(*) AS request_count,
               count(latitude) AS geocoded_count
        FROM fact_service_requests
        GROUP BY ALL ORDER BY borough, community_board
    """)
    con.execute("""
        CREATE TABLE agg_daily_requests AS
        SELECT created_date, category_key, borough, agency, status_group,
               count(*) AS request_count,
               count(closed_at) AS closed_count
        FROM fact_service_requests
        GROUP BY ALL ORDER BY ALL
    """)
    con.execute("""
        CREATE TABLE agg_resolution_metrics AS
        SELECT created_date, category_key, borough,
               count(*) AS request_count,
               count(hours_to_close) AS closed_with_duration_count,
               count(*) FILTER (WHERE status_group = 'open') AS open_count,
               median(hours_to_close) AS median_hours_to_close,
               quantile_cont(hours_to_close, 0.9) AS p90_hours_to_close,
               count(*) FILTER (WHERE hours_to_close <= 24) AS closed_within_1d_count,
               count(*) FILTER (WHERE hours_to_close <= 24 * 7) AS closed_within_7d_count
        FROM fact_service_requests
        GROUP BY ALL ORDER BY ALL
    """)


def _create_model_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Explicit schemas for Milestone 2 outputs. Created EMPTY in Milestone 1 by design."""
    con.execute("""
        CREATE TABLE model_predictions (
          prediction_id VARCHAR, model_name VARCHAR, model_version VARCHAR,
          training_cutoff DATE, generated_at TIMESTAMP, target VARCHAR,
          category_key VARCHAR, borough VARCHAR, unique_key VARCHAR, target_date DATE,
          predicted_value DOUBLE, lower_bound DOUBLE, upper_bound DOUBLE, interval_level DOUBLE)
    """)
    con.execute("""
        CREATE TABLE model_evaluations (
          evaluation_id VARCHAR, model_name VARCHAR, model_version VARCHAR,
          evaluated_at TIMESTAMP, split VARCHAR, fold INTEGER,
          train_start DATE, train_end DATE, test_start DATE, test_end DATE,
          segment VARCHAR, metric VARCHAR, value DOUBLE, n_observations BIGINT)
    """)


def _write_quality_run(con: duckdb.DuckDBPyConnection, paths: DataPaths, s: BuildSummary) -> None:
    out_dir = paths.processed / "data_quality_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    con.execute("CREATE TABLE dq(rows JSON)")
    con.execute("INSERT INTO dq VALUES (?)", [json.dumps(s.quality)])
    con.execute(
        """
        CREATE TABLE data_quality_run AS
        SELECT ?::VARCHAR AS build_id, ?::TIMESTAMPTZ AS built_at_utc,
               r->>'rule_id' AS rule_id, r->>'description' AS description,
               r->>'category' AS category, r->>'severity' AS severity,
               (r->>'records_checked')::BIGINT AS records_checked,
               (r->>'records_failed')::BIGINT AS records_failed,
               (r->>'failure_rate')::DOUBLE AS failure_rate,
               (r->>'threshold')::DOUBLE AS threshold,
               r->>'status' AS status, r->>'detail' AS detail
        FROM (SELECT unnest(from_json(rows, '["JSON"]')) AS r FROM dq)
    """,
        [s.build_id, s.built_at_utc],
    )
    target = (out_dir / f"build={s.build_id}.parquet").as_posix()
    con.execute(f"COPY data_quality_run TO '{target}' (FORMAT parquet)")


def _export_tables(con: duckdb.DuckDBPyConnection, paths: DataPaths) -> None:
    """Write each table to a temp file then rename, so readers never see a partial file."""
    for name in TABLES:
        final = paths.table(name)
        tmp = final.with_suffix(".parquet.tmp")
        con.execute(f"COPY {name} TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
        try:
            tmp.replace(final)
        except PermissionError:
            # Windows: a reader holds the file open. Fall back to copy-over.
            shutil.copyfile(tmp, final)
            tmp.unlink(missing_ok=True)


def _previous_summary(paths: DataPaths) -> dict[str, Any] | None:
    f = paths.state / "last_build.json"
    if not f.exists():
        return None
    data: dict[str, Any] = read_json(f)
    return data
