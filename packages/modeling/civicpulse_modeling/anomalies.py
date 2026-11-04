"""Anomaly detection on daily request counts (category x borough, plus citywide per category).

Detector (``seasonal_mad``), for day d of one series, using ONLY days before d:
    expected(d) = median of y on the same weekday over the previous ``baseline_weeks`` weeks
    residual(d) = y(d) - expected(d)
    scale(d)    = max(1.4826 * MAD(residuals over the previous ``mad_window_days`` days),
                      sqrt(max(expected(d), 1)))          # Poisson floor for low counts
    score(d)    = residual(d) / scale(d)                   # robust z-score
    flagged     = |score| >= z_threshold and |residual| >= min_abs_deviation

Comparator (``rolling_median_mad``): the same, but expected = median of the previous 28 days (no
weekday seasonality). It exists to test whether the seasonal baseline earns its keep.

Evaluation uses SYNTHETIC injected anomalies on the real series: there are no ground-truth labels
for real anomalies, and none are invented. Detections on the uninjected real data are reported as
"unverified alerts", never as true or false positives.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import polars as pl

from civicpulse_modeling.config import AnomalyConfig
from civicpulse_modeling.data import DailySeries

DETECTORS = ("seasonal_mad", "rolling_median_mad")


def _expected(y: np.ndarray, detector: str, weeks: int) -> np.ndarray:
    n = len(y)
    exp = np.full(n, np.nan)
    if detector == "seasonal_mad":
        for i in range(7 * weeks, n):
            exp[i] = np.median(y[i - 7 * weeks : i : 7])
    elif detector == "rolling_median_mad":
        for i in range(28, n):
            exp[i] = np.median(y[i - 28 : i])
    else:
        raise ValueError(detector)
    return exp


def score_series(y: np.ndarray, cfg: AnomalyConfig, detector: str = "seasonal_mad") -> dict[str, np.ndarray]:
    exp = _expected(y, detector, cfg.baseline_weeks)
    resid = y - exp
    n = len(y)
    mad_scale = np.full(n, np.nan)
    w = cfg.mad_window_days
    for i in range(n):
        past = resid[max(0, i - w) : i]
        past = past[~np.isnan(past)]
        if len(past) >= w // 2:
            mad_scale[i] = 1.4826 * np.median(np.abs(past - np.median(past)))
    scale = np.fmax(mad_scale, np.sqrt(np.maximum(exp, 1)))
    scale[np.isnan(mad_scale)] = np.nan  # not enough history yet: no score
    score = resid / scale
    return {"expected": exp, "residual": resid, "scale": scale, "score": score}


def flag(score: np.ndarray, resid: np.ndarray, threshold: float, min_abs: float) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return (np.abs(score) >= threshold) & (np.abs(resid) >= min_abs)


def with_citywide(ds: DailySeries) -> tuple[list[tuple[str, str]], np.ndarray]:
    keys = list(ds.keys)
    vals = [ds.values]
    for cat in sorted({c for c, _ in ds.keys}):
        idx = [i for i, (c, _) in enumerate(ds.keys) if c == cat]
        keys.append((cat, "CITYWIDE"))
        vals.append(ds.values[idx].sum(axis=0, keepdims=True))
    return keys, np.vstack(vals)


def detect_all(ds: DailySeries, cfg: AnomalyConfig) -> pl.DataFrame:
    """Score every day of every series with the primary detector."""
    keys, values = with_citywide(ds)
    dates = ds.dates.astype(dt.date).tolist()
    parts = []
    for (cat, bor), y in zip(keys, values, strict=True):
        r = score_series(y, cfg)
        flags = flag(r["score"], r["residual"], cfg.z_threshold, cfg.min_abs_deviation)
        parts.append(
            pl.DataFrame(
                {
                    "date": dates,
                    "category_key": [cat] * len(y),
                    "borough": [bor] * len(y),
                    "actual": y,
                    "expected": r["expected"],
                    "residual": r["residual"],
                    "scale": r["scale"],
                    "score": r["score"],
                    "is_anomaly": flags,
                }
            )
        )
    df = pl.concat(parts).with_columns(
        pl.when(~pl.col("is_anomaly"))
        .then(None)
        .when(pl.col("residual") > 0)
        .then(pl.lit("spike"))
        .otherwise(pl.lit("drop"))
        .alias("direction")
    )
    return df.filter(pl.col("score").is_not_nan())


# ---------------------------------------------------------------- synthetic-injection evaluation


def _pick_days(rng: np.random.Generator, lo: int, hi: int, k: int, gap: int) -> list[int]:
    chosen: list[int] = []
    tries = 0
    while len(chosen) < k and tries < 10_000:
        tries += 1
        d = int(rng.integers(lo, hi))
        if all(abs(d - c) >= gap for c in chosen):
            chosen.append(d)
    return sorted(chosen)


def evaluate_injections(ds: DailySeries, cfg: AnomalyConfig, seed: int) -> dict[str, Any]:
    """Inject known spikes/drops into the real series and measure recall per detector/threshold.

    Each series gets ``injections_per_series`` days (>= ``min_gap_days`` apart) in the evaluation
    window; each injected day gets a random magnitude m and direction. Spike: y + round(m * E);
    drop: y - round(min(m, 0.9) * E), where E is the clean series' seasonal expectation.
    """
    ev = cfg.evaluation
    rng = np.random.default_rng(seed)
    keys, values = with_citywide(ds)
    lo = max(ds.index_of(ev.start), 7 * cfg.baseline_weeks + cfg.mad_window_days)
    hi = len(ds.dates)
    rows = []
    alerts: list[dict[str, Any]] = []
    for (cat, bor), y in zip(keys, values, strict=True):
        clean_exp = _expected(y, "seasonal_mad", cfg.baseline_weeks)
        days = _pick_days(rng, lo, hi, ev.injections_per_series, ev.min_gap_days)
        mags = rng.choice(ev.magnitudes, size=len(days))
        dirs = rng.choice(["spike", "drop"], size=len(days))
        injected = y.copy()
        for d, m, direction in zip(days, mags, dirs, strict=True):
            e = clean_exp[d] if not np.isnan(clean_exp[d]) else y[d]
            delta = round(m * e) if direction == "spike" else -round(min(m, 0.9) * e)
            injected[d] = max(injected[d] + delta, 0)
        injected_mask = np.zeros(len(y), bool)
        injected_mask[days] = True
        for det in DETECTORS:
            r = score_series(injected, cfg, det)
            for thr in ev.thresholds:
                f = flag(r["score"], r["residual"], thr, cfg.min_abs_deviation)
                window = np.zeros(len(y), bool)
                window[lo:hi] = True
                for d, m, direction in zip(days, mags, dirs, strict=True):
                    hit = bool(f[d]) and ((r["residual"][d] > 0) == (direction == "spike"))
                    rows.append(
                        {
                            "detector": det,
                            "threshold": thr,
                            "category_key": cat,
                            "borough": bor,
                            "magnitude": float(m),
                            "direction": str(direction),
                            "detected": hit,
                            "series_mean": float(np.mean(y[lo:hi])),
                        }
                    )
                alerts.append(
                    {
                        "detector": det,
                        "threshold": thr,
                        "alerts_uninjected": int((f & window & ~injected_mask).sum()),
                        "days_uninjected": int((window & ~injected_mask).sum()),
                        "alerts_injected": int((f & injected_mask).sum()),
                    }
                )
    df = pl.DataFrame(rows)
    al = pl.DataFrame(alerts).group_by(["detector", "threshold"]).sum().sort(["detector", "threshold"])
    summary = (
        df.group_by(["detector", "threshold"])
        .agg(pl.col("detected").mean().alias("recall"), pl.len().alias("n_injected"))
        .join(al, on=["detector", "threshold"])
        .with_columns((pl.col("alerts_uninjected") / pl.col("days_uninjected")).alias("alert_rate_uninjected_days"))
        .sort(["detector", "threshold"])
    )
    by_mag = (
        df.group_by(["detector", "threshold", "magnitude", "direction"])
        .agg(pl.col("detected").mean().alias("recall"), pl.len().alias("n"))
        .sort(["detector", "threshold", "direction", "magnitude"])
    )
    by_size = (
        df.with_columns(
            pl.when(pl.col("series_mean") < 20)
            .then(pl.lit("<20/day"))
            .when(pl.col("series_mean") < 100)
            .then(pl.lit("20-100/day"))
            .otherwise(pl.lit(">=100/day"))
            .alias("series_size")
        )
        .group_by(["detector", "threshold", "series_size"])
        .agg(pl.col("detected").mean().alias("recall"), pl.len().alias("n"))
        .sort(["detector", "threshold", "series_size"])
    )
    return {
        "synthetic": True,
        "window_start": ds.dates[lo].astype(dt.date).isoformat(),
        "window_end": ds.dates[hi - 1].astype(dt.date).isoformat(),
        "series": len(keys),
        "summary": summary.to_dicts(),
        "by_magnitude": by_mag.to_dicts(),
        "by_series_size": by_size.to_dicts(),
    }
