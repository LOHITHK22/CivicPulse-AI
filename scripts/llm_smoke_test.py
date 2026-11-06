"""Real-LLM smoke test: ONE investigation against a paid OpenAI-compatible provider. Never runs by default.

It makes paid API calls only when ALL of the following hold, otherwise it exits with BLOCKED and no network call:
  * the command line contains --authorize-paid-run, and CIVICPULSE_SMOKE_AUTHORIZED=yes is set in the environment
    (two separate, explicit acts of authorization);
  * CIVICPULSE_LLM_PROVIDER=openai and the paid-AI guard has no blockers: CIVICPULSE_AI_PAID_ENABLED=true, an API key,
    an access token, a positive daily budget and both token prices (see civicpulse_agent.config.paid_ai_blockers).

Hard limits for the run: at most 3 MCP tool calls, at most 800 output tokens per provider call, no regeneration, no
automatic retries (the provider makes one HTTP request per call and never retries), one investigation, no
background calls. Real MCP over stdio, real warehouse.

The result records verdict (PASS / FAIL / BLOCKED), checks, tool calls, evidence attribution, token usage and the
estimated cost from the configured prices. Credentials are never printed or written: output passes through the
report sanitizer and is scanned for the key before writing.

Usage (only after the specific run has been authorized):
    CIVICPULSE_SMOKE_AUTHORIZED=yes python scripts/llm_smoke_test.py --authorize-paid-run \
        [--out docs/evaluation/llm_smoke_result.json]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from civicpulse_agent.budget import Prices, UsageGovernor
from civicpulse_agent.config import AgentSettings, paid_ai_blockers
from civicpulse_agent.orchestrator import InvestigationResult, Orchestrator
from civicpulse_agent.providers import LLMProvider, OpenAICompatibleProvider
from civicpulse_agent.report import _sanitize

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "Which borough had the most residential noise complaints during October 2026?"
EXPECTED_TOOL = "compare_boroughs"
MAX_TOOL_CALLS = 3
MAX_OUTPUT_TOKENS = 800
AUTH_ENV = "CIVICPULSE_SMOKE_AUTHORIZED"
EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 3}


def preflight(settings: AgentSettings, flag: bool, env: dict[str, str]) -> list[str]:
    """Every reason the paid run must not start. Empty list = allowed."""
    blockers = []
    if not flag:
        blockers.append("missing_--authorize-paid-run")
    if env.get(AUTH_ENV, "").strip().lower() != "yes":
        blockers.append(f"missing_{AUTH_ENV}=yes")
    if settings.llm_provider != "openai":
        blockers.append("provider_is_not_openai")
    blockers += paid_ai_blockers(settings)
    return blockers


def limited(settings: AgentSettings) -> AgentSettings:
    return settings.model_copy(
        update={
            "ai_max_tool_calls": MAX_TOOL_CALLS,
            "ai_max_output_tokens": MAX_OUTPUT_TOKENS,
            "ai_max_regenerations": 0,
            "ai_rate_limit_per_minute": 1,
            "ai_daily_request_limit": 1,
        }
    )


def default_provider(s: AgentSettings) -> LLMProvider:
    assert s.openai_api_key is not None  # preflight guarantees it
    return OpenAICompatibleProvider(
        api_key=s.openai_api_key.get_secret_value(),
        model=s.llm_model,
        base_url=s.openai_base_url,
        timeout=s.ai_request_timeout,
    )


def evaluate(r: InvestigationResult) -> tuple[str, list[dict[str, Any]]]:
    tools = [t for t in r.trace if t.kind == "tool"]
    ev_ids = {e["evidence_id"] for e in r.evidence}
    cited = set(re.findall(r"\bE\d{1,2}\b", r.answer))
    claim_ids = {i for c in r.claims for i in c.get("evidence_ids") or []}
    checks = [
        ("provider_is_live", bool(r.provider.get("live_llm"))),
        ("status_answered", r.status == "answered"),
        ("tool_calls_within_limit", len(tools) <= MAX_TOOL_CALLS),
        ("expected_tool_selected", any(t.name == EXPECTED_TOOL for t in tools)),
        ("all_tool_calls_ok", bool(tools) and all(t.status == "ok" for t in tools)),
        ("validation_passed", r.validation.get("ok") is True),
        ("no_regeneration", r.validation.get("regenerations", 0) == 0),
        ("claims_present", bool(r.claims)),
        ("claims_cite_existing_evidence", bool(claim_ids) and claim_ids <= ev_ids),
        ("answer_cites_existing_evidence", bool(cited) and cited <= ev_ids),
        (
            "output_tokens_within_limit",
            r.usage.get("output_tokens", 0) <= MAX_OUTPUT_TOKENS * max(1, r.usage.get("provider_calls", 1)),
        ),
    ]
    out = [{"check": n, "ok": ok} for n, ok in checks]
    return ("PASS" if all(ok for _, ok in checks) else "FAIL"), out


def _numbers_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _numbers_only(v) for k, v in value.items() if isinstance(v, (int, float, dict))}
    return value if isinstance(value, (int, float)) else None


def _write(out: Path, record: dict[str, Any], secrets: list[str]) -> None:
    safe = _sanitize({k: v for k, v in record.items() if k != "usage"})
    if "usage" in record:  # token counts would match the secret-key filter; keep them, numbers only
        safe["usage"] = _numbers_only(record["usage"])
    text = json.dumps(safe, indent=2, ensure_ascii=False)
    for s in secrets:
        if s and s in text:  # belt and braces: never persist a credential
            raise RuntimeError("refusing to write output that contains a credential")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8", newline="\n")


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    settings: AgentSettings | None = None,
    provider_factory: Callable[[AgentSettings], LLMProvider] = default_provider,
    gateway: Any = None,
    today: dt.date | None = None,
) -> int:
    ap = argparse.ArgumentParser(description="Paid real-LLM smoke test (one investigation). See module docstring.")
    ap.add_argument("--authorize-paid-run", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "evaluation" / "llm_smoke_result.json"))
    args = ap.parse_args(argv)
    env = dict(os.environ) if env is None else env
    s = settings or AgentSettings()
    record: dict[str, Any] = {
        "question": QUESTION,
        "limits": {"max_tool_calls": MAX_TOOL_CALLS, "max_output_tokens": MAX_OUTPUT_TOKENS, "retries": 0},
        "started_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    blockers = preflight(s, args.authorize_paid_run, env)
    if blockers:
        print(json.dumps({"verdict": "BLOCKED", "blockers": blockers}, indent=2))
        return EXIT["BLOCKED"]  # nothing written, no provider constructed, no network call
    s = limited(s)
    secrets = [x.get_secret_value() for x in (s.openai_api_key, s.ai_access_token) if x is not None]
    governor = UsageGovernor(1, 1, s.ai_daily_budget, Prices(s.ai_price_input_per_mtok, s.ai_price_output_per_mtok))
    try:
        orch = Orchestrator(s, provider_factory(s), governor, gateway=gateway, today=today)
        result = asyncio.run(orch.investigate(QUESTION, "llm-smoke-test"))
    except Exception as exc:  # report the failure kind only; never echo provider bodies or headers
        record |= {"verdict": "FAIL", "error": type(exc).__name__}
        _write(Path(args.out), record, secrets)
        print(json.dumps({"verdict": "FAIL", "error": type(exc).__name__}))
        return EXIT["FAIL"]
    verdict, checks = evaluate(result)
    record |= {
        "verdict": verdict,
        "checks": checks,
        "provider": result.provider,
        "status": result.status,
        "tools": [
            {"tool": t.name, "status": t.status, "evidence_id": t.evidence_id} for t in result.trace if t.kind == "tool"
        ],
        "claims_valid": len(result.claims),
        "usage": result.usage,  # actual tokens reported by the provider and estimated cost from configured prices
        "answer": result.answer,
        "duration_ms": result.duration_ms,
    }
    _write(Path(args.out), record, secrets)
    print(
        json.dumps(
            {"verdict": verdict, "usage": result.usage, "failed_checks": [c["check"] for c in checks if not c["ok"]]},
            indent=2,
        )
    )
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main())
