"""Tool logic for the CivicPulse MCP server.

Every number comes from the shared ``AnalyticsService`` / ``PredictionService`` (the same code the
FastAPI dashboard uses) or from saved evaluation artifacts. Nothing here defines a business metric
and nothing accepts SQL: inputs are typed, allow-listed values, outputs are bounded ``ToolResult``s.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from civicpulse_analytics.filters import MAX_RANGE_DAYS, Filters
from civicpulse_analytics.predictions import CAUSALITY_NOTE, PredictionService
from civicpulse_analytics.service import DEFINITIONS, AnalyticsService
from civicpulse_mcp import capabilities as cap
from civicpulse_mcp.capabilities import Capability
from civicpulse_mcp.envelope import (
    Coverage,
    Measurement,
    Provenance,
    QueryInfo,
    ToolResult,
    clean_json,
    enforce_size,
)

BOROUGHS5 = ("BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND")
CATEGORIES = ("noise_residential", "heat_hot_water")
DAY_GRAIN_MAX_DAYS = 400
DESCRIPTOR_TOP = 20
ANOMALY_MAX_DAYS = 1500
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

VOLUME_DEFINITION = (
    "Number of 311 service requests whose created date (NYC local) falls in the period and matches all "
    "filters, from the warehouse daily aggregate."
)
SHARE_DEFINITION = "Requests in the group divided by all requests matching the same filters and window."
LOADED_CATEGORIES_NOTE = (
    "The warehouse loads only two complaint categories (Noise - Residential and HEAT/HOT WATER), so "
    "shares are shares of these categories, not of all 311 complaints."
)


class ToolInputError(ValueError):
    """Arguments are well-typed but invalid together (e.g. reversed dates, mismatched filters)."""


def sanitize_text(value: Any, limit: int = 80) -> str:
    """Values from the external dataset are untrusted: strip control characters and bound length."""
    return _CONTROL.sub(" ", str(value))[:limit]


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _cat_filter(category: str) -> list[str]:
    return [] if category == "all" else [category]


def _boro_filter(borough: str) -> list[str]:
    return [] if borough == "ALL" else [borough]


def _bucket_end(start: dt.date, grain: str) -> dt.date:
    if grain == "day":
        return start
    if grain == "week":
        return start + dt.timedelta(days=6)
    return start.replace(day=calendar.monthrange(start.year, start.month)[1])


class CivicPulseTools:
    def __init__(self, data_dir: Path) -> None:
        self.svc = AnalyticsService(data_dir)
        self.pred = PredictionService(data_dir)

    # ------------------------------------------------------------------ helpers
    def _provenance(self, model: str | None = None, artifact: str | None = None) -> Provenance:
        opts = self.svc.filter_options()
        fresh = self.svc.freshness()
        build = self.svc.build_info() or {}
        p = Provenance(
            dataset_id=cap.DATASET_ID,
            dataset_name=cap.DATASET_NAME,
            data_build_id=build.get("build_id"),
            snapshot_at_local=fresh.snapshot_at_local,
            data_cutoff=fresh.latest_created_at,
            last_complete_date=_iso(opts.last_complete_date),
            source_rows_updated_at=fresh.source_rows_updated_at,
        )
        if model:
            reg = self.pred.registry()
            p.model_run_id = reg["run_id"]
            p.model_name = model
            p.model_version = reg["models"][model]["version"]
            p.artifact = artifact
        return p

    def _coverage(self, start: dt.date, end: dt.date) -> tuple[Coverage, dt.date | None, dt.date | None]:
        if end < start:
            raise ToolInputError("end_date must be on or after start_date")
        if (end - start).days > MAX_RANGE_DAYS:
            raise ToolInputError(f"the date range may not exceed {MAX_RANGE_DAYS} days")
        opts = self.svc.filter_options()
        lo, hi = opts.min_date, opts.last_complete_date
        if lo is None or hi is None:
            raise ToolInputError("the warehouse is empty")
        eff_start, eff_end = max(start, lo), min(end, hi)
        partial = start < lo or end > hi
        note = None
        if eff_start > eff_end:
            note = (
                f"The requested period {start}..{end} is entirely outside the data, which covers {lo}..{hi} "
                f"(complete days). No values exist for it."
            )
            return (
                Coverage(
                    requested_start=_iso(start),
                    requested_end=_iso(end),
                    available_start=_iso(lo),
                    available_end=_iso(hi),
                    effective_start=None,
                    effective_end=None,
                    partial=True,
                    note=note,
                ),
                None,
                None,
            )
        if partial:
            note = (
                f"Only {eff_start}..{eff_end} of the requested {start}..{end} is covered by the data "
                f"(available {lo}..{hi}); totals cover the effective period only."
            )
        return (
            Coverage(
                requested_start=_iso(start),
                requested_end=_iso(end),
                available_start=_iso(lo),
                available_end=_iso(hi),
                effective_start=_iso(eff_start),
                effective_end=_iso(eff_end),
                partial=partial,
                note=note,
            ),
            eff_start,
            eff_end,
        )

    @staticmethod
    def _query(t0: float, rows: int, calculation: str, truncated: bool = False, limit: int | None = None) -> QueryInfo:
        return QueryInfo(
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 2),
            rows=rows,
            truncated=truncated,
            row_limit=limit,
            calculation=calculation,
        )

    def _no_data(self, tool: str, t0: float, coverage: Coverage, capability: Capability) -> ToolResult:
        return ToolResult(
            tool=tool,
            status="no_data",
            capability=capability,
            summary=coverage.note or "No data for the requested period.",
            provenance=self._provenance(),
            coverage=coverage,
            limitations=[cap.SNAPSHOT],
            query=self._query(t0, 0, "No query executed: the period is outside the warehouse."),
        )

    # ------------------------------------------------------------------ 1. trends
    def get_service_request_trends(
        self, start_date: dt.date, end_date: dt.date, category: str, borough: str, granularity: str
    ) -> ToolResult:
        t0 = time.perf_counter()
        tool = "get_service_request_trends"
        if granularity == "day" and (end_date - start_date).days + 1 > DAY_GRAIN_MAX_DAYS:
            raise ToolInputError(f"daily granularity is limited to {DAY_GRAIN_MAX_DAYS} days; use week or month")
        coverage, eff_start, eff_end = self._coverage(start_date, end_date)
        if eff_start is None or eff_end is None:
            return self._no_data(tool, t0, coverage, Capability.VALIDATED)
        f = Filters(start=eff_start, end=eff_end, boroughs=_boro_filter(borough), categories=_cat_filter(category))
        series = self.svc.volume_series(f, granularity, "none")
        kpis = self.svc.kpis(f)
        points: list[dict[str, Any]] = []
        for p in series.points:
            b_end = _bucket_end(p.period, granularity)
            partial = p.period < eff_start or b_end > eff_end
            points.append({"period": p.period.isoformat(), "value": int(p.value), "partial_bucket": partial})
        total = sum(p["value"] for p in points)
        complete = [p for p in points if not p["partial_bucket"]]
        top = sorted(points, key=lambda p: (-p["value"], p["period"]))[:5]
        m = [
            Measurement(name="total_requests", value=total, unit="requests", definition=VOLUME_DEFINITION),
            Measurement(name="periods", value=len(points), unit=granularity),
        ]
        if complete:
            peak = max(complete, key=lambda p: p["value"])
            low = min(complete, key=lambda p: p["value"])
            m += [
                Measurement(
                    name=f"mean_requests_per_{granularity}",
                    value=round(sum(p["value"] for p in complete) / len(complete), 1),
                    unit="requests",
                    definition="Mean over complete periods only (partial first/last buckets excluded).",
                ),
                Measurement(name=f"peak_{granularity}", value=peak["period"], definition="Period with most requests"),
                Measurement(name=f"peak_{granularity}_requests", value=peak["value"], unit="requests"),
                Measurement(name=f"lowest_{granularity}", value=low["period"]),
                Measurement(name=f"lowest_{granularity}_requests", value=low["value"], unit="requests"),
            ]
        comp = kpis.comparison
        if comp.prior_total is not None:
            m += [
                Measurement(
                    name="prior_period_total",
                    value=comp.prior_total,
                    unit="requests",
                    definition="Same filters, preceding period of equal length "
                    f"({comp.prior_start}..{comp.prior_end}).",
                ),
                Measurement(name="pct_change_vs_prior_period", value=comp.pct_change, unit="fraction"),
            ]
        label_c = "all loaded categories" if category == "all" else category
        label_b = "all boroughs" if borough == "ALL" else borough
        result = ToolResult(
            tool=tool,
            status="ok",
            capability=Capability.VALIDATED,
            summary=f"{total:,} requests ({label_c}, {label_b}) created {eff_start}..{eff_end}, by {granularity}.",
            key_measurements=m,
            data={
                "filters": {
                    "start_date": eff_start.isoformat(),
                    "end_date": eff_end.isoformat(),
                    "category": category,
                    "borough": borough,
                    "granularity": granularity,
                },
                "points": points,
                "top_periods": top,
                "prior_period": clean_json(comp.model_dump()),
            },
            metric_definitions={
                "volume": VOLUME_DEFINITION,
                "partial_bucket": "A week/month bucket only partly inside the effective period; "
                "its value is incomplete.",
                "comparison": DEFINITIONS["comparison"],
                "time_basis": DEFINITIONS["time_basis"],
            },
            provenance=self._provenance(),
            coverage=coverage,
            uncertainty=["Exact counts of recorded requests; no sampling uncertainty, but see limitations."],
            limitations=[cap.REPORTED_NOT_INCIDENTS, cap.SNAPSHOT, cap.POINT_IN_TIME],
            query=self._query(
                t0,
                len(points),
                "sum(request_count) from agg_daily_requests grouped by period (AnalyticsService.volume_series); "
                "prior period from AnalyticsService.kpis.",
            ),
        )
        return enforce_size(result)

    # ------------------------------------------------------------------ 2. compare
    def compare_boroughs(
        self, start_date: dt.date, end_date: dt.date, boroughs: list[str], category: str
    ) -> ToolResult:
        t0 = time.perf_counter()
        tool = "compare_boroughs"
        sel = list(dict.fromkeys(boroughs))
        if len(sel) < 2:
            raise ToolInputError("select at least two different boroughs")
        coverage, eff_start, eff_end = self._coverage(start_date, end_date)
        if eff_start is None or eff_end is None:
            return self._no_data(tool, t0, coverage, Capability.VALIDATED)
        rows: list[dict[str, Any]] = []
        for b in sel:
            k = self.svc.kpis(Filters(start=eff_start, end=eff_end, boroughs=[b], categories=_cat_filter(category)))
            rows.append(
                {
                    "borough": b,
                    "requests": k.total_requests,
                    "median_hours_to_close": k.median_hours_to_close,
                    "open_beyond_7d_pct": k.open_beyond_threshold_pct,
                    "open_beyond_7d_numerator": k.open_beyond_threshold_numerator,
                    "open_beyond_7d_denominator": k.open_beyond_threshold_denominator,
                    "prior_period_requests": k.comparison.prior_total,
                    "pct_change_vs_prior_period": k.comparison.pct_change,
                }
            )
        total = sum(r["requests"] for r in rows)
        rows.sort(key=lambda r: (-r["requests"], r["borough"]))
        for i, r in enumerate(rows, 1):
            r["rank_by_requests"] = i
            r["share_of_selected"] = round(r["requests"] / total, 4) if total else None
        pairs: list[dict[str, Any]] = []
        for i, a in enumerate(rows):
            for low in rows[i + 1 :]:
                pairs.append(
                    {
                        "higher": a["borough"],
                        "lower": low["borough"],
                        "difference_requests": a["requests"] - low["requests"],
                        "ratio": round(a["requests"] / low["requests"], 4) if low["requests"] else None,
                        "pct_more": round((a["requests"] - low["requests"]) / low["requests"], 4)
                        if low["requests"]
                        else None,
                    }
                )
        m = [Measurement(name="total_requests_selected", value=total, unit="requests", definition=VOLUME_DEFINITION)]
        for r in rows:
            m.append(Measurement(name=f"requests_{r['borough']}", value=r["requests"], unit="requests"))
            m.append(
                Measurement(name=f"share_of_selected_{r['borough']}", value=r["share_of_selected"], unit="fraction")
            )
        m.append(Measurement(name="highest_borough", value=rows[0]["borough"]))
        if len(rows) == 2 and pairs:
            m += [
                Measurement(name="difference_requests", value=pairs[0]["difference_requests"], unit="requests"),
                Measurement(name="pct_more", value=pairs[0]["pct_more"], unit="fraction"),
            ]
        label_c = "all loaded categories" if category == "all" else category
        result = ToolResult(
            tool=tool,
            status="ok",
            capability=Capability.VALIDATED,
            summary=(
                f"{rows[0]['borough']} had the most requests ({rows[0]['requests']:,}) among "
                f"{', '.join(r['borough'] for r in rows)} for {label_c}, {eff_start}..{eff_end}."
            ),
            key_measurements=m,
            data={
                "filters": {
                    "start_date": eff_start.isoformat(),
                    "end_date": eff_end.isoformat(),
                    "category": category,
                    "boroughs": sel,
                },
                "rows": rows,
                "pairwise": pairs[:10],
            },
            metric_definitions={
                "requests": VOLUME_DEFINITION,
                "share_of_selected": "Borough requests / total requests across the selected boroughs (same window "
                "and category). The denominator is the selection, not the whole city.",
                "median_hours_to_close": DEFINITIONS["median_hours_to_close"],
                "open_beyond_7d_pct": DEFINITIONS["open_beyond_threshold_pct"].replace("N days", "7 days"),
                "pct_change_vs_prior_period": DEFINITIONS["comparison"],
            },
            provenance=self._provenance(),
            coverage=coverage,
            uncertainty=["Exact counts; differences between boroughs are descriptive, not tested for significance."],
            limitations=[cap.RAW_COUNTS, cap.REPORTED_NOT_INCIDENTS, cap.MEDIAN_CENSORING, cap.STATUS_SNAPSHOT],
            query=self._query(t0, len(rows), "AnalyticsService.kpis per borough (one query set per borough)."),
        )
        return enforce_size(result)

    # ------------------------------------------------------------------ 3. breakdown
    def get_category_breakdown(
        self, start_date: dt.date, end_date: dt.date, borough: str, level: str, category: str | None
    ) -> ToolResult:
        t0 = time.perf_counter()
        tool = "get_category_breakdown"
        if level == "category" and category is not None:
            raise ToolInputError(
                "a category filter cannot be combined with level='category' (it would return a single row); "
                "use level='descriptor' to break one category down"
            )
        if level == "descriptor" and category is None:
            raise ToolInputError("level='descriptor' requires a category")
        coverage, eff_start, eff_end = self._coverage(start_date, end_date)
        if eff_start is None or eff_end is None:
            return self._no_data(tool, t0, coverage, Capability.VALIDATED)
        f = Filters(
            start=eff_start,
            end=eff_end,
            boroughs=_boro_filter(borough),
            categories=[category] if category else [],
        )
        bd = self.svc.breakdown(f, "category_key" if level == "category" else "descriptor")
        total = sum(r.requests for r in bd.rows)
        rows: list[dict[str, Any]] = [
            {
                "key": sanitize_text(r.key),
                "label": sanitize_text(r.label),
                "requests": r.requests,
                "share": r.share,
                "median_hours_to_close": r.median_hours_to_close,
            }
            for r in bd.rows
        ]
        truncated = len(rows) > DESCRIPTOR_TOP
        if truncated:
            rest = rows[DESCRIPTOR_TOP:]
            rows = rows[:DESCRIPTOR_TOP]
            n_rest = sum(r["requests"] for r in rest)
            rows.append(
                {
                    "key": "__other__",
                    "label": f"{len(rest)} other descriptors",
                    "requests": n_rest,
                    "share": round(n_rest / total, 4) if total else None,
                    "median_hours_to_close": None,
                }
            )
        m = [Measurement(name="total_requests", value=total, unit="requests", definition=VOLUME_DEFINITION)]
        for r in rows[:10]:
            m.append(Measurement(name=f"requests_{r['key']}", value=r["requests"], unit="requests"))
            m.append(
                Measurement(name=f"share_{r['key']}", value=r["share"], unit="fraction", definition=SHARE_DEFINITION)
            )
        top = rows[0]["label"] if rows else None
        scope = "complaint category" if level == "category" else f"descriptor within {category}"
        result = ToolResult(
            tool=tool,
            status="ok" if rows else "no_data",
            capability=Capability.VALIDATED,
            summary=f"Breakdown by {scope}, {borough if borough != 'ALL' else 'all boroughs'}, "
            f"{eff_start}..{eff_end}: {total:,} requests; largest group {top}.",
            key_measurements=m,
            data={
                "filters": {
                    "start_date": eff_start.isoformat(),
                    "end_date": eff_end.isoformat(),
                    "borough": borough,
                    "level": level,
                    "category": category,
                },
                "rows": rows,
            },
            metric_definitions={
                "requests": VOLUME_DEFINITION,
                "share": SHARE_DEFINITION,
                "median_hours_to_close": DEFINITIONS["median_hours_to_close"],
            },
            provenance=self._provenance(),
            coverage=coverage,
            uncertainty=[],
            limitations=[
                LOADED_CATEGORIES_NOTE,
                cap.REPORTED_NOT_INCIDENTS,
                "Descriptor labels are source text and are shown verbatim (untrusted, length-limited).",
            ],
            query=self._query(
                t0,
                len(rows),
                f"AnalyticsService.breakdown(dimension={'category_key' if level == 'category' else 'descriptor'}).",
                truncated,
                DESCRIPTOR_TOP,
            ),
        )
        return enforce_size(result)

    # ------------------------------------------------------------------ 4. forecast
    def _forecast_eval_context(self, category: str) -> dict[str, Any]:
        ev = self.pred.evaluation("forecast")
        overall_test = {r["model"]: r for r in ev["overall"] if r["period"] == "test"}
        cat_test = {r["model"]: r for r in ev["by_category"] if r["period"] == "test" and r["category_key"] == category}
        periods = {p["period"]: p for p in self.pred.backtest_periods()}
        comparisons = [
            {
                "baseline": c["baseline"],
                "metric": c["metric"],
                "candidate_wape": round(c["candidate_value"], 4),
                "baseline_wape": round(c["baseline_value"], 4),
                "relative_improvement": round(c["relative_improvement"], 4),
                "wape_diff_ci95": [
                    round(c["wape_diff_baseline_minus_candidate"]["ci_low"], 4),
                    round(c["wape_diff_baseline_minus_candidate"]["ci_high"], 4),
                ],
                "significant_at_95": c["significant_at_95"],
                "bootstrap": f"paired over {c['wape_diff_baseline_minus_candidate']['n_groups']} test origins, "
                f"{c['wape_diff_baseline_minus_candidate']['n_boot']} resamples",
            }
            for c in ev["comparisons"]
            if c["period"] == "test"
        ]
        return {
            "ev": ev,
            "overall_test": overall_test,
            "category_test": cat_test,
            "periods": periods,
            "comparisons": comparisons,
        }

    def forecast_request_volume(self, category: str, borough: str) -> ToolResult:
        t0 = time.perf_counter()
        tool = "forecast_request_volume"
        ctx = self._forecast_eval_context(category)
        ev = ctx["ev"]
        selected = ev["selection"]["selected"]
        test_p = ctx["periods"].get("test", {})
        series_list = list(BOROUGHS5) if borough == "ALL" else [borough]
        per_series = []
        for b in series_list:
            fc = self.pred.forecast(category, b, history_days=28)
            per_series.append(
                {
                    "borough": b,
                    "forecast": fc["forecast"],
                    "recent_history": fc["history"][-14:],
                    "series_test_wape": {
                        r["model"]: round(r["wape"], 4)
                        for r in fc["test_metrics"]
                        if r["model"] in ("lightgbm", "naive")
                    },
                }
            )
        origin = self.pred.forecast(category, series_list[0], history_days=7)["origin"]
        level = ev["interval"]["level"]
        m: list[Measurement] = []
        if borough != "ALL":
            s = per_series[0]
            for p in s["forecast"]:
                m.append(
                    Measurement(
                        name=f"forecast_{p['target_date']}",
                        value=round(p["predicted"], 1),
                        unit="requests",
                        definition=f"Point forecast, horizon {p['horizon']} day(s); "
                        f"nominal {int(level * 100)}% interval "
                        f"[{round(p['lower'], 1) if p['lower'] is not None else 'n/a'}, "
                        f"{round(p['upper'], 1) if p['upper'] is not None else 'n/a'}]",
                    )
                )
            week_total = round(sum(p["predicted"] for p in s["forecast"]), 1)
            m.append(Measurement(name="forecast_7day_total", value=week_total, unit="requests"))
            for model_name, w in s["series_test_wape"].items():
                m.append(Measurement(name=f"series_test_wape_{model_name}", value=w, unit="WAPE"))
        else:
            totals = []
            for i in range(len(per_series[0]["forecast"])):
                day = per_series[0]["forecast"][i]["target_date"]
                total = round(sum(s["forecast"][i]["predicted"] for s in per_series), 1)
                totals.append({"target_date": day, "predicted_all_boroughs": total})
                m.append(Measurement(name=f"forecast_all_boroughs_{day}", value=total, unit="requests"))
            m.append(
                Measurement(
                    name="forecast_7day_total_all_boroughs",
                    value=round(sum(t["predicted_all_boroughs"] for t in totals), 1),
                    unit="requests",
                    definition="Sum of the five borough point forecasts. No interval: the interval of a sum is not "
                    "the sum of intervals.",
                )
            )
        ot = ctx["overall_test"]
        ct = ctx["category_test"]
        m += [
            Measurement(name="test_wape_selected_model_all_series", value=round(ot[selected]["wape"], 4), unit="WAPE"),
            Measurement(name="test_wape_naive_all_series", value=round(ot["naive"]["wape"], 4), unit="WAPE"),
            Measurement(
                name="test_wape_seasonal_naive_all_series", value=round(ot["seasonal_naive"]["wape"], 4), unit="WAPE"
            ),
            Measurement(name=f"test_wape_selected_model_{category}", value=round(ct[selected]["wape"], 4), unit="WAPE"),
            Measurement(name=f"test_wape_naive_{category}", value=round(ct["naive"]["wape"], 4), unit="WAPE"),
            Measurement(name="interval_nominal_level", value=level, unit="fraction"),
            Measurement(
                name=f"interval_test_coverage_{category}",
                value=round(ct[selected]["interval_coverage"], 4),
                unit="fraction",
                definition="Share of test outcomes inside the interval.",
            ),
        ]
        better_than_naive_in_category = ct[selected]["wape"] < ct["naive"]["wape"]
        data: dict[str, Any] = {
            "category": category,
            "borough": borough,
            "model": selected,
            "origin": origin,
            "horizon_days": ev["horizon_days"],
            "forecast_window": {
                "first": per_series[0]["forecast"][0]["target_date"] if per_series[0]["forecast"] else None,
                "last": per_series[0]["forecast"][-1]["target_date"] if per_series[0]["forecast"] else None,
            },
            "series": per_series,
            "evaluation": {
                "test_period_targets": [test_p.get("first_target"), test_p.get("last_target")],
                "test_origins": test_p.get("origins"),
                "overall_test_wape": {k: round(v["wape"], 4) for k, v in ot.items()},
                "category_test_wape": {k: round(v["wape"], 4) for k, v in ct.items()},
                "comparisons_vs_baselines": ctx["comparisons"],
                "selected_beats_naive_in_category": better_than_naive_in_category,
                "interval": {
                    "method": ev["interval"]["method"],
                    "nominal_level": level,
                    "test_coverage_all": round(ot[selected]["interval_coverage"], 4),
                    "test_coverage_category": round(ct[selected]["interval_coverage"], 4),
                },
                "selection_rule": ev["selection"]["rule"],
            },
            "training_cutoff": ev["production"]["origin"],
            "trained_rows": ev["production"]["trained_rows"],
        }
        if borough == "ALL":
            data["all_boroughs_point_total"] = totals
        uncertainty = [
            f"Intervals are nominal {int(level * 100)}%; measured test coverage was "
            f"{ot[selected]['interval_coverage']:.1%} overall and "
            f"{ct[selected]['interval_coverage']:.1%} for {category}.",
        ]
        if not better_than_naive_in_category:
            uncertainty.append(
                f"For {category} the selected model is not more accurate than the naive baseline on test "
                f"(WAPE {ct[selected]['wape']:.3f} vs {ct['naive']['wape']:.3f})."
            )
        result = ToolResult(
            tool=tool,
            status="ok",
            capability=Capability.EXPERIMENTAL,
            summary=f"{selected} 7-day forecast for {category}, "
            f"{borough if borough != 'ALL' else 'all five boroughs'}, "
            f"from origin {origin} (dataset cutoff), covering {data['forecast_window']['first']}.."
            f"{data['forecast_window']['last']}.",
            key_measurements=m,
            data=clean_json(data),
            metric_definitions={
                "WAPE": "sum |actual - forecast| / sum actual, pooled over all test points (lower is better).",
                "interval": ev["interval"]["method"],
                "origin": "Last complete day of data used to produce the forecast (training cutoff).",
                "horizon": "Days after the origin (1-7).",
            },
            provenance=self._provenance("forecast", "forecast.parquet + forecast_evaluation.json"),
            uncertainty=uncertainty,
            limitations=[*cap.FORECAST_LIMITS, cap.SNAPSHOT],
            query=self._query(
                t0, sum(len(s["forecast"]) for s in per_series), "Precomputed forecasts from the saved run."
            ),
        )
        return enforce_size(result)

    # ------------------------------------------------------------------ 5. delay (restricted)
    def predict_closure_delay(self, request: str, unique_key: str | None) -> ToolResult:
        t0 = time.perf_counter()
        tool = "predict_closure_delay"
        ev = self.pred.evaluation("delay_risk")
        sel = ev["selection"]["selected"]
        metrics = {}
        for name, mm in ev["models"].items():
            o = mm["test"]["overall"]
            metrics[name] = {
                k: round(o[k], 5) for k in ("pr_auc", "roc_auc", "brier", "ece", "mean_predicted", "positive_rate")
            }
        by_cat = [
            {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in r.items()
                if k in ("category_key", "n", "positive_rate", "roc_auc", "pr_auc", "mean_predicted")
            }
            for r in ev["models"][sel]["test"]["by_category"]
        ]
        splits = {
            k: {kk: v.get(kk) for kk in ("created_from", "created_to", "n", "positive_rate")}
            for k, v in ev["splits"].items()
        }
        o = ev["models"][sel]["test"]["overall"]
        m = [
            Measurement(
                name="test_positive_rate",
                value=round(o["positive_rate"], 4),
                unit="fraction",
                definition="Observed share not closed within 7 days, test period 2026-01-01..2026-10-25.",
            ),
            Measurement(name="test_mean_predicted", value=round(o["mean_predicted"], 4), unit="fraction"),
            Measurement(name="test_pr_auc_selected", value=round(o["pr_auc"], 4)),
            Measurement(name="test_roc_auc_selected", value=round(o["roc_auc"], 4)),
            Measurement(name="test_brier_selected", value=round(o["brier"], 5)),
            Measurement(
                name="test_brier_majority_class",
                value=round(ev["models"]["majority_class"]["test"]["overall"]["brier"], 5),
            ),
            Measurement(name="train_positive_rate", value=round(ev["train_prevalence"], 4), unit="fraction"),
        ]
        for r in by_cat:
            m.append(Measurement(name=f"within_category_roc_auc_{r['category_key']}", value=r["roc_auc"]))
        asked_numbers = request != "research_summary"
        summary = (
            "Numerical closure-delay predictions are DISABLED for the AI analyst: the model is experimental, "
            "under-calibrated on 2026 data and not validated for prioritization, allocation or ranking."
            if asked_numbers
            else "Closure-delay research summary: methodology, measured evaluation and limitations only "
            "(numerical predictions are disabled)."
        )
        result = ToolResult(
            tool=tool,
            status="restricted",
            capability=Capability.DISABLED,
            summary=summary,
            key_measurements=m,
            data={
                "numerical_predictions_enabled": cap.DELAY_NUMERIC_PREDICTIONS_ENABLED,
                "request": request,
                "unique_key_received": unique_key is not None,
                "decision": "Numerical predictions, risk rankings and per-request explanations are withheld.",
                "decision_basis": cap.AUDIT_DOC,
                "prohibited_uses": list(cap.PROHIBITED_DELAY_USES),
                "methodology": {
                    "target": ev["target"],
                    "target_definition": ev["target_definition"],
                    "label_snapshot": ev["snapshot"],
                    "features_used": ev["features"],
                    "split_rules": ev["split_rules"],
                    "selection": ev["selection"],
                },
                "splits": splits,
                "test_metrics_by_model": metrics,
                "test_by_category_selected_model": by_cat,
                "audit_findings": [
                    "All 2023-2024 records (and about half of 2025) were bulk-rewritten by the publisher on "
                    "2025-12-26; no 2026 record was. Their closure fields come from different processing states.",
                    "Heat closure-timestamp semantics changed around September 2025.",
                    "95.0% of mature 2026 heat positives are still open in the snapshot, not closed late.",
                ],
                "evaluation_page": "/delay-risk (experimental evaluation, unchanged)",
            },
            metric_definitions={
                "target": ev["target_definition"],
                "pr_auc": "Area under the precision-recall curve on the held-out test period.",
                "brier": "Mean squared error of predicted probabilities (lower is better).",
                "ece": "Expected calibration error.",
            },
            provenance=self._provenance("delay_risk", "delay_evaluation.json"),
            uncertainty=["Validation had about 200 positives, so model selection was noisy."],
            limitations=list(cap.DELAY_LIMITS),
            query=self._query(
                t0, len(metrics), "Read from the saved delay-risk evaluation artifact; no model executed."
            ),
        )
        return enforce_size(result)

    # ------------------------------------------------------------------ 6. anomalies
    def detect_unusual_spikes(
        self, start_date: dt.date, end_date: dt.date, category: str, geography: str, direction: str, limit: int
    ) -> ToolResult:
        t0 = time.perf_counter()
        tool = "detect_unusual_spikes"
        if end_date < start_date:
            raise ToolInputError("end_date must be on or after start_date")
        if (end_date - start_date).days > ANOMALY_MAX_DAYS:
            raise ToolInputError(f"the date range may not exceed {ANOMALY_MAX_DAYS} days")
        win = self.pred.anomaly_window()
        lo, hi = dt.date.fromisoformat(win["first"]), dt.date.fromisoformat(win["last"])
        eff_start, eff_end = max(start_date, lo), min(end_date, hi)
        coverage = Coverage(
            requested_start=_iso(start_date),
            requested_end=_iso(end_date),
            available_start=_iso(lo),
            available_end=_iso(hi),
            effective_start=_iso(eff_start) if eff_start <= eff_end else None,
            effective_end=_iso(eff_end) if eff_start <= eff_end else None,
            partial=start_date < lo or end_date > hi,
            note=None if lo <= start_date and end_date <= hi else f"The detector scored days {lo}..{hi} only.",
        )
        if eff_start > eff_end:
            return self._no_data(tool, t0, coverage, Capability.EXPERIMENTAL)
        cat = None if category == "all" else category
        geo = None if geography == "ALL_SERIES" else geography
        dirn = None if direction == "any" else direction
        res = self.pred.anomalies(cat, geo, dirn, eff_start, eff_end, limit, order_by="score")
        ev = self.pred.evaluation("anomaly")
        det = ev["detector"]
        primary = next(
            s
            for s in ev["summary"]
            if s["detector"] == det["primary"] and abs(s["threshold"] - det["z_threshold"]) < 1e-9
        )
        flags = [
            {
                "date": r["date"],
                "category": r["category_key"],
                "geography": r["borough"],
                "actual": int(r["actual"]),
                "expected": round(r["expected"], 1),
                "residual": round(r["residual"], 1),
                "score": round(r["score"], 2),
                "direction": r["direction"],
            }
            for r in res["rows"]
        ]
        m = [
            Measurement(
                name="flags_in_window",
                value=res["total"],
                unit="series-days",
                definition="Series-days whose robust z-score met the detector threshold.",
            ),
            Measurement(name="flags_returned", value=len(flags)),
            Measurement(name="z_threshold", value=det["z_threshold"]),
            Measurement(name="min_abs_deviation", value=det["min_abs_deviation"], unit="requests"),
            Measurement(
                name="synthetic_recall",
                value=round(primary["recall"], 4),
                unit="fraction",
                definition="Recall on 144 injected synthetic anomalies (not real incidents).",
            ),
            Measurement(
                name="alert_rate_uninjected_days",
                value=round(primary["alert_rate_uninjected_days"], 4),
                unit="fraction",
                definition="Share of ordinary days flagged. Not a false-positive rate.",
            ),
        ]
        for i, fl in enumerate(flags[:5], 1):
            m += [
                Measurement(name=f"flag{i}_date", value=fl["date"]),
                Measurement(name=f"flag{i}_actual", value=fl["actual"], unit="requests"),
                Measurement(name=f"flag{i}_expected", value=fl["expected"], unit="requests"),
                Measurement(name=f"flag{i}_score", value=fl["score"]),
            ]
        uncertainty = ["No ground-truth labels exist; each flag is unverified."]
        span = (eff_end - eff_start).days
        touches_oct = span >= 365 or any((eff_start + dt.timedelta(days=d)).month == 10 for d in range(span + 1))
        if touches_oct and category in ("all", "heat_hot_water"):
            uncertainty.append(
                "The window includes October: heating-season onset produces many heat flags because the baseline "
                "(median of the previous 8 same weekdays) lags the seasonal step. These flags are expected seasonal "
                "transitions as often as unusual events."
            )
        if geography == "ALL_SERIES":
            uncertainty.append("ALL_SERIES includes citywide and borough series, so one surge can be flagged twice.")
        result = ToolResult(
            tool=tool,
            status="ok",
            capability=Capability.EXPERIMENTAL,
            summary=f"{res['total']} statistical flags ({direction}) for {category}, {geography}, "
            f"{eff_start}..{eff_end}; "
            f"showing the {len(flags)} with the largest |score|. Flags are unverified.",
            key_measurements=m,
            data={
                "filters": {
                    "start_date": eff_start.isoformat(),
                    "end_date": eff_end.isoformat(),
                    "category": category,
                    "geography": geography,
                    "direction": direction,
                    "limit": limit,
                },
                "flags": flags,
                "label_status": "unverified statistical flags, not confirmed incidents",
                "detector": det,
            },
            metric_definitions={
                "expected": "Median of the same weekday over the previous 8 weeks.",
                "residual": "actual - expected.",
                "score": "residual / max(1.4826 x MAD of residuals over the previous 56 days, sqrt(max(expected, 1))).",
                "flag": f"|score| >= {det['z_threshold']} and |residual| >= {det['min_abs_deviation']} requests.",
            },
            provenance=self._provenance("anomaly", "anomaly_scores.parquet + anomaly_evaluation.json"),
            coverage=coverage,
            uncertainty=uncertainty,
            limitations=list(cap.ANOMALY_LIMITS),
            query=self._query(
                t0,
                len(flags),
                "PredictionService.anomalies ordered by |score| (precomputed causal detector scores).",
                res["total"] > len(flags),
                limit,
            ),
        )
        return enforce_size(result)

    # ------------------------------------------------------------------ 7. explain
    def explain_model_prediction(
        self, model: str, category: str | None, geography: str | None, date: dt.date | None, top_k: int
    ) -> ToolResult:
        t0 = time.perf_counter()
        tool = "explain_model_prediction"
        if model == "forecast":
            ex = self.pred.explanations("forecast")["global"]
            gain = ex["gain_production_model"][:top_k]
            perm = ex["permutation_test_wape_increase"][:top_k]
            m = [
                Measurement(
                    name=f"perm_rank{i}_{p['feature']}",
                    value=round(p["wape_increase_mean"], 5),
                    definition="Mean increase in test WAPE when the feature is permuted.",
                )
                for i, p in enumerate(perm, 1)
            ] + [
                Measurement(name=f"gain_rank{i}_{g['feature']}", value=round(g["gain"], 1))
                for i, g in enumerate(gain, 1)
            ]
            data: dict[str, Any] = {
                "model": "forecast",
                "scope": "global (whole model). No per-forecast attribution was computed.",
                "gain_production_model": [clean_json(g) for g in gain],
                "permutation_test_wape_increase": [clean_json(p) for p in perm],
                "methods": {
                    "gain": "Total split gain per feature in the production LightGBM model.",
                    "permutation": "Increase in test WAPE when the feature is randomly permuted.",
                },
            }
            summary = f"Global feature importance for the forecast model; top feature by gain: {gain[0]['feature']}."
            prov = self._provenance("forecast", "forecast_evaluation.json")
            limits = [cap.ATTRIBUTION_NOT_CAUSAL, *cap.FORECAST_LIMITS[:1]]
            capability = Capability.EXPERIMENTAL
        elif model == "delay_risk":
            ex = self.pred.explanations("delay_risk")["global"]
            perm = ex["permutation_test_pr_auc_drop"]["lightgbm"][:top_k]
            coefs = [
                {"feature": c["feature"], "level": sanitize_text(c.get("level", "")), "coefficient": c["coefficient"]}
                for c in ex["logistic_regression_coefficients"][:top_k]
            ]
            m = [
                Measurement(
                    name=f"perm_rank{i}_{p['feature']}",
                    value=round(p["pr_auc_drop_mean"], 5),
                    definition="Mean drop in test PR-AUC when the feature is permuted.",
                )
                for i, p in enumerate(perm, 1)
            ]
            data = {
                "model": "delay_risk",
                "scope": "global research explanations only; per-request explanations are DISABLED",
                "permutation_test_pr_auc_drop": [clean_json(p) for p in perm],
                "logistic_regression_coefficients": [clean_json(c) for c in coefs],
            }
            summary = "Global explanations of the experimental delay-risk model (per-request explanations disabled)."
            prov = self._provenance("delay_risk", "delay_evaluation.json")
            limits = [cap.ATTRIBUTION_NOT_CAUSAL, *cap.DELAY_LIMITS]
            capability = Capability.EXPERIMENTAL
        else:
            if category is None or geography is None or date is None:
                raise ToolInputError("anomaly explanations require category, geography and date")
            pts = self.pred.anomaly_series(category, geography, date - dt.timedelta(days=56), date)["points"]
            by_day = {p["date"]: p for p in pts}
            target = by_day.get(date.isoformat())
            if target is None:
                raise ToolInputError(f"{date} is not a scored day for {category} / {geography}")
            prior_same_dow = [
                by_day[(date - dt.timedelta(days=7 * k)).isoformat()]["actual"]
                for k in range(1, 9)
                if (date - dt.timedelta(days=7 * k)).isoformat() in by_day
            ]
            srt = sorted(prior_same_dow)
            n = len(srt)
            median = (srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2) if n else None
            residual = target["actual"] - target["expected"]
            scale = residual / target["score"] if target["score"] else None
            m = [
                Measurement(name="actual", value=int(target["actual"]), unit="requests"),
                Measurement(name="expected", value=round(target["expected"], 1), unit="requests"),
                Measurement(name="residual", value=round(residual, 1), unit="requests"),
                Measurement(name="robust_scale", value=round(scale, 2) if scale else None),
                Measurement(name="score", value=round(target["score"], 2)),
                Measurement(
                    name="recomputed_expected",
                    value=median,
                    unit="requests",
                    definition="Median of the previous 8 same-weekday actuals, recomputed from the series.",
                ),
            ]
            data = {
                "model": "anomaly",
                "series": {"category": category, "geography": geography, "date": date.isoformat()},
                "is_flagged": bool(target["is_anomaly"]),
                "direction": target["direction"],
                "previous_same_weekday_actuals": [int(v) for v in prior_same_dow],
                "recomputed_expected_matches_stored": median is not None and abs(median - target["expected"]) < 1e-6,
                "decomposition": "score = (actual - expected) / robust_scale",
            }
            summary = (
                f"{category}/{geography} on {date}: actual {int(target['actual'])} vs expected "
                f"{target['expected']:.1f}, robust z {target['score']:.2f} "
                f"({'flagged' if target['is_anomaly'] else 'not flagged'})."
            )
            prov = self._provenance("anomaly", "anomaly_scores.parquet")
            limits = list(cap.ANOMALY_LIMITS)
            capability = Capability.EXPERIMENTAL
        result = ToolResult(
            tool=tool,
            status="ok",
            capability=capability,
            summary=summary,
            key_measurements=m,
            data=clean_json(data),
            metric_definitions={"causality": CAUSALITY_NOTE},
            provenance=prov,
            uncertainty=["Attributions describe the fitted model, which itself has the limitations listed."],
            limitations=limits,
            query=self._query(t0, len(m), "Read from saved explanation artifacts / recomputed from the stored series."),
        )
        return enforce_size(result)

    # ------------------------------------------------------------------ 8. quality
    def get_dataset_quality(self, now: Callable[[], dt.datetime] | None = None) -> ToolResult:
        t0 = time.perf_counter()
        tool = "get_dataset_quality"
        rep = self.svc.quality_report()
        opts = self.svc.filter_options()
        build = rep.build or {}
        fresh = rep.freshness
        now_utc = (now or (lambda: dt.datetime.now(dt.UTC)))()
        hours_since_cutoff = None
        if fresh.latest_created_at:
            from zoneinfo import ZoneInfo

            cutoff = dt.datetime.fromisoformat(fresh.latest_created_at).replace(tzinfo=ZoneInfo("America/New_York"))
            hours_since_cutoff = round((now_utc - cutoff).total_seconds() / 3600, 1)
        issues = [
            {
                "rule_id": r.rule_id,
                "status": r.status,
                "description": sanitize_text(r.description, 200),
                "detail": sanitize_text(r.detail, 300),
            }
            for r in rep.rules
            if r.status in ("warn", "fail")
        ]
        m = [
            Measurement(
                name="record_count",
                value=build.get("accepted_rows"),
                unit="requests",
                definition="Distinct service requests in the warehouse.",
            ),
            Measurement(name="distinct_keys", value=build.get("distinct_keys")),
            Measurement(
                name="duplicate_raw_rows",
                value=build.get("duplicate_raw_rows"),
                unit="rows",
                definition="Raw extracted rows that repeated an already-seen unique_key (re-fetched updates); "
                "deduplicated to the latest version during the build.",
            ),
            Measurement(name="rejected_rows", value=build.get("rejected_rows"), unit="rows"),
            Measurement(name="data_cutoff", value=fresh.latest_created_at, definition="Newest created_at (NYC local)."),
            Measurement(name="first_date", value=_iso(opts.min_date)),
            Measurement(name="last_complete_date", value=_iso(opts.last_complete_date)),
            Measurement(
                name="hours_since_data_cutoff",
                value=hours_since_cutoff,
                unit="hours",
                definition="Time between the newest request in the data and when this tool ran.",
            ),
            Measurement(name="latest_ingestion_build_at_utc", value=fresh.last_build_at_utc),
            Measurement(name="quality_rules_warn", value=rep.status_counts.get("warn", 0)),
            Measurement(name="quality_rules_fail", value=rep.status_counts.get("fail", 0)),
        ]
        warnings = [
            f"Snapshot, not live: newest request {fresh.latest_created_at} (NYC); "
            f"{hours_since_cutoff} hours before this check."
            if hours_since_cutoff is not None
            else cap.SNAPSHOT,
            "The publisher bulk-rewrote all 2023-2024 records on 2025-12-26; open/closed status is not comparable "
            "across that boundary.",
            LOADED_CATEGORIES_NOTE,
        ] + [f"{i['rule_id']} ({i['status']}): {i['description']}" for i in issues]
        result = ToolResult(
            tool=tool,
            status="ok",
            capability=Capability.VALIDATED,
            summary=f"{build.get('accepted_rows'):,} requests, {_iso(opts.min_date)}..{fresh.latest_created_at} (NYC); "
            f"build {build.get('build_id')}; {len(issues)} quality rules warn/fail. Snapshot, not live.",
            key_measurements=m,
            data={
                "build": {
                    k: build.get(k)
                    for k in (
                        "build_id",
                        "built_at_utc",
                        "raw_files",
                        "raw_rows",
                        "accepted_rows",
                        "distinct_keys",
                        "duplicate_raw_rows",
                        "rejected_rows",
                        "min_created_at",
                        "max_created_at",
                        "snapshot_at_local",
                    )
                },
                "freshness": clean_json(fresh.model_dump()),
                "hours_since_data_cutoff": hours_since_cutoff,
                "quality_status_counts": rep.status_counts,
                "quality_issues": issues,
                "source": {
                    k: (rep.source or {}).get(k) for k in ("name", "rows_updated_at", "checked_at", "attribution")
                },
                "manifest_reference": cap.MANIFEST_DOC,
                "warnings": warnings,
                "categories_loaded": [c["key"] for c in opts.categories],
            },
            metric_definitions={
                "data_cutoff": "Newest request created_at in the snapshot.",
                "freshness": "Live = false always; the warehouse is rebuilt only when the pipeline is re-run.",
                "duplicate_raw_rows": "Raw rows repeating a unique_key; the warehouse keeps one row per request.",
            },
            provenance=self._provenance(),
            uncertainty=[],
            limitations=[cap.SNAPSHOT, cap.POINT_IN_TIME, cap.STATUS_SNAPSHOT],
            query=self._query(t0, len(rep.rules), "AnalyticsService.quality_report + build metadata."),
        )
        return enforce_size(result)

    # ------------------------------------------------------------------ 9. evaluation
    def get_model_evaluation(self, model: str) -> ToolResult:
        t0 = time.perf_counter()
        tool = "get_model_evaluation"
        ev = self.pred.evaluation(model)
        m: list[Measurement] = []
        if model == "forecast":
            sel = ev["selection"]["selected"]
            overall = [
                {
                    k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in r.items()
                    if k in ("model", "period", "n", "wape", "mae", "bias", "interval_coverage", "n_with_interval")
                }
                for r in ev["overall"]
            ]
            for r in overall:
                if r["period"] == "test":
                    m.append(Measurement(name=f"test_wape_{r['model']}", value=r["wape"], unit="WAPE"))
                    m.append(
                        Measurement(
                            name=f"test_interval_coverage_{r['model']}", value=r["interval_coverage"], unit="fraction"
                        )
                    )
            ctx = self._forecast_eval_context("noise_residential")
            for c in ctx["comparisons"]:
                m.append(
                    Measurement(
                        name=f"relative_wape_improvement_vs_{c['baseline']}",
                        value=c["relative_improvement"],
                        unit="fraction",
                    )
                )
                m.append(Measurement(name=f"significant_vs_{c['baseline']}", value=str(c["significant_at_95"]).lower()))
                m.append(Measurement(name=f"wape_diff_ci_low_vs_{c['baseline']}", value=c["wape_diff_ci95"][0]))
                m.append(Measurement(name=f"wape_diff_ci_high_vs_{c['baseline']}", value=c["wape_diff_ci95"][1]))
            by_cat = [
                {
                    k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in r.items()
                    if k in ("model", "period", "category_key", "wape", "interval_coverage")
                }
                for r in ev["by_category"]
                if r["period"] == "test"
            ]
            for r in by_cat:
                m.append(Measurement(name=f"test_wape_{r['model']}_{r['category_key']}", value=r["wape"], unit="WAPE"))
            data: dict[str, Any] = {
                "selected_model": sel,
                "selection": ev["selection"],
                "periods_configured": ev["periods"],
                "periods_actual_targets": self.pred.backtest_periods(),
                "overall": overall,
                "comparisons_vs_baselines": ctx["comparisons"],
                "by_category_test": by_cat,
                "by_horizon_test_selected": [
                    {"horizon": r["horizon"], "wape": round(r["wape"], 4)}
                    for r in ev["by_horizon"]
                    if r["period"] == "test" and r["model"] == sel
                ],
                "interval": ev["interval"],
                "production": ev["production"],
            }
            summary = (
                f"Forecast evaluation: {sel} test WAPE {ctx['overall_test'][sel]['wape']:.3f}; "
                "significantly better than "
                + ", ".join(c["baseline"] for c in ctx["comparisons"] if c["significant_at_95"])
                + "; NOT significantly better than "
                + (", ".join(c["baseline"] for c in ctx["comparisons"] if not c["significant_at_95"]) or "none")
                + "."
            )
            capability, limits = Capability.VALIDATED, list(cap.FORECAST_LIMITS)
            artifact = "forecast_evaluation.json"
        elif model == "delay_risk":
            sel = ev["selection"]["selected"]
            per_model = {}
            for name, mm in ev["models"].items():
                per_model[name] = {
                    period: {
                        k: round(mm[period]["overall"][k], 5)
                        for k in ("pr_auc", "roc_auc", "brier", "ece", "log_loss", "mean_predicted", "positive_rate")
                    }
                    for period in ("validation", "test")
                }
                per_model[name]["test_at_threshold"] = clean_json(mm["test"]["at_threshold"])
                per_model[name]["test_top_10pct"] = clean_json(mm["test"]["top_10pct"])
                t = per_model[name]["test"]
                m.append(Measurement(name=f"test_pr_auc_{name}", value=t["pr_auc"]))
                m.append(Measurement(name=f"test_roc_auc_{name}", value=t["roc_auc"]))
                m.append(Measurement(name=f"test_brier_{name}", value=t["brier"]))
                m.append(Measurement(name=f"test_mean_predicted_{name}", value=t["mean_predicted"], unit="fraction"))
            m.append(
                Measurement(name="test_positive_rate", value=per_model[sel]["test"]["positive_rate"], unit="fraction")
            )
            by_cat = [
                {
                    k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in r.items()
                    if k in ("category_key", "n", "positive_rate", "roc_auc", "pr_auc", "mean_predicted")
                }
                for r in ev["models"][sel]["test"]["by_category"]
            ]
            for r in by_cat:
                m.append(Measurement(name=f"test_roc_auc_{sel}_{r['category_key']}", value=r["roc_auc"]))
            data = {
                "selected_model": sel,
                "selection": ev["selection"],
                "target_definition": ev["target_definition"],
                "snapshot": ev["snapshot"],
                "splits": ev["splits"],
                "metrics": per_model,
                "by_category_test_selected": by_cat,
                "calibration": "Every reliability decile under-predicts on test; no post-hoc recalibration was done.",
                "status": "EXPERIMENTAL model; numerical predictions DISABLED for the AI analyst",
            }
            summary = (
                f"Delay-risk evaluation ({sel}): test PR-AUC {per_model[sel]['test']['pr_auc']:.3f}, ROC-AUC "
                f"{per_model[sel]['test']['roc_auc']:.3f}, mean predicted "
                f"{per_model[sel]['test']['mean_predicted']:.4f} vs "
                f"observed {per_model[sel]['test']['positive_rate']:.4f}. Experimental; not decision-grade."
            )
            capability, limits = Capability.VALIDATED, list(cap.DELAY_LIMITS)
            artifact = "delay_evaluation.json"
        else:
            det = ev["detector"]
            rows = [clean_json(s) for s in ev["summary"]]
            for s in rows:
                tag = f"{s['detector']}_z{s['threshold']}"
                m.append(Measurement(name=f"synthetic_recall_{tag}", value=round(s["recall"], 4), unit="fraction"))
                m.append(
                    Measurement(
                        name=f"alert_rate_uninjected_{tag}",
                        value=round(s["alert_rate_uninjected_days"], 4),
                        unit="fraction",
                    )
                )
            m.append(Measurement(name="real_data_flags", value=ev["real_data"]["alerts"]))
            m.append(Measurement(name="real_data_days_scored", value=ev["real_data"]["days_scored"]))
            data = {
                "detector": det,
                "evaluation_window": [ev["window_start"], ev["window_end"]],
                "synthetic_summary": rows,
                "recall_by_magnitude_primary": [
                    clean_json(r)
                    for r in ev["by_magnitude"]
                    if r["detector"] == det["primary"] and abs(r["threshold"] - det["z_threshold"]) < 1e-9
                ],
                "recall_by_series_size_primary": [
                    clean_json(r)
                    for r in ev["by_series_size"]
                    if r["detector"] == det["primary"] and abs(r["threshold"] - det["z_threshold"]) < 1e-9
                ],
                "real_data": ev["real_data"],
                "false_positive_definition": "Not measurable: no real labels. 'Alert rate on uninjected days' is the "
                "share of ordinary days that raise a flag, which may include real anomalies.",
            }
            summary = (
                f"Anomaly detector {det['primary']} (|z| >= {det['z_threshold']}): "
                "synthetic-injection evaluation only; "
                f"{ev['real_data']['alerts']} unverified flags on real data."
            )
            capability, limits = (
                Capability.VALIDATED,
                [
                    *cap.ANOMALY_LIMITS,
                    "Synthetic recall is NOT VERIFIED by the AI readiness audit (injections are not persisted).",
                ],
            )
            artifact = "anomaly_evaluation.json"
        result = ToolResult(
            tool=tool,
            status="ok",
            capability=capability,
            summary=summary,
            key_measurements=m[:40],
            data=clean_json(data),
            metric_definitions={
                "WAPE": "sum |actual - forecast| / sum actual",
                "PR-AUC": "area under the precision-recall curve",
                "recall (synthetic)": "share of injected anomalies flagged in the injected direction",
            },
            provenance=self._provenance(model, artifact),
            uncertainty=[
                "Values are read verbatim from the saved artifact and were reproduced by the AI readiness audit."
            ],
            limitations=limits,
            query=self._query(t0, len(m), f"Read {artifact} from model run (no recomputation)."),
        )
        return enforce_size(result)
