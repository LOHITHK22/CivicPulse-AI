"""Seven-day demand forecasting by category x borough with rolling-origin backtesting.

Definitions
* Series: daily count of requests *created* per (category_key, borough), on a dense calendar.
* Origin ``t``: the last fully observed day. A forecast made at origin ``t`` predicts days
  ``t+1 .. t+H`` (H = 7) using ONLY counts dated ``<= t``.
* Leakage control: every feature for (series, origin t, horizon h) is computed from indices
  ``<= t``; a model evaluated at origin ``T`` is trained only on rows whose TARGET date
  ``t + h <= T``. Both rules are asserted in tests.

Models
* ``naive``           y(t+h) = y(t)
* ``seasonal_naive``  y(t+h) = y(t+h-7)            (same weekday last week; valid because h <= 7)
* ``rolling_mean``    y(t+h) = mean(y(t-27 .. t))
* ``lightgbm``        one global direct multi-horizon model, retrained at every origin, trained on
                      log1p(count) relative to the 28-day level (scale-free across series).

Prediction intervals (all models): sequential split-conformal. At origin ``T`` the interval for
category ``c`` and horizon ``h`` uses empirical quantiles of the scaled residual
``(y - yhat) / sqrt(yhat + 1)`` from earlier backtest forecasts of the same model, category and
horizon whose target dates are ``<= T``. No interval is produced until enough residuals exist.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from civicpulse_modeling import metrics as M
from civicpulse_modeling.config import ForecastConfig
from civicpulse_modeling.data import DailySeries
from civicpulse_shared.logging import log

logger = logging.getLogger(__name__)

MODELS = ("naive", "seasonal_naive", "rolling_mean", "lightgbm")
BASELINES = ("naive", "seasonal_naive", "rolling_mean")
CATEGORICAL_FEATURES = ("category_code", "borough_code")
FEATURES = (
    "level_log",
    *(f"lag{k}" for k in range(7)),
    *(f"same_dow_lag{k}" for k in range(1, 5)),
    "mean7",
    "std28",
    "yoy_same_dow",
    "yoy_week",
    "yoy_shift",
    "horizon",
    "target_dow",
    "target_month",
    "target_doy_sin",
    "target_doy_cos",
    *CATEGORICAL_FEATURES,
)
YEAR = 364  # 52 weeks: keeps the weekday aligned
MIN_ORIGIN_INDEX = YEAR + 27 + 3  # first index with every feature defined


@dataclass
class FeatureFrame:
    """One row per (series, origin index t, horizon h)."""

    series: np.ndarray  # int
    t: np.ndarray  # origin index
    h: np.ndarray  # horizon 1..H
    X: np.ndarray  # float64 (n_rows, len(FEATURES))
    level: np.ndarray  # 28-day mean at origin
    y: np.ndarray  # actual count at t+h (nan if not observed yet)
    baselines: dict[str, np.ndarray]

    @property
    def target_index(self) -> np.ndarray:
        return np.asarray(self.t + self.h)


def _rolling_mean(v: np.ndarray, w: int) -> np.ndarray:
    """Trailing mean over the last ``w`` values INCLUDING index i; nan where i < w - 1."""
    c = np.cumsum(np.c_[np.zeros((v.shape[0], 1)), v], axis=1)
    out = np.full(v.shape, np.nan)
    out[:, w - 1 :] = (c[:, w:] - c[:, :-w]) / w
    return np.asarray(out)


def _rolling_std(v: np.ndarray, w: int) -> np.ndarray:
    m = _rolling_mean(v, w)
    m2 = _rolling_mean(v**2, w)
    return np.asarray(np.sqrt(np.maximum(m2 - m**2, 0)))


def build_features(ds: DailySeries, horizon: int) -> FeatureFrame:
    if horizon > 7:
        raise ValueError("same-weekday features assume horizon <= 7")
    v = ds.values
    n_series, n_days = v.shape
    mean28 = _rolling_mean(v, 28)
    mean7 = _rolling_mean(v, 7)
    std28 = _rolling_std(v, 28)
    mean7_centered = np.full(v.shape, np.nan)  # mean of d-3..d+3
    mean7_centered[:, 3:-3] = mean7[:, 6:]
    cats = sorted({c for c, _ in ds.keys})
    bors = sorted({b for _, b in ds.keys})
    cat_code = np.array([cats.index(c) for c, _ in ds.keys])
    bor_code = np.array([bors.index(b) for _, b in ds.keys])

    t_idx = np.arange(MIN_ORIGIN_INDEX, n_days)
    s_g, t_g, h_g = np.meshgrid(np.arange(n_series), t_idx, np.arange(1, horizon + 1), indexing="ij")
    s, t, h = s_g.ravel(), t_g.ravel(), h_g.ravel()
    d = t + h
    level = mean28[s, t]
    llev = np.log1p(level)

    def rel(x: np.ndarray) -> np.ndarray:
        return np.log1p(x) - llev

    target_dates = ds.dates[0] + d.astype("timedelta64[D]")
    dow = ((target_dates.astype("datetime64[D]").astype(np.int64) + 3) % 7).astype(float)  # Mon=0
    months = target_dates.astype("datetime64[M]").astype(np.int64) % 12 + 1
    doy = (target_dates - target_dates.astype("datetime64[Y]")).astype(np.int64)
    cols = [
        llev,
        *(rel(v[s, t - k]) for k in range(7)),
        *(rel(v[s, d - 7 * k]) for k in range(1, 5)),
        rel(mean7[s, t]),
        std28[s, t] / (level + 1),
        rel(v[s, d - YEAR]),
        rel(mean7_centered[s, d - YEAR]),
        np.log1p(mean7_centered[s, d - YEAR]) - np.log1p(mean28[s, t - YEAR]),
        h.astype(float),
        dow,
        months.astype(float),
        np.sin(2 * np.pi * doy / 365.25),
        np.cos(2 * np.pi * doy / 365.25),
        cat_code[s].astype(float),
        bor_code[s].astype(float),
    ]
    X = np.column_stack(cols)
    y = np.full(len(s), np.nan)
    known = d < n_days
    y[known] = v[s[known], d[known]]
    baselines = {
        "naive": v[s, t],
        "seasonal_naive": v[s, d - 7],
        "rolling_mean": level,
    }
    return FeatureFrame(series=s, t=t, h=h, X=X, level=level, y=y, baselines=baselines)


def origins_for_period(
    ds: DailySeries, start: dt.date, end: dt.date, horizon: int, step: int, min_history: int
) -> list[int]:
    """Origin indices whose whole horizon lies in [start, end] (target dates), stepping weekly."""
    first = ds.index_of(start) - 1
    last_target = ds.index_of(end)
    out = []
    t = first
    while t + horizon <= last_target:
        if t >= max(min_history, MIN_ORIGIN_INDEX):
            out.append(t)
        t += step
    return out


def _lgb_params(cfg: ForecastConfig, seed: int) -> dict[str, Any]:
    return {
        "objective": "regression",
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 4,
        "seed": seed,
        **cfg.lgbm,
    }


def train_lgbm(ff: FeatureFrame, rows: np.ndarray, cfg: ForecastConfig, seed: int) -> lgb.Booster:
    params = _lgb_params(cfg, seed)
    n_estimators = params.pop("n_estimators")
    target = np.log1p(ff.y[rows]) - np.log1p(ff.level[rows])
    ds = lgb.Dataset(
        ff.X[rows],
        label=target,
        feature_name=list(FEATURES),
        categorical_feature=list(CATEGORICAL_FEATURES),
        free_raw_data=True,
    )
    return lgb.train(params, ds, num_boost_round=n_estimators)


def predict_lgbm(model: lgb.Booster, ff: FeatureFrame, rows: np.ndarray, X: np.ndarray | None = None) -> np.ndarray:
    raw = model.predict(ff.X[rows] if X is None else X)
    return np.asarray(np.maximum(np.expm1(np.asarray(raw) + np.log1p(ff.level[rows])), 0.0))


def training_rows(ff: FeatureFrame, origin: int) -> np.ndarray:
    """Rows usable to train a model deployed at ``origin``: target observed on or before origin."""
    return np.flatnonzero((ff.target_index <= origin) & ~np.isnan(ff.y))


@dataclass
class Backtest:
    frame: pl.DataFrame  # one row per (model, origin, series, horizon)
    fold_models: dict[int, lgb.Booster]


def run_backtest(
    ds: DailySeries, ff: FeatureFrame, origins: list[int], period: dict[int, str], cfg: ForecastConfig, seed: int
) -> Backtest:
    parts: list[pl.DataFrame] = []
    fold_models: dict[int, lgb.Booster] = {}
    for T in origins:
        pred_rows = np.flatnonzero(ff.t == T)
        train = training_rows(ff, T)
        assert ff.target_index[train].max() <= T, "leakage: training target after origin"
        model = train_lgbm(ff, train, cfg, seed)
        fold_models[T] = model
        preds = {name: ff.baselines[name][pred_rows] for name in BASELINES}
        preds["lightgbm"] = predict_lgbm(model, ff, pred_rows)
        for name, p in preds.items():
            parts.append(_rows_frame(ds, ff, pred_rows, name, p, period[T]))
    log(logger, logging.INFO, "forecast backtest done", origins=len(origins))
    return Backtest(frame=pl.concat(parts), fold_models=fold_models)


def _rows_frame(
    ds: DailySeries, ff: FeatureFrame, rows: np.ndarray, model: str, pred: np.ndarray, period: str
) -> pl.DataFrame:
    origin_dates = ds.dates[ff.t[rows]].astype(dt.date)
    target_dates = ds.dates[ff.t[rows]] + ff.h[rows].astype("timedelta64[D]")
    return pl.DataFrame(
        {
            "model": [model] * len(rows),
            "period": [period] * len(rows),
            "origin": origin_dates.tolist(),
            "origin_index": ff.t[rows],
            "target_date": target_dates.astype(dt.date).tolist(),
            "horizon": ff.h[rows],
            "category_key": [ds.keys[i][0] for i in ff.series[rows]],
            "borough": [ds.keys[i][1] for i in ff.series[rows]],
            "actual": ff.y[rows],
            "predicted": pred.astype(float),
        }
    )


def add_conformal_intervals(bt: pl.DataFrame, level: float, min_residuals: int) -> pl.DataFrame:
    """Sequential split-conformal intervals (see module docstring). Uses only past residuals."""
    alpha = 1 - level
    df = bt.with_columns(
        ((pl.col("actual") - pl.col("predicted")) / (pl.col("predicted") + 1).sqrt()).alias("_r"),
        (pl.col("origin_index") + pl.col("horizon")).alias("_ti"),
    )
    lower = np.full(df.height, np.nan)
    upper = np.full(df.height, np.nan)
    keys = df.select("model", "category_key", "horizon").unique().iter_rows()
    for model, cat, h in keys:
        mask = (df["model"] == model) & (df["category_key"] == cat) & (df["horizon"] == h)
        idx = np.flatnonzero(mask.to_numpy())
        sub = df[idx]
        r = sub["_r"].to_numpy()
        ti = sub["_ti"].to_numpy()
        origin = sub["origin_index"].to_numpy()
        pred = sub["predicted"].to_numpy()
        order = np.argsort(ti, kind="stable")
        ti_sorted, r_sorted = ti[order], r[order]
        for T in np.unique(origin):
            pool = r_sorted[: np.searchsorted(ti_sorted, T, side="right")]
            pool = pool[~np.isnan(pool)]
            if len(pool) < min_residuals:
                continue
            q_lo, q_hi = np.quantile(pool, [alpha / 2, 1 - alpha / 2])
            sel = np.flatnonzero(origin == T)
            scale = np.sqrt(pred[sel] + 1)
            lower[idx[sel]] = np.maximum(pred[sel] + q_lo * scale, 0)
            upper[idx[sel]] = np.maximum(pred[sel] + q_hi * scale, 0)
    return bt.with_columns(pl.Series("lower", lower), pl.Series("upper", upper))


def interval_quantiles(
    bt: pl.DataFrame, model: str, level: float, min_residuals: int, up_to_target_index: int
) -> dict[tuple[str, int], tuple[float, float]]:
    """Residual quantiles for a production forecast (all backtest residuals up to the origin)."""
    alpha = 1 - level
    df = bt.filter(
        (pl.col("model") == model)
        & ((pl.col("origin_index") + pl.col("horizon")) <= up_to_target_index)
        & pl.col("actual").is_not_nan()
    ).with_columns(((pl.col("actual") - pl.col("predicted")) / (pl.col("predicted") + 1).sqrt()).alias("_r"))
    out: dict[tuple[str, int], tuple[float, float]] = {}
    for (cat, h), g in df.group_by(["category_key", "horizon"]):
        r = g["_r"].to_numpy()
        if len(r) >= min_residuals:
            lo, hi = np.quantile(r, [alpha / 2, 1 - alpha / 2])
            out[(str(cat), int(h))] = (float(lo), float(hi))
    return out


# ---------------------------------------------------------------- evaluation


def _metric_row(g: pl.DataFrame) -> dict[str, float]:
    y = g["actual"].to_numpy()
    p = g["predicted"].to_numpy()
    lo = g["lower"].to_numpy()
    hi = g["upper"].to_numpy()
    with_iv = ~np.isnan(lo)
    return {
        "n": len(y),
        "actual_total": float(y.sum()),
        "mae": M.mae(y, p),
        "wape": M.wape(y, p),
        "bias": M.bias(y, p),
        "interval_coverage": M.coverage(y, lo, hi),
        "interval_mean_width": M.mean_width(lo, hi),
        "n_with_interval": int(with_iv.sum()),
    }


def evaluate(bt: pl.DataFrame, by: list[str]) -> list[dict[str, Any]]:
    out = []
    for key, g in bt.group_by(by, maintain_order=True):
        out.append(dict(zip(by, key, strict=True)) | _metric_row(g))
    return sorted(out, key=lambda r: tuple(str(r[k]) for k in by))


def select_model(overall: list[dict[str, Any]]) -> tuple[str, dict[str, float]]:
    """Lowest VALIDATION WAPE wins (ties -> simpler model, by MODELS order)."""
    val = {r["model"]: r["wape"] for r in overall if r["period"] == "validation"}
    ranked = sorted(MODELS, key=lambda m: (val.get(m, float("inf")), MODELS.index(m)))
    return ranked[0], val


def permutation_importance(
    ff: FeatureFrame, fold_models: dict[int, lgb.Booster], origins: list[int], repeats: int, seed: int
) -> list[dict[str, float]]:
    """Increase in test WAPE when one feature is shuffled across the rows of each fold."""
    rng = np.random.default_rng(seed)
    rows_by_fold = {T: np.flatnonzero(ff.t == T) for T in origins}
    y = np.concatenate([ff.y[r] for r in rows_by_fold.values()])
    base = np.concatenate([predict_lgbm(fold_models[T], ff, r) for T, r in rows_by_fold.items()])
    base_wape = M.wape(y, base)
    out: list[dict[str, Any]] = []
    for j, name in enumerate(FEATURES):
        incs = []
        for _ in range(repeats):
            preds = []
            for T, r in rows_by_fold.items():
                X = ff.X[r].copy()
                X[:, j] = X[rng.permutation(len(r)), j]
                preds.append(predict_lgbm(fold_models[T], ff, r, X))
            incs.append(M.wape(y, np.concatenate(preds)) - base_wape)
        out.append(
            {"feature": name, "wape_increase_mean": float(np.mean(incs)), "wape_increase_std": float(np.std(incs))}
        )
    return sorted(out, key=lambda r: -r["wape_increase_mean"])
