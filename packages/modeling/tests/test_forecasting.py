"""Forecasting: temporal leakage, origin construction, baselines, conformal intervals, serialization."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
from modeling_fixtures import fast_cfg, synthetic_fact, synthetic_paths  # noqa: F401  (fixtures)

from civicpulse_modeling import forecasting as F
from civicpulse_modeling.data import DailySeries, daily_series


def _toy_series(n_days: int = 900, seed: int = 0) -> DailySeries:
    rng = np.random.default_rng(seed)
    dates = np.arange(np.datetime64("2023-01-01"), np.datetime64("2023-01-01") + np.timedelta64(n_days, "D"))
    values = rng.poisson(20, size=(4, n_days)).astype(float)
    keys = [("a", "BRONX"), ("a", "QUEENS"), ("b", "BRONX"), ("b", "QUEENS")]
    return DailySeries(keys=keys, dates=dates, values=values, source_gap_dates=[])


def test_features_use_only_data_up_to_origin():
    ds = _toy_series()
    ff = F.build_features(ds, 7)
    t0 = 600
    perturbed = ds.values.copy()
    perturbed[:, t0 + 1 :] = np.random.default_rng(1).poisson(500, size=perturbed[:, t0 + 1 :].shape)
    ff2 = F.build_features(DailySeries(ds.keys, ds.dates, perturbed, []), 7)
    rows = ff.t <= t0
    assert np.array_equal(ff.X[rows], ff2.X[rows], equal_nan=True), "a feature at origin <= t0 changed"
    assert np.array_equal(ff.baselines["naive"][rows], ff2.baselines["naive"][rows])
    assert np.array_equal(ff.baselines["seasonal_naive"][rows], ff2.baselines["seasonal_naive"][rows])
    # ...while targets after t0 did change, so the test has power.
    assert not np.array_equal(ff.y[rows], ff2.y[rows], equal_nan=True)


def test_training_rows_never_include_targets_after_origin():
    ds = _toy_series()
    ff = F.build_features(ds, 7)
    for origin in (500, 650, 800):
        rows = F.training_rows(ff, origin)
        assert len(rows) > 0
        assert (ff.target_index[rows] <= origin).all()
        # Every row whose target is observed by the origin is used (expanding window).
        expected = np.flatnonzero((ff.target_index <= origin) & ~np.isnan(ff.y))
        assert np.array_equal(rows, expected)


def test_baseline_definitions():
    ds = _toy_series()
    ff = F.build_features(ds, 7)
    i = np.flatnonzero((ff.t == 700) & (ff.h == 3) & (ff.series == 2))[0]
    v = ds.values[2]
    assert ff.baselines["naive"][i] == v[700]
    assert ff.baselines["seasonal_naive"][i] == v[703 - 7]
    assert np.isclose(ff.baselines["rolling_mean"][i], v[673:701].mean())
    assert ff.y[i] == v[703]


def test_origins_cover_period_with_full_horizon():
    ds = _toy_series()
    start, end = dt.date(2024, 6, 1), dt.date(2024, 7, 31)
    origins = F.origins_for_period(ds, start, end, horizon=7, step=7, min_history=400)
    assert origins
    first_target = ds.dates[origins[0] + 1].astype(dt.date)
    last_target = ds.dates[origins[-1] + 7].astype(dt.date)
    assert first_target == start
    assert last_target <= end
    assert np.all(np.diff(origins) == 7)


def test_conformal_intervals_use_only_past_residuals():
    rng = np.random.default_rng(3)
    origins = np.repeat(np.arange(100, 100 + 7 * 30, 7), 7)
    horizons = np.tile(np.arange(1, 8), 30)
    bt = pl.DataFrame(
        {
            "model": ["m"] * len(origins),
            "category_key": ["a"] * len(origins),
            "borough": ["X"] * len(origins),
            "origin_index": origins,
            "horizon": horizons,
            "actual": rng.poisson(50, len(origins)).astype(float),
            "predicted": np.full(len(origins), 50.0),
        }
    )
    out = F.add_conformal_intervals(bt, 0.9, min_residuals=5)
    # Corrupt the actuals of the LAST 5 origins: intervals of earlier origins must not move.
    late = bt["origin_index"] >= origins.max() - 7 * 4
    bt2 = bt.with_columns(pl.when(late).then(pl.lit(5000.0)).otherwise(pl.col("actual")).alias("actual"))
    out2 = F.add_conformal_intervals(bt2, 0.9, min_residuals=5)
    early = (bt["origin_index"] + 7 < origins.max() - 7 * 4).to_numpy()
    assert np.array_equal(out["lower"].to_numpy()[early], out2["lower"].to_numpy()[early], equal_nan=True)
    assert np.array_equal(out["upper"].to_numpy()[early], out2["upper"].to_numpy()[early], equal_nan=True)
    # The first origin has no past residuals -> no interval.
    assert out.filter(pl.col("origin_index") == origins.min())["lower"].is_nan().all()
    lo, hi = out["lower"].to_numpy(), out["upper"].to_numpy()
    ok = ~np.isnan(lo)
    assert ok.any() and (lo[ok] <= hi[ok]).all() and (lo[ok] >= 0).all()


def test_lightgbm_backtest_serialization_and_validity(synthetic_paths, fast_cfg, tmp_path):  # noqa: F811
    ds = daily_series(synthetic_paths, fast_cfg.boroughs, end=dt.date(2026, 10, 31))
    ff = F.build_features(ds, 7)
    origin = ds.index_of(dt.date(2026, 6, 30))
    model = F.train_lgbm(ff, F.training_rows(ff, origin), fast_cfg.forecast, fast_cfg.seed)
    rows = np.flatnonzero(ff.t == origin)
    pred = F.predict_lgbm(model, ff, rows)
    assert pred.shape == (len(ds.keys) * 7,)
    assert np.isfinite(pred).all() and (pred >= 0).all()
    # Save -> load gives identical predictions.
    path = tmp_path / "m.txt"
    model.save_model(str(path))
    import lightgbm as lgb

    loaded = lgb.Booster(model_file=str(path))
    assert np.allclose(F.predict_lgbm(loaded, ff, rows), pred)
    # Deterministic: retraining with the same seed reproduces the model.
    again = F.train_lgbm(ff, F.training_rows(ff, origin), fast_cfg.forecast, fast_cfg.seed)
    assert np.array_equal(F.predict_lgbm(again, ff, rows), pred)


def test_daily_series_is_dense_and_reports_source_gaps(synthetic_fact, tmp_path):  # noqa: F811
    from modeling_fixtures import write_warehouse

    gap_day = dt.date(2024, 3, 5)
    paths = write_warehouse(tmp_path / "d", synthetic_fact.filter(pl.col("created_date") != gap_day))
    ds = daily_series(paths, ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"], end=dt.date(2024, 12, 31))
    assert ds.source_gap_dates == [gap_day]
    assert ds.values[:, ds.index_of(gap_day)].sum() == 0  # reported, not imputed
    assert len(ds.dates) == (dt.date(2024, 12, 31) - dt.date(2023, 1, 1)).days + 1
    expected = synthetic_fact.filter(
        (pl.col("created_date") <= dt.date(2024, 12, 31)) & (pl.col("created_date") != gap_day)
    ).height
    assert ds.values.sum() == expected
