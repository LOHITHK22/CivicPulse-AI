# Deployment architecture (Milestone 5)

```
Browser ──HTTPS──► Vercel edge ──► Next.js (apps/web)
                                    │  pages: server/client components (no secrets)
                                    │  app/api/v1/[...path], app/api/health/ready  ← server-side proxy
                                    │    + X-CivicPulse-Proxy-Auth: <CIVICPULSE_PROXY_SECRET>
                                    │    + X-CivicPulse-Client: <browser IP from the edge header>
                                    │    + Authorization: Bearer <CIVICPULSE_AI_ACCESS_TOKEN>   (/api/v1/ai/* only)
                                    │    − browser Authorization, cookies, X-CivicPulse-* (dropped)
                                    ▼ HTTPS
                     Fly.io machine (1 GB) ── services/api/start.py
                                    │  1. restore pinned snapshot (SHA-256) → /app/artifacts/snapshots/<id>
                                    │  2. exec uvicorn: 1 worker, --limit-concurrency 32, graceful shutdown
                                    ▼
                     FastAPI (proxy-only mode: /api/* without the secret → 401; /docs, /openapi.json off)
                       ├─ DuckDB in-memory views over the snapshot's Parquet (memory/threads capped)
                       └─ AI analyst → MCP server as a stdio subprocess per investigation (no network port)
```

## Trust boundaries

| Boundary | Control |
|---|---|
| Browser → Next.js | Same origin. HSTS, frame denial, nosniff, nonce-free CSP subset. The client bundle contains no API URL, secret or token (checked: 0 matches of the secret values or their variable names in `.next/static`). |
| Next.js → API | Shared proxy secret (constant-time comparison). The API trusts the forwarded client address **only** on requests carrying the secret, and only if it is a bare IP literal. |
| Public internet → API URL | Proxy-only mode returns 401 for every `/api/*` request without the secret. Health endpoints stay open for platform checks. Docs and schema are disabled. |
| API → MCP | stdio subprocess with a minimal environment (no API key). Allow-listed read-only tools, per-call timeouts. |
| Artifact source → container | HTTPS only; archive SHA-256 pinned in configuration; safe extraction (no absolute paths, `..` or links, only listed files); per-file SHA-256; atomic install. A mismatch refuses to start. |

## Client identity, rate limits and report ownership

* The edge sets the browser address in a header (`CIVICPULSE_CLIENT_IP_HEADER`). The proxy takes its rightmost
  entry and forwards it as `X-CivicPulse-Client`. The API uses `via-proxy:<ip>` as the identity for rate limits
  and report ownership.
* `CIVICPULSE_API_TRUSTED_PROXY_HOPS` stays `0`: `X-Forwarded-For` is never trusted on its own.
* If no client header is configured, all browsers share the proxy's identity. That is safe but coarse: shared
  limits, and reports visible to any user behind the same proxy.
* Verified locally: a report created by client A returns 404 to client B. Client A hitting the per-client AI
  limit (429) does not throttle client B.

## Process-local state (one instance by design)

The rate limiter, AI daily budget, concurrency gate and report store live in the API process. With one machine
these limits hold for the whole deployment. They are **not** global across replicas, so no replicas are
configured. A restart resets them and drops stored reports: a report URL returns 404 after a restart (verified).
Reports are not persistent. A user can download the report file and re-verify it offline.

## Configuration files

`deploy/fly.staging.toml` (API) · `apps/web` as the Vercel project root (no `vercel.json` needed) ·
`docker-compose.yml` (local, same wiring) · `services/api/start.py` · `services/api/Dockerfile`.
