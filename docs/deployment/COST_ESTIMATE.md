# Cost estimate (Milestone 5)

Nothing has been provisioned or purchased. Figures come from `HOSTING_DECISION.md`. Re-check every rate marked
*(verify)* there before approving.

## Monthly estimate

| Item | Staging (auto-stop, light use) | Production (always on) |
|---|---|---|
| Vercel Hobby (web) | $0 | $0 (non-commercial personal use; Pro is $20/user-month if that ever changes *(verify)*) |
| Fly.io shared-cpu-1x, 1 GB (API) | ≈ $0.93 per 100 running hours (prorated from $6.70 for ~730 h) | ≈ $6.70 |
| Fly.io egress | ≈ $0 at portfolio traffic (JSON responses are small; first-boot artifact download is ingress) | ≈ $0–1 |
| Snapshot storage: GitHub Release | $0 | $0 |
| Snapshot storage: Cloudflare R2 (alternative) | $0 (86 MB ≪ 10 GB free; reads ≪ 10 M free) | $0 |
| Paid LLM | $0: disabled (`CIVICPULSE_AI_PAID_ENABLED=false`) | $0 unless separately approved |
| **Total** | **≈ $0–3** | **≈ $7–8** |

## Minimum resources

One Fly.io machine with 1 GB RAM and a shared vCPU. No volume is needed: the snapshot is re-verified on boot and
downloaded again only when the machine's disk is fresh, which is about 86 MB per cold boot. No managed database,
Redis, Kubernetes, GPU or second replica.

## Overage risks and controls

| Risk | Control |
|---|---|
| Fly.io has no hard spending cap by default *(verify)* | One machine (`fly scale count 1`), `--ha=false`, `[[vm]]` fixed at 1 GB, `hard_limit` 32 concurrent requests. Check the billing dashboard for any alert or limit feature. |
| Traffic spike on the API | uvicorn `--limit-concurrency 32` (HTTP 503 beyond), API token-bucket rate limit (600/min per client), AI limits (6/min per client, 2 concurrent overall). All process-local: one instance enforces them. |
| Vercel Hobby usage limits | Hobby pauses features at its limits instead of billing *(verify)*. |
| R2 above the free tier | Charged without a hard cap. At 86 MB per snapshot, about 100 kept snapshots are needed to reach 10 GB. Delete old snapshots per `ROLLBACK.md` retention. |
| Paid AI enabled by mistake | Kill switch defaults off. A paid provider also needs a key, an access token, a budget and prices (fails closed). `scripts/staging_check.py` fails if paid AI is on. |
| Automatic plan upgrades | None of the chosen services upgrade plans automatically. Usage-based billing applies on Fly.io and R2 only. |

## Sleep and restart behaviour

Staging machines stop when idle and start on the next request. Expect roughly 5–15 s for the first response
(boot, snapshot restore and verify, API start; locally restore was 3.75 s and API start 2.8 s). Any restart loses
the in-memory reports, rate-limit counters and daily budget counters (see `ROLLBACK.md`). For production, set
`min_machines_running = 1` to avoid cold starts. That is the always-on cost above.
