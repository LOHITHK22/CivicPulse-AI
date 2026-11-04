"""Data-quality rules evaluated on every warehouse build.

Severity meanings:
* ``invariant`` — must never fail (status ``fail`` if any record violates it).
* ``reject``    — violating records are quarantined in ``rejected_records``.
* ``flag``      — records are kept but flagged (``dq_*`` columns) and excluded from the affected
                  measure (e.g. invalid closures get no duration).
* ``info``      — measured and reported, no automatic action.

Status: ``pass`` / ``warn`` (rate above threshold) / ``fail`` (invariant broken) / ``info``.
All rules are documented in docs/data/QUALITY_RULES.md.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from typing import Any

import duckdb

from civicpulse_shared.config import IngestionConfig

MISSINGNESS_COLUMNS = (
    "closed_at",
    "descriptor",
    "agency",
    "community_board",
    "incident_zip",
    "latitude",
    "channel",
    "location_type",
    "council_district",
)


@dataclass
class QualityResult:
    rule_id: str
    description: str
    category: str
    severity: str
    records_checked: int
    records_failed: int
    threshold: float | None
    status: str
    detail: str = ""

    @property
    def failure_rate(self) -> float:
        return self.records_failed / self.records_checked if self.records_checked else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["failure_rate"] = round(self.failure_rate, 6)
        return d


def _status(severity: str, failed: int, checked: int, threshold: float | None) -> str:
    if severity == "invariant":
        return "fail" if failed else "pass"
    if threshold is None:
        return "info"
    rate = failed / checked if checked else 0.0
    return "warn" if rate > threshold else "pass"


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    row = con.execute(sql, params or []).fetchone()
    return row[0] if row else None


def run_quality_checks(
    con: duckdb.DuckDBPyConnection,
    cfg: IngestionConfig,
    *,
    snapshot_local: dt.datetime,
    previous_descriptors: set[str] | None,
    source_meta: dict[str, Any],
) -> list[QualityResult]:
    results: list[QualityResult] = []

    def add(
        rule_id: str,
        desc: str,
        category: str,
        severity: str,
        failed: int,
        checked: int,
        threshold: float | None,
        detail: str = "",
    ) -> None:
        results.append(
            QualityResult(
                rule_id,
                desc,
                category,
                severity,
                int(checked),
                int(failed),
                threshold,
                _status(severity, failed, checked, threshold),
                detail,
            )
        )

    n_fact = int(_scalar(con, "SELECT count(*) FROM fact_service_requests"))
    n_dedup = int(_scalar(con, "SELECT count(*) FROM dedup"))
    n_raw = int(_scalar(con, "SELECT count(*) FROM raw"))

    # --- Uniqueness ---------------------------------------------------------------
    dup_fact = int(_scalar(con, "SELECT count(*) - count(DISTINCT unique_key) FROM fact_service_requests"))
    add("DQ001", "unique_key is unique in fact_service_requests", "uniqueness", "invariant", dup_fact, n_fact, 0)
    add(
        "DQ002",
        "Raw rows superseded by a newer version of the same unique_key (deduplicated)",
        "uniqueness",
        "info",
        n_raw - n_dedup,
        n_raw,
        None,
        "Expected when incremental runs re-fetch updated records.",
    )
    same_page_dups = int(
        _scalar(
            con,
            """
        SELECT coalesce(sum(c - 1), 0) FROM (
          SELECT count(*) AS c FROM raw WHERE unique_key IS NOT NULL
          GROUP BY _ingest_run_id, _page_no, unique_key HAVING count(*) > 1)""",
        )
    )
    add("DQ003", "Duplicate unique_key within a single source page", "uniqueness", "flag", same_page_dups, n_raw, 0.0)

    # --- Rejections ---------------------------------------------------------------
    reject_rules = [
        ("DQ010", "missing_unique_key", "Record has a unique_key", "completeness"),
        ("DQ011", "invalid_created_date", "created_date parses as a timestamp", "validity"),
        ("DQ012", "category_not_configured", "complaint_type is a configured category", "validity"),
        ("DQ013", "created_before_window", "created_date is within the configured window", "validity"),
        ("DQ014", "created_in_future", "created_date is not in the future", "validity"),
    ]
    for rule_id, reason, desc, cat in reject_rules:
        failed = int(_scalar(con, "SELECT count(*) FROM rejected_records WHERE reject_reason = ?", [reason]))
        add(rule_id, desc, cat, "reject", failed, n_dedup, 0.005)

    # --- Flags ----------------------------------------------------------------------
    flag_rules = [
        ("DQ020", "dq_closed_before_created", "closed_date is not earlier than created_date", 0.001),
        ("DQ021", "dq_closed_in_future", "closed_date is not in the future", 0.0),
        ("DQ022", "dq_closed_status_without_date", "Closed status has a closed_date", 0.005),
        ("DQ023", "dq_invalid_borough", "borough is one of the five NYC boroughs", 0.001),
        ("DQ024", "dq_invalid_coordinates", "latitude/longitude present and inside NYC bounding box", 0.02),
        ("DQ025", "dq_missing_zip", "incident_zip is a 5-digit ZIP", 0.02),
    ]
    for rule_id, col, desc, thr in flag_rules:
        failed = int(_scalar(con, f"SELECT count(*) FROM fact_service_requests WHERE {col}"))
        add(rule_id, desc, "validity", "flag", failed, n_fact, thr)

    neg = int(_scalar(con, "SELECT count(*) FROM fact_service_requests WHERE hours_to_close < 0"))
    add("DQ026", "hours_to_close is never negative", "validity", "invariant", neg, n_fact, 0)

    # --- Missingness ---------------------------------------------------------------
    for col in MISSINGNESS_COLUMNS:
        missing = int(_scalar(con, f"SELECT count(*) FILTER (WHERE {col} IS NULL) FROM fact_service_requests"))
        note = "Open requests legitimately have no closed_at." if col == "closed_at" else ""
        add(
            f"DQ03{MISSINGNESS_COLUMNS.index(col)}",
            f"Missingness of {col}",
            "missingness",
            "info",
            missing,
            n_fact,
            None,
            note,
        )

    # --- Freshness -----------------------------------------------------------------
    max_created = _scalar(con, "SELECT max(created_at) FROM fact_service_requests")
    if max_created is not None:
        lag_h = (snapshot_local - max_created).total_seconds() / 3600
        add(
            "DQ040",
            "Latest created_at is within 72h of build time (only meaningful for incremental "
            "snapshots; a bounded backfill window is expected to be stale)",
            "freshness",
            "flag",
            int(lag_h > 72),
            1,
            0.0,
            json.dumps({"latest_created_at": str(max_created), "lag_hours": round(lag_h, 1)}),
        )
    rows_updated = source_meta.get("rows_updated_at")
    if rows_updated:
        src_ts = dt.datetime.fromisoformat(rows_updated)
        checked = dt.datetime.fromisoformat(source_meta.get("checked_at", rows_updated))
        src_lag = (checked - src_ts).total_seconds() / 3600
        add(
            "DQ041",
            "Source dataset updated within 48h of the extraction check",
            "freshness",
            "flag",
            int(src_lag > 48),
            1,
            0.0,
            json.dumps({"source_rows_updated_at": rows_updated, "lag_hours": round(src_lag, 1)}),
        )

        # The metadata timestamp can move without new rows being published (observed 2026-11-04),
        # so also check the newest row version actually received.
        newest_epoch = _scalar(con, "SELECT epoch(max(source_updated_at)) FROM fact_service_requests")
        if newest_epoch is not None:
            newest = dt.datetime.fromtimestamp(float(newest_epoch), dt.UTC)
            chk = checked if checked.tzinfo else checked.replace(tzinfo=dt.UTC)
            row_lag = (chk - newest).total_seconds() / 3600
            add(
                "DQ042",
                "Newest record version (:updated_at) received within 48h of the extraction check",
                "freshness",
                "flag",
                int(row_lag > 48),
                1,
                0.0,
                json.dumps({"newest_row_updated_at": newest.isoformat(), "lag_hours": round(row_lag, 1)}),
            )

    # --- Category stability ---------------------------------------------------------
    for c in cfg.categories:
        recent = int(
            _scalar(
                con,
                """
            SELECT count(*) FROM fact_service_requests
            WHERE category_key = ? AND created_at >= (SELECT max(created_at) FROM fact_service_requests)
                  - INTERVAL 7 DAY""",
                [c.key],
            )
        )
        add(
            f"DQ050_{c.key}",
            f"Category '{c.key}' has records in the latest 7 days of data",
            "consistency",
            "flag",
            int(recent == 0),
            1,
            0.0,
            json.dumps({"recent_records": recent}),
        )
    current = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT descriptor FROM fact_service_requests WHERE descriptor IS NOT NULL"
        ).fetchall()
    }
    if previous_descriptors is not None:
        added, removed = sorted(current - previous_descriptors), sorted(previous_descriptors - current)
        add(
            "DQ051",
            "Descriptor values unchanged since previous build",
            "consistency",
            "flag",
            len(added) + len(removed),
            max(len(current), 1),
            0.0,
            json.dumps({"added": added[:20], "removed": removed[:20]}),
        )

    # --- Schema drift ---------------------------------------------------------------
    report = source_meta.get("schema_report")
    if report is not None:
        n_drift = len(report.get("added", [])) + len(report.get("removed", [])) + len(report.get("type_changed", {}))
        add(
            "DQ060",
            "Source schema matches committed snapshot",
            "schema",
            "flag",
            n_drift,
            max(n_drift, 1),
            0.0,
            json.dumps(report),
        )
    return results
