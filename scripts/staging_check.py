"""Check a deployed CivicPulse AI frontend (staging or production) from the outside. Read-only, no secrets.

Verifies through the public frontend origin only (the way a browser sees it):
  * readiness (/api/health/ready) and the served warehouse build matches the pinned snapshot manifest
  * dataset cutoff and freshness are reported and labelled as a snapshot (not live)
  * analytics, geo, model evaluation, anomaly and data-quality endpoints answer 200
  * the AI analyst is in deterministic demonstration mode with paid AI disabled
  * security headers on the HTML; a POST without JSON content type is refused

Usage:
    python scripts/staging_check.py https://<frontend-host> [--manifest dist/<snapshot>.manifest.json]
Exit 0 = all checks passed, 1 = a check failed, 2 = usage error. Prints one JSON document. Suitable for a
scheduled uptime/freshness monitor (e.g. a GitHub Actions cron) once staging exists.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

Getter = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, dict[str, str], bytes]]
ENDPOINTS = [
    "/api/v1/meta/filters",
    "/api/v1/metrics/kpis",
    "/api/v1/geo/boroughs",
    "/api/v1/models/forecast/evaluation",
    "/api/v1/predictions/delay-risk/summary",
    "/api/v1/anomalies",
    "/api/v1/quality",
]
HEADERS = ["strict-transport-security", "x-frame-options", "x-content-type-options", "content-security-policy"]


def http(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(  # noqa: S310 (operator-supplied https URL, checked in main)
        url, data=body, method=method, headers={"User-Agent": "civicpulse-check/1", **headers}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (operator-supplied https URL)
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def run(base: str, manifest: dict[str, Any] | None, get: Getter = http) -> dict[str, Any]:
    base = base.rstrip("/")
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **detail: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), **detail})

    status, _, body = get("GET", base + "/api/health/ready", None, {})
    ready = json.loads(body) if status == 200 else {}
    check("ready", status == 200 and ready.get("ready") is True, status=status, build_id=ready.get("build_id"))
    if manifest:
        check("build_matches_pinned_snapshot", ready.get("build_id") == manifest.get("build_id"))
        check("row_count_matches_snapshot", ready.get("fact_rows") == manifest.get("fact_rows"))
    for path in ENDPOINTS:
        s, _, b = get("GET", base + path, None, {})
        check(f"GET {path}", s == 200, status=s)
        if path == "/api/v1/quality" and s == 200:
            fresh = json.loads(b).get("freshness") or {}
            check(
                "freshness_reported_as_snapshot",
                fresh.get("is_live") is False and bool(fresh.get("latest_created_at")),
                dataset_cutoff=fresh.get("latest_created_at"),
                data_lag_hours=fresh.get("data_lag_hours"),
            )
    s, _, b = get("GET", base + "/api/v1/ai/status", None, {})
    ai = json.loads(b) if s == 200 else {}
    check(
        "ai_demo_mode_paid_disabled",
        s == 200 and ai.get("live_llm") is False and (ai.get("paid_ai") or {}).get("enabled") is False,
        provider=ai.get("provider"),
    )
    s, h, _ = get("GET", base + "/", None, {})
    check("home_page", s == 200, status=s)
    check("security_headers", all(k in h for k in HEADERS), missing=[k for k in HEADERS if k not in h])
    s, _, _ = get("POST", base + "/api/v1/ai/investigations", b"x", {"Content-Type": "text/plain"})
    check("non_json_post_refused", s in (401, 403, 415), status=s)
    return {"base": base, "ok": all(c["ok"] for c in checks), "checks": checks}


def main(argv: list[str] | None = None, get: Getter = http) -> int:
    ap = argparse.ArgumentParser(description="External check of a deployed CivicPulse AI frontend.")
    ap.add_argument("base_url")
    ap.add_argument("--manifest", help="snapshot sidecar manifest to compare the served build against")
    a = ap.parse_args(argv)
    if not a.base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        print(json.dumps({"ok": False, "error": "base URL must be https (or localhost)"}))
        return 2
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8")) if a.manifest else None
    result = run(a.base_url, manifest, get)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
