"""Read-only access to precomputed model outputs (Milestone 2).

The API never trains or executes models: ``civicpulse-model train`` writes every prediction,
score and evaluation to ``<data_dir>/models/runs/<run_id>/`` and flips ``models/current.json``.
This service only reads those files, so a public request can never trigger training, load an
arbitrary file, or run code. Inputs are validated against allow-lists derived from the outputs.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import duckdb

from civicpulse_analytics.service import DataUnavailableError
from civicpulse_shared.paths import read_json

MODEL_NAMES = ("forecast", "delay_risk", "anomaly")
EVALUATION_FILES = {
    "forecast": "forecast_evaluation.json",
    "delay_risk": "delay_evaluation.json",
    "anomaly": "anomaly_evaluation.json",
}
PARQUETS = {
    "forecast": "forecast.parquet",
    "backtest": "forecast_backtest.parquet",
    "delay_scores": "delay_scores.parquet",
    "anomalies": "anomaly_scores.parquet",
}
FORECAST_MODELS = ("naive", "seasonal_naive", "rolling_mean", "lightgbm")
CAUSALITY_NOTE = (
    "Importances and contributions describe associations the model uses to predict. They are not "
    "causal effects: changing a feature in the real world would not necessarily change the outcome."
)
MAX_SERIES_DAYS = 400


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


class PredictionService:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "models"
        self._con = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        self._run_id: str | None = None
        self._json_cache: dict[str, Any] = {}

    # -- infrastructure -------------------------------------------------------------
    def _current(self) -> tuple[str, Path]:
        pointer = self.root / "current.json"
        if not pointer.exists():
            raise DataUnavailableError("no trained models: run `civicpulse-model train` (see README)")
        cur = read_json(pointer)
        run_id = str(cur["run_id"])
        run_dir = (self.root / "runs" / run_id).resolve()
        if run_dir.parent != (self.root / "runs").resolve() or not run_dir.is_dir():
            raise DataUnavailableError("model pointer is invalid")
        with self._lock:
            if run_id != self._run_id:
                self._json_cache.clear()
                for name, fname in PARQUETS.items():
                    f = run_dir / fname
                    if not f.exists():
                        raise DataUnavailableError(f"model output missing: {fname}")
                    self._con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{f.as_posix()}')")
                self._run_id = run_id
        return run_id, run_dir

    def _json(self, fname: str) -> dict[str, Any]:
        _, run_dir = self._current()
        with self._lock:
            if fname not in self._json_cache:
                f = run_dir / fname
                if not f.exists():
                    raise DataUnavailableError(f"model output missing: {fname}")
                self._json_cache[fname] = json.loads(f.read_text(encoding="utf-8"))
            data: dict[str, Any] = self._json_cache[fname]
        return data

    def _rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        self._current()
        cur = self._con.cursor()
        try:
            rel = cur.execute(sql, params or [])
            cols = [d[0] for d in rel.description]
            return [_clean(dict(zip(cols, r, strict=True))) for r in rel.fetchall()]
        finally:
            cur.close()

    def _meta(self, started: float, rows: int) -> dict[str, Any]:
        return {
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "rows": rows,
            "model_run_id": self._run_id,
        }

    # -- validation -----------------------------------------------------------------
    def series_options(self) -> dict[str, list[str]]:
        rows = self._rows("SELECT DISTINCT category_key, borough FROM forecast ORDER BY 1, 2")
        return {
            "categories": sorted({r["category_key"] for r in rows}),
            "boroughs": sorted({r["borough"] for r in rows}),
        }

    def _check_series(self, category: str, borough: str, allow_citywide: bool = False) -> None:
        opts = self.series_options()
        if category not in opts["categories"]:
            raise ValueError(f"unknown category '{category}'; expected one of {opts['categories']}")
        allowed = opts["boroughs"] + (["CITYWIDE"] if allow_citywide else [])
        if borough not in allowed:
            raise ValueError(f"unknown borough '{borough}'; expected one of {allowed}")

    # -- metadata -------------------------------------------------------------------
    def registry(self) -> dict[str, Any]:
        run_id, _ = self._current()
        m = self._json("manifest.json")
        fc = self._json("forecast_evaluation.json")
        dr = self._json("delay_evaluation.json")
        an = self._json("anomaly_evaluation.json")
        out: dict[str, Any] = _clean(
            {
                "run_id": run_id,
                "created_at_utc": m["created_at_utc"],
                "data": m["data"],
                "git_commit": m["git_commit"],
                "config_sha256": m["config_sha256"],
                "seed": m["seed"],
                "versions": m["versions"],
                "models": {
                    "forecast": {
                        "version": m["models"]["forecast"]["version"],
                        "selected": fc["selection"]["selected"],
                        "selection_rule": fc["selection"]["rule"],
                        "candidates": list(FORECAST_MODELS),
                        "periods": fc["periods"],
                        "interval": fc["interval"],
                        "production_origin": fc["production"]["origin"],
                    },
                    "delay_risk": {
                        "version": m["models"]["delay_risk"]["version"],
                        "selected": dr["selection"]["selected"],
                        "selection_rule": dr["selection"]["rule"],
                        "candidates": list(dr["models"]),
                        "target": dr["target"],
                        "target_definition": dr["target_definition"],
                        "snapshot": dr["snapshot"],
                        "splits": dr["splits"],
                    },
                    "anomaly": {
                        "version": m["models"]["anomaly"]["version"],
                        "detector": an["detector"],
                    },
                },
            }
        )
        return out

    def evaluation(self, name: str) -> dict[str, Any]:
        if name not in EVALUATION_FILES:
            raise ValueError(f"unknown model '{name}'; expected one of {list(EVALUATION_FILES)}")
        return self._json(EVALUATION_FILES[name])

    def explanations(self, name: str) -> dict[str, Any]:
        if name == "forecast":
            fi = self._json("forecast_evaluation.json")["feature_importance"]
            return {"model": name, "global": fi, "causality_note": CAUSALITY_NOTE}
        if name == "delay_risk":
            ex = self._json("delay_evaluation.json")["explanations"]
            return {
                "model": name,
                "global": ex,
                "local": "per-request contributions: /api/v1/predictions/delay-risk/{unique_key}",
                "causality_note": CAUSALITY_NOTE,
            }
        if name == "anomaly":
            return {
                "model": name,
                "global": "Each alert is explained by its own numbers: actual vs expected "
                "(median of the same weekday over the previous weeks) and the robust z-score.",
                "causality_note": "An alert means the count was unusual, not why. Weather, outages, "
                "policy and reporting changes all produce alerts.",
            }
        raise ValueError(f"unknown model '{name}'")

    # -- forecasting ----------------------------------------------------------------
    def forecast(self, category: str, borough: str, history_days: int) -> dict[str, Any]:
        t0 = time.perf_counter()
        self._check_series(category, borough)
        fc = self._rows(
            """SELECT target_date, horizon, predicted, lower, upper, model, interval_level, origin
               FROM forecast WHERE category_key = ? AND borough = ? ORDER BY target_date""",
            [category, borough],
        )
        history = self._rows(
            """SELECT date, actual FROM anomalies WHERE category_key = ? AND borough = ?
               AND date > (SELECT max(origin) FROM forecast) - ?::INTEGER ORDER BY date""",
            [category, borough, history_days],
        )
        ev = self._json("forecast_evaluation.json")
        series_metrics = [
            r
            for r in ev["by_series"]
            if r["category_key"] == category and r["borough"] == borough and r["period"] == "test"
        ]
        return {
            "category_key": category,
            "borough": borough,
            "origin": fc[0]["origin"] if fc else None,
            "model": fc[0]["model"] if fc else None,
            "interval_level": fc[0]["interval_level"] if fc else None,
            "forecast": [{k: r[k] for k in ("target_date", "horizon", "predicted", "lower", "upper")} for r in fc],
            "history": history,
            "test_metrics": series_metrics,
            "meta": self._meta(t0, len(fc) + len(history)),
        }

    def backtest(self, category: str, borough: str, horizon: int, model: str | None) -> dict[str, Any]:
        t0 = time.perf_counter()
        self._check_series(category, borough)
        ev = self._json("forecast_evaluation.json")
        model = model or ev["selection"]["selected"]
        if model not in FORECAST_MODELS:
            raise ValueError(f"unknown model '{model}'")
        rows = self._rows(
            """SELECT target_date, origin, period, actual, predicted, lower, upper FROM backtest
               WHERE category_key = ? AND borough = ? AND horizon = ? AND model = ? ORDER BY target_date""",
            [category, borough, horizon, model],
        )
        return {
            "category_key": category,
            "borough": borough,
            "horizon": horizon,
            "model": model,
            "points": rows,
            "meta": self._meta(t0, len(rows)),
        }

    # -- delay risk -----------------------------------------------------------------
    def delay_risk_list(self, category: str | None, borough: str | None, min_risk: float, limit: int) -> dict[str, Any]:
        t0 = time.perf_counter()
        where = ["split = 'immature'", "risk >= ?"]
        params: list[Any] = [min_risk]
        if category is not None:
            where.append("category_key = ?")
            params.append(category)
        if borough is not None:
            where.append("borough = ?")
            params.append(borough)
        clause = " AND ".join(where)
        total = self._rows(f"SELECT count(*) AS n FROM delay_scores WHERE {clause}", params)[0]["n"]
        rows = self._rows(
            f"""SELECT unique_key, created_at, category_key, descriptor, borough, community_board, channel, risk
                FROM delay_scores WHERE {clause} ORDER BY risk DESC, created_at DESC, unique_key LIMIT ?""",
            [*params, limit],
        )
        return {"total": total, "rows": rows, "meta": self._meta(t0, len(rows))}

    def delay_risk_summary(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        live = self._rows(
            """SELECT category_key, borough, count(*) AS requests, avg(risk) AS mean_risk,
                      count(*) FILTER (WHERE risk >= 0.5) AS risk_at_least_50pct
               FROM delay_scores WHERE split = 'immature' GROUP BY ALL ORDER BY 1, 2"""
        )
        hist = self._rows(
            """SELECT least(floor(risk * 10), 9)::INTEGER AS bin, count(*) AS requests,
                      avg(label) AS observed_rate
               FROM delay_scores WHERE split = ? GROUP BY ALL ORDER BY 1""",
            ["test"],
        )
        live_hist = self._rows(
            """SELECT least(floor(risk * 10), 9)::INTEGER AS bin, count(*) AS requests
               FROM delay_scores WHERE split = 'immature' GROUP BY ALL ORDER BY 1"""
        )
        span = self._rows(
            "SELECT min(created_at) AS first, max(created_at) AS last, count(*) AS n "
            "FROM delay_scores WHERE split = 'immature'"
        )[0]
        return {
            "live_window": span,
            "live_by_segment": live,
            "test_risk_histogram": hist,
            "live_risk_histogram": live_hist,
            "meta": self._meta(t0, len(live)),
        }

    def delay_risk_request(self, unique_key: str) -> dict[str, Any] | None:
        rows = self._rows(
            """SELECT unique_key, created_at, category_key, descriptor, borough, community_board, channel,
                      split, label, risk, explanation, model FROM delay_scores WHERE unique_key = ?""",
            [unique_key],
        )
        if not rows:
            return None
        r = rows[0]
        r["explanation"] = json.loads(r["explanation"]) if r["explanation"] else None
        r["status"] = (
            "prediction: outcome not yet known" if r["split"] == "immature" else "held-out test request: outcome known"
        )
        r["causality_note"] = CAUSALITY_NOTE
        return r

    # -- anomalies ------------------------------------------------------------------
    def anomalies(
        self, category: str | None, borough: str | None, direction: str | None, start: Any, end: Any, limit: int
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        where = ["is_anomaly"]
        params: list[Any] = []
        for col, val in (("category_key", category), ("borough", borough), ("direction", direction)):
            if val is not None:
                where.append(f"{col} = ?")
                params.append(val)
        if start is not None:
            where.append("date >= ?")
            params.append(start)
        if end is not None:
            where.append("date <= ?")
            params.append(end)
        clause = " AND ".join(where)
        total = self._rows(f"SELECT count(*) AS n FROM anomalies WHERE {clause}", params)[0]["n"]
        rows = self._rows(
            f"""SELECT date, category_key, borough, actual, expected, residual, score, direction
                FROM anomalies WHERE {clause} ORDER BY date DESC, abs(score) DESC LIMIT ?""",
            [*params, limit],
        )
        return {
            "total": total,
            "rows": rows,
            "label_status": "unverified: detected by the model, not confirmed events",
            "meta": self._meta(t0, len(rows)),
        }

    def anomaly_series(self, category: str, borough: str, start: Any, end: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        self._check_series(category, borough, allow_citywide=True)
        if start is not None and end is not None and (end - start).days > MAX_SERIES_DAYS:
            raise ValueError(f"date range too long (max {MAX_SERIES_DAYS} days)")
        rng = self._rows("SELECT min(date) AS lo, max(date) AS hi FROM anomalies")[0]
        if end is None:
            end = rng["hi"]
        rows = self._rows(
            """SELECT date, actual, expected, score, is_anomaly, direction FROM anomalies
               WHERE category_key = ? AND borough = ? AND date <= ?::DATE
                 AND date >= coalesce(?::DATE, ?::DATE - INTERVAL 180 DAY) ORDER BY date""",
            [category, borough, end, start, end],
        )
        return {
            "category_key": category,
            "borough": borough,
            "points": rows,
            "available": rng,
            "meta": self._meta(t0, len(rows)),
        }
