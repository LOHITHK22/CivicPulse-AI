# Platform setup (staging): exact steps

Run these only after you approve cost, accounts and snapshot publication (`PRODUCTION_CHECKLIST.md`, Gate 1).
Secret **values** never appear here. Generate each one locally and paste it only into the platform's secret
store:

```bash
python -c "import secrets; print(secrets.token_hex(24))"   # once for PROXY, once for TOKEN
```

## 1. Publish the snapshot (owner, after approval)

```bash
python -m civicpulse_shared.artifacts pack --data-dir data --out dist/artifacts
# confirm archive_sha256 in dist/artifacts/*.manifest.json equals ARTIFACT_MANIFEST.md (b18623fa…)
gh release create snapshot-20261104T194022Z-5da243 dist/artifacts/civicpulse-snapshot-*.tar.gz \
  dist/artifacts/civicpulse-snapshot-*.manifest.json --title "Data snapshot 20261104T194022Z-5da243" \
  --notes "NYC 311 erm2-nwe9 derived snapshot; see DATA_NOTICE.md inside the archive."
```
The asset URL (`https://github.com/LOHITHK22/CivicPulse-AI/releases/download/<tag>/<archive>`) becomes
`CIVICPULSE_ARTIFACT_URL`. For a private alternative, use an R2 bucket with a pre-signed URL; the query string
is never logged.

## 2. API on Fly.io

```bash
fly auth login                                     # interactive, in your own terminal
fly apps create <unique-app-name>                  # then set `app` in deploy/fly.staging.toml
# edit deploy/fly.staging.toml [env]: CIVICPULSE_ARTIFACT_URL, CIVICPULSE_API_CORS_ORIGINS (step 3 origin)
fly secrets set --config deploy/fly.staging.toml --stage \
  CIVICPULSE_API_PROXY_SECRET=<PROXY> CIVICPULSE_AI_ACCESS_TOKEN=<TOKEN> CIVICPULSE_ARTIFACT_SHA256=<archive_sha256>
git checkout <reviewed commit>
fly deploy --config deploy/fly.staging.toml --dockerfile services/api/Dockerfile --ha=false
fly scale count 1 --config deploy/fly.staging.toml --yes
fly logs --config deploy/fly.staging.toml          # expect "artifact_ready", then "Application startup complete"
```
Check from outside: `https://<app>.fly.dev/health/ready` should return 200, and
`https://<app>.fly.dev/api/v1/metrics/kpis` should return **401** (proxy-only mode).

## 3. Web on Vercel (Hobby)

1. Import the GitHub repo, set **Root Directory = `apps/web`**, framework Next.js (auto), Node 22.
2. Project → Settings → Functions → region **Washington, D.C. (`iad1`)**.
3. Environment variables, scoped to the environment you deploy (all server-side; do **not** add a
   `NEXT_PUBLIC_` prefix):
   `API_BASE_URL` = `https://<app>.fly.dev`, `CIVICPULSE_PROXY_SECRET` = `<PROXY>`,
   `CIVICPULSE_AI_ACCESS_TOKEN` = `<TOKEN>`, `CIVICPULSE_CLIENT_IP_HEADER` = `x-vercel-forwarded-for`.
4. Deploy. Put the resulting fixed origin (e.g. `https://<project>.vercel.app`) into the API's
   `CIVICPULSE_API_CORS_ORIGINS`, then redeploy the API. Preview deployments have other origins, so their AI
   Analyst POSTs get 403; the data pages still work.
5. Optional: Vercel Deployment Protection (Hobby: Vercel Authentication on previews) to restrict staging access.

## 4. Verify

```bash
python scripts/staging_check.py https://<project>.vercel.app --manifest dist/artifacts/<snapshot>.manifest.json
```
Then repeat the Security rows of `STAGING_VERIFICATION.md` with `curl`, including one check: send a forged
`X-Vercel-Forwarded-For` header and confirm the report ownership and rate limit still follow your real address.
Record every result, including failures.

## Required variable names (summary)

| Where | Secrets (platform secret store) | Plain configuration |
|---|---|---|
| Fly.io | `CIVICPULSE_API_PROXY_SECRET`, `CIVICPULSE_AI_ACCESS_TOKEN`, `CIVICPULSE_ARTIFACT_SHA256` (+ `CIVICPULSE_ARTIFACT_TOKEN` only for a private URL) | everything in `[env]` of `deploy/fly.staging.toml` |
| Vercel | `CIVICPULSE_PROXY_SECRET`, `CIVICPULSE_AI_ACCESS_TOKEN` | `API_BASE_URL`, `CIVICPULSE_CLIENT_IP_HEADER` |

Never set `CIVICPULSE_OPENAI_API_KEY`, and keep `CIVICPULSE_AI_PAID_ENABLED=false`.
