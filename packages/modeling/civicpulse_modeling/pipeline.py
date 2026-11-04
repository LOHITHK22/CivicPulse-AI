"""Train and evaluate every Milestone 2 model from one warehouse build, reproducibly.

Outputs go to ``<data_dir>/models/runs/<run_id>/`` and ``<data_dir>/models/current.json`` is
switched to the new run only after every file is written (readers never see a partial run).
Nothing here is reachable from the public API: training is an offline CLI step.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import platform
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
import sklearn

from civicpulse_modeling import anomalies as A
from civicpulse_modeling import delay_risk as D
from civicpulse_modeling import forecasting as F
from civicpulse_modeling import metrics as M
from civicpulse_modeling.config import DEFAULT_MODELING_CONFIG, ModelingConfig
from civicpulse_modeling.data import DailySeries, DataInfo, daily_series, data_info
from civicpulse_shared.config import REPO_ROOT
from civicpulse_shared.logging import log
from civicpulse_shared.paths import DataPaths, atomic_write_json

logger = logging.getLogger(__name__)


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - fixed argv, no shell, no user input
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _clean(obj: Any) -> Any:
    """JSON-safe: nan/inf -> None, numpy scalars -> python, dates -> iso."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, dt.date):
        return obj.isoformat()
    return obj


def _span(split: Any) -> dict[str, str | None]:
    """JSON-friendly ``{start, end}`` for a configured date span (end ``None`` = open-ended)."""
    return {
        "start": split.start.isoformat() if split.start else None,
        "end": split.end.isoformat() if split.end else None,
    }


def _desc(rows: list[dict[str, Any]], key: str, absolute: bool = False) -> list[dict[str, Any]]:
    """Sort rows by a numeric field, largest (or largest absolute) first."""
    return sorted(rows, key=lambda r: -(abs(r[key]) if absolute else r[key]))


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, _clean(payload))


# ---------------------------------------------------------------- forecasting


def run_forecasting(cfg: ModelingConfig, ds: DailySeries, info: DataInfo, out: Path) -> dict[str, Any]:
    fc = cfg.forecast
    ff = F.build_features(ds, fc.horizon_days)
    test_end = fc.test.end or info.last_complete_date
    assert fc.validation.end is not None
    val_origins = F.origins_for_period(
        ds, fc.validation.start, fc.validation.end, fc.horizon_days, fc.origin_step_days, fc.min_history_days
    )
    test_origins = F.origins_for_period(
        ds, fc.test.start, test_end, fc.horizon_days, fc.origin_step_days, fc.min_history_days
    )
    if not val_origins or not test_origins:
        raise ValueError("not enough history for forecast validation/test origins")
    period = {t: "validation" for t in val_origins} | {t: "test" for t in test_origins}
    bt = F.run_backtest(ds, ff, val_origins + test_origins, period, fc, cfg.seed)
    frame = F.add_conformal_intervals(bt.frame, fc.interval_level, fc.min_conformal_residuals)

    overall = F.evaluate(frame, ["model", "period"])
    by_horizon = F.evaluate(frame, ["model", "period", "horizon"])
    by_series = F.evaluate(frame, ["model", "period", "category_key", "borough"])
    by_category = F.evaluate(frame, ["model", "period", "category_key"])
    by_origin = F.evaluate(frame, ["model", "period", "origin"])
    selected, val_wape = F.select_model(overall)

    test = frame.filter(pl.col("period") == "test").sort(["origin_index", "category_key", "borough", "horizon"])
    piv = {m: test.filter(pl.col("model") == m) for m in F.MODELS}
    y = piv["naive"]["actual"].to_numpy()
    groups = piv["naive"]["origin_index"].to_numpy()
    comparisons = []
    test_wape = {r["model"]: r["wape"] for r in overall if r["period"] == "test"}
    for base in F.BASELINES:
        boot = M.paired_bootstrap_wape_diff(
            y,
            piv[base]["predicted"].to_numpy(),
            piv["lightgbm"]["predicted"].to_numpy(),
            groups,
            n_boot=2000,
            seed=cfg.seed,
        )
        comparisons.append(
            {
                "candidate": "lightgbm",
                "baseline": base,
                "metric": "wape",
                "period": "test",
                "baseline_value": test_wape[base],
                "candidate_value": test_wape["lightgbm"],
                "relative_improvement": M.relative_improvement(test_wape[base], test_wape["lightgbm"]),
                "wape_diff_baseline_minus_candidate": boot,
                "significant_at_95": boot["ci_low"] > 0 or boot["ci_high"] < 0,
            }
        )

    importance_perm = F.permutation_importance(ff, bt.fold_models, test_origins, repeats=5, seed=cfg.seed)

    # Production forecast from the last complete day.
    T = len(ds.dates) - 1
    final_rows = np.flatnonzero(ff.t == T)
    prod_model = F.train_lgbm(ff, F.training_rows(ff, T), fc, cfg.seed)
    prod_model.save_model(str(out / "forecast_lightgbm.txt"))
    preds = F.predict_lgbm(prod_model, ff, final_rows) if selected == "lightgbm" else ff.baselines[selected][final_rows]
    q = F.interval_quantiles(frame, selected, fc.interval_level, fc.min_conformal_residuals, T)
    cats = [ds.keys[i][0] for i in ff.series[final_rows]]
    lo = np.array([q.get((c, int(h)), (np.nan, np.nan))[0] for c, h in zip(cats, ff.h[final_rows], strict=True)])
    hi = np.array([q.get((c, int(h)), (np.nan, np.nan))[1] for c, h in zip(cats, ff.h[final_rows], strict=True)])
    scale = np.sqrt(preds + 1)
    forecast = pl.DataFrame(
        {
            "origin": [ds.dates[T].astype(dt.date)] * len(final_rows),
            "target_date": (ds.dates[T] + ff.h[final_rows].astype("timedelta64[D]")).astype(dt.date).tolist(),
            "horizon": ff.h[final_rows],
            "category_key": cats,
            "borough": [ds.keys[i][1] for i in ff.series[final_rows]],
            "predicted": preds.astype(float),
            "lower": np.maximum(preds + lo * scale, 0),
            "upper": np.maximum(preds + hi * scale, 0),
            "model": [selected] * len(final_rows),
            "interval_level": [fc.interval_level] * len(final_rows),
        }
    )
    forecast.write_parquet(out / "forecast.parquet")
    frame.drop("origin_index").write_parquet(out / "forecast_backtest.parquet")

    gain = prod_model.feature_importance("gain")
    evaluation = {
        "target": "daily count of requests created, per category x borough",
        "horizon_days": fc.horizon_days,
        "series": [{"category_key": c, "borough": b} for c, b in ds.keys],
        "history": {
            "start": ds.dates[0].astype(dt.date),
            "end": ds.dates[-1].astype(dt.date),
            "source_gap_dates": ds.source_gap_dates,
        },
        "periods": {
            "validation": {
                "target_start": fc.validation.start,
                "target_end": fc.validation.end,
                "origins": len(val_origins),
                "first_origin": ds.dates[val_origins[0]].astype(dt.date),
                "last_origin": ds.dates[val_origins[-1]].astype(dt.date),
            },
            "test": {
                "target_start": fc.test.start,
                "target_end": test_end,
                "origins": len(test_origins),
                "first_origin": ds.dates[test_origins[0]].astype(dt.date),
                "last_origin": ds.dates[test_origins[-1]].astype(dt.date),
            },
            "training": "expanding window: every row with target date <= origin, from "
            f"{ds.dates[F.MIN_ORIGIN_INDEX].astype(dt.date)} (first origin with a full year of history)",
        },
        "interval": {
            "method": "sequential split-conformal on scaled residuals (y - yhat)/sqrt(yhat + 1), "
            "per model x category x horizon, using only residuals with target date <= origin",
            "level": fc.interval_level,
            "min_residuals": fc.min_conformal_residuals,
        },
        "overall": overall,
        "by_horizon": by_horizon,
        "by_category": by_category,
        "by_series": by_series,
        "by_origin": by_origin,
        "selection": {
            "rule": "lowest validation WAPE; ties go to the simpler model",
            "validation_wape": val_wape,
            "selected": selected,
        },
        "comparisons": comparisons,
        "feature_importance": {
            "permutation_test_wape_increase": importance_perm,
            "gain_production_model": _desc(
                [{"feature": f, "gain": float(g)} for f, g in zip(F.FEATURES, gain, strict=True)], "gain"
            ),
        },
        "production": {
            "origin": ds.dates[T].astype(dt.date),
            "model": selected,
            "trained_rows": len(F.training_rows(ff, T)),
        },
        "lgbm_params": fc.lgbm,
    }
    _write_json(out / "forecast_evaluation.json", evaluation)
    return {"selected": selected, "test_wape": test_wape}


# ---------------------------------------------------------------- delay risk


def run_delay_risk(cfg: ModelingConfig, paths: DataPaths, info: DataInfo, out: Path) -> dict[str, Any]:
    dc = cfg.delay_risk
    t0 = time.perf_counter()
    snap = D.snapshot_from_data(info.max_created_at, info.source_rows_updated_at, dc.threshold_days)
    df = D.assign_splits(D.load_frame(paths, dc.threshold_days), dc, snap)
    sizes = D.log_split_sizes(df)
    train, valid, test = (df.filter(pl.col("split") == s) for s in ("train", "validation", "test"))
    if min(train.height, valid.height, test.height) == 0:
        raise ValueError(f"empty delay-risk split: {sizes}")
    enc = D.Encoder.fit(train, dc.min_category_count)

    prevalence = float(train["y"].mean())  # type: ignore[arg-type]
    logreg = D.LogisticModel.fit(enc, train, dc.logreg, cfg.seed)
    log(logger, logging.INFO, "logistic regression trained", elapsed_s=round(time.perf_counter() - t0, 1))
    booster = D.train_lgbm(enc, train, valid, dc.lgbm, cfg.seed)
    log(
        logger,
        logging.INFO,
        "lightgbm trained",
        best_iteration=booster.best_iteration,
        elapsed_s=round(time.perf_counter() - t0, 1),
    )

    def score(name: str, frame: pl.DataFrame) -> np.ndarray:
        if name == "majority_class":
            return np.full(frame.height, prevalence)
        if name == "logistic_regression":
            return logreg.predict_proba(frame)
        return np.asarray(booster.predict(enc.lgb_matrix(frame)))

    names = ("majority_class", "logistic_regression", "lightgbm")
    results: dict[str, Any] = {}
    for name in names:
        pv, pt = score(name, valid), score(name, test)
        thr = M.best_f1_threshold(valid["y"].to_numpy(), pv) if name != "majority_class" else 0.5
        results[name] = {
            "threshold_rule": "max F1 on validation" if name != "majority_class" else "majority class (constant)",
            "threshold": thr,
            "validation": D.evaluate_split(valid, pv, thr),
            "test": D.evaluate_split(test, pt, thr),
        }
    val_pr = {n: results[n]["validation"]["overall"]["pr_auc"] for n in names}
    selected = max(names, key=lambda n: (np.nan_to_num(val_pr[n], nan=-1.0), -names.index(n)))

    sample = test.sample(n=min(dc.permutation_sample, test.height), seed=cfg.seed)
    log(logger, logging.INFO, "delay-risk evaluated", elapsed_s=round(time.perf_counter() - t0, 1))
    perm = {
        n: D.permutation_importance(lambda f, n=n: score(n, f), sample, dc.permutation_repeats, cfg.seed)
        for n in ("logistic_regression", "lightgbm")
    }
    coef_rows: list[dict[str, Any]] = [
        {"feature": c, "level": lvl, "coefficient": coef}
        for c in D.CATEGORICAL
        for lvl, coef in zip(enc.vocab[c], logreg.coef_cat[c], strict=True)
    ]
    coef_rows += [{"feature": c, "level": None, "coefficient": logreg.coef_num[c]} for c in D.NUMERIC]

    # Scores: test (labels known) and immature requests (genuine predictions; outcome not yet known).
    scored = df.filter(pl.col("split").is_in(["test", "immature"]))
    p = score(selected, scored)
    # Per-request explanations only for unresolved (immature) requests: TreeSHAP is costly per row.
    imm = (scored["split"] == "immature").to_numpy()
    imm_frame = scored.filter(pl.col("split") == "immature")
    contrib = (
        D.lgbm_contributions(booster, enc, imm_frame)
        if selected == "lightgbm"
        else logreg.contributions(imm_frame)
        if selected == "logistic_regression"
        else np.zeros((imm_frame.height, len(D.FEATURES)))
    )
    expl: list[str | None] = [None] * scored.height
    top = D.top_contributions(contrib, imm_frame)
    for i, t in zip(np.flatnonzero(imm), top, strict=True):
        expl[int(i)] = json.dumps(t)
    scored.select(
        "unique_key",
        "created_at",
        "created_date",
        "category_key",
        "descriptor",
        "borough",
        "community_board",
        "channel",
        "split",
        pl.when(pl.col("split") == "test").then(pl.col("y")).otherwise(None).alias("label"),
    ).with_columns(
        pl.Series("risk", p.astype(float)),
        pl.Series("explanation", expl, dtype=pl.Utf8),
        pl.lit(selected).alias("model"),
    ).write_parquet(out / "delay_scores.parquet")

    booster.save_model(str(out / "delay_lightgbm.txt"))
    _write_json(out / "delay_logreg.json", logreg.to_dict())
    _write_json(out / "delay_encoder.json", enc.to_dict())
    evaluation = {
        "target": D.TARGET,
        "target_definition": f"1 if no recorded closure within {dc.threshold_days} x 24 h of created_at",
        "snapshot": snap.to_dict(),
        "splits": sizes,
        "split_rules": {
            "train": _span(dc.train),
            "validation": _span(dc.validation),
            "test": _span(dc.test),
            "embargo_days": dc.embargo_days,
            "excluded": "immature (created after maturity cutoff), label_unknown (invalid closure), "
            "embargo (gap between splits)",
        },
        "features": {"categorical": D.CATEGORICAL, "numeric": D.NUMERIC, "never_used": sorted(D.FORBIDDEN_FEATURES)},
        "models": results,
        "selection": {
            "rule": "highest validation PR-AUC; ties go to the simpler model",
            "validation_pr_auc": val_pr,
            "selected": selected,
        },
        "explanations": {
            "logistic_regression_coefficients": _desc(coef_rows, "coefficient", absolute=True),
            "lightgbm_gain": _desc(
                [
                    {"feature": f, "gain": float(g)}
                    for f, g in zip(D.FEATURES, booster.feature_importance("gain"), strict=True)
                ],
                "gain",
            ),
            "permutation_test_pr_auc_drop": perm,
            "permutation_sample": sample.height,
        },
        "lightgbm_best_iteration": booster.best_iteration,
        "train_prevalence": prevalence,
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
    _write_json(out / "delay_evaluation.json", evaluation)
    return {"selected": selected, "validation_pr_auc": val_pr}


# ---------------------------------------------------------------- anomalies


def run_anomalies(cfg: ModelingConfig, ds: DailySeries, out: Path) -> dict[str, Any]:
    scores = A.detect_all(ds, cfg.anomaly)
    scores.write_parquet(out / "anomaly_scores.parquet")
    ev = A.evaluate_injections(ds, cfg.anomaly, cfg.seed)
    flagged = scores.filter(pl.col("is_anomaly"))
    ev["real_data"] = {
        "label_status": "unverified: no ground-truth labels exist for real 311 anomalies",
        "days_scored": scores.height,
        "alerts": flagged.height,
        "alerts_by_direction": flagged.group_by("direction").len().sort("direction").to_dicts(),
    }
    ev["detector"] = {
        "primary": "seasonal_mad",
        "z_threshold": cfg.anomaly.z_threshold,
        "min_abs_deviation": cfg.anomaly.min_abs_deviation,
        "baseline_weeks": cfg.anomaly.baseline_weeks,
        "mad_window_days": cfg.anomaly.mad_window_days,
    }
    _write_json(out / "anomaly_evaluation.json", ev)
    return {"alerts": flagged.height}


# ---------------------------------------------------------------- orchestration


def train_all(
    cfg: ModelingConfig, paths: DataPaths, *, config_path: Path | None = None, run_id: str | None = None
) -> dict[str, Any]:
    t0 = time.perf_counter()
    info = data_info(paths)
    run_id = run_id or dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    root = paths.root / "models"
    out = root / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    ds = daily_series(paths, cfg.boroughs, end=info.last_complete_date)
    log(
        logger,
        logging.INFO,
        "modeling started",
        run_id=run_id,
        rows=info.rows,
        last_complete=str(info.last_complete_date),
    )

    fc = run_forecasting(cfg, ds, info, out)
    dr = run_delay_risk(cfg, paths, info, out)
    an = run_anomalies(cfg, ds, out)

    cfg_bytes = (config_path or DEFAULT_MODELING_CONFIG).read_bytes()
    manifest = {
        "run_id": run_id,
        "created_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "data": info.to_dict(),
        "config_sha256": hashlib.sha256(cfg_bytes).hexdigest(),
        "config": cfg.model_dump(mode="json"),
        "seed": cfg.seed,
        "git_commit": _git_commit(),
        "versions": {
            "python": platform.python_version(),
            "lightgbm": lgb.__version__,
            "scikit-learn": sklearn.__version__,
            "numpy": np.__version__,
            "polars": pl.__version__,
        },
        "models": {
            "forecast": {"version": f"forecast-{run_id}", **fc},
            "delay_risk": {"version": f"delay-risk-{run_id}", **dr},
            "anomaly": {"version": f"anomaly-{run_id}", **an},
        },
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
    _write_json(out / "manifest.json", manifest)
    atomic_write_json(root / "current.json", {"run_id": run_id, "path": f"runs/{run_id}"})
    log(logger, logging.INFO, "modeling finished", run_id=run_id, elapsed_s=manifest["elapsed_s"])
    return manifest
