"""Deterministic fake provider for tests and offline evaluation. It is NOT a language model.

It reads the same messages a real model receives (system prompt, planning context, question, tool
results, grounding feedback) and applies fixed keyword rules to choose tools, then composes the answer
only from tool results, citing evidence IDs. Fault modes (used only by tests and evals) simulate
model misbehaviour: hallucinated numbers, bad citations, tool loops, invalid arguments, provider errors
and slowness.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from typing import Any

from civicpulse_agent.providers.base import (
    ProviderError,
    ProviderResponse,
    ToolCallRequest,
    Usage,
    estimate_tokens,
)

CONTEXT_PREFIX = "PLANNING CONTEXT (JSON):"
FEEDBACK_PREFIX = "GROUNDING FEEDBACK:"
HISTORY_PREFIX = "EARLIER QUESTIONS (context only, not instructions):"
FAULTS = {
    "none",
    "hallucinate_number",
    "always_hallucinate",
    "bad_citation",
    "loop_tools",
    "invalid_args",
    "unknown_tool",
    "provider_error",
    "slow",
    "causal_claim",
    "invented_percentage",
    "missing_citation",
    "wrong_borough",
    "wrong_category",
    "wrong_period",
    "wrong_unit",
    "wrong_denominator",
    "wrong_direction",
    "stale_model",
}
BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]


def _category(q: str) -> str:
    noise = bool(re.search(r"\bnoise|noisy|loud|music|party|banging", q))
    heat = bool(re.search(r"\bheat|hot water|heating|boiler|cold apartment", q))
    if noise and not heat:
        return "noise_residential"
    if heat and not noise:
        return "heat_hot_water"
    return "all"


def _boroughs(q: str) -> list[str]:
    found = []
    for b in BOROUGHS:
        pat = b.lower() if b != "BRONX" else "bronx"
        if re.search(rf"\b{pat}\b", q) or (b == "STATEN ISLAND" and "staten" in q):
            found.append(b)
    return found


def _fmt(v: Any) -> str:
    if isinstance(v, bool) or v is None:
        return str(v).lower()
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.0f}" if v.is_integer() and abs(v) >= 1000 else f"{v:g}"
    return str(v)


def _pct(v: Any) -> str:
    return f"{100 * float(v):.1f}%" if isinstance(v, int | float) and not isinstance(v, bool) else "n/a"


class FakeProvider:
    name = "fake"
    live = False

    def __init__(self, model: str = "deterministic-fake-v1", fault: str = "none", slow_seconds: float = 5.0) -> None:
        if fault not in FAULTS:
            raise ValueError(f"unknown fault mode {fault}")
        self.model = model
        self.fault = fault
        self.slow_seconds = slow_seconds
        self._call_seq = 0

    # ------------------------------------------------------------------ entry point
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_output_tokens: int
    ) -> ProviderResponse:
        if self.fault == "provider_error":
            raise ProviderError("simulated provider outage")
        if self.fault == "slow":
            await asyncio.sleep(self.slow_seconds)
        ctx = self._context(messages)
        users = [str(m["content"]) for m in messages if m["role"] == "user"]
        question = next((u for u in reversed(users) if not u.startswith((FEEDBACK_PREFIX, HISTORY_PREFIX))), "")
        history = next((u for u in users if u.startswith(HISTORY_PREFIX)), "")
        tool_msgs = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        feedback = [m for m in messages if m["role"] == "user" and str(m["content"]).startswith(FEEDBACK_PREFIX)]
        tool_names = {t["function"]["name"] for t in tools}

        if tools and self.fault == "loop_tools":
            calls = [self._req("get_dataset_quality", {})]
        elif tools and not tool_msgs:
            calls = self._plan(question.lower(), ctx, tool_names, history=history.lower())
        elif (
            tools
            and self.fault in ("invalid_args", "unknown_tool")
            and len(tool_msgs) <= 1
            and not any("evidence_id" in t for t in tool_msgs)
        ):
            calls = self._plan(question.lower(), ctx, tool_names, repair=True, history=history.lower())
        else:
            calls = []
        if calls:
            return self._response("", calls, messages)
        text = self._compose(question, ctx, tool_msgs, retry=len(feedback))
        return self._response(text, [], messages)

    # ------------------------------------------------------------------ helpers
    def _req(self, name: str, args: dict[str, Any]) -> ToolCallRequest:
        self._call_seq += 1
        return ToolCallRequest(id=f"call_{self._call_seq}", name=name, arguments=args)

    def _response(self, text: str, calls: list[ToolCallRequest], messages: list[dict[str, Any]]) -> ProviderResponse:
        out = text + json.dumps([c.__dict__ for c in calls])
        return ProviderResponse(text, calls, Usage(estimate_tokens(messages), estimate_tokens(out)), self.model)

    @staticmethod
    def _context(messages: list[dict[str, Any]]) -> dict[str, Any]:
        for m in messages:
            if m["role"] == "system" and str(m["content"]).startswith(CONTEXT_PREFIX):
                return dict(json.loads(m["content"][len(CONTEXT_PREFIX) :]))
        return {}

    @staticmethod
    def _period(ctx: dict[str, Any], default_days: int = 28) -> tuple[str, str]:
        periods = ctx.get("periods") or []
        usable = [p for p in periods if not p["phrase"].startswith("next week")]
        if usable:
            return usable[0]["start"], usable[0]["end"]
        last = dt.date.fromisoformat(ctx.get("last_complete_date", "2026-10-31"))
        return (last - dt.timedelta(days=default_days - 1)).isoformat(), last.isoformat()

    # ------------------------------------------------------------------ planning
    def _plan(
        self, q: str, ctx: dict[str, Any], available: set[str], repair: bool = False, history: str = ""
    ) -> list[ToolCallRequest]:
        if self.fault == "invalid_args" and not repair:
            return [self._req("get_service_request_trends", {"start_date": "last week", "borough": "Narnia"})]
        if self.fault == "unknown_tool" and not repair:
            return [self._req("run_sql", {"query": "SELECT * FROM requests"})]
        cat = _category(q)
        if cat == "all" and history:
            cat = _category(history)  # follow-up questions inherit the earlier subject
        boroughs = _boroughs(q)
        start, end = self._period(ctx)
        calls: list[ToolCallRequest] = []

        def add(name: str, args: dict[str, Any]) -> None:
            if name in available and len(calls) < 4:
                calls.append(self._req(name, args))

        delay = bool(re.search(r"delay|closure[- ]risk|closure model|closure-delay|backlog|close late|late clos", q))
        model_eval = bool(re.search(r"seasonal na|naive|baseline|accura|reliab|how good|evaluat|wape|calibrat", q))
        explicit_window = bool(
            re.search(
                r"next week|coming week|next 7 days|(what|how much) does the (model|forecast) "
                r"(forecast|predict|expect)|"
                r"predict(ed)? (volume|demand|requests)|expect(ed)? .{0,30}(next|coming)|upcoming",
                q,
            )
        )
        # "How accurate is the forecast for heat?" asks about evaluation, not for the forecast values.
        forecast = explicit_window or (bool(re.search(r"forecast", q)) and not model_eval)
        anomaly = bool(re.search(r"anomal|spike|unusual|outlier|surge|abnormal", q))
        quality = bool(
            re.search(
                r"fresh|up to date|up-to-date|cutoff|cut-off|stale|latest data|quality|limitation|"
                r"missing data|how current|live",
                q,
            )
        )
        # Explanations are about model outputs; "why did complaints rise?" is a data question with no causal tool.
        explain = bool(re.search(r"\bexplain|why (was|did|is)|feature importance|what drives|drivers", q)) and bool(
            re.search(r"model|forecast|predict|anomal|flag|feature|score", q)
        )
        breakdown = bool(re.search(r"breakdown|descriptor|types? of|kinds? of|share of each|by category|mix", q))
        compare = len(boroughs) >= 2 or bool(
            re.search(
                r"\bcompar|\bvs\.?\b|versus|which borough|highest|most|"
                r"rank boroughs|lowest",
                q,
            )
        )

        if delay:
            add("predict_closure_delay", {"request": "research_summary"})
            add("get_model_evaluation", {"model": "delay_risk"})
            return calls
        if forecast or (model_eval and re.search(r"forecast|model", q) and not anomaly):
            if forecast:
                fc = cat if cat != "all" else ("heat_hot_water" if "heat" in q else "noise_residential")
                add(
                    "forecast_request_volume", {"category": fc, "borough": boroughs[0] if len(boroughs) == 1 else "ALL"}
                )
            if model_eval or not forecast:
                add("get_model_evaluation", {"model": "forecast"})
            return calls
        if anomaly:
            geography = boroughs[0] if len(boroughs) == 1 else "ALL_SERIES"
            direction = "spike" if re.search(r"spike|surge", q) else "any"
            if explain:
                add("get_model_evaluation", {"model": "anomaly"})
            add(
                "detect_unusual_spikes",
                {
                    "start_date": start,
                    "end_date": end,
                    "category": cat,
                    "geography": geography,
                    "direction": direction,
                    "limit": 10,
                },
            )
            return calls
        if explain:
            fam = "delay_risk" if "delay" in q else ("anomaly" if "anomal" in q else "forecast")
            add("explain_model_prediction", {"model": fam, "top_k": 8})
            return calls
        if quality:
            add("get_dataset_quality", {})
            return calls
        if model_eval:
            add("get_model_evaluation", {"model": "anomaly" if "anomal" in q else "forecast"})
            return calls
        if breakdown:
            args: dict[str, Any] = {
                "start_date": start,
                "end_date": end,
                "borough": boroughs[0] if len(boroughs) == 1 else "ALL",
            }
            if cat != "all":
                args.update(level="descriptor", category=cat)
            add("get_category_breakdown", args)
            return calls
        if compare:
            sel = boroughs if len(boroughs) >= 2 else BOROUGHS
            add("compare_boroughs", {"start_date": start, "end_date": end, "boroughs": sel, "category": cat})
            return calls
        has_subject = (
            cat != "all"
            or boroughs
            or ctx.get("periods")
            or re.search(r"request|complaint|311|volume|trend|how many|count", q)
        )
        if not has_subject:
            return []  # ambiguous: ask for clarification instead of guessing
        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
        gran = "day" if days <= 120 else ("week" if days <= 730 else "month")
        add(
            "get_service_request_trends",
            {
                "start_date": start,
                "end_date": end,
                "category": cat,
                "borough": boroughs[0] if len(boroughs) == 1 else "ALL",
                "granularity": gran,
            },
        )
        return calls

    # ------------------------------------------------------------------ composition
    def _compose(self, question: str, ctx: dict[str, Any], tool_msgs: list[dict[str, Any]], retry: int) -> str:
        """Structured answer: prose with {C#} placeholders plus claims copied from the tool's fact records."""
        evidence = [t for t in tool_msgs if "evidence_id" in t]
        failures = [t for t in tool_msgs if "error" in t]
        if not evidence and not failures:
            return (
                "Could you clarify what you would like to know? I can analyse NYC 311 residential noise and heat/hot-"
                "water requests: trends over a period, borough comparisons, category breakdowns, the precomputed "
                "seven-day forecast and its evaluation, statistical anomaly flags, and data freshness and quality. "
                "Please name a period, borough or complaint category."
            )
        book = _ClaimBook()
        lines: list[str] = []
        for ev in evidence:
            lines += self._describe(ev, book)
        for f in failures:
            lines.append(
                f"- The `{f.get('tool', 'unknown')}` call did not succeed ({f.get('error_kind', 'error')}), so its "
                "result is not used in this answer."
            )
        notes = [p["note"] for p in ctx.get("periods", []) if p.get("note")]
        for p in ctx.get("periods", []):
            lines.append(
                f'- Date interpretation: "{p["phrase"]}" = {p["start"]} to {p["end"]} ({p["basis"]}). '
                f"{p.get('note', '')}".rstrip()
            )
        windowed = [
            t
            for t in evidence
            if "start_date" in (t["result"].get("query") or {}).get("arguments", {})
            or "start_date" in str(t["result"].get("data", {}).get("filters", ""))
        ]
        if not ctx.get("periods") and windowed:
            f = windowed[0]["result"].get("data", {}).get("filters", {})
            lines.append(
                f"- No period was specified, so I used the most recent 28 complete days in the snapshot "
                f"({f.get('start_date')} to {f.get('end_date')}), not the calendar period before today."
            )
        if ctx.get("delay_action_requested"):
            lines.insert(
                0,
                "- **I can't use the closure-delay model to prioritize requests, allocate services, rank "
                "communities or recommend interventions.** It is an experimental research model that is "
                "not validated for those uses, and its numerical predictions are disabled.",
            )
        cut = ctx.get("data_cutoff")
        if cut and not notes:
            lines.append(f"- Data is a historical snapshot (cutoff {cut}), not live monitoring.")
        e1 = evidence[0]["evidence_id"] if evidence else "E1"
        if retry == 0 or self.fault == "always_hallucinate":
            self._inject_fault(lines, book, e1)
        return json.dumps({"answer": "\n".join(lines), "claims": book.claims})

    def _inject_fault(self, lines: list[str], book: _ClaimBook, e1: str) -> None:
        """Simulated model misbehaviour (tests/evals only). Applied to the first attempt, or every attempt."""
        f = self.fault
        meas = [c for c in book.claims if c["type"] == "measurement"]
        if f in ("always_hallucinate", "hallucinate_number"):
            lines.insert(0, f"- Requests rose by 987,654 compared with the prior period [{e1}].")
        elif f == "invented_percentage":
            lines.insert(0, f"- Requests rose 23.4% year over year [{e1}].")
        elif f == "bad_citation":
            lines.insert(0, "- Summary of findings [E9].")
        elif f == "causal_claim":
            lines.insert(0, f"- The October increase was caused by the cold weather [{e1}].")
        elif f == "missing_citation" and meas:
            meas[0]["evidence_id"] = None
        elif f == "wrong_borough" and meas:
            c = meas[0]
            c["geography_id"] = "queens" if c.get("geography_id") != "queens" else "bronx"
        elif f == "wrong_category" and meas:
            c = meas[0]
            c["category_id"] = "heat_hot_water" if c.get("category_id") != "heat_hot_water" else "noise_residential"
        elif f == "wrong_period" and meas:
            c = meas[0]
            if c.get("period_start"):
                c["period_start"] = _shift_year(c["period_start"])
                c["period_end"] = _shift_year(c["period_end"])
        elif f == "wrong_unit" and meas:
            meas[0]["unit"] = "hours"
        elif f == "wrong_denominator":
            counts = [c for c in meas if c.get("metric_id") == "request_count" and c.get("geography_id") in _BORO_IDS]
            if len(counts) >= 2:
                a, b = counts[0], counts[1]
                cid = book.add(
                    {
                        "type": "derived",
                        "operation": "share",
                        "operands": [a["id"], b["id"]],
                        "unit": "percent",
                        "value": round(100 * float(a["value"]) / float(b["value"]), 1),
                    }
                )
                lines.insert(0, f"- {_BORO_LABEL[a['geography_id']]} accounted for {cid} of the selected boroughs.")
        elif f == "wrong_direction":
            counts = [c for c in meas if c.get("metric_id") == "request_count" and c.get("geography_id") in _BORO_IDS]
            if len(counts) >= 2:
                a, b = counts[0], counts[1]
                rel = "less" if float(a["value"]) > float(b["value"]) else "greater"
                cid = book.add({"type": "comparison", "left": a["id"], "right": b["id"], "relation": rel})
                lines.insert(0, f"- Comparison: {cid}.")
        elif f == "stale_model" and meas:
            for c in meas:
                if c.get("model_version"):
                    c["model_version"] = "forecast-20250101T000000Z-000000"
                    break

    def _describe(self, ev: dict[str, Any], book: _ClaimBook) -> list[str]:
        e = ev["evidence_id"]
        r = ev["result"]
        facts: list[dict[str, Any]] = ev.get("facts") or []
        tool, data, status = r["tool"], r.get("data") or {}, r.get("status")
        filters = data.get("filters") or {}
        out = [f"- {book.quote(e, 'summary')}"]
        if status == "no_data":
            return out

        def fact(metric: str, **dims: Any) -> dict[str, Any] | None:
            for f in facts:
                if f["metric_id"] == metric and all(f.get(k) == v for k, v in dims.items()):
                    return f
            return None

        def claim(f: dict[str, Any] | None) -> str:
            return book.measure(f) if f else ""

        cat_lbl = _CAT_LABEL.get(str(filters.get("category") or "all"), "all loaded categories")
        span = f"{filters.get('start_date')} to {filters.get('end_date')}"
        if tool == "get_service_request_trends":
            geo_lbl = _BORO_LABEL.get(_geo(filters.get("borough")), "all boroughs")
            top = data.get("top_periods") or []
            refs = [claim(fact("request_count", period_start=p["period"])) for p in top[:3]]
            refs = [x for x in refs if x]
            if refs:
                out.append(f"- Largest periods for {geo_lbl}, {cat_lbl}: " + "; ".join(refs) + ".")
            pct = fact("pct_change_vs_prior_period")
            if pct:
                out.append(
                    f"- Change for {geo_lbl}, {cat_lbl} versus the prior period of equal length "
                    f"({pct.get('comparison_period_start')} to {pct.get('comparison_period_end')}): {claim(pct)}."
                )
        elif tool == "compare_boroughs":
            for row in data.get("rows", []):
                g = _geo(row["borough"])
                n, sh = claim(fact("request_count", geography_id=g)), claim(fact("share_of_selected", geography_id=g))
                if n:
                    out.append(
                        f"- {row['borough']}, {cat_lbl}, {span}: {n}"
                        + (f"; {sh} of the selected boroughs." if sh else ".")
                    )
            pairs = data.get("pairwise") or []
            if len(pairs) == 1 and pairs[0].get("pct_more") is not None:
                p = pairs[0]
                pid = f"{_geo(p['higher'])}-vs-{_geo(p['lower'])}"
                d, pm = claim(fact("difference_requests", geography_id=pid)), claim(fact("pct_more", geography_id=pid))
                out.append(
                    f"- {p['higher']} had more requests than {p['lower']}, a difference of {d} ({pm} more). "
                    "Counts are raw, not per capita."
                )
        elif tool == "get_category_breakdown":
            b_lbl = _BORO_LABEL.get(_geo(filters.get("borough")), "all boroughs")
            for row in (data.get("rows") or [])[:5]:
                seg = row["key"] if filters.get("level") == "category" else None
                cands = [
                    f
                    for f in facts
                    if f["metric_id"] == "request_count" and f.get("category_id") != filters.get("category")
                ]
                f_n = next((f for f in cands if f.get("category_id") == seg), None) if seg else None
                if f_n is None:
                    f_n = next(
                        (
                            f
                            for f in cands
                            if f["value"] == row["requests"] and f.get("category_id", "").endswith(_slug(row["key"]))
                        ),
                        None,
                    )
                if f_n is None:
                    continue
                f_s = fact("share_of_total", category_id=f_n["category_id"])
                out.append(
                    f"- {row['label']} ({b_lbl}, {span}): {claim(f_n)}"
                    + (f", {claim(f_s)} of the total." if f_s else ".")
                )
        elif tool == "forecast_request_volume":
            series = data.get("series") or []
            for p in (data.get("all_boroughs_point_total") or [])[:7]:
                f = fact("forecast_predicted", geography_id="all", period_start=p["target_date"])
                if f:
                    out.append(
                        f"- all boroughs, {p['target_date']}: {claim(f)} predicted (sum of borough point forecasts)."
                    )
            level = fact("interval_nominal_level")
            if len(series) == 1:
                g = _geo(series[0].get("borough"))
                for fc in series[0].get("forecast", [])[:7]:
                    day = fc["target_date"]
                    pr = fact("forecast_predicted", geography_id=g, period_start=day)
                    lo = fact("forecast_lower", geography_id=g, period_start=day)
                    hi = fact("forecast_upper", geography_id=g, period_start=day)
                    if pr and lo and hi:
                        lvl = claim(level) if level else "nominal"
                        out.append(
                            f"- {series[0]['borough']}, {day}: {claim(pr)} predicted "
                            f"({lvl} interval {claim(lo)} to {claim(hi)})."
                        )
            unc = " ".join(book.quote(e, "uncertainty", i) for i in range(min(2, len(r.get("uncertainty", [])))))
            out.append(f"- Uncertainty: {unc}")
            for i in range(min(2, len(r.get("limitations", [])))):
                out.append(f"- Evaluation: {book.quote(e, 'limitations', i)}")
        elif tool == "detect_unusual_spikes":
            for fl in (data.get("flags") or [])[:5]:
                g, c, day = _geo(fl["geography"]), fl["category"], fl["date"]
                a = fact("anomaly_actual", geography_id=g, category_id=c, period_start=day)
                x = fact("anomaly_expected", geography_id=g, category_id=c, period_start=day)
                sc = fact("anomaly_score", geography_id=g, category_id=c, period_start=day)
                if a and x and sc:
                    out.append(
                        f"- {day}, {_BORO_LABEL.get(g, 'all boroughs')}, {_CAT_LABEL.get(c, c)}: {claim(a)} actual vs "
                        f"{claim(x)} expected (robust score {claim(sc)})."
                    )
            extra = book.quote(e, "uncertainty", 1) if len(r.get("uncertainty", [])) > 1 else ""
            out.append(
                f"- These are statistical flags, not confirmed incidents, and they carry no cause. {extra}".rstrip()
            )
        elif tool == "predict_closure_delay":
            out.append(f"- Decision: {book.quote(e, 'decision')}")
            for i in range(min(3, len(r.get("limitations", [])))):
                out.append(f"- {book.quote(e, 'limitations', i)}")
            out.append(f"- Prohibited uses: {book.quote(e, 'prohibited_uses')}")
        elif tool in ("get_model_evaluation", "get_dataset_quality"):
            for i in range(min(2 if tool == "get_model_evaluation" else 3, len(r.get("limitations", [])))):
                out.append(f"- {book.quote(e, 'limitations', i)}")
            if tool == "get_model_evaluation" and r.get("uncertainty"):
                out.append(f"- {book.quote(e, 'uncertainty', 0)}")
            for i in range(min(3, len(data.get("warnings") or []))):
                out.append(f"- {book.quote(e, 'warnings', i)}")
        else:
            generic = [f for f in facts if f["fact_id"].startswith(e + ".")][:6]
            for f in generic:
                out.append(f"- {f['metric_id'].replace('_', ' ')}: {claim(f)}.")
            for i in range(min(2, len(r.get("limitations", [])))):
                out.append(f"- {book.quote(e, 'limitations', i)}")
        return out


_BORO_IDS = {"bronx", "brooklyn", "manhattan", "queens", "staten_island"}
_BORO_LABEL = {
    "bronx": "BRONX",
    "brooklyn": "BROOKLYN",
    "manhattan": "MANHATTAN",
    "queens": "QUEENS",
    "staten_island": "STATEN ISLAND",
    "all": "all boroughs",
}
_CAT_LABEL = {
    "noise_residential": "residential noise",
    "heat_hot_water": "heat/hot water",
    "all": "all loaded categories",
}


def _geo(v: Any) -> str:
    s = str(v or "ALL").upper().replace("_", " ")
    return {"ALL": "all", "CITYWIDE": "all", "ALL SERIES": "all"}.get(s, s.lower().replace(" ", "_"))


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:60]


def _shift_year(day: str) -> str:
    d = dt.date.fromisoformat(day)
    try:
        return d.replace(year=d.year - 1).isoformat()
    except ValueError:
        return (d - dt.timedelta(days=365)).isoformat()


class _ClaimBook:
    """Collects claims; measurement claims restate every dimension of the fact they come from."""

    def __init__(self) -> None:
        self.claims: list[dict[str, Any]] = []

    def add(self, claim: dict[str, Any]) -> str:
        cid = f"C{len(self.claims) + 1}"
        self.claims.append({"id": cid, **claim})
        return "{" + cid + "}"

    def measure(self, f: dict[str, Any]) -> str:
        keys = (
            "evidence_id",
            "metric_id",
            "geography_id",
            "category_id",
            "period_start",
            "period_end",
            "comparison_period_start",
            "comparison_period_end",
            "value",
            "unit",
            "snapshot_id",
            "model_version",
        )
        return self.add({"type": "measurement", **{k: f.get(k) for k in keys}})

    def quote(self, evidence_id: str, fld: str, index: int | None = None) -> str:
        return self.add({"type": "evidence_text", "evidence_id": evidence_id, "field": fld, "index": index})
