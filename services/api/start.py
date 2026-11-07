"""Container entrypoint: restore the pinned data/model snapshot (when configured), then exec uvicorn.

Environment (see docs/deployment/ENVIRONMENT_VARIABLES.md):
  CIVICPULSE_ARTIFACT_URL      https URL or local path of the snapshot archive (unset = bundled sample data)
  CIVICPULSE_ARTIFACT_SHA256   pinned archive SHA-256 (required with the URL; mismatch = refuse to start)
  CIVICPULSE_ARTIFACT_ROOT     where snapshots are installed (default /app/artifacts; a persistent disk avoids
                               re-downloading on restart, an ephemeral one re-verifies and re-downloads once)
  CIVICPULSE_ARTIFACT_TOKEN    optional bearer token for a private artifact URL (removed before the API starts)
  CIVICPULSE_REQUIRE_ARTIFACT  true = refuse to start on the bundled sample (staging/production)
  PORT                         listen port (default 8000)
  CIVICPULSE_API_MAX_CONNECTIONS  uvicorn --limit-concurrency (default 32; excess gets HTTP 503)
  CIVICPULSE_FORWARDED_ALLOW_IPS  peers whose X-Forwarded-Proto/For uvicorn honours (default 127.0.0.1)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes")


def _log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, "component": "start", **fields}), flush=True)


def prepare(env: dict[str, str]) -> list[str]:
    """Restore artifacts into ``env`` (mutated) and return the uvicorn argv. Raises SystemExit on failure."""
    url = env.get("CIVICPULSE_ARTIFACT_URL", "").strip()
    token = env.pop("CIVICPULSE_ARTIFACT_TOKEN", "").strip() or None  # never visible to the API or MCP child
    if url:
        sha = env.get("CIVICPULSE_ARTIFACT_SHA256", "").strip()
        if not sha:
            _log("startup_failed", reason="CIVICPULSE_ARTIFACT_SHA256 is required with CIVICPULSE_ARTIFACT_URL")
            raise SystemExit(2)
        from civicpulse_shared.artifacts import ArtifactError, restore

        started = time.perf_counter()
        try:
            snap = restore(url, Path(env.get("CIVICPULSE_ARTIFACT_ROOT", "/app/artifacts")), sha, token=token)
        except ArtifactError as exc:
            _log("startup_failed", reason=str(exc))
            raise SystemExit(2) from None
        env["CIVICPULSE_DATA_DIR"] = str(snap)
        _log("artifact_ready", snapshot=snap.name, seconds=round(time.perf_counter() - started, 2))
    elif _truthy(env.get("CIVICPULSE_REQUIRE_ARTIFACT")):
        _log("startup_failed", reason="CIVICPULSE_REQUIRE_ARTIFACT=true but CIVICPULSE_ARTIFACT_URL is not set")
        raise SystemExit(2)
    else:
        _log("artifact_skipped", data_dir=env.get("CIVICPULSE_DATA_DIR", "default"))
    port = env.get("PORT", "8000").strip()
    limit = env.get("CIVICPULSE_API_MAX_CONNECTIONS", "32").strip()
    if not port.isdigit() or not limit.isdigit():
        _log("startup_failed", reason="PORT and CIVICPULSE_API_MAX_CONNECTIONS must be integers")
        raise SystemExit(2)
    return [
        "uvicorn",
        "civicpulse_api.main:app",
        "--host",
        "0.0.0.0",  # noqa: S104 (container listens on all interfaces; the platform fronts it)
        "--port",
        port,
        "--workers",
        "1",  # one process: rate limits, budget and report store are process-local by design
        "--limit-concurrency",
        limit,
        "--timeout-graceful-shutdown",
        "20",
        "--no-server-header",
        "--no-access-log",  # the app logs one structured line per request (path only, no query string)
        "--proxy-headers",
        "--forwarded-allow-ips",
        env.get("CIVICPULSE_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    ]


def main() -> None:
    env = dict(os.environ)
    argv = prepare(env)
    os.execvpe(argv[0], argv, env)  # noqa: S606 (fixed argv, no shell)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        sys.exit(exc.code)
