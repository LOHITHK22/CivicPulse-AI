"""Typed result models returned by the analytics service (and serialized by the API)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class QueryMeta(BaseModel):
    elapsed_ms: float
    rows: int
    data_build_id: str | None
    snapshot_at_local: str | None


class FilterOptions(BaseModel):
    min_date: dt.date | None
    max_date: dt.date | None
    last_complete_date: dt.date | None = Field(
        default=None, description="Latest day fully covered by the snapshot (default dashboard window end)"
    )
    categories: list[dict[str, Any]]
    boroughs: list[str]
    agencies: list[str]
    statuses: list[str]
    total_records: int


class Freshness(BaseModel):
    latest_created_at: str | None
    snapshot_at_local: str | None
    data_lag_hours: float | None = Field(description="snapshot time minus latest created_at")
    source_rows_updated_at: str | None
    last_build_at_utc: str | None
    is_live: bool = Field(default=False, description="Always false: this is a versioned snapshot, not a stream")


class PeriodComparison(BaseModel):
    current_total: int
    prior_total: int | None
    prior_start: dt.date | None
    prior_end: dt.date | None
    pct_change: float | None
    note: str | None = None


class Kpis(BaseModel):
    total_requests: int
    comparison: PeriodComparison
    open_requests: int
    closed_requests: int
    unknown_status_requests: int
    median_hours_to_close: float | None
    closed_with_valid_duration: int
    open_beyond_threshold_pct: float | None
    open_beyond_threshold_numerator: int
    open_beyond_threshold_denominator: int
    threshold_days: int
    freshness: Freshness
    categories_with_unusual_increase: int | None = None
    unusual_increase_note: str = "Requires anomaly detection (Milestone 2); not computed yet."
    definitions: dict[str, str]
    meta: QueryMeta


class SeriesPoint(BaseModel):
    period: dt.date
    key: str
    value: float


class Series(BaseModel):
    grain: str
    points: list[SeriesPoint]
    meta: QueryMeta


class BreakdownRow(BaseModel):
    key: str
    label: str
    requests: int
    share: float
    open_requests: int
    closed_requests: int
    median_hours_to_close: float | None
    closed_within_7d_pct: float | None


class Breakdown(BaseModel):
    dimension: str
    rows: list[BreakdownRow]
    meta: QueryMeta


class Bucket(BaseModel):
    label: str
    lower_hours: float | None
    upper_hours: float | None
    count: int


class ClosureDistribution(BaseModel):
    buckets: list[Bucket]
    still_open: int
    excluded_invalid: int
    meta: QueryMeta


class HeatCell(BaseModel):
    dow: int
    hour: int
    count: int


class HourlyPattern(BaseModel):
    cells: list[HeatCell]
    meta: QueryMeta


class RequestRow(BaseModel):
    unique_key: str
    created_at: dt.datetime
    closed_at: dt.datetime | None
    category_key: str
    complaint_type: str
    descriptor: str | None
    agency: str | None
    status: str
    status_group: str
    borough: str
    community_board: str | None
    incident_zip: str | None
    hours_to_close: float | None


class RequestPage(BaseModel):
    rows: list[RequestRow]
    total: int
    page: int
    page_size: int
    sort: str
    meta: QueryMeta


class RequestDetail(RequestRow):
    agency_name: str | None
    location_type: str | None
    channel: str | None
    council_district: str | None
    police_precinct: str | None
    due_at: dt.datetime | None
    resolution_updated_at: dt.datetime | None
    quality_flags: list[str]
    lineage: dict[str, Any]


class QualityRule(BaseModel):
    rule_id: str
    description: str
    category: str
    severity: str
    records_checked: int
    records_failed: int
    failure_rate: float
    threshold: float | None
    status: str
    detail: str


class QualityReport(BaseModel):
    build: dict[str, Any] | None
    rules: list[QualityRule]
    status_counts: dict[str, int]
    runs: list[dict[str, Any]]
    failed_batches: list[dict[str, Any]]
    source: dict[str, Any] | None
    freshness: Freshness
    build_history: list[dict[str, Any]]
