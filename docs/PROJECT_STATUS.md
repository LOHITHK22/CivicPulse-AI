# Project status

Last updated: 2026-11-06. Current milestone: **5, deployment preparation, awaiting review** (branch
`feature/milestone-5-deployment`, PR to `main`, not merged). **Cloud staging is not deployed**: it needs an
approved account, payment card and snapshot publication. Milestones 1–4 are merged into `main`.

## Milestone 5: Deployment preparation

| Capability | Status | Evidence |
|---|---|---|
| Hosting decision and cost (Vercel Hobby + one Fly.io 1 GB machine; ≈ $0–3 staging, ≈ $7–8 production) | Researched, **not provisioned** | `docs/deployment/HOSTING_DECISION.md`, `COST_ESTIMATE.md` |
| Snapshot artifacts (pack, SHA-256 manifest, pinned restore, safe extraction, atomic install, reuse) | Implemented, tested; full snapshot packed locally (69.0 MB, coordinates removed, data notice embedded), **not published** | `docs/deployment/ARTIFACT_MANIFEST.md`, `tests/test_artifacts.py` |
| Container entrypoint (restore, fail closed, 1 worker, concurrency cap, graceful shutdown, non-root) | Implemented, tested | `services/api/start.py`, `services/api/tests/test_start.py` |
| Server-side Next.js proxy (token injection, proxy secret, client address, credential stripping) | Implemented, tested | `apps/web/lib/server/proxy.ts`, `proxy.test.ts` |
| Proxy-only API mode; client attribution and report ownership behind the proxy | Implemented, tested | `services/api/tests/test_api_proxy.py` |
| Production-like local staging (Docker, full snapshot, proxy-only, 1 GiB cap) | Verified; peak API memory 373 MiB | `docs/deployment/STAGING_VERIFICATION.md` |
| CI: container build + staging-like smoke | Added | `.github/workflows/ci.yml` (`container` job) |
| Cloud staging / production | **Not deployed** (blocked on approval and accounts) | `docs/deployment/PRODUCTION_CHECKLIST.md` |

### Verification (2026-11-06)
- `pytest`: 369 passed, 3 deselected (opt-in `live` tests); ruff check, ruff format, `mypy --strict` clean.
- Frontend: tsc, eslint, `npm run test:unit` (15/15), `next build` clean; Playwright 48/48 (1 worker).
- Docker Compose (production-like overlay): all checks in `STAGING_VERIFICATION.md`.
- gitleaks 8.30.1: no leaks in branch commits, full history, or tracked + untracked working-tree files.

### Known limitations (Milestone 5)
- No cloud deployment; real HTTPS, cold starts, Vercel client-IP header and artifact download are unverified.
- Limits and reports remain process-local; one instance only. Reports do not survive restarts.
- The app CSP omits `script-src`. No live LLM is verified; paid AI stays disabled.

## Milestone 4: Product completion

| Capability | Status | Evidence |
|---|---|---|
| Semantic evidence validation (structured claims, derived values recomputed, prose without numbers) | Implemented, tested (fake provider) | `docs/evaluation/SEMANTIC_GROUNDING.md` |
| Geographic Intelligence (MapLibre, DCP boundaries, volume/change modes, ranking table, detail + trend, caveats) | Implemented, browser-verified desktop + mobile | `apps/web/tests/geo.spec.ts`, `docs/screenshots/geo.png` |
| Investigation reports (JSON schema v1, Markdown, print HTML, checksums, offline re-verification) | Implemented, tested | `packages/agent/tests/test_report.py` |
| AI Analyst UX (Demonstration Mode banner, verified claims, copy verified answer, report export, trace with validation/cutoff/model versions) | Implemented, browser-verified | `apps/web/tests/analyst.spec.ts` |
| MCP performance | Measured; per-request stdio kept; pool **planned, not implemented** | `docs/performance/MCP_BENCHMARKS.md` |
| Security (paid-AI kill switch, fail-closed config, concurrency caps, injection tests) | Implemented, tested; limits are **process-local** | `docs/security/AI_THREAT_MODEL.md` |
| Real-LLM smoke test | Script ready, mock-tested; **real run BLOCKED** (no authorization, no credentials) | `scripts/llm_smoke_test.py` |
| Deployment | **Not deployed**; readiness and blockers documented | `docs/deployment/DEPLOYMENT_READINESS.md` |

### Verification (2026-11-05/06)
- `pytest`: 344 passed, 3 deselected (`live` NYC Open Data tests, opt-in); ruff check, ruff format, `mypy --strict` clean.
- Frontend: tsc, eslint, `npm run test:unit` (9/9), `next build` clean.
- Playwright: 48/48 (desktop + mobile, **1 worker** because of ~1.5–2 GB free RAM; full parallelism not run
  locally). No browser-facing code changed after that run.
- Agent evaluation: 70/70 (fake provider, real stdio MCP).
- Docker Compose: images build; health, geo, investigation over in-container stdio MCP, reports: OK.
- gitleaks 8.30.1: 0 findings in branch and full history; 0 in tracked files (226 matches only in ignored
  `.venv` and `.next` build output).

### Known limitations (Milestone 4)
- No live LLM has been run; all AI results use the deterministic fake provider.
- Rate limits, budget, concurrency gate and report store are per process, not global.
- Each investigation spends ~2.3 s on MCP subprocess lifecycle (measured); a session pool is not implemented.
- The browser has no access token; with `CIVICPULSE_AI_ACCESS_TOKEN` set, the UI needs server-side token injection.
- DQ040/DQ042 data-quality warnings are unchanged and still reported.

## Milestone 3: Real MCP server + evidence-grounded AI analyst

| Capability | Status | Evidence |
|---|---|---|
| AI readiness audit before exposing models to an agent | Done. 42/42 artifact checks reproduce; **closure-delay numeric predictions disabled** for the agent (historical status not reliably reconstructable) | `docs/evaluation/AI_READINESS_AUDIT.md` |
| MCP server, official SDK `mcp` 2.3, 10 typed read-only tools, 3 resources, audit log | Done | `docs/mcp/TOOLS.md`, `services/mcp/` |
| MCP client + protocol verification (stdio, both protocol versions; token-protected HTTP) | Done | `docs/mcp/PROTOCOL_VERIFICATION.md` |
| Orchestrator: provider abstraction (fake / OpenAI-compatible), allow-list + schema validation, tool/token/time/cost budgets, evidence IDs, deterministic grounding, bounded regeneration, verified fallback | Done | `docs/mcp/ARCHITECTURE.md`, `packages/agent/` |
| API: `POST /api/v1/ai/investigations` (only non-GET route), `GET /api/v1/ai/status`; Origin, size, strict JSON, rate limits, optional token | Done | `services/api/tests/test_api_ai.py` |
| AI Analyst page: suggestions, answers with clickable evidence, charts, freshness, trace panel, model warnings, copy / retry / new / clear, loading and error states, keyboard and mobile | Done | `apps/web/tests/analyst.spec.ts`, `docs/screenshots/analyst.png` |
| Evaluation: 60 deterministic scenarios on the full snapshot via real stdio MCP | **60/60 with the FAKE provider** (first run 56/60; fixes disclosed). **No real-LLM evaluation run.** | `docs/evaluation/AGENT_EVAL.md` |
| Threat model | Done | `docs/security/AI_THREAT_MODEL.md` |
| Regression | pytest 244 passed (3 live tests deselected); Playwright 37/37 on the full snapshot; ruff, mypy, tsc, eslint, `next build` clean | this file, §Verification |
| Docker / compose | **Verified locally** (Docker 27.5.1): images build; in-container API spawns the MCP server over stdio and answers through the web proxy | §Verification |
| Secret scan | detect-secrets 1.5.0: 0 findings in the 38 changed files | |

### Verification (2026-11-04)
- `pytest`: 244 passed, 3 deselected (`live`). Agent: 51 unit + 20 orchestrator; API AI: 10; MCP: 37.
- Playwright (4 workers, real API + Next production build, fake provider): 37 passed, including axe
  checks on `/analyst` (empty and with an answered, expanded investigation) and mobile overflow.
- `scripts/agent_eval.py`: 60/60, median 2.15 s per investigation.
- Docker compose (bundled sample): `/api/v1/ai/status` 200; an investigation returned `answered`,
  MCP `civicpulse-mcp` 0.3.0, protocol 2025-11-25, stdio; `/analyst` 200.

### Known limitations (Milestone 3)
- **No live LLM has been tested.** The default provider is a deterministic rule-based fake. The
  OpenAI-compatible provider is tested only against a mocked HTTP transport. Paid testing needs
  explicit authorization and a key.
- Grounding verifies that numbers exist in cited evidence, not that prose attributes them correctly.
- Input screening is regex-based and bypassable by paraphrase; containment relies on the read-only
  tool surface, budgets and output validation.
- Rate limits and the cost ceiling are in-process (single instance). The cost ceiling needs
  per-token prices configured to limit spend.
- The analyst endpoint is anonymous unless `CIVICPULSE_AI_ACCESS_TOKEN` is set; it must be set (and
  injected server-side) before any public deployment.
- Each investigation spawns an MCP process (~1.5–2 s overhead).
- Answers describe a historical snapshot (cutoff 2026-11-01 02:04:40 NYC), never live operations.

## Milestone 2: Predictive modeling (branch `feature/milestone-2-predictive`)

| Capability | Status | Evidence |
|---|---|---|
| History expanded to 2023-01-01 (6 resumable half-year backfills plus an incremental) | Done | 1,738,141 new rows. Every window re-counted live and matching. Contiguous coverage. M1 files untouched. `docs/data/MANIFEST.md` |
| Source freshness check on actual rows (DQ042) | Done | Warns: newest row version is 2026-11-02 02:38 UTC |
| Daily 7-day forecasts (naive, seasonal naive, 28-day mean, LightGBM), rolling-origin backtest, conformal intervals | Done | `docs/modeling/FORECASTING.md`, leakage tests |
| Closure-delay risk (majority, logistic regression, LightGBM), maturity cutoff, embargoed chronological splits, calibration | Done, **weak model** | `docs/modeling/DELAY_RISK.md` |
| Anomaly detection (seasonal MAD, rolling-median comparator), synthetic injection evaluation | Done | `docs/modeling/ANOMALIES.md` |
| Explainability (coefficients, gain, permutation, TreeSHAP) with a causality caveat | Done | Model docs, `/api/v1/models/{name}/explanations` |
| Read-only model API (10 endpoints, bounded inputs, no training or execution endpoints) | Done | `services/api/tests/test_api_predictions.py` |
| Prediction Lab UI: Forecasts, Delay Risk, Anomalies, Model Evaluation | Done. 28/28 Playwright on the real snapshot and on the sample | `apps/web/tests/prediction-lab.spec.ts`, screenshots in `docs/screenshots/` |
| Reproducibility | Done | Two full training runs on the same warehouse give identical metrics (`docs/evaluation/MODEL_EVALUATION.md`) |
| Local secret scan | Done | detect-secrets 1.5.0: 0 findings in tracked files and in each commit |
| Docker / compose | **Verified locally** (Docker 27.5.1): API image builds, ML-free runtime, healthy; compose serves all pages | `docs/evaluation/MODEL_EVALUATION.md` §6 |
| CI | Updated (`.[dev,ml]`, sample models, modeling coverage). **Never run on GitHub** (no remote) | `.github/workflows/ci.yml` |

### Known limitations (Milestone 2)
- **Forecasting:**
  - The forecast is not significantly better than the naive baseline (bootstrap CI includes 0).
  - Heat/hot water is not improved, because there are no weather inputs.
  - 90% intervals cover 86.4% on test.
- **Delay risk:**
  - The 2026 label drift (heat not-closed-in-7-days rate 3–14% vs 0.1–0.5% before) leaves
    probabilities under-calibrated.
  - Within-category ranking is at chance.
  - Snapshot fields are used because point-in-time history cannot be reconstructed.
- **Anomalies:**
  - There are no real labels. Recall is about 30% on synthetic injections.
  - The October heating-season onset produces many alerts.
- **Data:** the snapshot is stale upstream (no rows after 2026-11-01 02:06 created time). It is
  not live.

## Milestone 1: Foundation and real analytics

| Capability | Status | Evidence |
|---|---|---|
| Research (dataset, API, biases, versions, MCP SDK) | Done | `docs/research/SOURCES.md` |
| Architecture decision record | Done | `docs/architecture/SYSTEM_DESIGN.md` (ADR-001) |
| Ingestion: pagination, filters, retries/backoff/jitter, 429/Retry-After, timeouts, schema validation, checkpoints/resume, watermark + lookback, failed-batch log, lineage | Done | 23 ingestion tests; real backfill (772,357 rows) and real incremental run (214,264 rows) |
| Warehouse (DuckDB + Parquet): fact, dims, aggregates, rejects, quality history, model tables (empty) | Done | 6 transform tests; deterministic-rebuild test |
| Data-quality rules + report | Done | 30 rules; `docs/data/QUALITY_RULES.md` |
| Analytics service + FastAPI endpoints | Done | 30 analytics tests (vs independent Polars), 31 API tests |
| Dashboard: Overview, Explorer, Data Quality, About | Done | 18 Playwright tests (desktop + mobile, axe accessibility) |
| Pages for Milestones 2–4 | Placeholders, explicitly labelled "Planned", no fake data | E2E test asserts the label |
| CI workflows (lint, types, tests, build, E2E, secret scan, dependency audits) | Written; **not yet run on GitHub** (no remote) | `.github/workflows/` (E2E job reproduced locally against the sample warehouse) |
| Docker / compose | Written; not verified during M1 (Docker daemon unavailable). **Verified in M2**, see above | `services/api/Dockerfile`, `apps/web/Dockerfile` |
| Secret scan | **Not run locally** (gitleaks not installed). Runs in CI once pushed. Manual review: no secrets in the repo. | |

### Known limitations
- The data is a **snapshot** (newest request 2026-11-01 02:04 NYC time). It is not live.
- Only 12 months are loaded, so seasonality can't be separated from trend.
- The "categories with unusual increases" KPI, historical-vs-predicted charts and anomaly overlays
  are **not computed** until Milestone 2.
- Without trusted proxy configuration, rate limiting is effectively global behind the web proxy.
- The source dataset was **51.7 h stale** at the last extraction check (rule DQ041 warns). This is
  an upstream issue.
- Licensing terms page could not be retrieved during research. Attribution is applied
  conservatively, and the terms need re-verifying before public launch.

## Milestone 5: not started
- Hosting research, deployment, case study, evidence-based resume bullets.
