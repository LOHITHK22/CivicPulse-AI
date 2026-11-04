"""HTTP contracts for the Milestone 2 model endpoints (precomputed outputs from the real sample)."""

from __future__ import annotations

import json

import polars as pl
import pytest
from fastapi.testclient import TestClient

from civicpulse_analytics.predictions import PredictionService
from civicpulse_api import main as api_main

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client(sample_models):
    pred = PredictionService(sample_models.root)
    api_main.app.dependency_overrides[api_main.get_predictions] = lambda: pred
    with TestClient(api_main.app) as c:
        yield c
    api_main.app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def run_dir(sample_models):
    return sample_models.root / "models" / "runs" / "sample-run"


def test_registry_describes_current_run(client, run_dir):
    body = client.get("/api/v1/models").json()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert body["run_id"] == "sample-run"
    assert body["data"]["fact_sha256"] == manifest["data"]["fact_sha256"]
    assert body["models"]["forecast"]["selected"] in body["models"]["forecast"]["candidates"]
    assert body["models"]["delay_risk"]["target"] == "request_not_closed_within_7_days"


def test_forecast_endpoint_returns_seven_days_matching_file(client, run_dir):
    r = client.get("/api/v1/predictions/forecast", params={"category": "heat_hot_water", "borough": "BRONX"})
    assert r.status_code == 200
    body = r.json()
    expected = (
        pl.read_parquet(run_dir / "forecast.parquet")
        .filter((pl.col("category_key") == "heat_hot_water") & (pl.col("borough") == "BRONX"))
        .sort("target_date")
    )
    assert [p["predicted"] for p in body["forecast"]] == pytest.approx(expected["predicted"].to_list())
    assert [p["horizon"] for p in body["forecast"]] == list(range(1, 8))
    assert all(p["predicted"] >= 0 for p in body["forecast"])
    assert all(h["date"] <= body["origin"] for h in body["history"])  # history never extends past origin


@pytest.mark.parametrize(
    "params",
    [
        {"category": "heat_hot_water", "borough": "ATLANTIS"},
        {"category": "fireworks", "borough": "BRONX"},
        {"category": "heat_hot_water"},
        {"category": "heat_hot_water", "borough": "bronx; drop table"},
    ],
)
def test_forecast_rejects_invalid_series(client, params):
    assert client.get("/api/v1/predictions/forecast", params=params).status_code == 422


def test_backtest_points_are_out_of_sample(client):
    r = client.get(
        "/api/v1/predictions/forecast/backtest",
        params={"category": "noise_residential", "borough": "QUEENS", "horizon": 3, "model": "naive"},
    )
    assert r.status_code == 200
    pts = r.json()["points"]
    assert pts and all(p["origin"] < p["target_date"] for p in pts)
    assert (
        client.get(
            "/api/v1/predictions/forecast/backtest",
            params={"category": "noise_residential", "borough": "QUEENS", "horizon": 9},
        ).status_code
        == 422
    )


def test_delay_risk_list_only_returns_unresolved_predictions(client, run_dir):
    r = client.get("/api/v1/predictions/delay-risk", params={"limit": 100})
    assert r.status_code == 200
    rows = r.json()["rows"]
    scores = pl.read_parquet(run_dir / "delay_scores.parquet")
    immature = set(scores.filter(pl.col("split") == "immature")["unique_key"].to_list())
    assert {x["unique_key"] for x in rows} <= immature
    risks = [x["risk"] for x in rows]
    assert risks == sorted(risks, reverse=True) and all(0 <= x <= 1 for x in risks)
    assert client.get("/api/v1/predictions/delay-risk", params={"limit": 1000}).status_code == 422


def test_delay_risk_request_detail(client, run_dir):
    scores = pl.read_parquet(run_dir / "delay_scores.parquet")
    key = scores.filter(pl.col("split") == "immature")["unique_key"][0]
    body = client.get(f"/api/v1/predictions/delay-risk/{key}").json()
    assert body["label"] is None and body["explanation"] and "causality_note" in body
    test_key = scores.filter(pl.col("split") == "test")["unique_key"][0]
    assert client.get(f"/api/v1/predictions/delay-risk/{test_key}").json()["label"] in (0, 1)
    assert client.get("/api/v1/predictions/delay-risk/999999999999").status_code == 404
    assert client.get("/api/v1/predictions/delay-risk/abc").status_code == 422


def test_anomaly_endpoints(client):
    r = client.get("/api/v1/anomalies", params={"limit": 10})
    assert r.status_code == 200 and r.json()["label_status"].startswith("unverified")
    s = client.get("/api/v1/anomalies/series", params={"category": "heat_hot_water", "borough": "CITYWIDE"})
    assert s.status_code == 200
    pts = s.json()["points"]
    assert pts and {"actual", "expected", "score", "is_anomaly"} <= set(pts[0])
    too_long = client.get(
        "/api/v1/anomalies/series",
        params={"category": "heat_hot_water", "borough": "BRONX", "start": "2023-01-01", "end": "2026-01-01"},
    )
    assert too_long.status_code == 422


@pytest.mark.parametrize("name", ["forecast", "delay_risk", "anomaly"])
def test_evaluation_and_explanations(client, name):
    assert client.get(f"/api/v1/models/{name}/evaluation").status_code == 200
    ex = client.get(f"/api/v1/models/{name}/explanations").json()
    assert "causality_note" in ex


def test_unknown_model_name_and_write_methods_rejected(client):
    assert client.get("/api/v1/models/train/evaluation").status_code == 422
    assert client.post("/api/v1/models").status_code == 405
    assert client.put("/api/v1/predictions/forecast").status_code == 405


def test_models_unavailable_returns_503(tmp_path):
    pred = PredictionService(tmp_path)
    api_main.app.dependency_overrides[api_main.get_predictions] = lambda: pred
    try:
        with TestClient(api_main.app) as c:
            r = c.get("/api/v1/models")
        assert r.status_code == 503 and r.json()["code"] == "data_unavailable"
    finally:
        api_main.app.dependency_overrides.clear()
