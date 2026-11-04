"""HTTP contract tests: real FastAPI app over the warehouse built from the real-data sample."""

from __future__ import annotations

import polars as pl
import pytest
from fastapi.testclient import TestClient

from civicpulse_analytics import AnalyticsService
from civicpulse_api import main as api_main
from civicpulse_api.ratelimit import TokenBucketLimiter

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client(sample_warehouse):
    svc = AnalyticsService(sample_warehouse.root)
    api_main.app.dependency_overrides[api_main.get_service] = lambda: svc
    with TestClient(api_main.app) as c:
        yield c
    api_main.app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def fact(sample_warehouse) -> pl.DataFrame:
    return pl.read_parquet(sample_warehouse.table("fact_service_requests"))


def test_health_and_readiness(client, fact):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["ready"] is True and r.json()["fact_rows"] == fact.height


def test_kpis_endpoint_matches_warehouse(client, fact):
    r = client.get("/api/v1/metrics/kpis", params={"borough": ["BRONX", "BROOKLYN"], "category": "heat_hot_water"})
    assert r.status_code == 200
    expected = fact.filter(
        pl.col("borough").is_in(["BRONX", "BROOKLYN"]) & (pl.col("category_key") == "heat_hot_water")
    ).height
    body = r.json()
    assert body["total_requests"] == expected
    assert body["freshness"]["is_live"] is False
    assert "median_hours_to_close" in body["definitions"]


def test_filters_endpoint(client, fact):
    body = client.get("/api/v1/meta/filters").json()
    assert body["total_records"] == fact.height
    assert {c["key"] for c in body["categories"]} == {"noise_residential", "heat_hot_water"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/metrics/volume?grain=week",
        "/api/v1/metrics/breakdown/borough",
        "/api/v1/metrics/breakdown/category_key",
        "/api/v1/metrics/closure-distribution",
        "/api/v1/metrics/hourly",
        "/api/v1/requests",
        "/api/v1/quality",
    ],
)
def test_endpoints_return_200_with_meta(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert r.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/metrics/kpis?borough=atlantis",
        "/api/v1/metrics/kpis?start=2026-05-01&end=2026-01-01",
        "/api/v1/metrics/kpis?threshold_days=0",
        "/api/v1/metrics/volume?grain=hour",
        "/api/v1/metrics/breakdown/unique_key",
        "/api/v1/requests?sort=secret",
        "/api/v1/requests?page_size=500",
        "/api/v1/requests/abc",
        "/api/v1/metrics/kpis?status=deleted",
    ],
)
def test_invalid_arguments_are_rejected_with_422(client, path):
    r = client.get(path)
    assert r.status_code == 422, (path, r.text)
    assert "Traceback" not in r.text


def test_unknown_request_is_404(client):
    assert client.get("/api/v1/requests/1").status_code == 404


def test_request_detail(client, fact):
    key = fact["unique_key"][0]
    body = client.get(f"/api/v1/requests/{key}").json()
    assert body["unique_key"] == key and "lineage" in body and "latitude" not in body


def test_csv_export_is_bounded_and_labelled(client, fact):
    r = client.get("/api/v1/requests/export.csv", params={"borough": "QUEENS"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    expected = fact.filter(pl.col("borough") == "QUEENS").height
    assert len(lines) - 1 == min(expected, 10_000)
    assert r.headers["X-Total-Matching"] == str(expected)


def test_write_methods_are_refused(client):
    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)("/api/v1/requests").status_code == 405


def test_overlong_url_rejected(client):
    assert client.get("/api/v1/requests?q=" + "a" * 5000).status_code == 414


def test_cors_only_allows_configured_origin(client):
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_token_bucket_limits_and_refills():
    lim = TokenBucketLimiter(per_minute=60, burst=2)
    assert lim.allow("a", now=0)[0] and lim.allow("a", now=0)[0]
    allowed, retry = lim.allow("a", now=0)
    assert not allowed and retry == pytest.approx(1.0)
    assert lim.allow("b", now=0)[0]  # independent per client
    assert lim.allow("a", now=1.01)[0]  # refilled


def test_data_unavailable_returns_503(tmp_path):
    api_main.app.dependency_overrides[api_main.get_service] = lambda: AnalyticsService(tmp_path)
    try:
        with TestClient(api_main.app) as c:
            r = c.get("/api/v1/metrics/kpis")
            assert r.status_code == 503 and r.json()["code"] == "data_unavailable"
            assert c.get("/health/ready").status_code == 503
    finally:
        api_main.app.dependency_overrides.clear()


class _Req:
    def __init__(self, peer: str, xff: str | None = None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def test_client_key_ignores_spoofable_xff_by_default():
    assert api_main.client_key(_Req("10.0.0.5", "1.2.3.4"), 0) == "10.0.0.5"


def test_client_key_uses_rightmost_trusted_hop():
    # Edge proxy appended the real peer (198.51.100.7) after a client-forged value.
    assert api_main.client_key(_Req("10.0.0.5", "6.6.6.6, 198.51.100.7"), 1) == "198.51.100.7"
    assert api_main.client_key(_Req("10.0.0.5"), 1) == "10.0.0.5"  # header missing -> peer
