"""Opt-in LIVE checks against the real NYC Open Data API.  Run with:  pytest -m live

These are excluded from the default run and from CI so the suite never depends on network
availability. They verify assumptions the pipeline relies on.
"""

from __future__ import annotations

import datetime as dt

import pytest

from civicpulse_ingestion.extract import build_where
from civicpulse_ingestion.schema import compare_metadata
from civicpulse_ingestion.socrata import SocrataClient
from civicpulse_shared.config import load_ingestion_config

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live():
    cfg = load_ingestion_config()
    cfg.http.max_retries = 2
    with SocrataClient(cfg.source, cfg.http) as c:
        yield cfg, c


def test_live_schema_has_every_selected_field(live):
    cfg, client = live
    report = compare_metadata(client.fetch_metadata(), cfg.source.fields)
    assert report.missing_selected == []


def test_live_keyset_pagination_is_contiguous(live):
    cfg, client = live
    where = build_where(cfg, start=dt.date(2026, 10, 30), end=dt.date(2026, 10, 31))  # one day keeps the server fast
    first = client.fetch_page(where, after_id=None, limit=6).records
    second = client.fetch_page(where, after_id=first[2][":id"], limit=3).records
    assert [r[":id"] for r in second] == [r[":id"] for r in first[3:6]]


def test_live_records_are_in_configured_categories(live):
    cfg, client = live
    where = build_where(cfg, start=dt.date(2026, 10, 30), end=dt.date(2026, 10, 31))  # one day keeps the server fast
    recs = client.fetch_page(where, after_id=None, limit=50).records
    assert recs and {r["complaint_type"] for r in recs} <= set(cfg.complaint_types)
    assert all("incident_address" not in r for r in recs)
