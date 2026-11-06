"""Server-side configuration for the AI analyst. Secrets come only from the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CIVICPULSE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # provider
    llm_provider: Literal["fake", "openai", "disabled"] = "fake"
    llm_model: str = Field(default="deterministic-fake-v1", max_length=100)
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    # budgets and limits
    ai_max_tool_calls: int = Field(default=6, ge=1, le=10)
    ai_max_output_tokens: int = Field(default=900, ge=100, le=4000)
    ai_max_input_tokens: int = Field(default=24_000, ge=2_000, le=200_000, description="per investigation")
    ai_request_timeout: float = Field(default=60.0, ge=5, le=300)
    ai_max_regenerations: int = Field(default=1, ge=0, le=3)
    ai_daily_budget: float = Field(default=1.00, ge=0, description="USD per UTC day across all users")
    ai_price_input_per_mtok: float = Field(default=0.0, ge=0, description="USD per 1M input tokens")
    ai_price_output_per_mtok: float = Field(default=0.0, ge=0, description="USD per 1M output tokens")
    ai_rate_limit_per_minute: int = Field(default=6, ge=1, le=600, description="per client")
    ai_daily_request_limit: int = Field(default=500, ge=1, description="all clients, per UTC day")
    ai_max_question_chars: int = Field(default=500, ge=20, le=2000)
    ai_max_body_bytes: int = Field(default=8_192, ge=1_024, le=65_536)
    # Optional shared secret for the investigation endpoint. Unset = anonymous access (local demo only);
    # a public deployment must set it and inject it server-side (never in the browser bundle).
    ai_access_token: SecretStr | None = None
    # Paid-AI kill switch. A paid provider (openai) runs only when this is explicitly true AND an access token,
    # a positive daily budget and both token prices are configured; anything missing fails closed.
    ai_paid_enabled: bool = False
    # Concurrency (process-local): investigations running at once, overall and per client.
    ai_max_concurrent: int = Field(default=2, ge=1, le=32)
    ai_max_concurrent_per_client: int = Field(default=1, ge=1, le=8)
    # Investigation reports: results kept in process memory (bounded, expiring) so a report can be downloaded.
    ai_report_store_size: int = Field(default=100, ge=1, le=1_000)
    ai_report_ttl_seconds: int = Field(default=3_600, ge=60, le=86_400)

    # MCP connection
    data_dir: Path | None = None
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_url: str | None = None
    mcp_http_token: SecretStr | None = None
    mcp_protocol_mode: Literal["legacy", "auto"] = "legacy"
    mcp_call_timeout: float = Field(default=25.0, ge=1, le=120)

    @field_validator("openai_api_key", "mcp_http_token", "ai_access_token", "mcp_url", "data_dir", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: Any) -> Any:
        """`KEY=` in a .env copied from .env.example means "not configured", not "empty secret"."""
        return None if isinstance(v, str) and not v.strip() else v


PAID_PROVIDERS = frozenset({"openai"})


def paid_ai_blockers(settings: AgentSettings) -> list[str]:
    """Reasons a paid provider must not run. Empty for non-paid providers; any entry means fail closed."""
    if settings.llm_provider not in PAID_PROVIDERS:
        return []
    blockers = []
    if not settings.ai_paid_enabled:
        blockers.append("paid_ai_kill_switch_off")
    if settings.openai_api_key is None:
        blockers.append("missing_api_key")
    if settings.ai_access_token is None:
        blockers.append("missing_access_token")
    if settings.ai_daily_budget <= 0:
        blockers.append("missing_daily_budget")
    if settings.ai_price_input_per_mtok <= 0 or settings.ai_price_output_per_mtok <= 0:
        blockers.append("missing_token_prices")
    return blockers


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
