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
        evidence = [t for t in tool_msgs if "evidence_id" in t]
        failures = [t for t in tool_msgs if "error" in t]
        if not evidence and not failures:
            return (
                "Could you clarify what you would like to know? I can analyse NYC 311 residential noise and heat/hot-"
                "water requests: trends over a period, borough comparisons, category breakdowns, the precomputed "
                "7-day forecast and its evaluation, statistical anomaly flags, and data freshness and quality. "
                "Please name a period, borough or complaint category."
            )
        lines: list[str] = []
        for ev in evidence:
            lines += self._describe(ev)
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
        if self.fault == "always_hallucinate" or (self.fault == "hallucinate_number" and retry == 0):
            lines.insert(0, f"- Requests rose by 987,654 compared with the prior period [{e1}].")
        if self.fault == "bad_citation" and retry == 0:
            lines.insert(0, "- Summary of findings [E9].")
        if self.fault == "causal_claim" and retry == 0:
            lines.insert(0, f"- The October increase was caused by the cold weather [{e1}].")
        return "\n".join(lines)

    def _describe(self, ev: dict[str, Any]) -> list[str]:
        e = ev["evidence_id"]
        r = ev["result"]
        tool, data, status = r["tool"], r.get("data") or {}, r.get("status")
        out = [f"- {r['summary']} [{e}]"]
        if status == "no_data":
            return out
        if tool == "get_service_request_trends":
            top = data.get("top_periods") or []
            if top:
                out.append(
                    "- Largest periods: "
                    + "; ".join(f"{p['period']}: {_fmt(p['value'])}" for p in top[:3])
                    + f" [{e}]."
                )
            prior = data.get("prior_period") or {}
            if prior.get("pct_change") is not None:
                out.append(f"- Change vs the prior period of equal length: {_pct(prior['pct_change'])} [{e}].")
        elif tool == "compare_boroughs":
            for row in data.get("rows", []):
                out.append(
                    f"- {row['borough']}: {_fmt(row['requests'])} requests "
                    f"({_pct(row['share_of_selected'])} of the selected boroughs) [{e}]."
                )
            pairs = data.get("pairwise") or []
            if len(pairs) == 1 and pairs[0].get("pct_more") is not None:
                p = pairs[0]
                out.append(
                    f"- {p['higher']} had {_fmt(p['difference_requests'])} more requests than {p['lower']} "
                    f"({_pct(p['pct_more'])} more) [{e}]. Counts are raw, not per capita."
                )
        elif tool == "get_category_breakdown":
            for row in (data.get("rows") or [])[:5]:
                out.append(f"- {row['label']}: {_fmt(row['requests'])} ({_pct(row['share'])}) [{e}].")
        elif tool == "forecast_request_volume":
            for p in (data.get("all_boroughs_point_total") or [])[:7]:
                out.append(
                    f"- {p['target_date']}: {_fmt(p['predicted_all_boroughs'])} predicted (sum of borough "
                    f"point forecasts) [{e}]."
                )
            for s in (data.get("series") or [])[:1]:
                for f in s.get("forecast", [])[:7]:
                    if f.get("lower") is not None and len(data.get("series", [])) == 1:
                        out.append(
                            f"- {f['target_date']}: {_fmt(f['predicted'])} (90% interval {_fmt(f['lower'])}"
                            f" to {_fmt(f['upper'])}) [{e}]."
                        )
            out.append(f"- Uncertainty: {' '.join(r.get('uncertainty', [])[:2])} [{e}]")
            for lim in r.get("limitations", [])[:2]:
                out.append(f"- Evaluation: {lim} [{e}]")
        elif tool == "detect_unusual_spikes":
            for f in (data.get("flags") or [])[:5]:
                out.append(
                    f"- {f['date']} {f['geography']} {f['category']}: {_fmt(f['actual'])} actual vs "
                    f"{_fmt(f['expected'])} expected (score {_fmt(f['score'])}) [{e}]."
                )
            out.append(
                f"- These are statistical flags, not confirmed incidents, and they carry no cause. "
                f"{' '.join(r.get('uncertainty', [])[1:2])} [{e}]"
            )
        elif tool == "predict_closure_delay":
            out.append(f"- Decision: {data.get('decision', '')} [{e}]")
            for lim in r.get("limitations", [])[:3]:
                out.append(f"- {lim} [{e}]")
            out.append(f"- Prohibited uses: {'; '.join(data.get('prohibited_uses', [])[:5])}.")
        elif tool == "get_model_evaluation":
            for lim in r.get("limitations", [])[:2]:
                out.append(f"- {lim} [{e}]")
            for u in r.get("uncertainty", [])[:1]:
                out.append(f"- {u} [{e}]")
        elif tool == "get_dataset_quality":
            for lim in r.get("limitations", [])[:3]:
                out.append(f"- {lim} [{e}]")
            for w in (data.get("warnings") or [])[:3]:
                out.append(f"- {w} [{e}]")
        else:
            for m in r.get("key_measurements", [])[:6]:
                out.append(f"- {m['name'].replace('_', ' ')}: {_fmt(m['value'])} [{e}].")
            for lim in r.get("limitations", [])[:2]:
                out.append(f"- {lim} [{e}]")
        return out
