"""Run the deterministic agent evaluation scenarios.

Each scenario goes through the real orchestrator and a REAL MCP server over stdio (one server process
per investigation, exactly as in production). The provider is the deterministic FAKE provider unless
``--provider openai`` is given AND credentials are configured AND ``--allow-paid`` is passed; results
for the two providers are written to different files and must never be merged.

Usage:
    python scripts/agent_eval.py [--today 2026-11-04] [--only geo-01,fc-01] [--out docs/evaluation/agent_eval_fake.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

import anyio

from civicpulse_agent.budget import Prices, UsageGovernor
from civicpulse_agent.config import AgentSettings
from civicpulse_agent.orchestrator import InvestigationResult, Orchestrator
from civicpulse_agent.providers import FakeProvider, build_provider
from civicpulse_mcp.client import McpGateway, McpSettings
from civicpulse_shared.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "evals" / "agent_scenarios.json"


def check(sc: dict[str, Any], r: InvestigationResult) -> list[str]:
    fails: list[str] = []
    text = r.answer
    low = text.lower()
    tools = [t.name for t in r.trace if t.kind == "tool" and t.status != "skipped"]
    if r.status not in sc["expect_status"]:
        fails.append(f"status {r.status} not in {sc['expect_status']}")
    if "expect_tools" in sc and tools != sc["expect_tools"]:
        fails.append(f"tools {tools} != {sc['expect_tools']}")
    for s in sc.get("must_contain", []):
        if s.lower() not in low:
            fails.append(f"missing text: {s!r}")
    for s in sc.get("must_not_contain", []):
        if s.lower() in low:
            fails.append(f"forbidden text present: {s!r}")
    if sc.get("expect_evidence_status") and [e["status"] for e in r.evidence] != sc["expect_evidence_status"]:
        fails.append(f"evidence status {[e['status'] for e in r.evidence]} != {sc['expect_evidence_status']}")
    for name, status in sc.get("expect_trace", []):
        if not any(t.name == name and t.status == status for t in r.trace):
            fails.append(f"trace lacks {name}:{status}")
    if "expect_max_tool_calls" in sc and r.usage["tool_calls"] > sc["expect_max_tool_calls"]:
        fails.append(f"tool calls {r.usage['tool_calls']} > {sc['expect_max_tool_calls']}")
    if "expect_regenerations" in sc and r.validation.get("regenerations") != sc["expect_regenerations"]:
        fails.append(f"regenerations {r.validation.get('regenerations')} != {sc['expect_regenerations']}")
    if sc.get("expect_warning") and not any(w.level == sc["expect_warning"] for w in r.warnings):
        fails.append(f"no {sc['expect_warning']} warning")
    # Universal invariants
    if (
        r.status in ("answered", "clarification")
        and sc.get("require_grounding_ok", True)
        and not r.validation.get("ok")
    ):
        fails.append("final answer failed grounding")
    if r.status == "fallback" and not r.validation.get("fallback", {}).get("ok"):
        fails.append("fallback summary failed grounding")
    if r.status == "refused" and (tools or r.usage["provider_calls"]):
        fails.append("refusal made tool or provider calls")
    for bad in ("real-time", "live monitoring system", "confirmed incident"):
        if bad in low and f"not {bad}" not in low and "not live" not in low:
            fails.append(f"policy phrase {bad!r}")
    return fails


async def run_one(sc: dict[str, Any], base: AgentSettings, provider_name: str, today: dt.date) -> dict[str, Any]:
    settings = base.model_copy(update=sc.get("settings", {}))
    if provider_name == "fake":
        provider: Any = FakeProvider(fault=sc.get("fault", "none"), slow_seconds=sc.get("slow_seconds", 5.0))
    else:
        if sc.get("fault"):
            return {"id": sc["id"], "group": sc["group"], "skipped": "fault scenarios apply to the fake provider only"}
        provider = build_provider(settings)
    gw = McpGateway(McpSettings(transport="stdio", data_dir=settings.data_dir, call_timeout=settings.mcp_call_timeout))
    orch = Orchestrator(
        settings,
        provider,
        UsageGovernor(
            10_000,
            100_000,
            settings.ai_daily_budget,
            Prices(settings.ai_price_input_per_mtok, settings.ai_price_output_per_mtok),
        ),
        gateway=gw,
        today=today,
    )
    t0 = time.perf_counter()
    r = await orch.investigate(sc["question"], f"eval-{sc['id']}", sc.get("history"))
    fails = check(sc, r)
    return {
        "id": sc["id"],
        "group": sc["group"],
        "question": sc["question"],
        "fault": sc.get("fault", "none"),
        "passed": not fails,
        "failures": fails,
        "status": r.status,
        "tools": [f"{t.name}:{t.status}" for t in r.trace if t.kind == "tool"],
        "evidence_ids": [e["evidence_id"] for e in r.evidence],
        "grounding_ok": r.validation.get("ok"),
        "regenerations": r.validation.get("regenerations"),
        "numbers_checked": (r.validation.get("attempts") or [{}])[-1].get("numbers_checked"),
        "warnings": [w.level for w in r.warnings],
        "duration_s": round(time.perf_counter() - t0, 2),
        "answer_chars": len(r.answer),
        "mcp_protocol": r.mcp.get("protocol_version"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["fake", "openai"], default="fake")
    ap.add_argument("--allow-paid", action="store_true", help="required for --provider openai (paid API calls)")
    ap.add_argument("--today", default="2026-11-04", help="calendar date used to resolve relative periods")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.provider == "openai" and not a.allow_paid:
        print("refusing to make paid API calls without --allow-paid", file=sys.stderr)
        return 2
    spec = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    scenarios = [s for s in spec["scenarios"] if not a.only or s["id"] in a.only.split(",")]
    base = AgentSettings(
        _env_file=None,
        data_dir=get_settings().data_dir,  # type: ignore[call-arg]
        llm_provider=a.provider,
        ai_daily_budget=0 if a.provider == "fake" else 1.0,
    )
    today = dt.date.fromisoformat(a.today)
    results = []
    for sc in scenarios:
        res = anyio.run(run_one, sc, base, a.provider, today)
        results.append(res)
        mark = "SKIP" if "skipped" in res else ("PASS" if res["passed"] else "FAIL")
        print(
            f"{mark} {sc['id']:<10} {res.get('status', ''):<13} {res.get('duration_s', '')}s "
            f"{'; '.join(res.get('failures', []))}",
            flush=True,
        )
    ran = [r for r in results if "skipped" not in r]
    groups: dict[str, list[int]] = {}
    for r in ran:
        g = groups.setdefault(r["group"], [0, 0])
        g[0] += r["passed"]
        g[1] += 1
    summary = {
        "provider": a.provider,
        "provider_is_live_llm": a.provider != "fake",
        "run_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "today_used": a.today,
        "scenarios": len(ran),
        "passed": sum(r["passed"] for r in ran),
        "by_group": {k: {"passed": v[0], "total": v[1]} for k, v in sorted(groups.items())},
        "data_build": None,
        "results": results,
    }
    try:
        from civicpulse_analytics import AnalyticsService

        summary["data_build"] = (AnalyticsService(base.data_dir).build_info() or {}).get("build_id")
    except Exception as exc:  # report without the build id rather than fail the run
        print(f"could not read data build id: {type(exc).__name__}", file=sys.stderr)
    out = Path(a.out) if a.out else ROOT / "docs" / "evaluation" / f"agent_eval_{a.provider}.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\n{summary['passed']}/{summary['scenarios']} passed -> {out}")
    return 0 if summary["passed"] == summary["scenarios"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
