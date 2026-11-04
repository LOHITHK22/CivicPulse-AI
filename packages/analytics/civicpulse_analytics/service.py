"""Analytics service: reproducible SQL metrics over the Parquet warehouse via DuckDB.

Design notes
* Read-only. DuckDB runs in-memory with views over Parquet files, so the pipeline can rebuild
  tables while the API is running (no database file lock is held).
* Every metric definition lives here, once. The API (and later the MCP server) only call this.
* Results are cached per (method, arguments, data build id); a rebuild invalidates the cache.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import duckdb

from civicpulse_analytics.filters import BOROUGHS, STATUS_GROUPS, Filters
from civicpulse_analytics.models import (
    Breakdown,
    BreakdownRow,
    Bucket,
    ClosureDistribution,
    FilterOptions,
    Freshness,
    HeatCell,
    HourlyPattern,
    Kpis,
    PeriodComparison,
    QualityReport,
    QualityRule,
    QueryMeta,
    RequestDetail,
    RequestPage,
    RequestRow,
    Series,
    SeriesPoint,
)
from civicpulse_shared.paths import DataPaths, read_json

T = TypeVar("T")

VIEWS = (
    "fact_service_requests",
    "rejected_records",
    "dim_geography",
    "dim_complaint_category",
    "agg_daily_requests",
    "agg_resolution_metrics",
    "model_predictions",
    "model_evaluations",
)

SORTABLE = {
    "created_at": "created_at",
    "closed_at": "closed_at",
    "hours_to_close": "hours_to_close",
    "borough": "borough",
    "category_key": "category_key",
    "status": "status",
    "unique_key": "TRY_CAST(unique_key AS BIGINT)",
}
CLOSURE_BUCKETS: list[tuple[str, float | None, float | None]] = [
    ("< 1 hour", 0, 1),
    ("1–4 hours", 1, 4),
    ("4–12 hours", 4, 12),
    ("12–24 hours", 12, 24),
    ("1–2 days", 24, 48),
    ("2–3 days", 48, 72),
    ("3–7 days", 72, 168),
    ("7–14 days", 168, 336),
    ("14–30 days", 336, 720),
    ("30+ days", 720, None),
]
REQUEST_COLUMNS = (
    "unique_key, created_at, closed_at, category_key, complaint_type, descriptor, agency, status, "
    "status_group, borough, community_board, incident_zip, hours_to_close"
)
EXPORT_MAX_ROWS = 10_000
PAGE_SIZE_MAX = 100

DEFINITIONS = {
    "total_requests": "Count of service requests whose created_date falls in the selected range and match all filters.",
    "comparison": "Same metric for the immediately preceding period of equal length. Null when the "
    "prior period is not fully covered by the loaded data.",
    "open_requests": "Requests whose status at snapshot time is Open/In Progress/Assigned/Pending/"
    "Started. Status reflects the source at extraction time.",
    "median_hours_to_close": "Median of (closed_date - created_date) in hours over requests with a "
    "valid recorded closure. Excludes open requests, so it is biased low "
    "when many requests remain open (right-censoring).",
    "open_beyond_threshold_pct": "Among requests old enough to be judged (created at least N days "
    "before snapshot) and without closure-quality flags: share with no "
    "recorded closure within N days.",
    "time_basis": "created_date/closed_date are NYC local wall-clock times as published by the source.",
}


class DataUnavailableError(RuntimeError):
    """The warehouse has not been built yet (or files are missing)."""


class AnalyticsService:
    def __init__(self, data_dir: Path, *, cache_size: int = 256) -> None:
        self.paths = DataPaths(Path(data_dir))
        self._con = duckdb.connect(":memory:")
        self._con.execute("SET TimeZone='UTC'")
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._cache_size = cache_size
        self._views_ready = False

    # -- infrastructure ---------------------------------------------------------------
    def _ensure_views(self) -> None:
        if self._views_ready:
            return
        with self._lock:
            if self._views_ready:
                return
            missing = [v for v in VIEWS if not self.paths.table(v).exists()]
            if missing:
                raise DataUnavailableError(
                    f"warehouse tables missing: {missing}. Run `civicpulse extract` and `civicpulse build`."
                )
            for v in VIEWS:
                self._con.execute(
                    f"CREATE OR REPLACE VIEW {v} AS SELECT * FROM read_parquet('{self.paths.table(v).as_posix()}')"
                )
            self._views_ready = True

    def build_info(self) -> dict[str, Any] | None:
        f = self.paths.state / "last_build.json"
        if not f.exists():
            return None
        data: dict[str, Any] = read_json(f)
        return data

    def _build_id(self) -> str | None:
        b = self.build_info()
        return b.get("build_id") if b else None

    def _query(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        self._ensure_views()
        cur = self._con.cursor()
        try:
            return cur.execute(sql, params or []).fetchall()
        finally:
            cur.close()

    def _query_dicts(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        self._ensure_views()
        cur = self._con.cursor()
        try:
            rel = cur.execute(sql, params or [])
            cols = [d[0] for d in rel.description]
            return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
        finally:
            cur.close()

    def _cached(self, key: str, fn: Callable[[], T]) -> T:
        full = f"{self._build_id()}::{key}"
        with self._lock:
            if full in self._cache:
                self._cache.move_to_end(full)
                return self._cache[full]  # type: ignore[no-any-return]
        value = fn()
        with self._lock:
            self._cache[full] = value
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return value

    def _meta(self, started: float, rows: int) -> QueryMeta:
        b = self.build_info() or {}
        return QueryMeta(
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            rows=rows,
            data_build_id=b.get("build_id"),
            snapshot_at_local=b.get("snapshot_at_local"),
        )

    def health(self) -> dict[str, Any]:
        b = self.build_info()
        tables = {v: self.paths.table(v).exists() for v in VIEWS}
        ok = b is not None and all(tables.values())
        count = None
        if ok:
            row = self._query("SELECT count(*) FROM fact_service_requests")
            count = int(row[0][0])
        return {"ready": ok, "tables": tables, "fact_rows": count, "build_id": b.get("build_id") if b else None}

    # -- metadata ---------------------------------------------------------------------
    def filter_options(self) -> FilterOptions:
        def compute() -> FilterOptions:
            # The newest day is "complete" only if data reaches its last hour; otherwise the snapshot
            # cut it off mid-day and it would show a misleading drop in daily charts.
            r = self._query("""
                SELECT min(created_date), max(created_date), count(*),
                       CASE WHEN max(created_at) >= max(created_date) + INTERVAL 23 HOUR
                            THEN max(created_date) ELSE max(created_date) - 1 END
                FROM fact_service_requests""")[0]
            cats = self._query_dicts("""
                SELECT category_key AS key, any_value(category_label) AS label,
                       list(DISTINCT complaint_type ORDER BY complaint_type) AS complaint_types,
                       sum(request_count)::BIGINT AS requests
                FROM dim_complaint_category GROUP BY 1 ORDER BY 1""")
            agencies = [
                a[0]
                for a in self._query(
                    "SELECT DISTINCT agency FROM fact_service_requests WHERE agency IS NOT NULL ORDER BY 1"
                )
            ]
            boroughs = [b[0] for b in self._query("SELECT DISTINCT borough FROM fact_service_requests ORDER BY 1")]
            return FilterOptions(
                min_date=r[0],
                max_date=r[1],
                last_complete_date=r[3],
                categories=cats,
                boroughs=[b for b in BOROUGHS if b in boroughs],
                agencies=agencies,
                statuses=list(STATUS_GROUPS),
                total_records=int(r[2]),
            )

        return self._cached("filter_options", compute)

    def freshness(self) -> Freshness:
        b = self.build_info()
        if not b:
            return Freshness(
                latest_created_at=None,
                snapshot_at_local=None,
                data_lag_hours=None,
                source_rows_updated_at=None,
                last_build_at_utc=None,
            )
        lag = None
        if b.get("max_created_at") and b.get("snapshot_at_local"):
            lag = (
                dt.datetime.fromisoformat(b["snapshot_at_local"]) - dt.datetime.fromisoformat(b["max_created_at"])
            ).total_seconds() / 3600
        return Freshness(
            latest_created_at=b.get("max_created_at"),
            snapshot_at_local=b.get("snapshot_at_local"),
            data_lag_hours=round(lag, 1) if lag is not None else None,
            source_rows_updated_at=b.get("source_rows_updated_at"),
            last_build_at_utc=b.get("built_at_utc"),
        )

    # -- KPIs -------------------------------------------------------------------------
    def kpis(self, f: Filters, threshold_days: int = 7) -> Kpis:
        if not 1 <= threshold_days <= 90:
            raise ValueError("threshold_days must be between 1 and 90")
        return self._cached(f"kpis:{f.cache_key()}:{threshold_days}", lambda: self._kpis(f, threshold_days))

    def _kpis(self, f: Filters, threshold_days: int) -> Kpis:
        t0 = time.perf_counter()
        where, params = f.where()
        row = self._query(
            f"""
            SELECT count(*),
                   count(*) FILTER (WHERE status_group = 'open'),
                   count(*) FILTER (WHERE status_group = 'closed'),
                   count(*) FILTER (WHERE status_group = 'unknown'),
                   median(hours_to_close),
                   count(hours_to_close)
            FROM fact_service_requests WHERE {where}""",
            params,
        )[0]
        total, n_open, n_closed, n_unknown, med, n_dur = row

        snap = self._query("SELECT max(snapshot_at_local) FROM fact_service_requests")[0][0]
        mature = self._query(
            f"""
            SELECT count(*) AS denom,
                   count(*) FILTER (WHERE closed_at IS NULL OR hours_to_close > ? * 24) AS num
            FROM fact_service_requests
            WHERE {where} AND created_at <= ?::TIMESTAMP - to_days(?)
              AND NOT dq_closed_before_created AND NOT dq_closed_in_future
              AND NOT dq_closed_status_without_date""",
            [threshold_days, *params, snap, threshold_days],
        )[0]
        denom, num = int(mature[0]), int(mature[1])

        comparison = self._comparison(f, int(total))
        return Kpis(
            total_requests=int(total),
            comparison=comparison,
            open_requests=int(n_open),
            closed_requests=int(n_closed),
            unknown_status_requests=int(n_unknown),
            median_hours_to_close=round(float(med), 2) if med is not None else None,
            closed_with_valid_duration=int(n_dur),
            open_beyond_threshold_pct=round(num / denom, 4) if denom else None,
            open_beyond_threshold_numerator=num,
            open_beyond_threshold_denominator=denom,
            threshold_days=threshold_days,
            freshness=self.freshness(),
            definitions=DEFINITIONS,
            meta=self._meta(t0, 1),
        )

    def _comparison(self, f: Filters, current_total: int) -> PeriodComparison:
        opts = self.filter_options()
        start = f.start or opts.min_date
        end = f.end or opts.max_date
        if start is None or end is None or opts.min_date is None:
            return PeriodComparison(
                current_total=current_total,
                prior_total=None,
                prior_start=None,
                prior_end=None,
                pct_change=None,
                note="no data",
            )
        length = (end - start).days + 1
        prior_end = start - dt.timedelta(days=1)
        prior_start = prior_end - dt.timedelta(days=length - 1)
        if prior_start < opts.min_date:
            return PeriodComparison(
                current_total=current_total,
                prior_total=None,
                prior_start=prior_start,
                prior_end=prior_end,
                pct_change=None,
                note=f"Prior period starts {prior_start}, before loaded data "
                f"({opts.min_date}); comparison not computed.",
            )
        prior = f.model_copy(update={"start": prior_start, "end": prior_end})
        where, params = prior.where()
        prior_total = int(self._query(f"SELECT count(*) FROM fact_service_requests WHERE {where}", params)[0][0])
        pct = (current_total - prior_total) / prior_total if prior_total else None
        note = None if prior_total else "Prior period has zero requests; percent change undefined."
        return PeriodComparison(
            current_total=current_total,
            prior_total=prior_total,
            prior_start=prior_start,
            prior_end=prior_end,
            pct_change=round(pct, 4) if pct is not None else None,
            note=note,
        )

    # -- series & breakdowns ------------------------------------------------------------
    def volume_series(self, f: Filters, grain: str = "day", split_by: str = "category_key") -> Series:
        if grain not in ("day", "week", "month"):
            raise ValueError("grain must be day, week or month")
        if split_by not in ("category_key", "borough", "status_group", "none"):
            raise ValueError("split_by must be category_key, borough, status_group or none")

        def compute() -> Series:
            t0 = time.perf_counter()
            where, params = f.where()
            period = "created_date" if grain == "day" else f"date_trunc('{grain}', created_date)::DATE"
            key = "'all'" if split_by == "none" else split_by
            rows = self._query(
                f"""
                SELECT {period} AS period, {key} AS key, sum(request_count)::BIGINT AS value
                FROM agg_daily_requests WHERE {where}
                GROUP BY 1, 2 ORDER BY 1, 2""",
                params,
            )
            pts = [SeriesPoint(period=r[0], key=str(r[1]), value=float(r[2])) for r in rows]
            return Series(grain=grain, points=pts, meta=self._meta(t0, len(pts)))

        return self._cached(f"series:{grain}:{split_by}:{f.cache_key()}", compute)

    def breakdown(self, f: Filters, dimension: str) -> Breakdown:
        dims = {"category_key": "category_label", "borough": "borough", "agency": "agency_name"}
        if dimension not in dims:
            raise ValueError(f"dimension must be one of {list(dims)}")

        def compute() -> Breakdown:
            t0 = time.perf_counter()
            where, params = f.where()
            rows = self._query(
                f"""
                SELECT {dimension} AS key, any_value({dims[dimension]}) AS label, count(*) AS n,
                       count(*) FILTER (WHERE status_group = 'open') AS n_open,
                       count(*) FILTER (WHERE status_group = 'closed') AS n_closed,
                       median(hours_to_close) AS med,
                       count(*) FILTER (WHERE hours_to_close <= 168) AS within7,
                       count(*) FILTER (WHERE NOT dq_closed_before_created AND NOT dq_closed_in_future
                                        AND NOT dq_closed_status_without_date) AS valid
                FROM fact_service_requests WHERE {where}
                GROUP BY 1 ORDER BY n DESC""",
                params,
            )
            total = sum(int(r[2]) for r in rows) or 1
            out = [
                BreakdownRow(
                    key=str(r[0]),
                    label=str(r[1] or r[0]),
                    requests=int(r[2]),
                    share=round(int(r[2]) / total, 4),
                    open_requests=int(r[3]),
                    closed_requests=int(r[4]),
                    median_hours_to_close=round(float(r[5]), 2) if r[5] is not None else None,
                    closed_within_7d_pct=round(int(r[6]) / int(r[7]), 4) if r[7] else None,
                )
                for r in rows
            ]
            return Breakdown(dimension=dimension, rows=out, meta=self._meta(t0, len(out)))

        return self._cached(f"breakdown:{dimension}:{f.cache_key()}", compute)

    def closure_distribution(self, f: Filters) -> ClosureDistribution:
        def compute() -> ClosureDistribution:
            t0 = time.perf_counter()
            where, params = f.where()
            cases = " ".join(
                f"WHEN hours_to_close >= {lo} "
                + (f"AND hours_to_close < {hi} " if hi is not None else "")
                + f"THEN {i}"
                for i, (_, lo, hi) in enumerate(CLOSURE_BUCKETS)
            )
            rows = dict(
                self._query(
                    f"""
                SELECT CASE {cases} END AS b, count(*) FROM fact_service_requests
                WHERE {where} AND hours_to_close IS NOT NULL GROUP BY 1""",
                    params,
                )
            )
            extra = self._query(
                f"""
                SELECT count(*) FILTER (WHERE closed_at IS NULL AND status_group <> 'closed'),
                       count(*) FILTER (WHERE dq_closed_before_created OR dq_closed_in_future
                                        OR dq_closed_status_without_date)
                FROM fact_service_requests WHERE {where}""",
                params,
            )[0]
            buckets = [
                Bucket(label=lbl, lower_hours=lo, upper_hours=hi, count=int(rows.get(i, 0)))
                for i, (lbl, lo, hi) in enumerate(CLOSURE_BUCKETS)
            ]
            return ClosureDistribution(
                buckets=buckets,
                still_open=int(extra[0]),
                excluded_invalid=int(extra[1]),
                meta=self._meta(t0, len(buckets)),
            )

        return self._cached(f"closure:{f.cache_key()}", compute)

    def hourly_pattern(self, f: Filters) -> HourlyPattern:
        def compute() -> HourlyPattern:
            t0 = time.perf_counter()
            where, params = f.where()
            rows = self._query(
                f"""
                SELECT created_dow, created_hour, count(*) FROM fact_service_requests
                WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2""",
                params,
            )
            cells = [HeatCell(dow=int(r[0]), hour=int(r[1]), count=int(r[2])) for r in rows]
            return HourlyPattern(cells=cells, meta=self._meta(t0, len(cells)))

        return self._cached(f"hourly:{f.cache_key()}", compute)

    # -- request explorer ---------------------------------------------------------------
    def _search_clause(self, search: str | None) -> tuple[str, list[Any]]:
        if not search:
            return "TRUE", []
        s = search.strip()[:100]
        if s.isdigit():
            return "unique_key = ?", [s]
        like = f"%{s.replace('%', '').replace('_', '')}%"
        return ("(descriptor ILIKE ? OR community_board ILIKE ? OR incident_zip LIKE ? OR complaint_type ILIKE ?)"), [
            like,
            like,
            like,
            like,
        ]

    def list_requests(
        self, f: Filters, *, search: str | None = None, sort: str = "-created_at", page: int = 1, page_size: int = 25
    ) -> RequestPage:
        t0 = time.perf_counter()
        desc = sort.startswith("-")
        col = sort.lstrip("-")
        if col not in SORTABLE:
            raise ValueError(f"sort must be one of {sorted(SORTABLE)} (prefix '-' for descending)")
        if not 1 <= page_size <= PAGE_SIZE_MAX:
            raise ValueError(f"page_size must be 1..{PAGE_SIZE_MAX}")
        if not 1 <= page <= 100_000:
            raise ValueError("page out of range")
        where, params = f.where()
        sw, sp = self._search_clause(search)
        order = f"{SORTABLE[col]} {'DESC' if desc else 'ASC'} NULLS LAST, unique_key"
        total = int(
            self._query(f"SELECT count(*) FROM fact_service_requests WHERE {where} AND {sw}", [*params, *sp])[0][0]
        )
        rows = self._query_dicts(
            f"SELECT {REQUEST_COLUMNS} FROM fact_service_requests WHERE {where} AND {sw} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, *sp, page_size, (page - 1) * page_size],
        )
        return RequestPage(
            rows=[RequestRow(**r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            sort=sort,
            meta=self._meta(t0, len(rows)),
        )

    def export_requests(
        self, f: Filters, *, search: str | None = None, max_rows: int = EXPORT_MAX_ROWS
    ) -> tuple[list[str], list[tuple[Any, ...]], int]:
        """Bounded export. Returns (columns, rows, total_matching)."""
        max_rows = min(max_rows, EXPORT_MAX_ROWS)
        where, params = f.where()
        sw, sp = self._search_clause(search)
        total = int(
            self._query(f"SELECT count(*) FROM fact_service_requests WHERE {where} AND {sw}", [*params, *sp])[0][0]
        )
        rows = self._query(
            f"SELECT {REQUEST_COLUMNS} FROM fact_service_requests WHERE {where} AND {sw} "
            f"ORDER BY created_at DESC, unique_key LIMIT ?",
            [*params, *sp, max_rows],
        )
        return [c.strip() for c in REQUEST_COLUMNS.split(",")], rows, total

    def get_request(self, unique_key: str) -> RequestDetail | None:
        if not unique_key.isdigit() or len(unique_key) > 20:
            raise ValueError("unique_key must be numeric")
        rows = self._query_dicts(
            f"""
            SELECT {REQUEST_COLUMNS}, agency_name, location_type, channel, council_district,
                   police_precinct, due_at, resolution_updated_at,
                   dq_closed_before_created, dq_closed_in_future, dq_invalid_borough,
                   dq_invalid_coordinates, dq_missing_zip, dq_closed_status_without_date,
                   ingest_run_id, raw_file, source_updated_at::VARCHAR AS source_updated_at,
                   snapshot_at_local::VARCHAR AS snapshot_at_local
            FROM fact_service_requests WHERE unique_key = ?""",
            [unique_key],
        )
        if not rows:
            return None
        r = rows[0]
        flags = [k.removeprefix("dq_") for k in list(r) if k.startswith("dq_") and r[k]]
        lineage = {k: r.pop(k) for k in ("ingest_run_id", "raw_file", "source_updated_at", "snapshot_at_local")}
        lineage["source_dataset"] = "erm2-nwe9"
        for k in [k for k in r if k.startswith("dq_")]:
            r.pop(k)
        return RequestDetail(**r, quality_flags=flags, lineage=lineage)

    # -- data quality -------------------------------------------------------------------
    def quality_report(self) -> QualityReport:
        b = self.build_info()
        rules: list[QualityRule] = []
        history: list[dict[str, Any]] = []
        dq_glob = (self.paths.processed / "data_quality_runs" / "*.parquet").as_posix()
        if b and list((self.paths.processed / "data_quality_runs").glob("*.parquet")):
            cur = self._con.cursor()
            try:
                rel = cur.execute(f"""
                    SELECT build_id, max(built_at_utc)::VARCHAR AS built_at_utc,
                           count(*) FILTER (WHERE status = 'pass') AS pass,
                           count(*) FILTER (WHERE status = 'warn') AS warn,
                           count(*) FILTER (WHERE status = 'fail') AS fail
                    FROM read_parquet('{dq_glob}') GROUP BY 1 ORDER BY 2 DESC LIMIT 20""")
                cols = [d[0] for d in rel.description]
                history = [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
            finally:
                cur.close()
            rules = [QualityRule(**{**q, "detail": q.get("detail") or ""}) for q in b.get("quality", [])]
        counts: dict[str, int] = {}
        for r in rules:
            counts[r.status] = counts.get(r.status, 0) + 1
        runs = []
        if self.paths.runs.exists():
            for p in sorted(self.paths.runs.glob("*.json"), reverse=True)[:20]:
                s = read_json(p)
                runs.append(
                    {
                        k: s.get(k)
                        for k in (
                            "run_id",
                            "mode",
                            "status",
                            "started_at",
                            "finished_at",
                            "pages_written",
                            "rows_fetched",
                            "total_attempts",
                            "error",
                            "schema_warnings",
                        )
                    }
                )
        failed: list[dict[str, Any]] = []
        if self.paths.failed_batches_file.exists():
            lines = self.paths.failed_batches_file.read_text(encoding="utf-8").splitlines()[-20:]
            failed = [json.loads(line) for line in lines if line.strip()]
        source = read_json(self.paths.source_metadata_file) if self.paths.source_metadata_file.exists() else None
        build = None
        if b:
            build = {k: v for k, v in b.items() if k not in ("quality", "descriptor_values")}
        return QualityReport(
            build=build,
            rules=rules,
            status_counts=counts,
            runs=runs,
            failed_batches=failed,
            source=source,
            freshness=self.freshness(),
            build_history=history,
        )
