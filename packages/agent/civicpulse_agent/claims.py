"""Semantic evidence validation: structured facts, structured claims, deterministic checks, controlled rendering.

Pipeline::

    MCP tool result -> Fact records (extract_facts) -> Claim objects proposed by the provider
    -> validate_claims (dimension + value + provenance checks, derived arithmetic recomputed in code)
    -> narrative checks (no free numbers, prose dimensions agree with the claim) -> render_answer

The provider never writes a number into the final answer directly. It writes prose containing
``{C1}``-style placeholders plus a list of claims; every placeholder is replaced by text rendered here
from the *fact* value (not the provider's copy of it), and only claims that passed validation can be
rendered. Numbers anywhere else in the prose make the answer invalid.

A fact is the tuple (metric_id, geography_id, category_id, period, comparison period, unit, value,
snapshot, model version, method) read from one evidence record. A claim must match one fact on every
dimension, so a correct number attributed to the wrong borough, category, period, unit, snapshot or
model version is rejected.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from civicpulse_agent.evidence import Evidence

BOROUGH_IDS = {
    "BRONX": "bronx",
    "BROOKLYN": "brooklyn",
    "MANHATTAN": "manhattan",
    "QUEENS": "queens",
    "STATEN ISLAND": "staten_island",
}
GEO_LABEL = {v: k for k, v in BOROUGH_IDS.items()} | {"all": "all boroughs"}
CATEGORY_LABEL = {
    "noise_residential": "residential noise",
    "heat_hot_water": "heat/hot water",
    "all": "all loaded categories",
}
TEXT_FIELDS = ("summary", "limitations", "uncertainty", "warnings", "decision", "prohibited_uses")
DERIVED_OPS = ("pct_change", "difference", "share", "ratio")
FACTS_SHOWN_TO_MODEL = 80

ClaimStatus = Literal[
    "valid",
    "missing_citation",
    "invalid_evidence",
    "unknown_metric",
    "miscited",
    "geography_mismatch",
    "category_mismatch",
    "period_mismatch",
    "comparison_period_mismatch",
    "unit_mismatch",
    "value_mismatch",
    "missing_snapshot",
    "snapshot_mismatch",
    "model_version_mismatch",
    "stale_model_version",
    "method_mismatch",
    "conflicting_evidence",
    "invalid_operands",
    "invalid_denominator",
    "direction_incorrect",
    "invalid_text_reference",
    "malformed",
]


# ----------------------------------------------------------------------------------------------- normalizers
def geo_id(value: Any) -> str:
    """Canonical geography id: borough slug, 'all' (citywide) or 'selection:a+b' (a subset of boroughs)."""
    if value is None:
        return "all"
    if isinstance(value, list | tuple):
        ids = sorted(geo_id(v) for v in value)
        return "selection:" + "+".join(ids)
    s = str(value).strip()
    up = s.upper().replace("_", " ")
    if up in BOROUGH_IDS:
        return BOROUGH_IDS[up]
    if up in ("ALL", "CITYWIDE", "ALL BOROUGHS", "ALL SERIES", "NYC", "CITY"):
        return "all"
    low = s.lower()
    if low.startswith("selection:"):
        parts = [geo_id(p) for p in low.split(":", 1)[1].split("+") if p]
        return "selection:" + "+".join(sorted(parts))
    return low.replace(" ", "_")


def cat_id(value: Any) -> str:
    if value is None:
        return "all"
    s = str(value).strip().lower()
    if s in ("all", "all loaded categories", "all categories", ""):
        return "all"
    if ":" in s:
        base, seg = s.split(":", 1)
        return f"{cat_id(base)}:{seg}"
    if "noise" in s:
        return "noise_residential"
    if "heat" in s or "hot water" in s:
        return "heat_hot_water"
    return s.replace(" ", "_")


_UNIT_ALIASES = {
    "request": "requests",
    "requests": "requests",
    "count": "requests",
    "complaints": "requests",
    "%": "percent",
    "pct": "percent",
    "percent": "percent",
    "percentage": "percent",
    "fraction": "fraction",
    "share": "fraction",
    "proportion": "fraction",
    "hour": "hours",
    "hours": "hours",
    "wape": "WAPE",
    "": "",
}


def unit_id(value: Any) -> str:
    s = str(value or "").strip()
    return _UNIT_ALIASES.get(s.lower(), s)


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:60]


# ----------------------------------------------------------------------------------------------- facts
class Fact(BaseModel):
    fact_id: str
    evidence_id: str
    tool: str
    metric_id: str
    geography_id: str
    category_id: str
    period_start: str | None = None
    period_end: str | None = None
    comparison_period_start: str | None = None
    comparison_period_end: str | None = None
    value: float | str
    unit: str
    snapshot_id: str | None
    model_version: str | None = None
    method: str
    definition: str = ""

    def key(self) -> tuple[Any, ...]:
        return (
            self.metric_id,
            self.geography_id,
            self.category_id,
            self.period_start,
            self.period_end,
            self.comparison_period_start,
            self.comparison_period_end,
            self.unit,
            self.snapshot_id,
            self.model_version,
        )

    def for_model(self) -> dict[str, Any]:
        """Compact form shown to the language model next to the tool result."""
        d = self.model_dump(exclude={"definition", "tool"}, exclude_none=True)
        return d


class _FactBuilder:
    def __init__(self, ev: Evidence) -> None:
        self.ev = ev
        self.facts: list[Fact] = []
        data = ev.result.get("data") or {}
        filters = data.get("filters") or {}
        self.filters = filters
        self.geo = geo_id(filters.get("borough") or filters.get("geography"))
        self.cat = cat_id(filters.get("category"))
        self.start = filters.get("start_date")
        self.end = filters.get("end_date")
        self.method = (ev.result.get("query") or {}).get("calculation", "")[:200] or ev.tool

    def add(self, metric: str, value: Any, unit: str, **dims: Any) -> None:
        if value is None or isinstance(value, bool):
            return
        if isinstance(value, float) and not math.isfinite(value):
            return
        if not isinstance(value, int | float | str):
            return
        self.facts.append(
            Fact(
                fact_id=f"{self.ev.evidence_id}.F{len(self.facts) + 1}",
                evidence_id=self.ev.evidence_id,
                tool=self.ev.tool,
                metric_id=metric,
                geography_id=dims.pop("geography_id", self.geo),
                category_id=dims.pop("category_id", self.cat),
                period_start=dims.pop("period_start", self.start),
                period_end=dims.pop("period_end", self.end),
                value=float(value) if isinstance(value, int | float) else str(value),
                unit=unit_id(unit),
                snapshot_id=self.ev.data_build_id,
                model_version=dims.pop("model_version", self.ev.model_version),
                method=dims.pop("method", self.method),
                **dims,
            )
        )


def _bucket_end(start: str, grain: str) -> str:
    d = dt.date.fromisoformat(start)
    if grain == "week":
        return (d + dt.timedelta(days=6)).isoformat()
    if grain == "month":
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return (nxt - dt.timedelta(days=1)).isoformat()
    return start


def extract_facts(ev: Evidence) -> list[Fact]:
    """Deterministic fact records for one evidence item (tool-specific, then generic key measurements)."""
    if ev.status not in ("ok", "restricted"):
        return []
    b = _FactBuilder(ev)
    data = ev.result.get("data") or {}
    tool = ev.tool
    km = {m.get("name"): m for m in ev.key_measurements if isinstance(m, dict)}

    if tool == "get_service_request_trends":
        b.add("request_count", (km.get("total_requests") or {}).get("value"), "requests")
        grain = b.filters.get("granularity", "day")
        for p in data.get("points") or []:
            b.add(
                "request_count",
                p.get("value"),
                "requests",
                period_start=p["period"],
                period_end=_bucket_end(p["period"], grain),
                method=f"{grain} bucket of agg_daily_requests"
                + (" (partial bucket)" if p.get("partial_bucket") else ""),
            )
        prior = data.get("prior_period") or {}
        if prior.get("prior_total") is not None:
            b.add(
                "request_count",
                prior["prior_total"],
                "requests",
                period_start=prior.get("prior_start"),
                period_end=prior.get("prior_end"),
            )
            b.add(
                "pct_change_vs_prior_period",
                prior.get("pct_change"),
                "fraction",
                comparison_period_start=prior.get("prior_start"),
                comparison_period_end=prior.get("prior_end"),
            )
        name = f"mean_requests_per_{grain}"
        if name in km:
            b.add(name, km[name].get("value"), "requests")
    elif tool == "compare_boroughs":
        rows = data.get("rows") or []
        sel = geo_id(b.filters.get("boroughs") or [r["borough"] for r in rows])
        for r in rows:
            g = geo_id(r["borough"])
            b.add("request_count", r.get("requests"), "requests", geography_id=g)
            b.add("share_of_selected", r.get("share_of_selected"), "fraction", geography_id=g)
            b.add("rank_by_requests", r.get("rank_by_requests"), "rank", geography_id=g)
            b.add("median_hours_to_close", r.get("median_hours_to_close"), "hours", geography_id=g)
            b.add("open_beyond_7d_pct", r.get("open_beyond_7d_pct"), "fraction", geography_id=g)
            if r.get("prior_period_requests") is not None:
                b.add(
                    "pct_change_vs_prior_period",
                    r.get("pct_change_vs_prior_period"),
                    "fraction",
                    geography_id=g,
                    comparison_period_start=None,
                    comparison_period_end=None,
                )
                b.add("prior_period_request_count", r.get("prior_period_requests"), "requests", geography_id=g)
        total = (km.get("total_requests_selected") or {}).get("value")
        b.add("request_count", total, "requests", geography_id=sel)
        if rows:
            b.add("highest_geography", GEO_LABEL[geo_id(rows[0]["borough"])], "borough", geography_id=sel)
            b.add("lowest_geography", GEO_LABEL[geo_id(rows[-1]["borough"])], "borough", geography_id=sel)
        for p in data.get("pairwise") or []:
            pair = f"{geo_id(p['higher'])}-vs-{geo_id(p['lower'])}"
            b.add("difference_requests", p.get("difference_requests"), "requests", geography_id=pair)
            b.add("pct_more", p.get("pct_more"), "fraction", geography_id=pair)
    elif tool == "get_category_breakdown":
        level = b.filters.get("level", "category")
        for r in data.get("rows") or []:
            seg = cat_id(r["key"]) if level == "category" else f"{b.cat}:{_slug(r['key'])}"
            b.add("request_count", r.get("requests"), "requests", category_id=seg)
            b.add("share_of_total", r.get("share"), "fraction", category_id=seg)
        b.add("request_count", (km.get("total_requests") or {}).get("value"), "requests")
    elif tool == "forecast_request_volume":
        c = cat_id(data.get("category"))
        for s in data.get("series") or []:
            g = geo_id(s.get("borough"))
            for f in s.get("forecast") or []:
                day = f.get("target_date")
                for metric, key in (
                    ("forecast_predicted", "predicted"),
                    ("forecast_lower", "lower"),
                    ("forecast_upper", "upper"),
                ):
                    v = f.get(key)
                    b.add(
                        metric,
                        round(v, 1) if isinstance(v, float) else v,
                        "requests",
                        geography_id=g,
                        category_id=c,
                        period_start=day,
                        period_end=day,
                        method="precomputed forecast",
                    )
        for t in data.get("all_boroughs_point_total") or []:
            day = t.get("target_date")
            b.add(
                "forecast_predicted",
                t.get("predicted_all_boroughs"),
                "requests",
                geography_id="all",
                category_id=c,
                period_start=day,
                period_end=day,
                method="sum of borough point forecasts",
            )
        evaluation = data.get("evaluation") or {}
        tp = evaluation.get("test_period_targets") or [None, None]
        for model, w in (evaluation.get("category_test_wape") or {}).items():
            b.add(
                f"test_wape_{model}",
                w,
                "WAPE",
                geography_id="all",
                category_id=c,
                period_start=tp[0],
                period_end=tp[1],
                method="rolling-origin backtest, test period",
            )
        for model, w in (evaluation.get("overall_test_wape") or {}).items():
            b.add(
                f"test_wape_{model}",
                w,
                "WAPE",
                geography_id="all",
                category_id="all",
                period_start=tp[0],
                period_end=tp[1],
                method="rolling-origin backtest, test period",
            )
        interval = evaluation.get("interval") or {}
        b.add(
            "interval_nominal_level",
            interval.get("nominal_level"),
            "fraction",
            geography_id="all",
            category_id="all",
            period_start=None,
            period_end=None,
        )
        b.add(
            "interval_test_coverage",
            interval.get("test_coverage_category"),
            "fraction",
            geography_id="all",
            category_id=c,
            period_start=tp[0],
            period_end=tp[1],
        )
    elif tool == "detect_unusual_spikes":
        for f in data.get("flags") or []:
            g, c, day = geo_id(f.get("geography")), cat_id(f.get("category")), f.get("date")
            for metric, key, unit in (
                ("anomaly_actual", "actual", "requests"),
                ("anomaly_expected", "expected", "requests"),
                ("anomaly_score", "score", "z-score"),
            ):
                b.add(metric, f.get(key), unit, geography_id=g, category_id=c, period_start=day, period_end=day)

    # Generic key measurements (definitions travel with the fact); skip names already represented above.
    covered = {
        "total_requests",
        "total_requests_selected",
        "prior_period_total",
        "pct_change_vs_prior_period",
        "highest_borough",
        "difference_requests",
        "pct_more",
    }
    for km_name, m in km.items():
        if not km_name or km_name in covered or km_name.startswith(("requests_", "share_", "forecast_", "flag")):
            continue
        if tool == "forecast_request_volume" and km_name.startswith(("test_wape", "series_test_wape", "interval_")):
            continue
        b.add(_slug(km_name), m.get("value"), m.get("unit", ""), definition=str(m.get("definition", ""))[:300])
    return b.facts


def build_fact_index(evidence: list[Evidence]) -> tuple[list[Fact], set[tuple[Any, ...]]]:
    """All facts plus the set of fact keys that carry conflicting values across evidence records."""
    facts = [f for ev in evidence for f in extract_facts(ev)]
    by_key: dict[tuple[Any, ...], set[Any]] = {}
    for f in facts:
        by_key.setdefault(f.key(), set()).add(round(f.value, 9) if isinstance(f.value, float) else f.value)
    conflicts = {k for k, vals in by_key.items() if len(vals) > 1}
    return facts, conflicts


# ----------------------------------------------------------------------------------------------- claims
class Claim(BaseModel):
    """One quantitative (or quoted) statement proposed by the provider. Validated, never trusted."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^C\d{1,3}$")
    type: Literal["measurement", "derived", "comparison", "evidence_text"] = "measurement"
    evidence_id: str | None = None
    metric_id: str | None = None
    geography_id: str | None = None
    category_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    comparison_period_start: str | None = None
    comparison_period_end: str | None = None
    value: float | str | None = None
    unit: str | None = None
    snapshot_id: str | None = None
    model_version: str | None = None
    method: str | None = None
    operation: str | None = None
    operands: list[str] = Field(default_factory=list)
    denominator_geography_id: str | None = None
    left: str | None = None
    right: str | None = None
    relation: Literal["greater", "less", "equal"] | None = None
    field: str | None = None
    index: int | None = None


@dataclass
class ClaimCheck:
    claim: Claim
    status: str
    reason: str = ""
    fact: Fact | None = None
    computed: float | str | None = None  # authoritative value used for rendering
    unit: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    text: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "valid"

    def to_dict(self) -> dict[str, Any]:
        c = self.claim
        return {
            "id": c.id,
            "type": c.type,
            "status": self.status,
            "reason": self.reason,
            "evidence_ids": self.evidence_ids,
            "fact_id": self.fact.fact_id if self.fact else None,
            "metric_id": self.fact.metric_id if self.fact else c.metric_id or c.operation,
            "geography_id": self.fact.geography_id if self.fact else c.geography_id,
            "category_id": self.fact.category_id if self.fact else c.category_id,
            "period_start": self.fact.period_start if self.fact else c.period_start,
            "period_end": self.fact.period_end if self.fact else c.period_end,
            "value": self.computed if self.ok else c.value,
            "unit": self.unit or unit_id(c.unit),
            "snapshot_id": self.fact.snapshot_id if self.fact else c.snapshot_id,
            "model_version": self.fact.model_version if self.fact else c.model_version,
            "method": (self.fact.method if self.fact else c.method or c.operation),
            "operation": c.operation,
            "operands": c.operands or [x for x in (c.left, c.right) if x],
            "relation": c.relation,
            # The claim exactly as proposed, so a report can re-validate it offline without the LLM.
            "input": c.model_dump(exclude_none=True, exclude_defaults=True) | {"id": c.id, "type": c.type},
        }


def _decimals(v: float) -> int:
    s = repr(float(v))
    if "e" in s or "E" in s:
        return 9
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def _num_equal(claimed: float, actual: float, integer_exact: bool = False) -> bool:
    if integer_exact:
        return abs(claimed - actual) < 1e-9
    tol = max(1e-9, 0.5 * 10 ** (-_decimals(claimed)) + 1e-12)
    return bool(abs(claimed - actual) <= tol)


def _value_matches(claim_value: Any, claim_unit: str, fact_value: Any, fact_unit: str) -> bool:
    if isinstance(fact_value, str):
        return isinstance(claim_value, str) and claim_value.strip().upper() == fact_value.strip().upper()
    if isinstance(claim_value, str):
        try:
            claim_value = float(claim_value.replace(",", "").rstrip("%"))
        except ValueError:
            return False
    if claim_value is None:
        return False
    cv = float(claim_value)
    if fact_unit == "fraction" and claim_unit == "percent":
        cv = cv / 100.0
        tol = max(1e-9, 0.5 * 10 ** (-_decimals(float(claim_value))) / 100 + 1e-12)
        return bool(abs(cv - float(fact_value)) <= tol)
    return _num_equal(
        cv, float(fact_value), integer_exact=fact_unit in ("requests", "rank") and float(fact_value).is_integer()
    )


def _units_compatible(claim_unit: str, fact_unit: str) -> bool:
    return claim_unit == fact_unit or (fact_unit == "fraction" and claim_unit == "percent")


def _contains(whole: str, part: str) -> bool:
    if whole == part:
        return False
    if whole == "all":
        return True
    if whole.startswith("selection:"):
        return part in whole.split(":", 1)[1].split("+")
    return False


class ClaimValidator:
    def __init__(
        self,
        evidence: dict[str, Evidence],
        facts: list[Fact],
        conflicts: set[tuple[Any, ...]],
        expected_model_versions: dict[str, str] | None = None,
    ) -> None:
        self.evidence = evidence
        self.facts = facts
        self.conflicts = conflicts
        self.expected = expected_model_versions or {}
        self.results: dict[str, ClaimCheck] = {}

    # --------------------------------------------------------------- measurement
    def _measurement(self, c: Claim) -> ClaimCheck:
        def fail(status: str, reason: str, fact: Fact | None = None) -> ClaimCheck:
            return ClaimCheck(c, status, reason, fact, evidence_ids=[c.evidence_id] if c.evidence_id else [])

        if not c.evidence_id:
            return fail("missing_citation", "the claim has no evidence_id")
        if c.evidence_id not in self.evidence:
            return fail("invalid_evidence", f"evidence {c.evidence_id} does not exist")
        if not c.metric_id:
            return fail("malformed", "metric_id is required")
        metric = _slug(c.metric_id)
        cands = [f for f in self.facts if f.evidence_id == c.evidence_id and f.metric_id == metric]
        if not cands:
            elsewhere = sorted({f.evidence_id for f in self.facts if f.metric_id == metric})
            if elsewhere:
                return fail("miscited", f"metric {metric} is not in {c.evidence_id}; it is in {', '.join(elsewhere)}")
            return fail("unknown_metric", f"metric {metric} does not exist in any evidence")
        g = geo_id(c.geography_id)
        by_geo = [f for f in cands if f.geography_id == g]
        if not by_geo:
            opts = sorted({f.geography_id for f in cands})[:8]
            return fail(
                "geography_mismatch", f"{metric} in {c.evidence_id} has no value for geography '{g}' (has {opts})"
            )
        k = cat_id(c.category_id)
        by_cat = [f for f in by_geo if f.category_id == k]
        if not by_cat:
            opts = sorted({f.category_id for f in by_geo})[:8]
            return fail("category_mismatch", f"{metric} for {g} has no value for category '{k}' (has {opts})")
        by_period = [f for f in by_cat if f.period_start == c.period_start and f.period_end == c.period_end]
        if not by_period:
            opts = sorted({f"{f.period_start}..{f.period_end}" for f in by_cat})[:6]
            return fail(
                "period_mismatch",
                f"{metric} for {g}/{k} has no value for {c.period_start}..{c.period_end} (has {opts})",
            )
        by_cmp = [
            f
            for f in by_period
            if f.comparison_period_start is None
            or (
                f.comparison_period_start == c.comparison_period_start
                and f.comparison_period_end == c.comparison_period_end
            )
        ]
        if not by_cmp:
            f0 = by_period[0]
            return fail(
                "comparison_period_mismatch",
                f"{metric} compares with {f0.comparison_period_start}..{f0.comparison_period_end}, "
                f"not {c.comparison_period_start}..{c.comparison_period_end}",
                f0,
            )
        cu = unit_id(c.unit)
        by_unit = [f for f in by_cmp if _units_compatible(cu, f.unit)]
        if not by_unit:
            return fail("unit_mismatch", f"{metric} is measured in '{by_cmp[0].unit}', not '{cu}'", by_cmp[0])
        fact = next((f for f in by_unit if _value_matches(c.value, cu, f.value, f.unit)), None)
        if fact is None:
            shown = by_unit[0].value
            return fail(
                "value_mismatch",
                f"{metric} for {g}/{k} {c.period_start}..{c.period_end} is {shown}, not {c.value}",
                by_unit[0],
            )
        if fact.snapshot_id is None:
            return fail("missing_snapshot", f"evidence {c.evidence_id} carries no data snapshot id", fact)
        if c.snapshot_id != fact.snapshot_id:
            return fail("snapshot_mismatch", f"the evidence snapshot is {fact.snapshot_id}, not {c.snapshot_id}", fact)
        if fact.model_version is not None:
            if c.model_version != fact.model_version:
                return fail(
                    "model_version_mismatch", f"the model version is {fact.model_version}, not {c.model_version}", fact
                )
            model_name = (self.evidence[c.evidence_id].result.get("provenance") or {}).get("model_name")
            want = self.expected.get(model_name or "")
            if want and want != fact.model_version:
                return fail(
                    "stale_model_version", f"evidence uses model version {fact.model_version}; current is {want}", fact
                )
        if c.method and _slug(c.method) not in (_slug(fact.method), _slug(fact.tool)):
            return fail("method_mismatch", f"the calculation method is '{fact.method}'", fact)
        if fact.key() in self.conflicts:
            return fail("conflicting_evidence", f"evidence records disagree on {metric} for {g}/{k}", fact)
        return ClaimCheck(
            c, "valid", "", fact, computed=fact.value, unit=cu or fact.unit, evidence_ids=[fact.evidence_id]
        )

    # --------------------------------------------------------------- derived
    def _operand(self, cid: str) -> ClaimCheck | None:
        r = self.results.get(cid)
        return r if r is not None and r.ok and r.fact is not None and isinstance(r.computed, float) else None

    def _derived(self, c: Claim) -> ClaimCheck:
        def fail(status: str, reason: str) -> ClaimCheck:
            return ClaimCheck(c, status, reason)

        if c.operation not in DERIVED_OPS:
            return fail("malformed", f"operation must be one of {DERIVED_OPS}")
        if len(c.operands) != 2:
            return fail("invalid_operands", "derived claims need exactly two operand claim ids")
        a, b = (self._operand(x) for x in c.operands)
        if a is None or b is None:
            return fail("invalid_operands", "every operand must be an earlier, valid measurement claim")
        fa, fb = a.fact, b.fact
        assert fa is not None and fb is not None
        va, vb = float(fa.value), float(fb.value)
        if fa.metric_id != fb.metric_id or fa.unit != fb.unit:
            return fail(
                "invalid_operands",
                f"operands measure different things ({fa.metric_id}/{fa.unit} vs {fb.metric_id}/{fb.unit})",
            )
        if c.operation == "pct_change":
            if (fa.geography_id, fa.category_id) != (fb.geography_id, fb.category_id):
                return fail("invalid_operands", "a change must compare the same geography and category")
            if not (fb.period_end and fa.period_start and fb.period_end < fa.period_start):
                return fail("invalid_operands", "the second operand must be the earlier (base) period")
            if vb == 0:
                return fail("invalid_operands", "the base period is zero")
            computed, natural = (va - vb) / vb, "fraction"
        elif c.operation in ("difference", "ratio"):
            dims_differ = sum(
                x != y
                for x, y in (
                    (fa.geography_id, fb.geography_id),
                    (fa.category_id, fb.category_id),
                    ((fa.period_start, fa.period_end), (fb.period_start, fb.period_end)),
                )
            )
            if dims_differ != 1:
                return fail(
                    "invalid_operands", "operands must differ in exactly one dimension (geography, category or period)"
                )
            if c.operation == "ratio" and vb == 0:
                return fail("invalid_operands", "division by zero")
            computed = va - vb if c.operation == "difference" else va / vb
            natural = fa.unit if c.operation == "difference" else "ratio"
        else:  # share
            if (fa.period_start, fa.period_end) != (fb.period_start, fb.period_end):
                return fail("invalid_denominator", "numerator and denominator cover different periods")
            same_cat = fa.category_id == fb.category_id
            geo_ok = same_cat and _contains(fb.geography_id, fa.geography_id)
            cat_ok = (
                fa.geography_id == fb.geography_id
                and (fb.category_id == "all" or fa.category_id.startswith(fb.category_id + ":"))
                and fa.category_id != fb.category_id
            )
            if not (geo_ok or cat_ok):
                return fail(
                    "invalid_denominator",
                    f"{fb.geography_id}/{fb.category_id} does not contain {fa.geography_id}/{fa.category_id}",
                )
            if c.denominator_geography_id is not None and geo_id(c.denominator_geography_id) != fb.geography_id:
                return fail(
                    "invalid_denominator",
                    f"the stated denominator is {geo_id(c.denominator_geography_id)} "
                    f"but the operand is {fb.geography_id}",
                )
            if vb == 0:
                return fail("invalid_denominator", "the denominator is zero")
            computed, natural = va / vb, "fraction"
        cu = unit_id(c.unit) or natural
        if not (cu == natural or (natural == "fraction" and cu == "percent")):
            return fail("unit_mismatch", f"a {c.operation} is expressed as {natural}, not {cu}")
        if c.value is not None and not _value_matches(c.value, cu, computed, natural):
            return fail("value_mismatch", f"{c.operation} of the operands is {computed:.6g}, not {c.value}")
        return ClaimCheck(
            c,
            "valid",
            "",
            None,
            computed=round(computed, 6),
            unit=cu,
            evidence_ids=sorted({fa.evidence_id, fb.evidence_id}),
        )

    # --------------------------------------------------------------- comparison
    def _comparison(self, c: Claim) -> ClaimCheck:
        if not c.left or not c.right or c.relation is None:
            return ClaimCheck(c, "malformed", "comparison needs left, right and relation")
        lres, rres = self.results.get(c.left), self.results.get(c.right)
        if not (
            lres
            and rres
            and lres.ok
            and rres.ok
            and isinstance(lres.computed, float)
            and isinstance(rres.computed, float)
        ):
            return ClaimCheck(c, "invalid_operands", "left and right must be earlier, valid numeric claims")
        if lres.unit != rres.unit or (lres.fact and rres.fact and lres.fact.metric_id != rres.fact.metric_id):
            return ClaimCheck(c, "invalid_operands", "compared claims must share metric and unit")
        lv, rv = lres.computed, rres.computed
        actual = "greater" if lv > rv else ("less" if lv < rv else "equal")
        ids = sorted(set(lres.evidence_ids + rres.evidence_ids))
        if actual != c.relation:
            return ClaimCheck(
                c, "direction_incorrect", f"{c.left} is {actual} than {c.right}, not {c.relation}", evidence_ids=ids
            )
        return ClaimCheck(c, "valid", "", None, computed=actual, unit="", evidence_ids=ids)

    # --------------------------------------------------------------- quoted evidence text
    def _text(self, c: Claim) -> ClaimCheck:
        if not c.evidence_id:
            return ClaimCheck(c, "missing_citation", "the quote has no evidence_id")
        ev = self.evidence.get(c.evidence_id)
        if ev is None:
            return ClaimCheck(c, "invalid_evidence", f"evidence {c.evidence_id} does not exist")
        if c.field not in TEXT_FIELDS:
            return ClaimCheck(c, "invalid_text_reference", f"field must be one of {TEXT_FIELDS}")
        data = ev.result.get("data") or {}
        if c.field == "summary":
            text: Any = ev.summary
        elif c.field == "decision":
            text = data.get("decision")
        elif c.field == "prohibited_uses":
            text = "; ".join(str(x) for x in data.get("prohibited_uses") or []) + "."
        elif c.field == "warnings":
            ws = data.get("warnings") or []
            text = ws[c.index] if c.index is not None and 0 <= c.index < len(ws) else None
        else:
            items = getattr(ev, c.field)
            text = items[c.index] if c.index is not None and 0 <= c.index < len(items) else None
        if not isinstance(text, str) or not text:
            return ClaimCheck(c, "invalid_text_reference", f"{c.evidence_id}.{c.field}[{c.index}] does not exist")
        return ClaimCheck(c, "valid", "", None, computed=None, evidence_ids=[c.evidence_id], text=text)

    def validate(self, claims: list[Claim]) -> list[ClaimCheck]:
        out = []
        for c in claims:
            if c.id in self.results:
                chk = ClaimCheck(c, "malformed", f"duplicate claim id {c.id}")
            elif c.type == "measurement":
                chk = self._measurement(c)
            elif c.type == "derived":
                chk = self._derived(c)
            elif c.type == "comparison":
                chk = self._comparison(c)
            else:
                chk = self._text(c)
            self.results.setdefault(c.id, chk)
            out.append(chk)
        return out


# ----------------------------------------------------------------------------------------------- parsing
@dataclass
class ParsedAnswer:
    narrative: str
    claims: list[Claim]
    structured: bool
    errors: list[str]


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.S)


def parse_answer(text: str) -> ParsedAnswer:
    """Accept ``{"answer": "...{C1}...", "claims": [...]}``; anything else is unstructured prose."""
    raw = _FENCE.sub("", text or "").strip()
    if not raw.startswith("{"):
        return ParsedAnswer(text or "", [], False, [])
    try:
        obj = json.loads(raw)
    except ValueError:
        return ParsedAnswer(text, [], False, ["the answer looks like JSON but does not parse"])
    if not isinstance(obj, dict) or not isinstance(obj.get("answer"), str):
        return ParsedAnswer(text, [], False, ['the JSON answer needs a string field "answer"'])
    claims: list[Claim] = []
    errors: list[str] = []
    for i, c in enumerate((obj.get("claims") or [])[:60]):
        try:
            claims.append(Claim.model_validate(c))
        except ValidationError as exc:
            errors.append(f"claim #{i + 1} is malformed: {exc.errors()[0]['msg'][:120]}")
    return ParsedAnswer(obj["answer"], claims, True, errors)


# ----------------------------------------------------------------------------------------------- narrative checks
PLACEHOLDER = re.compile(r"\{(C\d{1,3})\}")
_BORO_WORDS = re.compile(r"\b(bronx|brooklyn|manhattan|queens|staten island|citywide|all (five )?boroughs)\b", re.I)
_CAT_WORDS = re.compile(r"\b(noise|heat|hot water|heating|all loaded categories)\b", re.I)
_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_UP = re.compile(r"\b(increas(e|ed|es|ing)|rose|rise|rising|grew|growth|jump(ed)?|climb(ed)?|up by)\b", re.I)
_DOWN = re.compile(r"\b(decreas(e|ed|es|ing)|declin(e|ed|es|ing)|fell|fall(ing)?|drop(ped|s)?|down by)\b", re.I)
_MOST = re.compile(r"\b(most|highest|largest|busiest|greatest)\b", re.I)
_LEAST = re.compile(r"\b(fewest|lowest|least|smallest|quietest)\b", re.I)


def _geo_word(word: str) -> str:
    w = word.lower()
    if w.startswith(("citywide", "all")):
        return "all"
    return geo_id(w.upper())


def _cat_word(word: str) -> str:
    w = word.lower()
    if w.startswith("all"):
        return "all"
    return "noise_residential" if w == "noise" else "heat_hot_water"


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        out += [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\[({*-])", line) if s.strip()]
    return out


@dataclass
class NarrativeReport:
    violations: list[str]
    free_numbers: list[str]
    used_claims: list[str]


def check_narrative(narrative: str, checks: dict[str, ClaimCheck], facts: list[Fact]) -> NarrativeReport:
    from civicpulse_agent.grounding import free_numbers

    violations: list[str] = []
    used = PLACEHOLDER.findall(narrative)
    for cid in dict.fromkeys(used):
        chk = checks.get(cid)
        if chk is None:
            violations.append(f"placeholder {{{cid}}} has no claim")
        elif not chk.ok:
            violations.append(f"claim {cid} failed validation ({chk.status}): {chk.reason}")
    masked = PLACEHOLDER.sub(lambda m: " " * len(m.group(0)), narrative)
    loose = free_numbers(masked)
    for tok in loose[:10]:
        violations.append(f"the number {tok} is written in prose; numbers may only appear through validated claims")

    highest = {f.value for f in facts if f.metric_id == "highest_geography"}
    lowest = {f.value for f in facts if f.metric_id == "lowest_geography"}
    for sent in _sentences(narrative):
        pos = 0
        for m in PLACEHOLDER.finditer(sent):
            seg = sent[pos : m.start()]
            pos = m.end()
            chk = checks.get(m.group(1))
            if chk is None or not chk.ok or chk.fact is None:
                if chk is not None and chk.ok and chk.claim.type == "derived" and chk.claim.operation == "pct_change":
                    _direction(seg, float(chk.computed or 0), m.group(1), violations)
                continue
            f = chk.fact
            boros = [_geo_word(b.group(0)) for b in _BORO_WORDS.finditer(seg)]
            scoped = not f.geography_id.startswith("selection:") and "-vs-" not in f.geography_id
            if boros and scoped and boros[-1] != f.geography_id:
                violations.append(
                    f"the prose attributes {{{m.group(1)}}} to {boros[-1]}, but the value is for {f.geography_id}"
                )
            cats = [_cat_word(c.group(0)) for c in _CAT_WORDS.finditer(seg)]
            if cats and cats[-1] != f.category_id.split(":")[0] and f.category_id != "all":
                violations.append(
                    f"the prose attributes {{{m.group(1)}}} to {cats[-1]}, but the value is for {f.category_id}"
                )
            if f.period_start and f.period_end:
                for d in _ISO.findall(seg):
                    if not (f.period_start <= d <= f.period_end) and not (
                        f.comparison_period_start and f.comparison_period_start <= d <= (f.comparison_period_end or d)
                    ):
                        violations.append(
                            f"the prose places {{{m.group(1)}}} on {d}, outside its period "
                            f"{f.period_start}..{f.period_end}"
                        )
            if f.metric_id.startswith("pct_change") and isinstance(f.value, float):
                _direction(seg, f.value, m.group(1), violations)
        # Superlatives about boroughs must agree with the evidence ranking.
        prose = PLACEHOLDER.sub(" ", sent)
        for rx, truth, word in ((_MOST, highest, "most"), (_LEAST, lowest, "fewest")):
            for sm in rx.finditer(prose):
                before = [b for b in _BORO_WORDS.finditer(prose[: sm.start()])]
                after = [b for b in _BORO_WORDS.finditer(prose[sm.end() :])]
                near = before[-1] if before else (after[0] if after else None)
                if near is None or not truth:
                    continue
                named = GEO_LABEL.get(_geo_word(near.group(0)), "")
                if named and named not in truth and named != "all boroughs":
                    violations.append(f"the prose says {named} had the {word}, which the evidence does not show")
    return NarrativeReport(violations, loose, list(dict.fromkeys(used)))


def _direction(seg: str, value: float, cid: str, violations: list[str]) -> None:
    if _UP.search(seg) and value < 0:
        violations.append(f"the prose describes {{{cid}}} as an increase, but it is a decrease")
    if _DOWN.search(seg) and value > 0:
        violations.append(f"the prose describes {{{cid}}} as a decrease, but it is an increase")


# ----------------------------------------------------------------------------------------------- rendering
def fmt_value(value: Any, unit: str) -> str:
    if isinstance(value, str):
        return value
    v = float(value)
    if unit in ("fraction", "percent"):
        pct = 100 * v
        return f"{pct:.0f}%" if abs(pct - round(pct)) < 1e-9 else f"{pct:.1f}%"
    if unit == "requests":
        return f"{v:,.0f}" if v.is_integer() else f"{v:,.1f}"
    if unit == "hours":
        return f"{v:,.1f} hours"
    if unit == "ratio":
        return f"{v:.2f}x"
    if unit == "WAPE":
        return f"WAPE {v:.3f}"
    return f"{v:,.4g}" + (f" {unit}" if unit and unit not in ("rank", "z-score") else "")


def _unit_word(unit: str) -> str:
    return {"requests": " requests"}.get(unit, "")


def _dims_text(f: Fact, sentence: str, disclosed: set[str]) -> str:
    parts: list[str] = []
    if f.period_start is None and f.geography_id == "all" and f.category_id == "all":
        return ""  # a global constant such as the nominal interval level
    named_geo = {_geo_word(b.group(0)) for b in _BORO_WORDS.finditer(sentence)}
    if f.geography_id in GEO_LABEL and f.geography_id not in named_geo:
        parts.append(GEO_LABEL[f.geography_id])
    named_cat = {_cat_word(c.group(0)) for c in _CAT_WORDS.finditer(sentence)}
    base_cat = f.category_id.split(":")[0]
    if base_cat in CATEGORY_LABEL and base_cat not in named_cat:
        parts.append(
            CATEGORY_LABEL[base_cat] + (f" ({f.category_id.split(':', 1)[1]})" if ":" in f.category_id else "")
        )
    if f.period_start and f.period_end:
        dates = _ISO.findall(sentence)
        if not any(f.period_start <= d <= f.period_end for d in dates):
            parts.append(f.period_start if f.period_start == f.period_end else f"{f.period_start} to {f.period_end}")
    # The model version is validated and listed with each claim, but not repeated inline.
    parts = [x for x in parts if x not in disclosed]
    disclosed.update(parts)
    return f" ({', '.join(parts)})" if parts else ""


def render_claim(
    chk: ClaimCheck,
    sentence: str,
    checks: dict[str, ClaimCheck],
    disclosed: set[str] | None = None,
    cites: list[str] | None = None,
) -> str:
    """Text for one validated claim. Numeric claims add their evidence ids to ``cites`` (cited once per sentence)."""
    c = chk.claim
    if c.type == "evidence_text":
        return f"{chk.text} [{', '.join(chk.evidence_ids)}]"
    if cites is not None:
        cites.extend(e for e in chk.evidence_ids if e not in cites)
        tail = ""
    else:
        tail = " [" + ", ".join(chk.evidence_ids) + "]" if chk.evidence_ids else ""
    if c.type == "measurement" and chk.fact is not None:
        f = chk.fact
        unit = chk.unit or f.unit
        dims = _dims_text(f, sentence, disclosed if disclosed is not None else set())
        return f"{fmt_value(f.value, unit)}{_unit_word(unit)}{dims}{tail}"
    if c.type == "derived":
        return f"{fmt_value(chk.computed, chk.unit)}{tail}"
    if c.type == "comparison" and c.left and c.right:
        lc, rc = checks[c.left], checks[c.right]
        verb = {"greater": "more than", "less": "less than", "equal": "the same as"}[str(chk.computed)]
        lv, rv = fmt_value(lc.computed, lc.unit), fmt_value(rc.computed, rc.unit)
        return f"{_label(lc)} {verb} {_label(rc)} ({lv} vs {rv}){tail}"
    return tail.strip()


def _label(chk: ClaimCheck) -> str:
    f = chk.fact
    if f is None:
        return chk.claim.id
    return GEO_LABEL.get(f.geography_id, f.geography_id)


def _append_cites(sentence: str, cites: list[str]) -> str:
    missing = [c for c in cites if not re.search(rf"\[[^\]]*\b{c}\b[^\]]*\]", sentence)]
    if not missing:
        return sentence
    tag = " [" + ", ".join(missing) + "]"
    m = re.search(r"([.!?:;]?)(\s*)$", sentence)
    end = m.start() if m else len(sentence)
    return sentence[:end] + tag + sentence[end:]


def render_answer(narrative: str, checks: dict[str, ClaimCheck]) -> str:
    """Replace placeholders with rendered, validated claims. Callers must only render validated narratives."""
    lines = []
    for line in narrative.splitlines():
        out = []
        for sent in re.split(r"((?<=[.!?])\s+(?=[A-Z\[({*-]))", line):
            seen: set[str] = set()
            cites: list[str] = []
            text = PLACEHOLDER.sub(
                lambda m, s=sent, d=seen, k=cites: (
                    render_claim(checks[m.group(1)], s, checks, d, k) if m.group(1) in checks else m.group(0)
                ),
                sent,
            )
            out.append(_append_cites(text, cites) if cites else text)
        lines.append("".join(out))
    return "\n".join(lines)


def derived_numbers(checks: dict[str, ClaimCheck]) -> dict[str, set[float]]:
    """Values computed in code (derived claims) per evidence id, so defense-in-depth number checks accept them."""
    out: dict[str, set[float]] = {}
    for chk in checks.values():
        if chk.ok and chk.claim.type == "derived" and isinstance(chk.computed, float):
            for eid in chk.evidence_ids:
                out.setdefault(eid, set()).add(chk.computed)
    return out


@dataclass
class SemanticReport:
    ok: bool
    structured: bool
    checks: list[ClaimCheck]
    narrative: NarrativeReport
    parse_errors: list[str]
    rendered: str

    def feedback(self) -> str:
        lines = list(self.parse_errors)
        if not self.structured:
            lines.append(
                'Return the answer as JSON {"answer": "...", "claims": [...]}: put every number in a claim and '
                "refer to it with {C#} in the answer text."
            )
        lines += [f"Claim {c.claim.id}: {c.status} - {c.reason}" for c in self.checks if not c.ok][:10]
        lines += self.narrative.violations[:10]
        return " ".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "structured": self.structured,
            "claims_total": len(self.checks),
            "claims_valid": sum(c.ok for c in self.checks),
            "claims": [c.to_dict() for c in self.checks[:60]],
            "narrative_violations": self.narrative.violations[:20],
            "parse_errors": self.parse_errors[:10],
        }


def evaluate_answer(
    text: str,
    evidence: dict[str, Evidence],
    facts: list[Fact],
    conflicts: set[tuple[Any, ...]],
    expected_model_versions: dict[str, str] | None = None,
) -> SemanticReport:
    parsed = parse_answer(text)
    validator = ClaimValidator(evidence, facts, conflicts, expected_model_versions)
    checks = validator.validate(parsed.claims)
    by_id = validator.results
    narrative = check_narrative(parsed.narrative, by_id, facts)
    used = set(narrative.used_claims)
    # Claims that were proposed but never referenced are harmless; failed ones referenced are violations above.
    ok = not parsed.errors and not narrative.violations and all(by_id[c].ok for c in used if c in by_id)
    rendered = render_answer(parsed.narrative, by_id) if ok else parsed.narrative
    return SemanticReport(
        ok, parsed.structured, [c for c in checks if c.claim.id in used] or checks, narrative, parsed.errors, rendered
    )
