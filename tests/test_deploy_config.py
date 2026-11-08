"""Static validation of the staging deployment configuration (no cloud access)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SECRET_NAMES = re.compile(r"SECRET|TOKEN|API_KEY|PASSWORD|SHA256", re.I)


def test_fly_staging_config_is_safe():
    cfg = tomllib.loads((ROOT / "deploy" / "fly.staging.toml").read_text(encoding="utf-8"))
    env = cfg["env"]
    assert env["CIVICPULSE_AI_PAID_ENABLED"] == "false" and env["CIVICPULSE_LLM_PROVIDER"] == "fake"
    assert env["CIVICPULSE_API_REQUIRE_PROXY_AUTH"] == "true" and env["CIVICPULSE_REQUIRE_ARTIFACT"] == "true"
    assert env["CIVICPULSE_API_TRUSTED_PROXY_HOPS"] == "0"
    assert not [k for k in env if SECRET_NAMES.search(k)], "secrets belong in `fly secrets`, not fly.toml"
    http = cfg["http_service"]
    assert http["force_https"] is True and http["internal_port"] == int(env["PORT"])
    assert http["checks"][0]["path"] == "/health/ready"
    assert cfg["vm"][0]["memory"] == "1gb"


def test_compose_keeps_api_private_and_passes_secrets_by_reference():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    api, web = compose["services"]["api"], compose["services"]["web"]
    assert "ports" not in api  # reachable only through the web proxy
    for svc in (api, web):
        for key, value in svc.get("environment", {}).items():
            if SECRET_NAMES.search(key):
                assert str(value).startswith("${"), f"{key} must come from the environment"


def test_no_public_rewrite_bypasses_the_proxy():
    cfg = (ROOT / "apps" / "web" / "next.config.ts").read_text(encoding="utf-8")
    assert "rewrites" not in cfg  # /api/v1 goes through app/api/v1/[...path]/route.ts (server-side secrets)
    assert "NEXT_PUBLIC_" not in cfg
