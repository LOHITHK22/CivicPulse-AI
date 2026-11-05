"""Bounded, evidence-grounded investigation loop.

question -> screening -> MCP session (discover tools, read dataset coverage) -> resolve relative dates ->
provider/tool loop (allow-list + JSON-schema validation + call budget + token budget + timeout) ->
evidence objects -> deterministic grounding validation -> bounded regeneration -> deterministic fallback.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio
from pydantic import BaseModel, Field

from civicpulse_agent.budget import BudgetExceeded, UsageGovernor
from civicpulse_agent.claims import (
    FACTS_SHOWN_TO_MODEL,
    Fact,
    SemanticReport,
    build_fact_index,
    derived_numbers,
    evaluate_answer,
    extract_facts,
)
from civicpulse_agent.config import AgentSettings
from civicpulse_agent.dates import resolve_periods
from civicpulse_agent.evidence import Evidence
from civicpulse_agent.grounding import GroundingReport, validate_answer
from civicpulse_agent.policy import (
    asks_for_delay_action,
    normalize_question,
    redact_output,
    screen_question,
    validate_tool_call,
)
from civicpulse_agent.prompts import SYSTEM_PROMPT
from civicpulse_agent.providers.base import LLMProvider, ProviderError, estimate_tokens
from civicpulse_agent.providers.fake import CONTEXT_PREFIX, FEEDBACK_PREFIX, HISTORY_PREFIX
from civicpulse_mcp.audit import sanitize
from civicpulse_mcp.client import McpGateway, McpSession, McpSettings

try:
    from zoneinfo import ZoneInfo

    NYC: dt.tzinfo = ZoneInfo("America/New_York")
except Exception:  # tzdata missing: fixed EST offset is close enough for calendar-date resolution
    NYC = dt.timezone(dt.timedelta(hours=-5))
Status = Literal["answered", "clarification", "refused", "fallback", "error"]


class TraceStep(BaseModel):
    step: int
    kind: Literal["llm", "tool", "validation", "policy"]
    name: str
    status: Literal["ok", "error", "rejected", "skipped", "restricted", "no_data", "passed", "failed"]
    arguments: dict[str, Any] | None = None
    duration_ms: float = 0.0
    error_kind: str | None = None
    message: str | None = None
    evidence_id: str | None = None
    data_source: str | None = None
    model_version: str | None = None
    capability: str | None = None


class Warning(BaseModel):
    level: Literal["info", "caution", "restricted"]
    capability: str | None = None
    tool: str | None = None
    message: str


class InvestigationResult(BaseModel):
    investigation_id: str
    question: str
    status: Status
    answer: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Warning] = Field(default_factory=list)
    periods: list[dict[str, str]] = Field(default_factory=list)
    freshness: dict[str, Any] = Field(default_factory=dict)
    charts: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list, description="Validated claims rendered in the answer")
    provider: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


def _llm_view(value: Any, depth: int = 0) -> Any:
    """Bounded copy of a tool result for the model's context (full result is kept for grounding)."""
    if depth > 6:
        return "<nested>"
    if isinstance(value, dict):
        return {k: _llm_view(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_llm_view(v, depth + 1) for v in value[:40]] + (["<truncated>"] if len(value) > 40 else [])
    if isinstance(value, str):
        return value[:600]
    return value


def tool_specs(session: McpSession) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    specs, schemas = [], {}
    for t in session.tools:
        schemas[t.name] = t.input_schema
        specs.append(
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description[:900], "parameters": t.input_schema},
            }
        )
    return specs, schemas


def build_charts(evidence: list[Evidence]) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    for ev in evidence:
        d = (ev.result.get("data") or {}) if ev.status in ("ok", "restricted") else {}
        base = {"evidence_id": ev.evidence_id}
        if ev.tool == "get_service_request_trends" and d.get("points"):
            charts.append(
                {
                    **base,
                    "kind": "line",
                    "title": ev.summary[:120],
                    "y_label": "requests",
                    "series": [
                        {"name": "requests", "points": [{"x": p["period"], "y": p["value"]} for p in d["points"][:400]]}
                    ],
                }
            )
        elif ev.tool == "compare_boroughs" and d.get("rows"):
            charts.append(
                {
                    **base,
                    "kind": "bar",
                    "title": "Requests by borough",
                    "y_label": "requests",
                    "series": [
                        {"name": "requests", "points": [{"x": r["borough"], "y": r["requests"]} for r in d["rows"]]}
                    ],
                }
            )
        elif ev.tool == "get_category_breakdown" and d.get("rows"):
            charts.append(
                {
                    **base,
                    "kind": "bar",
                    "title": "Breakdown",
                    "y_label": "requests",
                    "series": [
                        {"name": "requests", "points": [{"x": r["label"], "y": r["requests"]} for r in d["rows"][:12]]}
                    ],
                }
            )
        elif ev.tool == "forecast_request_volume":
            series = d.get("series") or []
            if len(series) == 1:
                s = series[0]
                hist = [
                    {"x": h.get("date") or h.get("target_date"), "y": h.get("actual") or h.get("value")}
                    for h in (s.get("recent_history") or [])[-28:]
                ]
                fc = s.get("forecast") or []
                charts.append(
                    {
                        **base,
                        "kind": "line",
                        "title": f"7-day forecast, {s.get('borough')}",
                        "y_label": "requests",
                        "series": [
                            {"name": "recent actual", "points": hist},
                            {"name": "forecast", "points": [{"x": f["target_date"], "y": f["predicted"]} for f in fc]},
                            {"name": "90% lower", "points": [{"x": f["target_date"], "y": f.get("lower")} for f in fc]},
                            {"name": "90% upper", "points": [{"x": f["target_date"], "y": f.get("upper")} for f in fc]},
                        ],
                    }
                )
            elif d.get("all_boroughs_point_total"):
                charts.append(
                    {
                        **base,
                        "kind": "bar",
                        "title": "Forecast, sum of borough point forecasts (no interval)",
                        "y_label": "requests",
                        "series": [
                            {
                                "name": "predicted",
                                "points": [
                                    {"x": p["target_date"], "y": p["predicted_all_boroughs"]}
                                    for p in d["all_boroughs_point_total"]
                                ],
                            }
                        ],
                    }
                )
        elif ev.tool == "detect_unusual_spikes" and d.get("flags"):
            flags = d["flags"][:15]
            charts.append(
                {
                    **base,
                    "kind": "bar",
                    "title": "Flagged days: actual vs expected",
                    "y_label": "requests",
                    "series": [
                        {
                            "name": "actual",
                            "points": [{"x": f"{f['date']} {f['geography']}", "y": f["actual"]} for f in flags],
                        },
                        {
                            "name": "expected",
                            "points": [{"x": f"{f['date']} {f['geography']}", "y": f["expected"]} for f in flags],
                        },
                    ],
                }
            )
    return charts[:4]


@dataclass
class Verdict:
    """Semantic claim validation plus the legacy number/policy checks on the rendered text."""

    semantic: SemanticReport
    legacy: GroundingReport

    @property
    def ok(self) -> bool:
        return self.semantic.ok and self.legacy.ok

    @property
    def rendered(self) -> str:
        return self.semantic.rendered

    def feedback(self) -> str:
        parts = [self.semantic.feedback()]
        if self.semantic.ok:
            parts.append(self.legacy.feedback())
        return " ".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        d = self.legacy.to_dict()
        d["ok"] = self.ok
        d["semantic"] = self.semantic.to_dict()
        return d


@dataclass
class _Run:
    trace: list[TraceStep]
    evidence: list[Evidence]
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    tool_calls: int = 0
    provider_calls: int = 0
    claims: list[dict[str, Any]] = field(default_factory=list)

    def add(self, **kw: Any) -> None:
        self.trace.append(TraceStep(step=len(self.trace) + 1, **kw))


class Orchestrator:
    _model_versions: dict[str, str] = {}  # noqa: RUF012 - replaced per investigation

    def __init__(
        self,
        settings: AgentSettings,
        provider: LLMProvider | None,
        governor: UsageGovernor,
        gateway: McpGateway | None = None,
        today: dt.date | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.governor = governor
        self.gateway = gateway or McpGateway(
            McpSettings(
                transport=settings.mcp_transport,
                data_dir=settings.data_dir,
                url=settings.mcp_url,
                http_token=settings.mcp_http_token.get_secret_value() if settings.mcp_http_token else None,
                protocol_mode=settings.mcp_protocol_mode,
                call_timeout=settings.mcp_call_timeout,
            )
        )
        self._today = today

    def today(self) -> dt.date:
        return self._today or dt.datetime.now(NYC).date()

    def _provider_info(self) -> dict[str, Any]:
        p = self.provider
        return {"name": p.name if p else "disabled", "model": p.model if p else None, "live_llm": bool(p and p.live)}

    # ------------------------------------------------------------------ public
    async def investigate(self, question: str, client_id: str, history: list[str] | None = None) -> InvestigationResult:
        t0 = time.perf_counter()
        inv_id = uuid.uuid4().hex[:16]
        q = normalize_question(question)
        run = _Run(trace=[], evidence=[])

        def result(status: Status, answer: str, **kw: Any) -> InvestigationResult:
            return InvestigationResult(
                investigation_id=inv_id,
                question=q,
                status=status,
                answer=redact_output(answer),
                evidence=[e.public() for e in run.evidence],
                trace=run.trace,
                provider=self._provider_info(),
                usage={
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "tool_calls": run.tool_calls,
                    "provider_calls": run.provider_calls,
                    "estimated_cost_usd": round(run.cost, 6),
                    "limits": {
                        "max_tool_calls": self.settings.ai_max_tool_calls,
                        "max_output_tokens": self.settings.ai_max_output_tokens,
                        "max_input_tokens": self.settings.ai_max_input_tokens,
                        "timeout_s": self.settings.ai_request_timeout,
                    },
                },
                charts=build_charts(run.evidence),
                claims=run.claims,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                **kw,
            )

        screening = screen_question(q, self.settings.ai_max_question_chars)
        if not screening.allowed:
            run.add(kind="policy", name="input_screening", status="rejected", message=screening.category)
            return result("refused", screening.message, validation={"screening": screening.category})
        run.add(kind="policy", name="input_screening", status="passed")
        if self.provider is None:
            return result("error", "The AI analyst is disabled on this server (CIVICPULSE_LLM_PROVIDER=disabled).")
        self.governor.admit(client_id)  # raises BudgetExceeded

        prior = [
            p
            for p in (normalize_question(h)[:500] for h in (history or [])[-3:])
            if p and screen_question(p, self.settings.ai_max_question_chars).allowed
        ]
        try:
            with anyio.fail_after(self.settings.ai_request_timeout):
                async with self.gateway.session() as session:
                    return await self._run(session, q, prior, run, result)
        except TimeoutError:
            run.add(
                kind="policy",
                name="request_timeout",
                status="error",
                error_kind="timeout",
                message=f"investigation exceeded {self.settings.ai_request_timeout:g}s",
            )
            return result(
                "error",
                "The investigation timed out before a verified answer was produced. Please try "
                "again or narrow the question.",
            )
        except BudgetExceeded:
            raise
        except Exception as exc:  # MCP start/connect failure: safe message, details only in the trace kind
            run.add(kind="tool", name="mcp_session", status="error", error_kind="transport", message=type(exc).__name__)
            return result("error", "The analysis tools are unavailable right now, so no answer could be verified.")

    # ------------------------------------------------------------------ core loop
    async def _run(
        self,
        session: McpSession,
        q: str,
        prior: list[str],
        run: _Run,
        result: Callable[..., InvestigationResult],
    ) -> InvestigationResult:
        s = self.settings
        await session.list_tools()
        specs, schemas = tool_specs(session)
        dataset = await session.read_json_resource("civicpulse://dataset") or {}
        self._model_versions = {k: str(v) for k, v in (dataset.get("model_versions") or {}).items()}
        first = dt.date.fromisoformat(dataset["first_date"])
        last = dt.date.fromisoformat(dataset["last_complete_date"])
        today = self.today()
        periods = resolve_periods(q, today, first, last)
        if not periods and prior:
            for p in reversed(prior):
                periods = resolve_periods(p, today, first, last)
                if periods:
                    break
        delay_action = asks_for_delay_action(q)
        ctx = {
            "today": today.isoformat(),
            "first_date": first.isoformat(),
            "last_complete_date": last.isoformat(),
            "data_cutoff": dataset.get("data_cutoff"),
            "data_build_id": dataset.get("build_id"),
            "live": False,
            "categories_loaded": dataset.get("categories"),
            "periods": [p.to_dict() for p in periods],
            "delay_action_requested": delay_action,
            "max_tool_calls": s.ai_max_tool_calls,
            "model_versions": self._model_versions,
        }
        mcp_info = {
            "protocol_version": session.protocol_version,
            "server": session.server_info.name if session.server_info else None,
            "server_version": session.server_info.version if session.server_info else None,
            "transport": s.mcp_transport,
            "tools_offered": len(specs),
        }
        freshness = {
            "data_cutoff": dataset.get("data_cutoff"),
            "last_complete_date": last.isoformat(),
            "first_date": first.isoformat(),
            "build_id": dataset.get("build_id"),
            "live": False,
            "today": today.isoformat(),
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": CONTEXT_PREFIX + json.dumps(ctx)},
        ]
        if prior:
            messages.append({"role": "user", "content": HISTORY_PREFIX + " " + " | ".join(prior)})
        messages.append({"role": "user", "content": q})
        warnings: list[Warning] = []
        if delay_action:
            warnings.append(
                Warning(
                    level="restricted",
                    capability="DISABLED",
                    tool="predict_closure_delay",
                    message="Requests to prioritize, allocate or rank using the closure-delay model "
                    "are refused: the model is experimental and not validated for decisions.",
                )
            )
        common = {"periods": ctx["periods"], "freshness": freshness, "mcp": mcp_info}

        # ---- tool loop
        answer: str | None = None
        budget_hit = False
        for _ in range(s.ai_max_tool_calls + 2):
            offer = specs if not budget_hit and run.tool_calls < s.ai_max_tool_calls else []
            resp = await self._complete(messages, offer, run)
            if isinstance(resp, str):
                return result("error", resp, validation={}, warnings=warnings, **common)
            if not resp.tool_calls or not offer:
                answer = resp.text
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": resp.text or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": c.arguments if isinstance(c.arguments, str) else json.dumps(c.arguments),
                            },
                        }
                        for c in resp.tool_calls
                    ],
                }
            )
            for call in resp.tool_calls:
                args_safe = sanitize(call.arguments) if isinstance(call.arguments, dict) else {"raw": "<invalid>"}
                if run.tool_calls >= s.ai_max_tool_calls:
                    budget_hit = True
                    run.add(
                        kind="tool",
                        name=call.name,
                        status="skipped",
                        arguments=args_safe,
                        message="tool-call budget exhausted",
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(
                                {
                                    "tool": call.name,
                                    "error": "tool-call budget exhausted; answer with the evidence you have",
                                    "error_kind": "budget",
                                }
                            ),
                        }
                    )
                    continue
                run.tool_calls += 1
                problem = validate_tool_call(call.name, call.arguments, schemas)
                if problem:
                    run.add(
                        kind="tool",
                        name=call.name[:60],
                        status="rejected",
                        arguments=args_safe,
                        error_kind="validation",
                        message=problem,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(
                                {"tool": call.name[:60], "error": problem, "error_kind": "validation"}
                            ),
                        }
                    )
                    continue
                assert isinstance(call.arguments, dict)
                out = await session.call(call.name, call.arguments)
                if not out.ok or out.structured is None:
                    run.add(
                        kind="tool",
                        name=call.name,
                        status="error",
                        arguments=args_safe,
                        duration_ms=out.duration_ms,
                        error_kind=out.error_kind,
                        message=(out.error or "")[:300],
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(
                                {"tool": call.name, "error": (out.error or "")[:300], "error_kind": out.error_kind}
                            ),
                        }
                    )
                    continue
                eid = f"E{len(run.evidence) + 1}"
                ev = Evidence.from_tool_result(eid, out.structured, call.arguments, out.duration_ms)
                run.evidence.append(ev)
                run.add(
                    kind="tool",
                    name=call.name,
                    status=ev.status if ev.status in ("ok", "restricted", "no_data") else "ok",
                    arguments=args_safe,
                    duration_ms=out.duration_ms,
                    evidence_id=eid,
                    data_source=ev.artifact or f"warehouse {ev.data_build_id}",
                    model_version=ev.model_version,
                    capability=ev.capability,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(
                            {
                                "evidence_id": eid,
                                "tool": call.name,
                                "status": ev.status,
                                "snapshot_id": ev.data_build_id,
                                "model_version": ev.model_version,
                                "facts": [f.for_model() for f in extract_facts(ev)[:FACTS_SHOWN_TO_MODEL]],
                                "result": _llm_view(out.structured),
                            },
                            default=str,
                        ),
                    }
                )
            if run.tool_calls >= s.ai_max_tool_calls:
                budget_hit = True
        if answer is None:
            resp = await self._complete(messages, [], run)
            if isinstance(resp, str):
                return result("error", resp, warnings=warnings, **common)
            answer = resp.text

        warnings += self._capability_warnings(run.evidence)
        evidence_map = {e.evidence_id: e for e in run.evidence}
        facts, conflicts = build_fact_index(run.evidence)
        check = self._make_checker(evidence_map, facts, conflicts, q)

        # ---- grounding + bounded regeneration
        attempts: list[dict[str, Any]] = []
        report = check(answer)
        attempts.append(report.to_dict())
        run.add(
            kind="validation",
            name="grounding",
            status="passed" if report.ok else "failed",
            message=None if report.ok else report.feedback()[:500],
        )
        regen = 0
        while not report.ok and regen < s.ai_max_regenerations:
            regen += 1
            messages += [
                {"role": "assistant", "content": answer},
                {
                    "role": "user",
                    "content": f"{FEEDBACK_PREFIX} {report.feedback()} Rewrite the answer as JSON; every "
                    "number must be a claim that matches a fact exactly (metric, geography, category, period, "
                    "unit, snapshot, model version).",
                },
            ]
            resp = await self._complete(messages, [], run)
            if isinstance(resp, str):
                break
            answer = resp.text
            report = check(answer)
            attempts.append(report.to_dict())
            run.add(
                kind="validation",
                name=f"grounding_retry_{regen}",
                status="passed" if report.ok else "failed",
                message=None if report.ok else report.feedback()[:500],
            )

        validation = {
            "ok": report.ok,
            "attempts": attempts,
            "regenerations": regen,
            "checks": "deterministic: structured claims (metric, geography, category, period, comparison period, "
            "unit, value, snapshot, model version, method, conflicts, derived arithmetic), prose dimension and "
            "direction checks, no free numbers; then citation existence, number support and policy phrases on the "
            "rendered text",
        }
        if not report.ok:
            fb_report = check(self._fallback(run.evidence, ctx))
            validation["fallback"] = fb_report.to_dict()
            run.add(kind="validation", name="fallback_summary", status="passed" if fb_report.ok else "failed")
            run.claims = [c.to_dict() for c in fb_report.semantic.checks if c.ok]
            fb = (
                fb_report.rendered
                if fb_report.ok
                else "The generated answer did not pass the automated evidence checks, and no verified summary "
                "could be produced. The evidence is insufficient to answer this question."
            )
            return result("fallback", fb, validation=validation, warnings=warnings, **common)
        run.claims = [c.to_dict() for c in report.semantic.checks if c.ok]
        answer = report.rendered
        status: Status = "answered" if run.evidence or report.legacy.numbers_checked else "clarification"
        if delay_action and not any(w in answer.lower() for w in ("can't", "cannot", "not validated", "must not")):
            answer = (
                "I can't use the closure-delay model to prioritize requests, allocate services, rank "
                "communities or recommend interventions; it is experimental and not validated.\n\n" + answer
            )
        return result(status, answer, validation=validation, warnings=warnings, **common)

    # ------------------------------------------------------------------ helpers
    async def _complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], run: _Run) -> Any:
        s = self.settings
        assert self.provider is not None
        est = estimate_tokens(messages) + estimate_tokens(tools)
        if run.input_tokens + est > s.ai_max_input_tokens:
            run.add(
                kind="llm",
                name="token_budget",
                status="error",
                error_kind="budget",
                message=f"input-token budget of {s.ai_max_input_tokens} would be exceeded",
            )
            return "The investigation reached its token budget before producing a verified answer."
        if not self.governor.can_spend(est, s.ai_max_output_tokens):
            run.add(
                kind="llm",
                name="cost_ceiling",
                status="error",
                error_kind="budget",
                message="global daily cost ceiling reached",
            )
            return "The analyst's daily cost ceiling has been reached. Please try again tomorrow."
        t0 = time.perf_counter()
        try:
            resp = await self.provider.complete(messages, tools, s.ai_max_output_tokens)
        except ProviderError as exc:
            run.add(
                kind="llm",
                name="provider",
                status="error",
                error_kind="provider",
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                message=str(exc)[:200],
            )
            return "The language-model provider failed, so no verified answer could be produced. Please retry."
        run.provider_calls += 1
        run.input_tokens += resp.usage.input_tokens
        run.output_tokens += resp.usage.output_tokens
        run.cost += self.governor.record(resp.usage.input_tokens, resp.usage.output_tokens)
        names = [c.name[:60] for c in resp.tool_calls]
        run.add(
            kind="llm",
            name="provider",
            status="ok",
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            message=(f"requested tools: {', '.join(names)}" if names else "composed answer")
            + f" (in {resp.usage.input_tokens} / out {resp.usage.output_tokens} tokens)",
        )
        return resp

    def _make_checker(
        self, evidence: dict[str, Evidence], facts: list[Fact], conflicts: set[tuple[Any, ...]], q: str
    ) -> Callable[[str], Verdict]:
        def check(answer: str) -> Verdict:
            sem = evaluate_answer(answer, evidence, facts, conflicts, self._model_versions)
            legacy = validate_answer(sem.rendered, evidence, q, derived_numbers({c.claim.id: c for c in sem.checks}))
            if not sem.rendered.strip():
                legacy.ok = False
                legacy.policy_violations.append("the answer is empty")
            return Verdict(sem, legacy)

        return check

    @staticmethod
    def _capability_warnings(evidence: list[Evidence]) -> list[Warning]:
        out: list[Warning] = []
        seen: set[str] = set()
        for ev in evidence:
            if ev.tool in seen:
                continue
            seen.add(ev.tool)
            if ev.capability == "DISABLED":
                out.append(
                    Warning(
                        level="restricted",
                        capability=ev.capability,
                        tool=ev.tool,
                        message="Closure-delay predictions are disabled: the historical status needed to "
                        "reconstruct the target is unreliable. Research metrics only; not for "
                        "prioritization, allocation or ranking.",
                    )
                )
            elif ev.capability in ("EXPERIMENTAL", "NOT_VERIFIED"):
                lim = ev.limitations[0] if ev.limitations else ""
                out.append(Warning(level="caution", capability=ev.capability, tool=ev.tool, message=lim[:400]))
        return out

    @staticmethod
    def _fallback(evidence: list[Evidence], ctx: dict[str, Any]) -> str:
        """Deterministic structured answer quoting tool-computed text; validated like any other answer."""
        lines = [
            "The generated answer did not pass the automated evidence checks, so this is a summary of the "
            "verified tool results instead:"
        ]
        claims: list[dict[str, Any]] = []

        def quote(eid: str, fld: str, index: int | None = None) -> str:
            cid = f"C{len(claims) + 1}"
            claims.append({"id": cid, "type": "evidence_text", "evidence_id": eid, "field": fld, "index": index})
            return "{" + cid + "}"

        for ev in evidence:
            lines.append(f"- {quote(ev.evidence_id, 'summary')}")
            if ev.uncertainty:
                lines.append(f"  - Uncertainty: {quote(ev.evidence_id, 'uncertainty', 0)}")
            if ev.tool == "detect_unusual_spikes":
                lines.append(f"  - Flags are statistical and unverified, not confirmed incidents [{ev.evidence_id}].")
            if ev.tool == "forecast_request_volume" and not ev.uncertainty:
                lines.append(f"  - Forecast intervals are nominal, not guaranteed [{ev.evidence_id}].")
        if not evidence:
            lines.append("- No tool returned evidence for this question, so the evidence is insufficient to answer it.")
        lines.append(f"- Data is a historical snapshot (cutoff {ctx.get('data_cutoff')}), not live monitoring.")
        return json.dumps({"answer": "\n".join(lines), "claims": claims})
