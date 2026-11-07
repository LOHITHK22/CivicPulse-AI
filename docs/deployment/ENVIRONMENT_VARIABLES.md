# Environment variables (deployment)

Secrets are marked **secret**. Set them in the platform's secret store (`fly secrets set`, Vercel project
environment variables), never in files, images or logs. The full API and AI list lives in `.env.example`; this
page covers what a deployment must set.

## API (Fly.io machine / `api` container)

| Variable | Staging value | Notes |
|---|---|---|
| `CIVICPULSE_ARTIFACT_URL` | https URL of the approved snapshot | Unset means the bundled sample. HTTPS only. |
| `CIVICPULSE_ARTIFACT_SHA256` | archive SHA-256 from the sidecar manifest | Required with the URL. A mismatch refuses to start. Not secret, but set it with the URL so the two change together. |
| `CIVICPULSE_ARTIFACT_TOKEN` | unset | **Secret.** Only for a private artifact URL; removed before the API starts. |
| `CIVICPULSE_ARTIFACT_ROOT` | `/app/artifacts` | Install location (the image makes it writable for the non-root user). |
| `CIVICPULSE_REQUIRE_ARTIFACT` | `true` | Refuse to serve the sample. |
| `CIVICPULSE_API_PROXY_SECRET` | 48 random hex characters | **Secret.** Must equal the web's `CIVICPULSE_PROXY_SECRET`. |
| `CIVICPULSE_API_REQUIRE_PROXY_AUTH` | `true` | Proxy-only `/api/*`; fails closed without a secret of at least 32 characters. Disables docs and schema. |
| `CIVICPULSE_API_TRUSTED_PROXY_HOPS` | `0` | Client identity comes from the authenticated proxy, never from raw `X-Forwarded-For`. |
| `CIVICPULSE_API_CORS_ORIGINS` | the frontend origin, e.g. `https://<project>.vercel.app` | Also the allow-list for the AI POST `Origin` check. |
| `CIVICPULSE_AI_ACCESS_TOKEN` | 48 random hex characters | **Secret.** Required on `/api/v1/ai/*`; the web proxy attaches it. |
| `CIVICPULSE_LLM_PROVIDER` / `CIVICPULSE_LLM_MODEL` | `fake` / `deterministic-fake-v1` | Demonstration mode. |
| `CIVICPULSE_AI_PAID_ENABLED` | `false` | Paid AI kill switch. An API key alone never enables paid calls. |
| `CIVICPULSE_OPENAI_API_KEY` | **unset** | **Secret.** Not set for staging. |
| `PORT` | `8000` | uvicorn port (matches `internal_port`). |
| `CIVICPULSE_API_MAX_CONNECTIONS` | `32` | uvicorn `--limit-concurrency`; HTTP 503 beyond. |
| `CIVICPULSE_FORWARDED_ALLOW_IPS` | default `127.0.0.1` | Peers whose `X-Forwarded-Proto` uvicorn honours (scheme only; identity uses the proxy secret). |
| `CIVICPULSE_DUCKDB_MEMORY_LIMIT` / `CIVICPULSE_DUCKDB_THREADS` | `512MB` / `2` | Per DuckDB connection; invalid values refuse to start. |
| `CIVICPULSE_LOG_LEVEL` | `INFO` | Structured JSON logs: one line per request, path only, no query string. |

## Web (Vercel project, root `apps/web`; or the `web` container)

All of these are server-only. None is `NEXT_PUBLIC_*`, so none reaches the browser bundle.

| Variable | Staging value | Notes |
|---|---|---|
| `API_BASE_URL` | `https://civicpulse-api-staging.fly.dev` (placeholder) | Read at runtime by the proxy route handlers. |
| `CIVICPULSE_PROXY_SECRET` | same value as the API's `CIVICPULSE_API_PROXY_SECRET` | **Secret.** |
| `CIVICPULSE_AI_ACCESS_TOKEN` | same value as the API's | **Secret.** Sent only on `/api/v1/ai/*`. |
| `CIVICPULSE_CLIENT_IP_HEADER` | `x-vercel-forwarded-for` *(verify on staging)* | The edge header holding the browser address. Behind an appending load balancer use `x-forwarded-for` (the rightmost entry is used). Unset means no per-client attribution. |

Generate secrets locally, for example `python -c "import secrets; print(secrets.token_hex(24))"`, and paste them
straight into the platform's secret store. Do not paste them into a chat, issue or log.
