"""Provider abstraction: a chat-completion interface with OpenAI-style tool calling."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(Exception):
    """The language-model provider failed (network, HTTP error, malformed response)."""


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: Any  # parsed JSON object, or the raw string when the model produced invalid JSON


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ProviderResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class LLMProvider(Protocol):
    name: str
    model: str
    live: bool  # True only for providers that call a real hosted model

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_output_tokens: int
    ) -> ProviderResponse: ...


def estimate_tokens(value: Any) -> int:
    """Rough token estimate (4 characters per token) used for budgeting when a provider reports no usage."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return max(1, len(text) // 4)
