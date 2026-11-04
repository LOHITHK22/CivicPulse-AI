"""Fixtures for modeling tests (imported by the test modules; not a conftest to avoid name clashes).

``synthetic_warehouse`` writes a SYNTHETIC ``fact_service_requests`` table (Poisson counts with a
weekly pattern and random closure times). It exists only to unit-test leakage, splitting and
serialization logic quickly and deterministically. It is never used for reported metrics.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from civicpulse_modeling.config import ModelingConfig, load_modeling_config
from civicpulse_shared.config import REPO_ROOT
from civicpulse_shared.paths import DataPaths, atomic_write_json

CI_CONFIG = REPO_ROOT / "configs" / "modeling.ci.yaml"
BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]
START = dt.date(2023, 1, 1)
END = dt.date(2026, 10, 31)


def make_fact(seed: int = 7, start: dt.date = START, end: dt.date = END, mean: float = 3.0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: dict[str, list[object]] = {
        k: []
        for k in (
            "unique_key",
            "created_at",
            "closed_at",
            "created_date",
            "created_hour",
            "created_dow",
            "created_month",
            "category_key",
            "descriptor",
            "borough",
            "community_board",
            "channel",
            "location_type",
            "dq_closed_before_created",
            "dq_closed_in_future",
            "dq_closed_status_without_date",
        )
    }
    key = 1
    n_days = (end - start).days + 1
    for di in range(n_days):
        day = start + dt.timedelta(days=di)
        weekly = 1.4 if day.isoweekday() >= 6 else 1.0
        for ci, cat in enumerate(("heat_hot_water", "noise_residential")):
            for bi, bor in enumerate(BOROUGHS):
                lam = (
                    mean
                    * weekly
                    * (1 + 0.3 * bi)
                    * (1.5 if (cat == "heat_hot_water" and day.month in (1, 2, 12)) else 1)
                )
                for _ in range(rng.poisson(lam)):
                    created = dt.datetime.combine(day, dt.time()) + dt.timedelta(seconds=int(rng.integers(0, 86400)))
                    slow = rng.random() < (0.2 + 0.15 * ci + 0.05 * bi)
                    hours = rng.exponential(400 if slow else 30)
                    closed = None if rng.random() < 0.03 else created + dt.timedelta(hours=float(hours))
                    rows["unique_key"].append(str(key))
                    rows["created_at"].append(created)
                    rows["closed_at"].append(closed)
                    rows["created_date"].append(day)
                    rows["created_hour"].append(created.hour)
                    rows["created_dow"].append(day.isoweekday())
                    rows["created_month"].append(day.month)
                    rows["category_key"].append(cat)
                    rows["descriptor"].append(["A", "B", "C"][int(rng.integers(0, 3))])
                    rows["borough"].append(bor)
                    rows["community_board"].append(f"{bi:02d} {bor}")
                    rows["channel"].append(["PHONE", "ONLINE", "MOBILE"][int(rng.integers(0, 3))])
                    rows["location_type"].append("RESIDENTIAL BUILDING")
                    rows["dq_closed_before_created"].append(False)
                    rows["dq_closed_in_future"].append(False)
                    rows["dq_closed_status_without_date"].append(False)
                    key += 1
    return pl.DataFrame(rows).with_columns(
        pl.col("created_hour").cast(pl.Int8), pl.col("created_dow").cast(pl.Int8), pl.col("created_month").cast(pl.Int8)
    )


def write_warehouse(root: Path, fact: pl.DataFrame) -> DataPaths:
    paths = DataPaths(root)
    paths.ensure()
    fact.write_parquet(paths.table("fact_service_requests"))
    atomic_write_json(paths.state / "last_build.json", {"build_id": "synthetic", "source_rows_updated_at": None})
    return paths


@pytest.fixture(scope="session")
def synthetic_fact() -> pl.DataFrame:
    return make_fact()


@pytest.fixture(scope="session")
def synthetic_paths(tmp_path_factory: pytest.TempPathFactory, synthetic_fact: pl.DataFrame) -> DataPaths:
    return write_warehouse(tmp_path_factory.mktemp("synthetic") / "data", synthetic_fact)


def fast_config() -> ModelingConfig:
    """configs/modeling.ci.yaml: production code paths, smaller models (test speed only)."""
    return load_modeling_config(CI_CONFIG)


@pytest.fixture(scope="session")
def fast_cfg() -> ModelingConfig:
    return fast_config()
