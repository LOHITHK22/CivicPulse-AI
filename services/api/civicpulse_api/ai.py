"""AI analyst routes: POST /api/v1/ai/investigations (the API's only non-GET route) and GET /api/v1/ai/status."""

from __future__ import annotations

import hmac
import logging
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from civicpulse_agent.budget import BudgetExceeded, Prices, UsageGovernor
from civicpulse_agent.config import AgentSettings
from civicpulse_agent.orchestrator import InvestigationResult, Orchestrator
from civicpulse_agent.policy import ALLOWED_TOOLS
from civicpulse_agent.providers import ProviderError, build_provider
from civicpulse_mcp.capabilities import CAPABILITY_TABLE, PROHIBITED_DELAY_USES
from civicpulse_shared.config import get_settings
from civicpulse_shared.logging import log

logger = logging.getLogger("civicpulse_api.ai")
INVESTIGATIONS_PATH = "/api/v1/ai/investigations"


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=2000)
    history: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=3)


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    s = AgentSettings()
    if s.data_dir is None:
        s = s.model_copy(update={"data_dir": get_settings().data_dir})
    return s


@lru_cache(maxsize=1)
def get_governor() -> UsageGovernor:
    s = get_agent_settings()
    return UsageGovernor(
        s.ai_rate_limit_per_minute,
        s.ai_daily_request_limit,
        s.ai_daily_budget,
        Prices(s.ai_price_input_per_mtok, s.ai_price_output_per_mtok),
    )


@lru_cache(maxsize=1)
def get_orchestrator() -> Orchestrator:
    s = get_agent_settings()
    try:
        provider = build_provider(s)
    except ProviderError:
        provider = None  # misconfigured (e.g. openai without key): analyst reports itself disabled
    return Orchestrator(s, provider, get_governor())


def origin_allowed(origin: str | None) -> bool:
    """Browsers always send Origin on cross-origin POSTs; non-browser clients may omit it."""
    return origin is None or origin in get_settings().cors_origins


def build_router(client_key: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

    @router.get("/status")
    def status() -> dict[str, Any]:
        """Analyst configuration without secrets: provider, limits, tool allow-list and capability classes."""
        s = get_agent_settings()
        orch = get_orchestrator()
        p = orch.provider
        return {
            "enabled": p is not None,
            "provider": p.name if p else "disabled",
            "model": p.model if p else None,
            "live_llm": bool(p and p.live),
            "auth_required": s.ai_access_token is not None,
            "limits": {
                "max_tool_calls": s.ai_max_tool_calls,
                "max_output_tokens": s.ai_max_output_tokens,
                "max_input_tokens": s.ai_max_input_tokens,
                "request_timeout_s": s.ai_request_timeout,
                "max_regenerations": s.ai_max_regenerations,
                "rate_limit_per_minute": s.ai_rate_limit_per_minute,
                "max_question_chars": s.ai_max_question_chars,
            },
            "usage": get_governor().snapshot(),
            "tools": sorted(ALLOWED_TOOLS),
            "capabilities": CAPABILITY_TABLE,
            "closure_delay_prohibited_uses": PROHIBITED_DELAY_USES,
            "mcp_transport": s.mcp_transport,
        }

    @router.post("/investigations", response_model=InvestigationResult)
    async def investigate(
        body: InvestigationRequest,
        request: Request,
        authorization: Annotated[str | None, Header(max_length=300)] = None,
    ) -> Any:
        s = get_agent_settings()
        if s.ai_access_token is not None:
            expected = f"Bearer {s.ai_access_token.get_secret_value()}"
            if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
                raise HTTPException(401, "authorization required")
        client = client_key(request)
        try:
            res = await get_orchestrator().investigate(body.question, client, body.history)
        except BudgetExceeded as exc:
            code = 429 if exc.reason == "rate_limited" else 503
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            log(logger, logging.WARNING, "ai_budget", reason=exc.reason)
            return JSONResponse({"detail": "AI analyst limit reached", "code": exc.reason}, code, headers=headers)
        log(
            logger,
            logging.INFO,
            "ai_investigation",
            investigation_id=res.investigation_id,
            status=res.status,
            question_chars=len(body.question),
            tools=[t.name for t in res.trace if t.kind == "tool"],
            tool_statuses=[t.status for t in res.trace if t.kind == "tool"],
            grounding_ok=res.validation.get("ok"),
            input_tokens=res.usage.get("input_tokens"),
            output_tokens=res.usage.get("output_tokens"),
            duration_ms=res.duration_ms,
        )
        return res

    return router
