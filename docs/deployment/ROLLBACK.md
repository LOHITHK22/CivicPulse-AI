# Rollback

An application version and a data/model snapshot are deployed together but rolled back independently. Every
deployment records three values: the Git commit, the image/release, and the snapshot pin
(`CIVICPULSE_ARTIFACT_URL` + `CIVICPULSE_ARTIFACT_SHA256`). Keep them in the release notes or PR.

## API (Fly.io)

```bash
fly releases --config deploy/fly.staging.toml                 # list releases with image refs
fly deploy --config deploy/fly.staging.toml --image <previous image ref> --ha=false   # redeploy a known-good image
```
Or check out the previous commit and run `fly deploy` as in `deploy/fly.staging.toml`.

## Snapshot (data + models)

Snapshots are immutable and keyed by their archive SHA-256, so rolling back means pinning the previous one:
```bash
fly secrets set CIVICPULSE_ARTIFACT_SHA256=<previous sha> --config deploy/fly.staging.toml   # restarts the machine
# and, if the URL changed, set CIVICPULSE_ARTIFACT_URL in [env] and redeploy
```
The container verifies the archive and then serves it. A wrong pin stops start-up instead of serving mixed data.
The warehouse and the model run inside one snapshot always match, so data and models cannot drift apart.

**Retention.** Keep at least the current and previous snapshot (archive plus `.manifest.json`) published. Never
delete or overwrite a snapshot that a release still pins.

## Frontend (Vercel)

Promote the previous production deployment in the Vercel dashboard ("Instant Rollback"), or redeploy the
previous commit. The web app has no data of its own. It must point at an API that still exposes the same
`/api/v1` routes. If the API is rolled back too, roll back the API first, then the frontend.

## What a rollback or restart loses

In-memory investigation reports (links return 404; downloaded report files stay verifiable offline), rate-limit
counters, the AI daily budget counter, and in-flight requests (uvicorn drains for up to 20 s on SIGTERM). No
persistent data is written by the application, so nothing needs restoring beyond the snapshot.

## Verify after rollback

```bash
python scripts/staging_check.py https://<frontend> --manifest <the pinned snapshot's .manifest.json>
```
