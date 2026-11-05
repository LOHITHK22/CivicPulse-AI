"""LLM providers. ``build_provider`` selects one from configuration; the API key never leaves the server."""

from __future__ import annotations

from civicpulse_agent.config import AgentSettings
from civicpulse_agent.providers.base import LLMProvider, ProviderError, ProviderResponse, ToolCallRequest, Usage
from civicpulse_agent.providers.fake import FakeProvider
from civicpulse_agent.providers.openai_compat import OpenAICompatibleProvider

__all__ = [
    "FakeProvider",
    "LLMProvider",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderResponse",
    "ToolCallRequest",
    "Usage",
    "build_provider",
]


def build_provider(settings: AgentSettings) -> LLMProvider | None:
    """Returns None when the analyst is disabled."""
    if settings.llm_provider == "disabled":
        return None
    if settings.llm_provider == "fake":
        return FakeProvider(model=settings.llm_model)
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    return OpenAICompatibleProvider(
        api_key=key, model=settings.llm_model, base_url=settings.openai_base_url, timeout=settings.ai_request_timeout
    )
