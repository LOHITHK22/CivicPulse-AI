"""OpenAI-compatible chat-completions provider (server-side key, bounded tokens and timeout).

NOT live-tested in this repository: no paid API call was authorized. Unit tests exercise it against a
mocked HTTP transport only.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from civicpulse_agent.providers.base import ProviderError, ProviderResponse, ToolCallRequest, Usage


class OpenAICompatibleProvider:
    name = "openai"
    live = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("CIVICPULSE_OPENAI_API_KEY is not configured")
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout
        self._transport = transport

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_output_tokens: int
    ) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "temperature": 0,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = True
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                resp = await http.post(self._url, headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            # Never echo the response body: it can contain request details. Status code only.
            raise ProviderError(f"provider returned HTTP {resp.status_code}")
        try:
            data = resp.json()
            msg = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("provider returned a malformed response") from exc
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args: Any = json.loads(raw)
            except (TypeError, ValueError):
                args = raw
            calls.append(ToolCallRequest(id=str(tc.get("id", "")), name=str(fn.get("name", "")), arguments=args))
        usage = data.get("usage") or {}
        return ProviderResponse(
            text=msg.get("content") or "",
            tool_calls=calls,
            usage=Usage(int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
            model=str(data.get("model", self.model)),
        )
