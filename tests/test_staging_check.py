"""The external staging check script, driven by an in-process fake frontend (no network)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("staging_check", ROOT / "scripts" / "staging_check.py")
assert spec and spec.loader
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

SEC = {k: "x" for k in sc.HEADERS}


def fake(paid: bool = False, build: str = "B1"):
    def get(method, url, body, headers):
        path = url.split("example.org", 1)[1]
        if method == "POST":
            return 415, {}, b"{}"
        if path == "/api/health/ready":
            return 200, {}, json.dumps({"ready": True, "build_id": build, "fact_rows": 10}).encode()
        if path == "/api/v1/quality":
            return 200, {}, json.dumps({"freshness": {"is_live": False, "latest_created_at": "2026-11-01"}}).encode()
        if path == "/api/v1/ai/status":
            return 200, {}, json.dumps({"provider": "fake", "live_llm": False, "paid_ai": {"enabled": paid}}).encode()
        return 200, SEC, b"{}"

    return get


def test_passes_on_a_safe_deployment(capsys):
    assert sc.main(["https://example.org"], get=fake()) == 0


def test_fails_when_paid_ai_is_on_or_snapshot_differs():
    assert not sc.run("https://example.org", None, fake(paid=True))["ok"]
    r = sc.run("https://example.org", {"build_id": "B0", "fact_rows": 10}, fake())
    assert {c["check"] for c in r["checks"] if not c["ok"]} == {"build_matches_pinned_snapshot"}


def test_refuses_plain_http(capsys):
    assert sc.main(["http://example.org"], get=fake()) == 2
