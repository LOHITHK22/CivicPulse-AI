"""Deployment proxy trust: client attribution, proxy-only mode and report ownership behind the Next.js proxy."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from test_api_ai import ORIGIN, URL, _investigate
from test_api_ai import setup as setup

from civicpulse_api import main as api_main
from civicpulse_shared.config import Settings

SECRET = "p" * 40


class _Req:
    def __init__(self, peer: str, headers: dict[str, str]):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers


def via_proxy(client_ip: str, secret: str = SECRET) -> dict[str, str]:
    return {"X-CivicPulse-Proxy-Auth": secret, "X-CivicPulse-Client": client_ip}


def test_client_key_trusts_forwarded_client_only_with_the_proxy_secret():
    h = {"x-civicpulse-proxy-auth": SECRET, "x-civicpulse-client": "203.0.113.9"}
    assert api_main.client_key(_Req("10.0.0.5", h), 0, SECRET) == "via-proxy:203.0.113.9"
    assert api_main.client_key(_Req("10.0.0.5", h), 0, None) == "10.0.0.5"  # no secret configured
    wrong = {**h, "x-civicpulse-proxy-auth": "q" * 40}
    assert api_main.client_key(_Req("10.0.0.5", wrong), 0, SECRET) == "10.0.0.5"  # forged by a direct caller
    for bad in ("1.2.3.4, 5.6.7.8", "<script>", "x" * 60, ""):
        assert api_main.client_key(_Req("10.0.0.5", {**h, "x-civicpulse-client": bad}), 0, SECRET) == "10.0.0.5"
    v6 = {**h, "x-civicpulse-client": "2001:db8::1"}
    assert api_main.client_key(_Req("10.0.0.5", v6), 0, SECRET) == "via-proxy:2001:db8::1"


def test_proxy_only_mode_fails_closed_without_a_strong_secret():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_require_proxy_auth=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_require_proxy_auth=True, api_proxy_secret="short")  # type: ignore[call-arg]
    assert Settings(_env_file=None, api_proxy_secret="  ").api_proxy_secret is None  # type: ignore[call-arg]


@pytest.fixture
def proxied(setup, monkeypatch, sample_models):
    _, state = setup
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        data_dir=sample_models.root,
        api_proxy_secret=SecretStr(SECRET),
        api_require_proxy_auth=True,
    )
    monkeypatch.setattr(api_main, "get_settings", lambda: s)
    with TestClient(api_main.create_app()) as c:
        yield c, state


def test_proxy_only_mode_rejects_direct_calls_but_keeps_health_open(proxied):
    c, _ = proxied
    assert c.get("/api/v1/meta/filters").status_code == 401
    assert c.get("/api/v1/meta/filters", headers=via_proxy("203.0.113.9", "q" * 40)).status_code == 401
    assert c.get("/api/v1/meta/filters", headers=via_proxy("203.0.113.9")).status_code == 200
    assert c.post(URL, json={"question": "How fresh is the data?"}, headers=ORIGIN).status_code == 401
    assert c.get("/health").status_code == 200
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_reports_are_owned_by_the_forwarded_client(proxied):
    c, _ = proxied
    alice, bob = via_proxy("203.0.113.9"), via_proxy("198.51.100.7")
    r = c.post(URL, json={"question": "How fresh is the data?"}, headers={**ORIGIN, **alice})
    assert r.status_code == 200, r.text
    inv = r.json()["investigation_id"]
    assert c.get(f"{URL}/{inv}/report", headers=alice).status_code == 200
    assert c.get(f"{URL}/{inv}/report", headers=bob).status_code == 404  # another browser behind the same proxy


def test_without_proxy_secret_behaviour_is_unchanged(setup):
    c, _ = setup
    inv = _investigate(c)
    # A forged proxy header is ignored when no secret is configured: the socket peer still owns the report.
    assert c.get(f"{URL}/{inv}/report", headers=via_proxy("198.51.100.7")).status_code == 200
