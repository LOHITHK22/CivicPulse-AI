"""The paid real-LLM smoke-test script, exercised with mocks only (no network, no paid calls).

The end-to-end test drives the REAL OpenAICompatibleProvider through an httpx.MockTransport whose handler answers
with the deterministic fake provider, so the HTTP request/response handling, limits and evaluation are real.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from tests_support_orchestrator import InMemoryGateway, make_orch

from civicpulse_agent.config import AgentSettings
from civicpulse_agent.providers import FakeProvider, OpenAICompatibleProvider

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("llm_smoke_test", ROOT / "scripts" / "llm_smoke_test.py")
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

KEY = "sk-test-SMOKEKEY-0000000000000000"
TOKEN = "t" * 40
AUTH_ENV = {"CIVICPULSE_SMOKE_AUTHORIZED": "yes"}


def full_settings(data_dir: Any = None, **over: Any) -> AgentSettings:
    base = {
        "llm_provider": "openai",
        "llm_model": "mock-model",
        "openai_api_key": SecretStr(KEY),
        "ai_access_token": SecretStr(TOKEN),
        "ai_paid_enabled": True,
        "ai_daily_budget": 0.25,
        "ai_price_input_per_mtok": 0.15,
        "ai_price_output_per_mtok": 0.60,
        "data_dir": data_dir,
    }
    return AgentSettings(_env_file=None, **{**base, **over})  # type: ignore[call-arg]


class Recorder:
    """httpx handler that answers OpenAI chat-completions requests using the deterministic fake provider."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = status
        self.fake = FakeProvider()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"body": body, "auth": request.headers.get("authorization")})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "secret detail " + KEY}})
        r = await self.fake.complete(body["messages"], body.get("tools") or [], body["max_tokens"])
        msg: dict[str, Any] = {"role": "assistant", "content": r.text or None}
        if r.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in r.tool_calls
            ]
        usage = {"prompt_tokens": 1200, "completion_tokens": 150}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": usage})


def factory(rec: Recorder):
    def make(s: AgentSettings) -> OpenAICompatibleProvider:
        return OpenAICompatibleProvider(
            api_key=s.openai_api_key.get_secret_value(),  # type: ignore[union-attr]
            model=s.llm_model,
            transport=httpx.MockTransport(rec),
        )

    return make


@pytest.fixture(scope="module")
def gateway(sample_models: Any) -> InMemoryGateway:
    return InMemoryGateway(sample_models.root)


@pytest.fixture(scope="module")
def today(sample_models: Any, gateway: InMemoryGateway) -> Any:
    return make_orch(gateway, sample_models)._today


def _never(_: AgentSettings) -> Any:
    raise AssertionError("provider must not be constructed when blocked")


@pytest.mark.parametrize(
    ("argv", "env", "over", "blocker"),
    [
        ([], AUTH_ENV, {}, "missing_--authorize-paid-run"),
        (["--authorize-paid-run"], {}, {}, "missing_CIVICPULSE_SMOKE_AUTHORIZED=yes"),
        (["--authorize-paid-run"], AUTH_ENV, {"openai_api_key": None}, "missing_api_key"),
        (["--authorize-paid-run"], AUTH_ENV, {"ai_paid_enabled": False}, "paid_ai_kill_switch_off"),
        (["--authorize-paid-run"], AUTH_ENV, {"ai_daily_budget": 0.0}, "missing_daily_budget"),
        (["--authorize-paid-run"], AUTH_ENV, {"ai_access_token": None}, "missing_access_token"),
        (["--authorize-paid-run"], AUTH_ENV, {"llm_provider": "fake"}, "provider_is_not_openai"),
    ],
)
def test_blocked_without_every_control(tmp_path, capsys, argv, env, over, blocker):
    out = tmp_path / "r.json"
    code = smoke.main([*argv, "--out", str(out)], env=env, settings=full_settings(**over), provider_factory=_never)
    assert code == 3
    printed = capsys.readouterr().out
    assert '"BLOCKED"' in printed and blocker in printed and KEY not in printed
    assert not out.exists()


def test_authorized_mock_run_passes_within_limits(tmp_path, capsys, gateway, today):
    rec = Recorder()
    out = tmp_path / "r.json"
    code = smoke.main(
        ["--authorize-paid-run", "--out", str(out)],
        env=AUTH_ENV,
        settings=full_settings(),
        provider_factory=factory(rec),
        gateway=gateway,
        today=today,
    )
    record = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0, record
    assert record["verdict"] == "PASS"
    assert [t["tool"] for t in record["tools"]] == ["compare_boroughs"]
    assert all(c["ok"] for c in record["checks"])
    assert all(r["body"]["max_tokens"] == 800 for r in rec.requests)  # output token limit sent to the provider
    assert 1 <= len(rec.requests) <= 4
    assert record["usage"]["output_tokens"] == 150 * len(rec.requests)  # usage as reported by the provider
    assert record["usage"]["estimated_cost_usd"] > 0
    text = out.read_text(encoding="utf-8") + capsys.readouterr().out
    assert KEY not in text and TOKEN not in text and "Bearer" not in text
    assert rec.requests[0]["auth"] == f"Bearer {KEY}"  # the key goes only to the provider endpoint


def test_provider_error_is_not_retried_and_not_echoed(tmp_path, capsys, gateway, today):
    rec = Recorder(status=500)
    out = tmp_path / "r.json"
    code = smoke.main(
        ["--authorize-paid-run", "--out", str(out)],
        env=AUTH_ENV,
        settings=full_settings(),
        provider_factory=factory(rec),
        gateway=gateway,
        today=today,
    )
    assert code == 1
    assert len(rec.requests) == 1  # exactly one HTTP request: no automatic retry
    text = out.read_text(encoding="utf-8") + capsys.readouterr().out
    assert '"FAIL"' in text and KEY not in text and "secret detail" not in text


def test_evaluate_flags_wrong_tool_and_excess_calls(gateway, sample_models):
    from tests_support_orchestrator import ask

    r = ask(make_orch(gateway, sample_models), "How fresh is the data?")
    r = r.model_copy(update={"provider": {"name": "openai", "model": "m", "live_llm": True}})
    verdict, checks = smoke.evaluate(r)
    failed = {c["check"] for c in checks if not c["ok"]}
    assert verdict == "FAIL" and "expected_tool_selected" in failed
    many = r.model_copy(update={"trace": r.trace + [t.model_copy() for t in r.trace if t.kind == "tool"] * 4})
    assert "tool_calls_within_limit" in {c["check"] for c in smoke.evaluate(many)[1] if not c["ok"]}
