"""Semantic evidence validation: claims must match a fact on every dimension, not just its number.

Evidence here is synthetic and small on purpose (values are illustrative, not real data): each test
states one way an answer can be numerically "supported" yet semantically wrong.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from civicpulse_agent.claims import (
    Claim,
    ClaimValidator,
    build_fact_index,
    cat_id,
    evaluate_answer,
    extract_facts,
    geo_id,
)
from civicpulse_agent.evidence import Evidence

BUILD = "20261104T194022Z-5da243"
FC_VERSION = "forecast-20261104T195715Z-27ea6f"
OCT = ("2026-10-01", "2026-10-31")


def _ev(eid: str, tool: str, data: dict[str, Any], km: list[dict[str, Any]] | None = None, **prov: Any) -> Evidence:
    structured = {
        "tool": tool,
        "status": "ok",
        "capability": "VALIDATED",
        "summary": f"{tool} summary 1,200 requests.",
        "key_measurements": km or [],
        "data": data,
        "provenance": {"data_build_id": BUILD, "data_cutoff": "2026-11-01 02:04:40", **prov},
        "query": {"calculation": "AnalyticsService.kpis per borough"},
        "limitations": ["Counts are raw, not per capita."],
        "uncertainty": ["Exact counts."],
    }
    return Evidence.from_tool_result(eid, structured, {}, 5.0)


def compare_ev(
    eid: str = "E1", brooklyn: int = 1200, queens: int = 950, category: str = "noise_residential"
) -> Evidence:
    total = brooklyn + queens
    rows = [
        {
            "borough": "BROOKLYN",
            "requests": brooklyn,
            "share_of_selected": round(brooklyn / total, 4),
            "rank_by_requests": 1,
        },
        {"borough": "QUEENS", "requests": queens, "share_of_selected": round(queens / total, 4), "rank_by_requests": 2},
    ]
    return _ev(
        eid,
        "compare_boroughs",
        {
            "filters": {
                "start_date": OCT[0],
                "end_date": OCT[1],
                "category": category,
                "boroughs": ["BROOKLYN", "QUEENS"],
            },
            "rows": rows,
            "pairwise": [
                {
                    "higher": "BROOKLYN",
                    "lower": "QUEENS",
                    "difference_requests": brooklyn - queens,
                    "pct_more": round((brooklyn - queens) / queens, 4),
                }
            ],
        },
        [{"name": "total_requests_selected", "value": total, "unit": "requests"}],
    )


def trend_ev(eid: str = "E2") -> Evidence:
    return _ev(
        eid,
        "get_service_request_trends",
        {
            "filters": {
                "start_date": OCT[0],
                "end_date": OCT[1],
                "category": "heat_hot_water",
                "borough": "ALL",
                "granularity": "week",
            },
            "points": [],
            "prior_period": {
                "prior_total": 800,
                "prior_start": "2026-08-31",
                "prior_end": "2026-09-30",
                "pct_change": 0.5,
            },
        },
        [{"name": "total_requests", "value": 1200, "unit": "requests"}],
    )


def forecast_ev(eid: str = "E3", version: str = FC_VERSION) -> Evidence:
    return _ev(
        eid,
        "forecast_request_volume",
        {
            "category": "heat_hot_water",
            "series": [
                {
                    "borough": "BRONX",
                    "forecast": [{"target_date": "2026-11-01", "predicted": 300.0, "lower": 200.0, "upper": 600.0}],
                }
            ],
            "evaluation": {"interval": {"nominal_level": 0.9}},
        },
        model_name="forecast",
        model_version=version,
    )


def m(
    cid: str,
    eid: str,
    metric: str,
    value: Any,
    unit: str = "requests",
    geo: str = "brooklyn",
    cat: str = "noise_residential",
    period: tuple[str | None, str | None] = OCT,
    **kw: Any,
) -> dict[str, Any]:
    return {
        "id": cid,
        "type": "measurement",
        "evidence_id": eid,
        "metric_id": metric,
        "geography_id": geo,
        "category_id": cat,
        "period_start": period[0],
        "period_end": period[1],
        "value": value,
        "unit": unit,
        "snapshot_id": BUILD,
        **kw,
    }


def run(claims: list[dict[str, Any]], *evs: Evidence, versions: dict[str, str] | None = None) -> dict[str, str]:
    evidence = {e.evidence_id: e for e in evs}
    facts, conflicts = build_fact_index(list(evs))
    v = ClaimValidator(evidence, facts, conflicts, versions)
    return {c.claim.id: c.status for c in v.validate([Claim.model_validate(c) for c in claims])}


def answer(narrative: str, claims: list[dict[str, Any]], *evs: Evidence) -> Any:
    evidence = {e.evidence_id: e for e in evs}
    facts, conflicts = build_fact_index(list(evs))
    return evaluate_answer(json.dumps({"answer": narrative, "claims": claims}), evidence, facts, conflicts)


# ------------------------------------------------------------------------------------------- facts
def test_facts_carry_every_dimension() -> None:
    facts = extract_facts(compare_ev())
    bk = next(f for f in facts if f.metric_id == "request_count" and f.geography_id == "brooklyn")
    assert (bk.category_id, bk.period_start, bk.period_end, bk.unit, bk.snapshot_id, bk.value) == (
        "noise_residential",
        *OCT,
        "requests",
        BUILD,
        1200.0,
    )
    sel = next(f for f in facts if f.metric_id == "request_count" and f.geography_id.startswith("selection:"))
    assert sel.geography_id == "selection:brooklyn+queens" and sel.value == 2150.0
    assert any(f.metric_id == "highest_geography" and f.value == "BROOKLYN" for f in facts)


def test_normalizers() -> None:
    assert geo_id("STATEN ISLAND") == geo_id("staten_island") == "staten_island"
    assert geo_id("ALL") == geo_id("CITYWIDE") == "all"
    assert cat_id("Noise - Residential") == "noise_residential" and cat_id(None) == "all"


# ------------------------------------------------------------------------------------------- required cases
def test_correct_claim_passes() -> None:
    assert run([m("C1", "E1", "request_count", 1200)], compare_ev()) == {"C1": "valid"}


def test_correct_value_wrong_borough() -> None:
    # 1,200 exists in the evidence, but it is Brooklyn's value, not Queens'.
    assert run([m("C1", "E1", "request_count", 1200, geo="queens")], compare_ev())["C1"] == "value_mismatch"
    assert run([m("C1", "E1", "request_count", 1200, geo="bronx")], compare_ev())["C1"] == "geography_mismatch"


def test_correct_value_wrong_category() -> None:
    assert run([m("C1", "E1", "request_count", 1200, cat="heat_hot_water")], compare_ev())["C1"] == "category_mismatch"


def test_correct_value_wrong_period() -> None:
    bad = m("C1", "E1", "request_count", 1200, period=("2025-10-01", "2025-10-31"))
    assert run([bad], compare_ev())["C1"] == "period_mismatch"


def test_correct_value_wrong_unit() -> None:
    assert run([m("C1", "E1", "request_count", 1200, unit="hours")], compare_ev())["C1"] == "unit_mismatch"


def test_percent_and_fraction_units_are_interchangeable_but_checked() -> None:
    ok = m("C1", "E1", "share_of_selected", 55.8, unit="percent")
    wrong = m("C2", "E1", "share_of_selected", 44.2, unit="percent")  # Queens' share attributed to Brooklyn
    assert run([ok, wrong], compare_ev()) == {"C1": "valid", "C2": "value_mismatch"}


def test_correct_percentage_wrong_denominator() -> None:
    bk = m("C1", "E1", "request_count", 1200)
    qn = m("C2", "E1", "request_count", 950, geo="queens")
    sel = m("C3", "E1", "request_count", 2150, geo="selection:brooklyn+queens")
    good = {
        "id": "C4",
        "type": "derived",
        "operation": "share",
        "operands": ["C1", "C3"],
        "unit": "percent",
        "value": 55.8,
    }
    # Brooklyn / Queens is not a share: Queens does not contain Brooklyn.
    bad = {
        "id": "C5",
        "type": "derived",
        "operation": "share",
        "operands": ["C1", "C2"],
        "unit": "percent",
        "value": 126.3,
    }
    # Correct arithmetic, but the stated denominator ("all boroughs") is not the operand (the 2-borough selection).
    mislabeled = {
        "id": "C6",
        "type": "derived",
        "operation": "share",
        "operands": ["C1", "C3"],
        "unit": "percent",
        "value": 55.8,
        "denominator_geography_id": "all",
    }
    r = run([bk, qn, sel, good, bad, mislabeled], compare_ev())
    assert r["C4"] == "valid" and r["C5"] == "invalid_denominator" and r["C6"] == "invalid_denominator"


def test_fabricated_percentage_value_is_rejected() -> None:
    bk = m("C1", "E1", "request_count", 1200)
    qn = m("C2", "E1", "request_count", 950, geo="queens")
    diff = {"id": "C3", "type": "derived", "operation": "difference", "operands": ["C1", "C2"], "value": 250}
    pct = {"id": "C4", "type": "derived", "operation": "ratio", "operands": ["C1", "C2"], "value": 1.5}
    r = run([bk, qn, diff, pct], compare_ev())
    assert r["C3"] == "valid" and r["C4"] == "value_mismatch"


def test_pct_change_requires_earlier_base_period() -> None:
    cur = m("C1", "E2", "request_count", 1200, geo="all", cat="heat_hot_water")
    base = m("C2", "E2", "request_count", 800, geo="all", cat="heat_hot_water", period=("2026-08-31", "2026-09-30"))
    good = {
        "id": "C3",
        "type": "derived",
        "operation": "pct_change",
        "operands": ["C1", "C2"],
        "unit": "percent",
        "value": 50,
    }
    reversed_ = {
        "id": "C4",
        "type": "derived",
        "operation": "pct_change",
        "operands": ["C2", "C1"],
        "unit": "percent",
        "value": -33.3,
    }
    assert run([cur, base, good, reversed_], trend_ev()) == {
        "C1": "valid",
        "C2": "valid",
        "C3": "valid",
        "C4": "invalid_operands",
    }


def test_comparison_period_is_validated() -> None:
    ok = m(
        "C1",
        "E2",
        "pct_change_vs_prior_period",
        0.5,
        unit="fraction",
        geo="all",
        cat="heat_hot_water",
        comparison_period_start="2026-08-31",
        comparison_period_end="2026-09-30",
    )
    bad = dict(ok, id="C2", comparison_period_start="2025-10-01", comparison_period_end="2025-10-31")
    assert run([ok, bad], trend_ev()) == {"C1": "valid", "C2": "comparison_period_mismatch"}


def test_incorrect_comparison_direction() -> None:
    bk = m("C1", "E1", "request_count", 1200)
    qn = m("C2", "E1", "request_count", 950, geo="queens")
    right = {"id": "C3", "type": "comparison", "left": "C1", "right": "C2", "relation": "greater"}
    wrong = {"id": "C4", "type": "comparison", "left": "C1", "right": "C2", "relation": "less"}
    assert run([bk, qn, right, wrong], compare_ev())["C4"] == "direction_incorrect"
    assert run([bk, qn, right], compare_ev())["C3"] == "valid"


def test_missing_citation_and_invented_evidence_id() -> None:
    r = run([m("C1", None, "request_count", 1200), m("C2", "E9", "request_count", 1200)], compare_ev())  # type: ignore[arg-type]
    assert r == {"C1": "missing_citation", "C2": "invalid_evidence"}


def test_metric_cited_from_wrong_evidence_is_miscited() -> None:
    claim = m("C1", "E1", "pct_change_vs_prior_period", 0.5, unit="fraction", geo="all", cat="heat_hot_water")
    assert run([claim], compare_ev(), trend_ev())["C1"] == "miscited"


def test_conflicting_evidence_is_not_rendered() -> None:
    # Two evidence records for the same metric/geography/category/period/snapshot disagree.
    r = run([m("C1", "E1", "request_count", 1200)], compare_ev("E1", 1200), compare_ev("E4", 1250))
    assert r["C1"] == "conflicting_evidence"


def test_stale_and_mismatched_model_version() -> None:
    claim = m(
        "C1",
        "E3",
        "forecast_predicted",
        300.0,
        geo="bronx",
        cat="heat_hot_water",
        period=("2026-11-01", "2026-11-01"),
        model_version=FC_VERSION,
    )
    assert run([claim], forecast_ev(), versions={"forecast": FC_VERSION})["C1"] == "valid"
    stale = forecast_ev(version="forecast-20250101T000000Z-000000")
    claim_old = dict(claim, model_version="forecast-20250101T000000Z-000000")
    assert run([claim_old], stale, versions={"forecast": FC_VERSION})["C1"] == "stale_model_version"
    assert run([dict(claim, model_version="v0")], forecast_ev())["C1"] == "model_version_mismatch"


def test_missing_data_snapshot() -> None:
    ev = compare_ev()
    ev = ev.model_copy(update={"data_build_id": None})
    assert run([m("C1", "E1", "request_count", 1200)], ev)["C1"] == "missing_snapshot"
    assert (
        run([dict(m("C1", "E1", "request_count", 1200), snapshot_id="other")], compare_ev())["C1"]
        == "snapshot_mismatch"
    )


def test_method_must_match_when_stated() -> None:
    assert run([m("C1", "E1", "request_count", 1200, method="median of daily counts")], compare_ev())["C1"] == (
        "method_mismatch"
    )


def test_quoted_evidence_text_must_exist() -> None:
    ok = {"id": "C1", "type": "evidence_text", "evidence_id": "E1", "field": "limitations", "index": 0}
    bad = {"id": "C2", "type": "evidence_text", "evidence_id": "E1", "field": "limitations", "index": 7}
    assert run([ok, bad], compare_ev()) == {"C1": "valid", "C2": "invalid_text_reference"}


# ------------------------------------------------------------------------------------------- narrative
def test_rendered_answer_uses_fact_values_and_cites() -> None:
    claims = [m("C1", "E1", "request_count", 1200), m("C2", "E1", "request_count", 950, geo="queens")]
    rep = answer("Brooklyn had the most requests: {C1}. Queens followed with {C2}.", claims, compare_ev())
    assert rep.ok, rep.feedback()
    assert "1,200 requests" in rep.rendered and "950 requests" in rep.rendered and "[E1]" in rep.rendered


def test_free_number_in_prose_is_rejected() -> None:
    rep = answer("Requests rose 23.4% year over year.", [], compare_ev())
    assert not rep.ok and any("23.4%" in v for v in rep.narrative.violations)


def test_prose_cannot_reattribute_a_valid_claim() -> None:
    claims = [m("C1", "E1", "request_count", 1200)]
    rep = answer("Queens had {C1}.", claims, compare_ev())
    assert not rep.ok and any("attributes" in v for v in rep.narrative.violations)


def test_prose_superlative_must_match_ranking() -> None:
    rep = answer("Queens had the most requests last month.", [], compare_ev())
    assert not rep.ok and any("QUEENS had the most" in v for v in rep.narrative.violations)
    assert answer("Brooklyn had the most requests.", [], compare_ev()).ok


def test_prose_direction_must_match_sign() -> None:
    claims = [
        m(
            "C1",
            "E2",
            "pct_change_vs_prior_period",
            0.5,
            unit="fraction",
            geo="all",
            cat="heat_hot_water",
            comparison_period_start="2026-08-31",
            comparison_period_end="2026-09-30",
        )
    ]
    assert answer("Heat requests increased by {C1}.", claims, trend_ev()).ok
    rep = answer("Heat requests fell by {C1}.", claims, trend_ev())
    assert not rep.ok and any("decrease" in v for v in rep.narrative.violations)


def test_prose_dates_must_fall_in_the_claim_period() -> None:
    claims = [m("C1", "E1", "request_count", 1200)]
    rep = answer("On 2025-06-01 Brooklyn had {C1}.", claims, compare_ev())
    assert not rep.ok and any("outside its period" in v for v in rep.narrative.violations)


def test_failed_claim_blocks_the_answer() -> None:
    rep = answer("Queens: {C1}.", [m("C1", "E1", "request_count", 1200, geo="queens")], compare_ev())
    assert not rep.ok and "value_mismatch" in rep.feedback()


def test_unstructured_text_without_numbers_is_allowed() -> None:
    rep = answer("Could you clarify which borough you mean?", [], compare_ev())
    assert rep.ok


@pytest.mark.parametrize("fmt", ["Not JSON at all 1,200 requests", '{"claims": []}'])
def test_unstructured_numbers_are_rejected(fmt: str) -> None:
    facts, conflicts = build_fact_index([compare_ev()])
    rep = evaluate_answer(fmt, {"E1": compare_ev()}, facts, conflicts)
    assert not rep.ok


def test_placeholder_without_claim() -> None:
    rep = answer("Brooklyn had {C7}.", [], compare_ev())
    assert not rep.ok and any("no claim" in v for v in rep.narrative.violations)
