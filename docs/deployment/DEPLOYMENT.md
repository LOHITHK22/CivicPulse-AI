# Deployment

Milestone 4 readiness and blockers: [`DEPLOYMENT_READINESS.md`](DEPLOYMENT_READINESS.md).

## Milestone 2 status
- **Docker: verified locally on 2026-11-04** (Docker Desktop 27.5.1).
  - `services/api/Dockerfile` is multi-stage. The builder installs `.[ml]` and trains
    smoke-scale models on the sample. The runtime has core dependencies only and runs as a
    non-root user.
  - `docker compose up --build` serves the web app with the API behind its proxy.
  - Images set `CIVICPULSE_REPO_ROOT=/app`. A non-editable install cannot locate `configs/`
    from the package path.
- **Models:** `civicpulse-model train` needs the `ml` extra (LightGBM needs OpenMP:
  `libgomp1` on Debian). The API serves precomputed outputs from `data/models/` and never
  trains.
- **Public demo:** still not deployed, and no cloud resources have been created.

## Milestone 1 status
- **Local developer mode: working and verified** (Windows 11, Python 3.12, Node 22).
- **Docker**: `services/api/Dockerfile`, `apps/web/Dockerfile` and `docker-compose.yml` are written
  but **not yet built or verified**, because the Docker daemon was not running during
  Milestone 1.
- **Public demo**: not deployed. Hosting research and cost analysis are Milestone 5 work. No
  cloud resources have been created.

## Local setup
```bash
py -3.12 -m venv .venv                       # Windows (python3.12 -m venv .venv elsewhere)
.venv/Scripts/python -m pip install -e ".[dev]"
cd apps/web && npm ci && cd ../..

# Option A: full real snapshot (network, ~5 min, ~770k rows)
.venv/Scripts/civicpulse extract --mode backfill
.venv/Scripts/civicpulse build
# Option B: offline, from the committed 969-record real sample
.venv/Scripts/python scripts/build_sample_warehouse.py --data-dir .sample-data
export CIVICPULSE_DATA_DIR=.sample-data

.venv/Scripts/python -m uvicorn civicpulse_api.main:app --port 8000
cd apps/web && npx next build && npx next start -p 3000      # http://localhost:3000
```

## Configuration that matters in production
| Setting | Notes |
|---|---|
| `API_BASE_URL` (web) | Read at **runtime** by the server-side proxy route handlers (Milestone 5; previously baked in at build by rewrites). See `ENVIRONMENT_VARIABLES.md`. |
| `CIVICPULSE_API_TRUSTED_PROXY_HOPS` | Default 0 means `X-Forwarded-For` is ignored. The Next.js proxy forwards client-supplied XFF verbatim, so trusting it would allow rate-limit evasion. Set it to N only when N edge proxies **append** the client IP. |
| `CIVICPULSE_API_RATE_LIMIT_PER_MINUTE` | Default 600, per client key. Without trusted hops, all traffic via the web proxy shares one bucket. One dashboard load makes ~10 API calls. |
| `CIVICPULSE_API_CORS_ORIGINS` | Only needed if browsers call the API directly (they don't in the default setup). |
| `CIVICPULSE_SOCRATA_APP_TOKEN` | Recommended for scheduled or incremental runs (higher throttling limits). |

## Scheduled refresh
Not configured. The pipeline has been shown idempotent (real incremental run plus rebuild, and
deterministic-rebuild tests). Scheduling still waits for a chosen deployment target and its
cost/approval (Milestone 5).
