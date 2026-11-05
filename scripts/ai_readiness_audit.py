"""Milestone 3, Stage A: independent re-verification of Milestone 2 results.

Recomputes reported numbers directly from the saved artifacts (backtest / score parquet files) and
the warehouse, without importing the modeling package, so a bug in the original code cannot also
hide in the check. Writes ``docs/evaluation/ai_readiness_audit.json``.

    .venv/Scripts/python scripts/ai_readiness_audit.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "docs" / "evaluation" / "ai_readiness_audit.json"
TOL = 1e-6


def q(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    rel = con.execute(sql, params or [])
    cols = [d[0] for d in rel.description]
    return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]


def check(name: str, reported: float, recomputed: float, tol: float = TOL) -> dict[str, Any]:
    ok = abs(reported - recomputed) <= tol
    return {"check": name, "reported": reported, "recomputed": recomputed, "match": ok}


def main() -> int:
    run_id = json.loads((DATA / "models" / "current.json").read_text(encoding="utf-8"))["run_id"]
    run = DATA / "models" / "runs" / run_id
    fc_eval = json.loads((run / "forecast_evaluation.json").read_text(encoding="utf-8"))
    dr_eval = json.loads((run / "delay_evaluation.json").read_text(encoding="utf-8"))
    an_eval = json.loads((run / "anomaly_evaluation.json").read_text(encoding="utf-8"))

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(f"CREATE VIEW bt AS SELECT * FROM read_parquet('{(run / 'forecast_backtest.parquet').as_posix()}')")
    con.execute(f"CREATE VIEW fc AS SELECT * FROM read_parquet('{(run / 'forecast.parquet').as_posix()}')")
    con.execute(f"CREATE VIEW ds AS SELECT * FROM read_parquet('{(run / 'delay_scores.parquet').as_posix()}')")
    con.execute(f"CREATE VIEW an AS SELECT * FROM read_parquet('{(run / 'anomaly_scores.parquet').as_posix()}')")
    fact = (DATA / "processed" / "fact_service_requests.parquet").as_posix()
    con.execute(f"CREATE VIEW f AS SELECT * FROM read_parquet('{fact}')")

    checks: list[dict[str, Any]] = []
    findings: dict[str, Any] = {"run_id": run_id}

    # ---------------- forecasting ----------------
    rows = q(
        con,
        """SELECT model, period, count(*) n, sum(abs(actual - predicted)) / sum(actual) wape,
                  avg(abs(actual - predicted)) mae
           FROM bt GROUP BY ALL ORDER BY 1, 2""",
    )
    reported = {(r["model"], r["period"]): r for r in fc_eval["overall"]}
    for r in rows:
        rep = reported[(r["model"], r["period"])]
        checks.append(check(f"forecast {r['model']} {r['period']} WAPE", rep["wape"], r["wape"]))
        checks.append(check(f"forecast {r['model']} {r['period']} n", rep["n"], r["n"], 0))
    # Coverage in Polars: missing intervals are stored as IEEE NaN, which this DuckDB build does not
    # report through isnan() on these columns (verified during the audit), so SQL would count them.
    bt_pl = pl.read_parquet(run / "forecast_backtest.parquet")
    for (model, period), grp in bt_pl.group_by(["model", "period"]):
        has = grp.filter(~(grp["lower"].is_nan() | grp["upper"].is_nan()))
        cov = float(((has["actual"] >= has["lower"]) & (has["actual"] <= has["upper"])).mean() or 0.0)
        rep = reported[(model, period)]
        checks.append(check(f"forecast {model} {period} 90% PI coverage", rep["interval_coverage"], cov))
        checks.append(check(f"forecast {model} {period} points with interval", rep["n_with_interval"], len(has), 0))
    # selection must follow validation WAPE only
    val = {r["model"]: r["wape"] for r in rows if r["period"] == "validation"}
    best_val = min(val, key=lambda m: val[m])
    checks.append(
        {
            "check": "forecast model selected by lowest validation WAPE",
            "reported": fc_eval["selection"]["selected"],
            "recomputed": best_val,
            "match": best_val == fc_eval["selection"]["selected"],
        }
    )
    # horizon integrity: every backtest row's target is origin + horizon and after the origin
    bad_h = q(con, "SELECT count(*) n FROM bt WHERE target_date <> origin + horizon::INTEGER OR target_date <= origin")
    checks.append(check("backtest rows with target != origin + horizon", 0, bad_h[0]["n"], 0))
    # periods do not overlap
    ov = q(
        con,
        """SELECT (SELECT max(target_date) FROM bt WHERE period='validation') v_end,
                  (SELECT min(target_date) FROM bt WHERE period='test') t_start""",
    )[0]
    checks.append(
        {
            "check": "validation targets end before test targets start",
            "reported": f"{ov['v_end']} < {ov['t_start']}",
            "recomputed": str(ov["v_end"] < ov["t_start"]),
            "match": bool(ov["v_end"] < ov["t_start"]),
        }
    )
    # independent paired bootstrap over test origins (different seed than training, same resampling unit)
    per_origin = q(
        con,
        """SELECT origin, model, sum(abs(actual - predicted)) abs_err, sum(actual) act
           FROM bt WHERE period = 'test' GROUP BY ALL ORDER BY origin, model""",
    )
    origins = sorted({r["origin"] for r in per_origin})
    table = {(r["origin"], r["model"]): (r["abs_err"], r["act"]) for r in per_origin}
    rng = np.random.default_rng(20261104)
    boot: dict[str, Any] = {}
    for base in ("naive", "seasonal_naive", "rolling_mean"):
        diffs = []
        idx = np.arange(len(origins))
        for _ in range(4000):
            s = rng.choice(idx, size=len(idx), replace=True)
            e_b = sum(table[(origins[i], base)][0] for i in s)
            e_c = sum(table[(origins[i], "lightgbm")][0] for i in s)
            a = sum(table[(origins[i], "lightgbm")][1] for i in s)
            diffs.append((e_b - e_c) / a)
        lo, hi = float(np.quantile(diffs, 0.025)), float(np.quantile(diffs, 0.975))
        rep = next(c for c in fc_eval["comparisons"] if c["baseline"] == base and c["period"] == "test")
        boot[base] = {
            "reported_ci": [
                rep["wape_diff_baseline_minus_candidate"]["ci_low"],
                rep["wape_diff_baseline_minus_candidate"]["ci_high"],
            ],
            "reported_significant": rep["significant_at_95"],
            "independent_ci_4000_resamples": [round(lo, 4), round(hi, 4)],
            "independent_significant": lo > 0,
            "agrees": (lo > 0) == rep["significant_at_95"],
        }
    findings["forecast_bootstrap"] = boot
    cat = q(
        con,
        """SELECT model, category_key, sum(abs(actual - predicted)) / sum(actual) wape
           FROM bt WHERE period='test' GROUP BY ALL ORDER BY 2, 1""",
    )
    findings["forecast_test_by_category"] = cat
    # production forecast integrity
    prod = q(
        con,
        "SELECT min(origin) o1, max(origin) o2, min(target_date) t1, max(target_date) t2, count(*) n, "
        "count(*) FILTER (WHERE lower IS NULL) n_no_int, any_value(model) m FROM fc",
    )[0]
    findings["production_forecast"] = {k: str(v) for k, v in prod.items()}
    # backtest actuals equal warehouse daily counts (MCP tools and models must agree on the data)
    mism = q(
        con,
        """WITH w AS (SELECT created_date d, category_key, borough, count(*) n FROM f
                      WHERE borough <> 'UNSPECIFIED' GROUP BY ALL),
                b AS (SELECT DISTINCT target_date d, category_key, borough, actual FROM bt)
           SELECT count(*) n_points, count(*) FILTER (WHERE coalesce(w.n, 0) <> b.actual) n_mismatch
           FROM b LEFT JOIN w USING (d, category_key, borough)""",
    )[0]
    checks.append(check("backtest actuals that differ from warehouse daily counts", 0, mism["n_mismatch"], 0))
    findings["backtest_points_compared_to_warehouse"] = mism["n_points"]

    # ---------------- delay risk ----------------
    snap = dr_eval["snapshot"]
    lab = q(
        con,
        """SELECT count(*) n, count(*) FILTER (WHERE ds.label <> w.y) n_mismatch FROM ds JOIN (
              SELECT unique_key, CASE WHEN closed_at IS NOT NULL AND closed_at <= created_at + INTERVAL 7 DAY
                                      THEN 0 ELSE 1 END y FROM f) w USING (unique_key)
           WHERE ds.split IN ('train', 'validation', 'test')""",
    )[0]
    checks.append(check("delay labels that differ from warehouse recomputation", 0, lab["n_mismatch"], 0))
    leak = q(con, f"SELECT count(*) n FROM ds WHERE split = 'test' AND created_at > '{snap['maturity_cutoff']}'")[0]
    checks.append(check("test rows created after maturity cutoff", 0, leak["n"], 0))
    split_rates = q(con, "SELECT split, count(*) n, avg(label) rate, avg(risk) mean_risk FROM ds GROUP BY 1 ORDER BY 1")
    findings["delay_splits"] = split_rates
    for s in split_rates:
        if s["split"] in dr_eval["splits"] and "n" in dr_eval["splits"][s["split"]]:
            checks.append(check(f"delay split {s['split']} n", dr_eval["splits"][s["split"]]["n"], s["n"], 0))

    # label drift investigation
    findings["heat_monthly_closure"] = q(
        con,
        """SELECT strftime(created_date, '%Y-%m') AS month, count(*) n,
                  round(avg((closed_at IS NULL)::INT), 4) still_open_share,
                  round(avg((closed_at IS NULL OR hours_to_close > 168)::INT), 4) not_closed_7d_share,
                  round(avg((closed_at IS NOT NULL AND hours_to_close > 168)::INT), 4) closed_late_share
           FROM f WHERE category_key = 'heat_hot_water' GROUP BY 1 ORDER BY 1""",
    )
    findings["source_update_bulk_rewrite"] = q(
        con,
        """SELECT year(created_date) created_year, category_key,
                  count(*) n,
                  count(*) FILTER (WHERE source_updated_at::DATE BETWEEN '2025-12-26' AND '2025-12-27')
                    n_rewritten_2025_12_26,
                  min(source_updated_at)::VARCHAR first_update, max(source_updated_at)::VARCHAR last_update
           FROM f GROUP BY ALL ORDER BY 1, 2""",
    )
    findings["heat_resolution_minus_closed_hours_median"] = q(
        con,
        """SELECT strftime(created_date, '%Y-%m') AS month,
                  round(quantile_cont(epoch(resolution_updated_at - closed_at) / 3600, 0.5), 1) median_hours
           FROM f WHERE category_key = 'heat_hot_water' AND closed_at IS NOT NULL
             AND created_date BETWEEN '2025-05-01' AND '2025-12-31' GROUP BY 1 ORDER BY 1""",
    )
    findings["open_status_values"] = q(
        con,
        """SELECT year(created_date) created_year, status, count(*) n FROM f
           WHERE category_key='heat_hot_water' AND closed_at IS NULL GROUP BY ALL ORDER BY 1, 3 DESC""",
    )
    findings["late_closures_over_30d_heat"] = q(
        con, "SELECT count(*) n FROM f WHERE category_key='heat_hot_water' AND hours_to_close > 720"
    )[0]["n"]

    # ---------------- anomalies ----------------
    a = q(
        con,
        "SELECT count(*) FILTER (WHERE is_anomaly) n_alerts, count(*) n_days, "
        "count(*) FILTER (WHERE is_anomaly AND direction='spike') spikes FROM an",
    )[0]
    checks.append(
        check("anomaly real-data alerts", an_eval["real_data"]["alerts"], a["n_alerts"], 0)
        if "alerts" in an_eval["real_data"]
        else {"check": "anomaly alerts", "recomputed": a["n_alerts"], "reported": None, "match": None}
    )
    oct_heat = q(
        con, "SELECT count(*) n FROM an WHERE is_anomaly AND category_key='heat_hot_water' AND month(date)=10"
    )[0]
    findings["anomalies"] = {**a, "heat_alerts_in_october": oct_heat["n"]}
    am = q(
        con,
        """WITH w AS (SELECT created_date d, category_key, borough, count(*) n FROM f
                      WHERE borough <> 'UNSPECIFIED' GROUP BY ALL)
           SELECT count(*) n_points, count(*) FILTER (WHERE coalesce(w.n,0) <> an.actual) n_mismatch
           FROM an LEFT JOIN w ON w.d = an.date AND w.category_key = an.category_key AND w.borough = an.borough
           WHERE an.borough <> 'CITYWIDE'""",
    )[0]
    checks.append(check("anomaly actuals that differ from warehouse daily counts", 0, am["n_mismatch"], 0))
    # Injected days are not persisted, so synthetic recall can only be re-derived by re-running the
    # seeded pipeline (test_metrics_anomalies_pipeline.py), not from artifacts. Record what is reported.
    findings["anomaly_synthetic_reported"] = an_eval["summary"]

    out = {
        "run_id": run_id,
        "checks": checks,
        "n_checks": len(checks),
        "n_failed": sum(1 for c in checks if c["match"] is False),
        "findings": findings,
    }
    OUT.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    for c in checks:
        print(
            ("OK  " if c["match"] else "FAIL") if c["match"] is not None else "N/A ",
            c["check"],
            c["reported"],
            c["recomputed"],
        )
    print(json.dumps(boot, indent=1))
    print(f"{out['n_failed']} of {out['n_checks']} checks failed -> {OUT}")
    return 1 if out["n_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
