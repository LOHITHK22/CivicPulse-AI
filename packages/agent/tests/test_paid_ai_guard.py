"""Paid-AI guard: kill switch and fail-closed configuration. No network calls; no provider is ever invoked."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from civicpulse_agent.config import AgentSettings, paid_ai_blockers
from civicpulse_agent.providers import FakeProvider, OpenAICompatibleProvider, ProviderError, build_provider

FULL: dict[str, Any] = {
    "llm_provider": "openai",
    "llm_model": "some-model",
    "openai_api_key": SecretStr("sk-test-not-a-real-key-000000"),
    "ai_access_token": SecretStr("a" * 40),
    "ai_paid_enabled": True,
    "ai_daily_budget": 0.50,
    "ai_price_input_per_mtok": 1.0,
    "ai_price_output_per_mtok": 2.0,
}


def settings(**over: Any) -> AgentSettings:
    return AgentSettings(_env_file=None, **{**FULL, **over})  # type: ignore[call-arg]


def test_fully_configured_paid_provider_has_no_blockers() -> None:
    s = settings()
    assert paid_ai_blockers(s) == []
    assert isinstance(build_provider(s), OpenAICompatibleProvider)  # constructed only; no request is sent


@pytest.mark.parametrize(
    ("override", "blocker"),
    [
        ({"ai_paid_enabled": False}, "paid_ai_kill_switch_off"),
        ({"openai_api_key": None}, "missing_api_key"),
        ({"ai_access_token": None}, "missing_access_token"),
        ({"ai_daily_budget": 0.0}, "missing_daily_budget"),
        ({"ai_price_input_per_mtok": 0.0}, "missing_token_prices"),
        ({"ai_price_output_per_mtok": 0.0}, "missing_token_prices"),
    ],
)
def test_each_missing_control_fails_closed(override: dict[str, Any], blocker: str) -> None:
    s = settings(**override)
    assert blocker in paid_ai_blockers(s)
    with pytest.raises(ProviderError, match="paid AI is blocked"):
        build_provider(s)


def test_kill_switch_defaults_off() -> None:
    s = AgentSettings(_env_file=None, llm_provider="openai")  # type: ignore[call-arg]
    assert s.ai_paid_enabled is False
    assert set(paid_ai_blockers(s)) >= {"paid_ai_kill_switch_off", "missing_api_key", "missing_access_token"}


def test_key_presence_never_switches_provider() -> None:
    s = settings(llm_provider="fake")  # a key, token and budget exist, but the provider is fake
    p = build_provider(s)
    assert isinstance(p, FakeProvider) and p.live is False
    assert paid_ai_blockers(s) == []  # not a paid provider


def test_disabled_provider_builds_nothing() -> None:
    assert build_provider(settings(llm_provider="disabled")) is None


def test_kill_switch_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIVICPULSE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("CIVICPULSE_AI_PAID_ENABLED", "false")
    s = AgentSettings(_env_file=None)  # type: ignore[call-arg]
    assert "paid_ai_kill_switch_off" in paid_ai_blockers(s)
