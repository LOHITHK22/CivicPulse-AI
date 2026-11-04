"""Read the warehouse for modeling: daily series, completeness and data fingerprints.

Models read ONLY the cleaned warehouse (``fact_service_requests``), never raw pages, so every
model is tied to one reproducible warehouse build (``build_id`` + file checksum).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from civicpulse_shared.paths import DataPaths, read_json


@dataclass(frozen=True)
class DataInfo:
    build_id: str
    fact_sha256: str
    rows: int
    min_created_at: str
    max_created_at: str
    last_complete_date: dt.date
    source_rows_updated_at: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "build_id": self.build_id,
            "fact_sha256": self.fact_sha256,
            "rows": self.rows,
            "min_created_at": self.min_created_at,
            "max_created_at": self.max_created_at,
            "last_complete_date": self.last_complete_date.isoformat(),
            "source_rows_updated_at": self.source_rows_updated_at,
        }


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def fact_path(paths: DataPaths) -> Path:
    p = paths.table("fact_service_requests")
    if not p.exists():
        raise FileNotFoundError(f"{p} not found: run `civicpulse build` first")
    return p


def connect(paths: DataPaths) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("SET TimeZone='UTC'")
    con.execute(f"CREATE VIEW fact AS SELECT * FROM read_parquet('{fact_path(paths).as_posix()}')")
    return con


def data_info(paths: DataPaths) -> DataInfo:
    build = read_json(paths.state / "last_build.json") if (paths.state / "last_build.json").exists() else {}
    with connect(paths) as con:
        row = con.execute(
            """SELECT count(*), min(created_at)::VARCHAR, max(created_at)::VARCHAR,
                      CASE WHEN max(created_at) >= max(created_date) + INTERVAL 23 HOUR
                           THEN max(created_date) ELSE max(created_date) - 1 END
               FROM fact"""
        ).fetchone()
    assert row is not None
    return DataInfo(
        build_id=str(build.get("build_id", "unknown")),
        fact_sha256=sha256_file(fact_path(paths)),
        rows=int(row[0]),
        min_created_at=row[1],
        max_created_at=row[2],
        last_complete_date=row[3],
        source_rows_updated_at=build.get("source_rows_updated_at"),
    )


@dataclass(frozen=True)
class DailySeries:
    """Dense daily counts: ``values[i, j]`` = requests of series ``keys[i]`` created on ``dates[j]``."""

    keys: list[tuple[str, str]]  # (category_key, borough)
    dates: np.ndarray  # datetime64[D]
    values: np.ndarray  # float64 (n_series, n_days)
    source_gap_dates: list[dt.date]  # days with ZERO records across all series (possible source gaps)

    def index_of(self, day: dt.date) -> int:
        return int((np.datetime64(day, "D") - self.dates[0]).astype(int))


def daily_series(paths: DataPaths, boroughs: list[str], end: dt.date, start: dt.date | None = None) -> DailySeries:
    """Category x borough daily counts on a complete calendar (zero where no requests were created).

    Zero counts inside one series are real observations (no request created). A day with zero
    records in EVERY series is far more likely a source outage than true silence, so those days are
    reported as ``source_gap_dates`` (they are never silently imputed).
    """
    with connect(paths) as con:
        df = con.sql(
            """SELECT category_key, borough, created_date, count(*)::DOUBLE AS n
               FROM fact WHERE created_date <= ? AND (? IS NULL OR created_date >= ?)
               GROUP BY ALL""",
            params=[end, start, start],
        ).pl()
        days_df = con.sql(
            "SELECT DISTINCT created_date FROM fact WHERE created_date <= ? AND (? IS NULL OR created_date >= ?)",
            params=[end, start, start],
        ).pl()
    if df.is_empty():
        raise ValueError("no data in the requested range")
    first = start or df["created_date"].min()
    assert isinstance(first, dt.date)
    dates = np.arange(np.datetime64(first, "D"), np.datetime64(end, "D") + np.timedelta64(1, "D"))
    cats = sorted(df["category_key"].unique().to_list())
    keys = [(c, b) for c in cats for b in boroughs]
    values = np.zeros((len(keys), len(dates)))
    pos = {k: i for i, k in enumerate(keys)}
    for c, b, d, n in df.iter_rows():
        if (c, b) in pos:
            values[pos[(c, b)], (np.datetime64(d, "D") - dates[0]).astype(int)] = n
    present = set(days_df["created_date"].to_list())
    gaps = [d.astype(dt.date) for d in dates if d.astype(dt.date) not in present]
    return DailySeries(keys=keys, dates=dates, values=values, source_gap_dates=gaps)


def frame_from_series(ds: DailySeries) -> pl.DataFrame:
    rows = []
    for i, (c, b) in enumerate(ds.keys):
        rows.append(
            pl.DataFrame(
                {
                    "category_key": [c] * len(ds.dates),
                    "borough": [b] * len(ds.dates),
                    "date": ds.dates.astype("datetime64[D]").astype(dt.date).tolist(),
                    "count": ds.values[i],
                }
            )
        )
    return pl.concat(rows)
