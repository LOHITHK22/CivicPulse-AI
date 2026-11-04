"""Metric correctness: every service result is checked against an independent Polars computation
over the warehouse built from the committed REAL NYC 311 sample."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from civicpulse_analytics import AnalyticsService, DataUnavailableError, Filters

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def svc(sample_warehouse) -> AnalyticsService:
    return AnalyticsService(sample_warehouse.root)


@pytest.fixture(scope="module")
def fact(sample_warehouse) -> pl.DataFrame:
    return pl.read_parquet(sample_warehouse.table("fact_service_requests"))


def apply(df: pl.DataFrame, f: Filters) -> pl.DataFrame:
    if f.start:
        df = df.filter(pl.col("created_date") >= f.start)
    if f.end:
        df = df.filter(pl.col("created_date") <= f.end)
    if f.boroughs:
        df = df.filter(pl.col("borough").is_in(f.boroughs))
    if f.categories:
        df = df.filter(pl.col("category_key").is_in(f.categories))
    if f.statuses:
        df = df.filter(pl.col("status_group").is_in(f.statuses))
    return df


FILTER_CASES = [
    Filters(),
    Filters(boroughs=["BROOKLYN"]),
    Filters(categories=["heat_hot_water"], boroughs=["BRONX", "QUEENS"]),
    Filters(start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31)),
    Filters(statuses=["open"]),
]


@pytest.mark.parametrize("f", FILTER_CASES, ids=lambda f: f.model_dump_json(exclude_defaults=True) or "all")
def test_kpi_counts_and_median_match_independent_computation(svc, fact, f):
    k = svc.kpis(f)
    sub = apply(fact, f)
    assert k.total_requests == sub.height
    assert k.open_requests == sub.filter(pl.col("status_group") == "open").height
    assert k.closed_requests == sub.filter(pl.col("status_group") == "closed").height
    med = sub["hours_to_close"].median()
    assert (k.median_hours_to_close is None and med is None) or k.median_hours_to_close == pytest.approx(med, abs=0.01)


def test_open_beyond_threshold_uses_mature_denominator(svc, fact):
    k = svc.kpis(Filters(), threshold_days=7)
    snap = fact["snapshot_at_local"].max()
    eligible = fact.filter(
        (pl.col("created_at") <= snap - dt.timedelta(days=7))
        & ~pl.col("dq_closed_before_created")
        & ~pl.col("dq_closed_in_future")
        & ~pl.col("dq_closed_status_without_date")
    )
    num = eligible.filter(pl.col("closed_at").is_null() | (pl.col("hours_to_close") > 168)).height
    assert k.open_beyond_threshold_denominator == eligible.height
    assert k.open_beyond_threshold_numerator == num
    assert k.open_beyond_threshold_pct == pytest.approx(num / eligible.height, abs=1e-4)


def test_prior_period_comparison(svc, fact):
    f = Filters(start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 31))
    k = svc.kpis(f)
    prior = apply(fact, Filters(start=dt.date(2026, 1, 29), end=dt.date(2026, 2, 28)))
    assert k.comparison.prior_start == dt.date(2026, 1, 29) and k.comparison.prior_end == dt.date(2026, 2, 28)
    assert k.comparison.prior_total == prior.height
    expected = (k.total_requests - prior.height) / prior.height
    assert k.comparison.pct_change == pytest.approx(expected, abs=1e-4)


def test_comparison_not_computed_when_prior_period_precedes_data(svc):
    opts = svc.filter_options()
    k = svc.kpis(Filters(start=opts.min_date, end=opts.min_date + dt.timedelta(days=6)))
    assert k.comparison.prior_total is None and k.comparison.pct_change is None
    assert "before loaded data" in (k.comparison.note or "")


def test_unusual_increase_kpi_is_honestly_not_computed(svc):
    k = svc.kpis(Filters())
    assert k.categories_with_unusual_increase is None
    assert "Milestone 2" in k.unusual_increase_note


@pytest.mark.parametrize("grain", ["day", "week", "month"])
def test_volume_series_sums_to_total(svc, fact, grain):
    s = svc.volume_series(Filters(boroughs=["MANHATTAN"]), grain)
    assert sum(p.value for p in s.points) == fact.filter(pl.col("borough") == "MANHATTAN").height


def test_daily_series_matches_fact_by_day(svc, fact):
    s = svc.volume_series(Filters(), "day", "none")
    expected = dict(fact.group_by("created_date").len().iter_rows())
    assert {p.period: int(p.value) for p in s.points} == expected


def test_date_boundaries_are_inclusive(svc, fact):
    d = fact["created_date"].min()
    k = svc.kpis(Filters(start=d, end=d))
    assert k.total_requests == fact.filter(pl.col("created_date") == d).height > 0


def test_borough_breakdown(svc, fact):
    b = svc.breakdown(Filters(), "borough")
    expected = dict(fact.group_by("borough").len().iter_rows())
    assert {r.key: r.requests for r in b.rows} == expected
    assert sum(r.share for r in b.rows) == pytest.approx(1.0, abs=1e-3)


def test_category_breakdown_respects_filters(svc, fact):
    b = svc.breakdown(Filters(boroughs=["QUEENS"]), "category_key")
    expected = dict(fact.filter(pl.col("borough") == "QUEENS").group_by("category_key").len().iter_rows())
    assert {r.key: r.requests for r in b.rows} == expected


def test_closure_distribution_partitions_closed_requests(svc, fact):
    d = svc.closure_distribution(Filters())
    assert sum(b.count for b in d.buckets) == fact["hours_to_close"].drop_nulls().len()
    lt1 = fact.filter(pl.col("hours_to_close") < 1).height
    assert d.buckets[0].count == lt1


def test_hourly_pattern_totals(svc, fact):
    h = svc.hourly_pattern(Filters())
    assert sum(c.count for c in h.cells) == fact.height
    assert all(1 <= c.dow <= 7 and 0 <= c.hour <= 23 for c in h.cells)


def test_request_listing_pagination_sorting_and_search(svc, fact):
    p1 = svc.list_requests(Filters(), page=1, page_size=10, sort="-created_at")
    p2 = svc.list_requests(Filters(), page=2, page_size=10, sort="-created_at")
    assert p1.total == fact.height
    assert {r.unique_key for r in p1.rows}.isdisjoint({r.unique_key for r in p2.rows})
    times = [r.created_at for r in p1.rows + p2.rows]
    assert times == sorted(times, reverse=True)
    key = fact["unique_key"][0]
    hit = svc.list_requests(Filters(), search=key)
    assert hit.total == 1 and hit.rows[0].unique_key == key


def test_listing_never_exposes_coordinates(svc):
    row = svc.list_requests(Filters(), page_size=1).rows[0]
    assert "latitude" not in row.model_dump() and "longitude" not in row.model_dump()


def test_request_detail_includes_lineage_and_flags(svc, fact):
    key = fact["unique_key"][0]
    d = svc.get_request(key)
    assert d is not None and d.unique_key == key
    assert d.lineage["source_dataset"] == "erm2-nwe9" and d.lineage["raw_file"].startswith("run=")
    assert svc.get_request("1") is None


@pytest.mark.parametrize("bad", [{"sort": "password"}, {"page_size": 1000}, {"page": 0}])
def test_listing_rejects_invalid_arguments(svc, bad):
    with pytest.raises(ValueError):
        svc.list_requests(Filters(), **bad)


def test_search_is_parameterized_not_injected(svc):
    res = svc.list_requests(Filters(), search="'; DROP TABLE fact_service_requests; --")
    assert res.total == 0
    assert svc.kpis(Filters()).total_requests > 0


def test_export_is_bounded(svc, fact):
    cols, rows, total = svc.export_requests(Filters(), max_rows=5)
    assert len(rows) == 5 and total == fact.height and "unique_key" in cols


def test_filter_validation():
    with pytest.raises(ValueError):
        Filters(boroughs=["Atlantis"])
    with pytest.raises(ValueError):
        Filters(start=dt.date(2026, 2, 1), end=dt.date(2026, 1, 1))
    with pytest.raises(ValueError):
        Filters(statuses=["deleted"])
    assert Filters(boroughs=["brooklyn ", "Brooklyn"]).boroughs == ["BROOKLYN"]


def test_quality_report(svc):
    q = svc.quality_report()
    assert q.build and q.build["accepted_rows"] > 0
    ids = {r.rule_id for r in q.rules}
    assert {"DQ001", "DQ020", "DQ024", "DQ026"} <= ids
    assert next(r for r in q.rules if r.rule_id == "DQ001").status == "pass"


def test_missing_warehouse_raises_clear_error(tmp_path):
    with pytest.raises(DataUnavailableError, match="civicpulse build"):
        AnalyticsService(tmp_path).kpis(Filters())


def test_last_complete_date_excludes_truncated_final_day(svc, fact):
    opts = svc.filter_options()
    last_ts = fact["created_at"].max()
    full_day = last_ts.hour >= 23
    expected = opts.max_date if full_day else opts.max_date - dt.timedelta(days=1)
    assert opts.last_complete_date == expected
