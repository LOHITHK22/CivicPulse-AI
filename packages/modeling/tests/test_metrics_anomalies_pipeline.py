"""Metric definitions (hand-computed), anomaly detection/evaluation, and the end-to-end pipeline."""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import polars as pl
import pytest
from modeling_fixtures import fast_cfg, synthetic_fact, synthetic_paths, write_warehouse  # noqa: F401

from civicpulse_modeling import anomalies as A
from civicpulse_modeling import metrics as M
from civicpulse_modeling.data import DailySeries

# ---------------------------------------------------------------- metrics


def test_regression_metrics_hand_computed():
    y = np.array([10.0, 0.0, 5.0])
    p = np.array([8.0, 1.0, 5.0])
    assert M.mae(y, p) == pytest.approx(1.0)
    assert M.wape(y, p) == pytest.approx(3 / 15)
    assert M.bias(y, p) == pytest.approx(-1 / 3)
    assert np.isnan(M.wape(np.zeros(3), p))
    lo, hi = np.array([9.0, 0.0, np.nan]), np.array([11.0, 0.5, np.nan])
    assert M.coverage(y, lo, hi) == pytest.approx(1.0)  # 2 of 2 rows with an interval
    assert M.mean_width(lo, hi) == pytest.approx(1.25)
    assert M.relative_improvement(0.20, 0.15) == pytest.approx(0.25)


def test_classification_metrics_hand_computed():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.1, 0.4, 0.35, 0.8])
    assert M.brier(y, p) == pytest.approx((0.01 + 0.16 + 0.4225 + 0.04) / 4)
    assert M.roc_auc(y, p) == pytest.approx(0.75)
    assert M.pr_auc(y, p) == pytest.approx((1 / 1 + 2 / 3) / 2)
    t = M.threshold_metrics(y, p, 0.35)
    assert (t["precision"], t["recall"]) == (pytest.approx(2 / 3), 1.0)
    assert np.isnan(M.roc_auc(np.zeros(4), p))
    assert M.precision_at_top(y, p, 0.25)["precision"] == 1.0


def test_best_f1_threshold_and_ece():
    y = np.array([0, 0, 1, 1, 1])
    p = np.array([0.1, 0.2, 0.6, 0.7, 0.9])
    assert M.best_f1_threshold(y, p) == pytest.approx(0.6)
    perfect = M.ece(np.array([0, 1] * 50), np.array([0.5] * 100), n_bins=2)
    assert perfect == pytest.approx(0.0)
    assert M.ece(np.zeros(100), np.full(100, 0.3), n_bins=5) == pytest.approx(0.3)


def test_paired_bootstrap_detects_clear_difference_and_is_seeded():
    rng = np.random.default_rng(0)
    y = rng.poisson(50, 700).astype(float)
    good = y + rng.normal(0, 2, 700)
    bad = y + rng.normal(0, 10, 700)
    groups = np.repeat(np.arange(100), 7)
    r1 = M.paired_bootstrap_wape_diff(y, bad, good, groups, n_boot=300, seed=1)
    r2 = M.paired_bootstrap_wape_diff(y, bad, good, groups, n_boot=300, seed=1)
    assert r1 == r2
    assert r1["ci_low"] > 0 and r1["diff"] == pytest.approx(M.wape(y, bad) - M.wape(y, good))


# ---------------------------------------------------------------- anomalies


def _flat(n=400, lam=50, seed=0):
    return np.random.default_rng(seed).poisson(lam, n).astype(float)


def test_detector_scores_use_only_past_days(fast_cfg):  # noqa: F811
    y = _flat()
    r = A.score_series(y, fast_cfg.anomaly)
    y2 = y.copy()
    y2[300:] = 10_000
    r2 = A.score_series(y2, fast_cfg.anomaly)
    assert np.array_equal(r["score"][:300], r2["score"][:300], equal_nan=True)
    assert np.isnan(r["score"][: 7 * fast_cfg.anomaly.baseline_weeks]).all()  # no score without history


def test_detector_finds_large_spike_and_drop_but_not_noise(fast_cfg):  # noqa: F811
    y = _flat()
    y[250] += 150  # +300% spike
    y[320] = 5  # -90% drop
    r = A.score_series(y, fast_cfg.anomaly)
    f = A.flag(r["score"], r["residual"], 3.5, 5)
    assert f[250] and r["residual"][250] > 0
    assert f[320] and r["residual"][320] < 0
    clean = A.score_series(_flat(seed=5), fast_cfg.anomaly)
    assert A.flag(clean["score"], clean["residual"], 3.5, 5).mean() < 0.01


def test_injection_evaluation_is_reproducible_and_labelled_synthetic(fast_cfg):  # noqa: F811
    dates = np.arange(np.datetime64("2023-01-01"), np.datetime64("2023-01-01") + np.timedelta64(800, "D"))
    vals = np.vstack([_flat(800, 40, 1), _flat(800, 8, 2)])
    ds = DailySeries([("a", "BRONX"), ("a", "QUEENS")], dates, vals, [])
    e1 = A.evaluate_injections(ds, fast_cfg.anomaly, seed=3)
    e2 = A.evaluate_injections(ds, fast_cfg.anomaly, seed=3)
    assert e1 == e2 and e1["synthetic"] is True
    big = [
        r
        for r in e1["by_magnitude"]
        if r["magnitude"] == 2.0
        and r["direction"] == "spike"
        and r["detector"] == "seasonal_mad"
        and r["threshold"] == 3.5
    ]
    assert big and all(r["recall"] >= 0.5 for r in big)
    summary = {(r["detector"], r["threshold"]): r for r in e1["summary"]}
    # Higher thresholds can only lower recall on the same injections.
    assert summary[("seasonal_mad", 3.0)]["recall"] >= summary[("seasonal_mad", 4.0)]["recall"]


# ---------------------------------------------------------------- end to end


@pytest.fixture(scope="module")
def trained(synthetic_paths, fast_cfg):  # noqa: F811
    from civicpulse_modeling.pipeline import train_all

    return train_all(fast_cfg, synthetic_paths, run_id="test-run-1")


def test_pipeline_writes_complete_run(trained, synthetic_paths):  # noqa: F811
    run = synthetic_paths.root / "models" / "runs" / "test-run-1"
    for f in (
        "manifest.json",
        "forecast.parquet",
        "forecast_backtest.parquet",
        "forecast_evaluation.json",
        "forecast_lightgbm.txt",
        "delay_scores.parquet",
        "delay_evaluation.json",
        "delay_lightgbm.txt",
        "delay_logreg.json",
        "delay_encoder.json",
        "anomaly_scores.parquet",
        "anomaly_evaluation.json",
    ):
        assert (run / f).exists(), f
    current = json.loads((synthetic_paths.root / "models" / "current.json").read_text())
    assert current["run_id"] == "test-run-1"
    m = json.loads((run / "manifest.json").read_text())
    assert m["data"]["build_id"] == "synthetic" and len(m["data"]["fact_sha256"]) == 64

    fc = pl.read_parquet(run / "forecast.parquet")
    assert fc.height == 10 * 7
    assert (fc["predicted"] >= 0).all() and fc["origin"].n_unique() == 1
    assert fc["target_date"].min() == fc["origin"][0] + dt.timedelta(days=1)
    ok = fc.filter(pl.col("lower").is_not_nan())
    assert ok.height > 0 and (ok["lower"] <= ok["upper"]).all()

    ev = json.loads((run / "forecast_evaluation.json").read_text())
    assert ev["selection"]["selected"] in ("naive", "seasonal_naive", "rolling_mean", "lightgbm")
    assert {r["model"] for r in ev["overall"]} == {"naive", "seasonal_naive", "rolling_mean", "lightgbm"}
    assert ev["periods"]["validation"]["last_origin"] < ev["periods"]["test"]["first_origin"]

    scores = pl.read_parquet(run / "delay_scores.parquet")
    assert ((scores["risk"] >= 0) & (scores["risk"] <= 1)).all()
    assert (
        scores.filter(pl.col("split") == "immature")["label"].null_count()
        == scores.filter(pl.col("split") == "immature").height
    )  # immature outcomes are never shown as labels
    imm = scores.filter(pl.col("split") == "immature")
    assert imm["explanation"].null_count() == 0
    dr = json.loads((run / "delay_evaluation.json").read_text())
    assert set(dr["models"]) == {"majority_class", "logistic_regression", "lightgbm"}
    assert dr["models"]["majority_class"]["test"]["overall"]["roc_auc"] == pytest.approx(0.5)


def test_pipeline_is_reproducible(trained, synthetic_paths, fast_cfg):  # noqa: F811
    from civicpulse_modeling.pipeline import train_all

    train_all(fast_cfg, synthetic_paths, run_id="test-run-2")
    a = synthetic_paths.root / "models" / "runs" / "test-run-1"
    b = synthetic_paths.root / "models" / "runs" / "test-run-2"
    for f in ("forecast.parquet", "forecast_backtest.parquet", "delay_scores.parquet", "anomaly_scores.parquet"):
        assert pl.read_parquet(a / f).equals(pl.read_parquet(b / f)), f
    ea = json.loads((a / "delay_evaluation.json").read_text())
    eb = json.loads((b / "delay_evaluation.json").read_text())
    ea.pop("elapsed_s"), eb.pop("elapsed_s")
    assert ea == eb
