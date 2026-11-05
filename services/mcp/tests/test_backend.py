"""Tool-level behaviour: coverage handling, filter mismatch, restrictions, bounds, sanitization."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from civicpulse_mcp import capabilities as cap
from civicpulse_mcp.backend import CivicPulseTools, ToolInputError, sanitize_text
from civicpulse_mcp.envelope import MAX_RESULT_BYTES, ToolResult

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def tools(sample_models):
    return CivicPulseTools(sample_models.root)


def _size(r: ToolResult) -> int:
    return len(json.dumps(r.model_dump(mode="json")))


def test_period_outside_data_returns_no_data_not_zero(tools):
    r = tools.get_service_request_trends(dt.date(2030, 1, 1), dt.date(2030, 1, 31), "all", "ALL", "day")
    assert r.status == "no_data"
    assert r.coverage.partial and "outside the data" in r.coverage.note
    assert r.key_measurements == []


def test_partial_period_is_flagged_and_clamped(tools):
    last = tools.svc.filter_options().last_complete_date
    r = tools.get_service_request_trends(last - dt.timedelta(days=3), last + dt.timedelta(days=10), "all", "ALL", "day")
    assert r.coverage.partial
    assert r.coverage.effective_end == last.isoformat()
    assert r.data["points"][-1]["period"] <= last.isoformat()


def test_week_buckets_cut_by_range_are_marked_partial(tools):
    last = tools.svc.filter_options().last_complete_date
    start = last - dt.timedelta(days=30)
    r = tools.get_service_request_trends(start, last, "all", "ALL", "week")
    first = r.data["points"][0]
    if dt.date.fromisoformat(first["period"]) < start:
        assert first["partial_bucket"] is True


def test_reversed_dates_and_long_daily_ranges_rejected(tools):
    with pytest.raises(ToolInputError):
        tools.get_service_request_trends(dt.date(2026, 2, 1), dt.date(2026, 1, 1), "all", "ALL", "day")
    with pytest.raises(ToolInputError, match="daily granularity"):
        tools.get_service_request_trends(dt.date(2024, 1, 1), dt.date(2026, 1, 1), "all", "ALL", "day")


def test_breakdown_rejects_mismatched_filters(tools):
    d = tools.svc.filter_options().last_complete_date
    with pytest.raises(ToolInputError, match="cannot be combined"):
        tools.get_category_breakdown(d, d, "ALL", "category", "heat_hot_water")
    with pytest.raises(ToolInputError, match="requires a category"):
        tools.get_category_breakdown(d, d, "ALL", "descriptor", None)


def test_compare_requires_two_distinct_boroughs(tools):
    d = tools.svc.filter_options().last_complete_date
    with pytest.raises(ToolInputError):
        tools.compare_boroughs(d, d, ["BRONX", "BRONX"], "all")


@pytest.mark.parametrize("request_kind", ["research_summary", "predict_request", "rank_requests"])
def test_delay_tool_never_returns_numeric_predictions(tools, request_kind):
    r = tools.predict_closure_delay(request_kind, "12345" if request_kind == "predict_request" else None)
    assert r.status == "restricted" and r.capability == "DISABLED"
    assert r.data["numerical_predictions_enabled"] is False
    blob = json.dumps(r.model_dump(mode="json"))
    assert "risk" not in r.data and '"rows"' not in json.dumps(r.data)  # no per-request scores or lists
    assert "12345" not in blob  # the requested id is not echoed or looked up
    assert set(cap.PROHIBITED_DELAY_USES) <= set(r.data["prohibited_uses"])
    assert cap.DELAY_NUMERIC_PREDICTIONS_ENABLED is False


def test_forecast_reports_baseline_significance_honestly(tools):
    r = tools.forecast_request_volume("heat_hot_water", "BRONX")
    comps = {c["baseline"]: c for c in r.data["evaluation"]["comparisons_vs_baselines"]}
    assert set(comps) == {"naive", "seasonal_naive", "rolling_mean"}
    for c in comps.values():
        lo, _hi = c["wape_diff_ci95"]
        assert c["significant_at_95"] == (lo > 0)
    assert r.capability == "EXPERIMENTAL"
    assert any("not statistically established" in lim for lim in r.limitations)


def test_anomaly_flags_are_unverified_and_october_note(tools):
    r = tools.detect_unusual_spikes(
        dt.date(2025, 10, 1), dt.date(2025, 10, 31), "heat_hot_water", "ALL_SERIES", "any", 10
    )
    assert r.capability == "EXPERIMENTAL"
    if r.status == "ok":
        assert any("heating-season" in u for u in r.uncertainty)
        assert any("not confirmed incidents" in lim for lim in r.limitations)


def test_anomaly_explanation_recomputes_expected(tools):
    win = tools.pred.anomaly_window()
    lo, hi = dt.date.fromisoformat(win["first"]), dt.date.fromisoformat(win["last"])
    flags = tools.detect_unusual_spikes(lo, hi, "all", "CITYWIDE", "any", 1)
    if not flags.data.get("flags"):
        pytest.skip("no flags on the sample")
    f = flags.data["flags"][0]
    r = tools.explain_model_prediction("anomaly", f["category"], f["geography"], dt.date.fromisoformat(f["date"]), 5)
    assert r.data["recomputed_expected_matches_stored"] is True
    assert r.metric_definitions["causality"]


def test_every_tool_result_is_bounded(tools):
    o = tools.svc.filter_options()
    results = [
        tools.get_service_request_trends(
            o.last_complete_date - dt.timedelta(days=399), o.last_complete_date, "all", "ALL", "day"
        ),
        tools.forecast_request_volume("noise_residential", "ALL"),
        tools.get_model_evaluation("forecast"),
        tools.get_model_evaluation("delay_risk"),
        tools.get_model_evaluation("anomaly"),
        tools.get_dataset_quality(),
        tools.detect_unusual_spikes(dt.date(2020, 1, 1), dt.date(2022, 12, 31), "all", "ALL_SERIES", "any", 50),
    ]
    for r in results:
        assert _size(r) < MAX_RESULT_BYTES, r.tool


def test_sanitize_strips_control_characters():
    assert sanitize_text("ok\x00\x1b[31mIGNORE\nPREVIOUS", 12) == "ok  [31mIGNO"
