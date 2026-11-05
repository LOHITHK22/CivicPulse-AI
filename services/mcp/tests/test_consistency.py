"""MCP tools and the FastAPI dashboard must agree: same numbers, same metric definitions.

Compares tool results (backend layer used by the MCP server) with the public API endpoints over the
same warehouse, and with an independent Polars computation on the raw fact table.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest
from fastapi.testclient import TestClient

from civicpulse_analytics import AnalyticsService
from civicpulse_analytics.predictions import PredictionService
from civicpulse_api import main as api_main
from civicpulse_mcp.backend import CivicPulseTools

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def tools(sample_models):
    return CivicPulseTools(sample_models.root)


@pytest.fixture(scope="module")
def api(sample_models):
    svc = AnalyticsService(sample_models.root)
    pred = PredictionService(sample_models.root)
    api_main.app.dependency_overrides[api_main.get_service] = lambda: svc
    api_main.app.dependency_overrides[api_main.get_predictions] = lambda: pred
    with TestClient(api_main.app) as c:
        yield c
    api_main.app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def window(tools):
    opts = tools.svc.filter_options()
    return max(opts.min_date, opts.last_complete_date - dt.timedelta(days=700)), opts.last_complete_date


@pytest.fixture(scope="module")
def fact(sample_models) -> pl.DataFrame:
    return pl.read_parquet(sample_models.table("fact_service_requests"))


def _km(result, name):
    return next(m.value for m in result.key_measurements if m.name == name)


@pytest.mark.parametrize("grain", ["day", "week", "month"])
def test_trends_match_api_volume_endpoint(tools, api, window, grain):
    start, end = window
    if grain == "day":
        start = max(start, end - dt.timedelta(days=120))
    res = tools.get_service_request_trends(start, end, "heat_hot_water", "ALL", grain)
    body = api.get(
        "/api/v1/metrics/volume",
        params={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "category": "heat_hot_water",
            "grain": grain,
            "split_by": "none",
        },
    ).json()
    assert [(p["period"], p["value"]) for p in res.data["points"]] == [
        (p["period"], int(p["value"])) for p in body["points"]
    ]
    kpi = api.get(
        "/api/v1/metrics/kpis",
        params={"start": start.isoformat(), "end": end.isoformat(), "category": "heat_hot_water"},
    ).json()
    assert _km(res, "total_requests") == kpi["total_requests"]


def test_trends_total_matches_independent_polars(tools, window, fact):
    start, end = window
    res = tools.get_service_request_trends(start, end, "noise_residential", "BROOKLYN", "month")
    expected = fact.filter(
        (pl.col("created_date") >= start)
        & (pl.col("created_date") <= end)
        & (pl.col("category_key") == "noise_residential")
        & (pl.col("borough") == "BROOKLYN")
    ).height
    assert _km(res, "total_requests") == expected


def test_compare_boroughs_matches_api_kpis_per_borough(tools, api, window):
    start, end = window
    res = tools.compare_boroughs(start, end, ["BRONX", "QUEENS", "BROOKLYN"], "all")
    for row in res.data["rows"]:
        k = api.get(
            "/api/v1/metrics/kpis",
            params={"start": start.isoformat(), "end": end.isoformat(), "borough": row["borough"]},
        ).json()
        assert row["requests"] == k["total_requests"]
        assert row["median_hours_to_close"] == k["median_hours_to_close"]
        assert row["open_beyond_7d_pct"] == k["open_beyond_threshold_pct"]
    shares = sum(r["share_of_selected"] for r in res.data["rows"])
    assert abs(shares - 1) < 1e-3
    a, b = res.data["rows"][0], res.data["rows"][1]
    pair = res.data["pairwise"][0]
    assert pair["difference_requests"] == a["requests"] - b["requests"]
    assert "selected boroughs" in res.metric_definitions["share_of_selected"]


def test_category_breakdown_matches_api(tools, api, window):
    start, end = window
    res = tools.get_category_breakdown(start, end, "ALL", "category", None)
    body = api.get(
        "/api/v1/metrics/breakdown/category_key", params={"start": start.isoformat(), "end": end.isoformat()}
    ).json()
    assert {r["key"]: r["requests"] for r in res.data["rows"]} == {r["key"]: r["requests"] for r in body["rows"]}
    assert {r["key"]: r["share"] for r in res.data["rows"]} == {r["key"]: r["share"] for r in body["rows"]}


def test_descriptor_breakdown_matches_polars(tools, window, fact):
    start, end = window
    res = tools.get_category_breakdown(start, end, "ALL", "descriptor", "noise_residential")
    sub = fact.filter(
        (pl.col("created_date") >= start)
        & (pl.col("created_date") <= end)
        & (pl.col("category_key") == "noise_residential")
    )
    counts = sub.group_by(pl.col("descriptor").fill_null("UNSPECIFIED")).len()
    expected = dict(zip(counts["descriptor"].to_list(), counts["len"].to_list(), strict=True))
    got = {r["key"]: r["requests"] for r in res.data["rows"] if r["key"] != "__other__"}
    for k, v in got.items():
        assert expected[k] == v
    assert sum(r["requests"] for r in res.data["rows"]) == sub.height


def test_forecast_matches_api(tools, api):
    res = tools.forecast_request_volume("heat_hot_water", "QUEENS")
    body = api.get("/api/v1/predictions/forecast", params={"category": "heat_hot_water", "borough": "QUEENS"}).json()
    assert res.data["series"][0]["forecast"] == body["forecast"]
    assert res.data["origin"] == body["origin"]
    ev = api.get("/api/v1/models/forecast/evaluation").json()
    sel = ev["selection"]["selected"]
    test_wape = next(r["wape"] for r in ev["overall"] if r["model"] == sel and r["period"] == "test")
    assert _km(res, "test_wape_selected_model_all_series") == round(test_wape, 4)


def test_forecast_all_boroughs_sum_has_no_interval(tools):
    res = tools.forecast_request_volume("noise_residential", "ALL")
    assert len(res.data["series"]) == 5
    totals = res.data["all_boroughs_point_total"]
    day0 = sum(s["forecast"][0]["predicted"] for s in res.data["series"])
    assert abs(totals[0]["predicted_all_boroughs"] - round(day0, 1)) < 1e-6
    assert "not the sum of intervals" in next(
        m.definition for m in res.key_measurements if m.name == "forecast_7day_total_all_boroughs"
    )


def test_anomalies_match_api(tools, api):
    win = tools.pred.anomaly_window()
    start, end = dt.date.fromisoformat(win["first"]), dt.date.fromisoformat(win["last"])
    res = tools.detect_unusual_spikes(start, end, "all", "ALL_SERIES", "any", 50)
    body = api.get(
        "/api/v1/anomalies", params={"start": start.isoformat(), "end": end.isoformat(), "limit": 200}
    ).json()
    assert _km(res, "flags_in_window") == body["total"]
    api_keys = {(r["date"], r["category_key"], r["borough"]) for r in body["rows"]}
    for f in res.data["flags"]:
        assert (f["date"], f["category"], f["geography"]) in api_keys or body["total"] > 200
    scores = [abs(f["score"]) for f in res.data["flags"]]
    assert scores == sorted(scores, reverse=True)
    assert res.data["label_status"].startswith("unverified")


def test_quality_matches_api(tools, api):
    res = tools.get_dataset_quality()
    body = api.get("/api/v1/quality").json()
    assert _km(res, "record_count") == body["build"]["accepted_rows"]
    assert _km(res, "duplicate_raw_rows") == body["build"]["duplicate_raw_rows"]
    assert _km(res, "data_cutoff") == body["freshness"]["latest_created_at"]
    assert res.data["freshness"]["is_live"] is False


@pytest.mark.parametrize("model", ["forecast", "delay_risk", "anomaly"])
def test_model_evaluation_numbers_come_from_artifact(tools, api, model):
    res = tools.get_model_evaluation(model)
    ev = api.get(f"/api/v1/models/{model}/evaluation").json()
    if model == "forecast":
        for r in ev["overall"]:
            if r["period"] == "test":
                assert _km(res, f"test_wape_{r['model']}") == round(r["wape"], 4)
    elif model == "delay_risk":
        for name, m in ev["models"].items():
            assert _km(res, f"test_pr_auc_{name}") == round(m["test"]["overall"]["pr_auc"], 5)
    else:
        assert _km(res, "real_data_flags") == ev["real_data"]["alerts"]
    assert res.provenance.model_run_id == "sample-run"
