# Staging verification (Milestone 5)

**Cloud staging: NOT DEPLOYED (blocked).** No Fly.io or Vercel account, credentials or CLI is available in this
environment, provisioning Fly.io needs a payment card, and publishing the snapshot needs your approval. Nothing
below ran against a cloud URL.

What did run is a **production-like local staging** on 2026-11-06 (Docker 27.5.1 on Windows/WSL2). It used the
same images and the same wiring as `deploy/fly.staging.toml`:
* the full snapshot installed through the artifact path, with the pinned SHA-256 and `CIVICPULSE_REQUIRE_ARTIFACT=true`;
* a proxy-only API with a random proxy secret and AI access token (generated locally, never printed, deleted after);
* `CIVICPULSE_CLIENT_IP_HEADER=x-forwarded-for` set on the web container to simulate the edge;
* a 1 GiB API memory cap and a 512 MiB web cap; DuckDB capped at 512 MB and 2 threads.

## Results (local production-like staging)

| Area | Check | Result |
|---|---|---|
| Artifacts | first boot: restore + deep verify of the 85.7 MB archive inside the container | PASS, 3.75 s |
| | restart: snapshot reused, not re-copied | PASS, 0.0 s |
| | wrong pinned SHA-256 → container exits 2, `startup_failed` log | PASS |
| | `CIVICPULSE_REQUIRE_ARTIFACT=true` without a URL → exits 2 | PASS |
| Backend | `/api/health/ready` via proxy: ready, 2,511,293 rows, build `20261104T194022Z-5da243` | PASS |
| | filters, KPIs, volume, geo, quality, models, forecast evaluation, forecast, backtest, delay-risk summary/list/explanations, anomalies, anomaly series, requests, CSV export | PASS (all 200 with valid parameters) |
| MCP | investigation through the proxy: `compare_boroughs` over stdio, protocol 2025-11-25, validation ok, 11 claims | PASS, 3.4 s |
| | 3 more investigations (MCP subprocess started and cleaned up each time) | PASS (200) |
| Frontend | `/`, `/geo`, `/explorer`, `/forecast`, `/analyst`, `/anomalies`, `/delay-risk`, `/quality`, `/models`, `/about` | PASS (200) |
| | Playwright (production build, real API, desktop + mobile, 1 worker) | PASS, 48/48 |
| Security | direct API call without proxy secret / with forged secret | PASS, 401 / 401 |
| | `/docs`, `/openapi.json` on the API | PASS, 404 |
| | report route with proxy secret but no bearer token | PASS, 401 |
| | browser-sent wrong `Authorization` is dropped; proxy injects the real token | PASS (investigation 200) |
| | report owner (client A): JSON / Markdown / HTML | PASS, 200 / 200 / 200 |
| | report requested by client B | PASS, 404 |
| | oversized body: 70 KB (proxy cap) / 9 KB (API cap 8 KB) | PASS, 413 / 413 |
| | invalid JSON / wrong content type / foreign Origin | PASS, 422 / 415 / 403 |
| | dot-segment escape (`/api/v1/..%2F..%2Fdocs`, `%2e%2e`) | PASS, 404 |
| | per-client AI rate limit: 6 × 200 then 429; another client unaffected (200) | PASS |
| | paid AI: `/api/v1/ai/status` → provider `fake`, `live_llm` false, `paid_ai.enabled` false | PASS |
| | proxy secret / token values or their variable names in `.next/static` and the HTML | PASS, 0 matches |
| | HTML headers: HSTS, CSP (frame-ancestors, base-uri, form-action, object-src), X-Frame-Options | PASS |
| | MCP not exposed: only web port 3000 published; API `expose`-only; MCP has no port | PASS |
| Reliability | API restart → ready through proxy | PASS, 4.75 s |
| | report after restart | 404, as documented (in-memory store) |
| | API stopped: data route → JSON 502 `backend_unavailable`; pages still 200 | PASS |
| | graceful stop (SIGTERM) | PASS, 1.66 s, exit 0 |
| Resources | API memory: after investigation / peak (3 investigations + CSV export) | 230 MiB / 373 MiB |
| | web memory | 85 MiB |
| | container start → healthy (healthcheck-interval bound) / restore → app ready | 16.4 s / 2.8 s |

## Not verified (needs real staging)

* Real HTTPS, Fly.io health checks, auto-stop cold starts and download from the real artifact URL.
* `CIVICPULSE_CLIENT_IP_HEADER` on Vercel: confirm `x-vercel-forwarded-for` carries the browser address and is
  not client-controlled.
* Vercel function timeout and region latency to `ewr`.
* External run of `scripts/staging_check.py` (unit-tested only).

## When staging exists

```bash
python scripts/staging_check.py https://<staging-frontend> --manifest dist/artifacts/<snapshot>.manifest.json
```
Then repeat the Security rows above against the staging URLs with `curl`. Record the results here, including
failures.
