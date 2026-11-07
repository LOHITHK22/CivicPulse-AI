# Production checklist

Production requires **explicit human approval** at each gate. Nothing here is automated, and no CI workflow
deploys.

## Gate 1: approvals (owner)

- [ ] Hosting and monthly cost approved (`HOSTING_DECISION.md`, `COST_ESTIMATE.md`); rates re-checked on vendor pages.
- [ ] Accounts created by the owner (Fly.io, Vercel). Billing alerts or limits configured where offered.
- [ ] Snapshot publication approved (public GitHub Release, or a private R2 bucket).
- [ ] NYC Open Data terms and attribution re-checked for the 311 data and the DCP borough boundaries.

## Gate 2: staging passes

- [ ] Snapshot published; `archive_sha256` matches `ARTIFACT_MANIFEST.md`.
- [ ] Secrets set in the platform stores only: proxy secret (both sides) and AI access token (both sides). No
      OpenAI key.
- [ ] API deployed from a reviewed commit: `fly deploy --config deploy/fly.staging.toml --dockerfile services/api/Dockerfile --ha=false`.
- [ ] Vercel project with root `apps/web`; server env per `ENVIRONMENT_VARIABLES.md`; `CIVICPULSE_CLIENT_IP_HEADER`
      verified on Vercel.
- [ ] `CIVICPULSE_API_CORS_ORIGINS` set to the exact frontend origin.
- [ ] `scripts/staging_check.py` passes against the staging frontend with the snapshot manifest.
- [ ] Every Security row of `STAGING_VERIFICATION.md` repeated against the staging URLs and recorded.
- [ ] Playwright run against the staging frontend (or the local production build of the same commit).
- [ ] CI and Security workflows green on the commit.

## Gate 3: production configuration

- [ ] Separate production app and secrets (never reuse staging secrets).
- [ ] `min_machines_running = 1` for production, or cold starts accepted and documented; still exactly one machine.
- [ ] Paid AI off: `CIVICPULSE_AI_PAID_ENABLED=false`, `CIVICPULSE_LLM_PROVIDER=fake`.
- [ ] UI labels intact: demonstration mode, research-only closure-delay model, unverified anomaly flags,
      snapshot cutoff (2026-11-01), not real-time.
- [ ] Uptime and freshness monitor: `scripts/staging_check.py` on a schedule (e.g. a GitHub Actions cron with
      the frontend URL as a variable; no secrets needed). Alert on failure via the workflow notification.
- [ ] Error monitoring: platform logs (structured JSON; `startup_failed`, `unhandled error` and MCP errors are
      single searchable lines). A paid APM is not required at this traffic.
- [ ] Rollback rehearsed once on staging (`ROLLBACK.md`).

## Known production limitations (accept or fix before launch)

* Rate limits, AI budget, concurrency gate and reports are process-local. Correct with one instance only.
* Reports do not survive restarts. Links expire on restart or after the TTL.
* The app CSP covers framing, base, form and object only. A full `script-src` policy with nonces is not
  implemented.
* No live LLM has been verified. Enabling one needs a separately approved smoke test (`scripts/llm_smoke_test.py`).
