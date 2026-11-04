"""Shared pytest fixtures.

``FakeSocrata`` emulates the behaviours of the real SODA endpoint that the pipeline depends on
(verified live on 2026-11-03): ``:id`` keyset pagination in *server* order (deliberately not
string order), ``$limit``, null fields omitted from records, ``:updated_at`` filtering, and
injectable failures (429 with Retry-After, 5xx, timeouts).
"""

from __future__ import annotations

import datetime as dt
import random
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from civicpulse_shared.config import load_ingestion_config
from civicpulse_shared.paths import DataPaths

REPO = Path(__file__).resolve().parent
SAMPLE = REPO / "data" / "samples" / "nyc311_raw_sample.parquet"

_ID_AFTER = re.compile(r":id > '([^']*)'")
_UPDATED_AFTER = re.compile(r":updated_at > '([^']*)'")


def make_record(i: int, **overrides: Any) -> dict[str, Any]:
    created = dt.datetime(2026, 1, 1, 8, 0) + dt.timedelta(hours=i)
    rec: dict[str, Any] = {
        ":id": f"row-{random.Random(i).randrange(16**8):08x}",
        ":updated_at": "2026-02-01T00:00:00.000Z",
        "unique_key": str(60_000_000 + i),
        "created_date": created.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "closed_date": (created + dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.000"),
        "agency": "HPD" if i % 2 else "NYPD",
        "agency_name": "Department of Housing Preservation and Development"
        if i % 2
        else "New York City Police Department",
        "complaint_type": "HEAT/HOT WATER" if i % 2 else "Noise - Residential",
        "descriptor": "ENTIRE BUILDING" if i % 2 else "Loud Music/Party",
        "status": "Closed",
        "borough": ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"][i % 5],
        "incident_zip": "10458",
        "community_board": "07 BRONX",
        "latitude": "40.86",
        "longitude": "-73.89",
    }
    rec.update(overrides)
    return {k: v for k, v in rec.items() if v is not None}  # Socrata omits nulls


class FakeSocrata:
    def __init__(self, records: list[dict[str, Any]], *, metadata: dict[str, Any] | None = None) -> None:
        # Server order is the list order — intentionally NOT sorted by :id string.
        self.records = records
        self.metadata = metadata or fake_metadata()
        self.failures: list[Any] = []  # queue: int status | "timeout" | ("429", retry_after)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failures:
            f = self.failures.pop(0)
            if f == "timeout":
                raise httpx.ReadTimeout("simulated timeout", request=request)
            if isinstance(f, tuple):
                return httpx.Response(int(f[0]), headers={"Retry-After": f[1]}, text="throttled")
            return httpx.Response(int(f), text="simulated failure")
        if request.url.path.startswith("/api/views/"):
            return httpx.Response(200, json=self.metadata)
        where = request.url.params.get("$where", "")
        limit = int(request.url.params.get("$limit", "1000"))
        rows = self.records
        m = _UPDATED_AFTER.search(where)
        if m:
            cutoff = m.group(1)
            rows = [r for r in rows if r[":updated_at"][:19] > cutoff]
        m = _ID_AFTER.search(where)
        if m:
            ids = [r[":id"] for r in rows]
            pos = ids.index(m.group(1)) + 1 if m.group(1) in ids else len(rows)
            rows = rows[pos:]
        return httpx.Response(200, json=rows[:limit])

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def fake_metadata() -> dict[str, Any]:
    from civicpulse_ingestion.schema import load_snapshot

    return {
        "name": "311 Service Requests from 2020 to Present",
        "attribution": "311",
        "rowsUpdatedAt": int(dt.datetime(2026, 2, 1, tzinfo=dt.UTC).timestamp()),
        "columns": [{"fieldName": k, "dataTypeName": v} for k, v in load_snapshot().items()],
    }


@pytest.fixture
def cfg() -> Any:
    c = load_ingestion_config()
    c.http.page_size = 10
    c.http.max_retries = 3
    c.backfill.start = dt.date(2026, 1, 1)
    c.backfill.end = dt.date(2026, 3, 1)
    return c


@pytest.fixture
def paths(tmp_path: Path) -> DataPaths:
    p = DataPaths(tmp_path / "data")
    p.ensure()
    return p


@pytest.fixture
def no_sleep() -> Iterator[list[float]]:
    slept: list[float] = []
    yield slept


@pytest.fixture(scope="session")
def sample_warehouse(tmp_path_factory: pytest.TempPathFactory) -> DataPaths:
    """Warehouse built from the committed REAL NYC 311 sample (see data/samples/README.md)."""
    import shutil

    from civicpulse_ingestion.transform import build_warehouse

    if not SAMPLE.exists():
        pytest.skip("committed sample missing; run `civicpulse sample`")
    root = tmp_path_factory.mktemp("sample") / "data"
    p = DataPaths(root)
    p.ensure()
    run_dir = p.raw / "run=sample"
    run_dir.mkdir(parents=True)
    shutil.copy(SAMPLE, run_dir / "page-00001.parquet")
    c = load_ingestion_config()
    build_warehouse(c, p, snapshot_at=dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.UTC))
    return p


@pytest.fixture(scope="session")
def sample_models(sample_warehouse: DataPaths) -> DataPaths:
    """Train every model on the committed real sample with the CI config (smoke scale)."""
    from civicpulse_modeling.config import load_modeling_config
    from civicpulse_modeling.pipeline import train_all

    cfg_path = REPO / "configs" / "modeling.ci.yaml"
    train_all(load_modeling_config(cfg_path), sample_warehouse, config_path=cfg_path, run_id="sample-run")
    return sample_warehouse
