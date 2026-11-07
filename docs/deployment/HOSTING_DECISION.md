# Hosting decision (Milestone 5)

Status: **recommendation prepared; nothing provisioned.** Prices were researched on 2026-11-06 from vendor pages
and third-party summaries (sources below). Several third-party figures disagree with each other, so every
number marked *(verify)* must be checked on the vendor page before you approve spending.

## What the application actually needs (measured, not assumed)

Measured locally in Docker 27.5.1 with the full snapshot (2,511,293 rows), a 1 GiB container cap, DuckDB capped
at 512 MB and 2 threads (`STAGING_VERIFICATION.md`):

| Quantity | Measured |
|---|---|
| Snapshot archive (warehouse + state + active model run, coordinates removed) | 69.0 MB (40 files, 72.9 MB unpacked) |
| API memory after warm-up + one investigation | 230 MiB |
| API memory, peak during 3 investigations (MCP subprocess each) + CSV export | **373 MiB** |
| Web (Next.js) memory | 85 MiB |
| Start: artifact restore + verify inside container (first boot) | 3.75 s; reuse on restart 0.0 s |
| Restart → ready through the proxy | 4.75 s |
| Investigation through the proxy (fake provider, stdio MCP) | 3.4 s |

So the backend needs one always-available Python container with **~1 GB RAM** (512 MB leaves almost no headroom
over the 373 MiB peak) and ~100 MB of local disk. No database server, queue, GPU or second replica is needed.

## Options compared

| | Vercel Hobby | Fly.io | Render | Railway | Cloudflare R2 |
|---|---|---|---|---|---|
| Role | frontend | backend container | backend container | backend container | artifact storage |
| Price for our size | $0 | shared-cpu-1x 1 GB ≈ **$6.70/mo** always-on *(verify)*; billed per second, $0 while stopped | Starter 512 MB $7/mo; Free 512 MB / 0.1 CPU sleeps after 15 min *(verify; plans renamed Aug 2026)* | Hobby $5/mo incl. $5 usage; RAM $10/GB-mo, CPU $20/vCPU-mo, egress $0.05/GB | 10 GB-month free, egress free |
| Free tier | yes (personal, non-commercial) | trial only for new orgs *(verify)* | yes, sleeps, 512 MB | trial credit | yes |
| Docker | n/a (Next.js native) | yes | yes | yes | n/a |
| Fits 1 GB need | n/a | yes | Starter too small; next tier much pricier *(verify)* | yes (usage-based) | n/a |
| Cold start | serverless functions | machine start ≈ boot + restore (~5–15 s) if auto-stop | Free tier wake-up | none if always on | n/a |
| Persistent disk | none | volumes $0.15/GB-mo (not needed) | paid disks | volumes | object store |
| HTTPS, health checks, rollback | yes | yes (`fly releases`, image rollback) | yes | yes | n/a |
| Limits that matter | 60 s max function duration; non-commercial use | no hard spending cap by default *(verify)* | 512 MB RAM | no hard cap; usage billing | charges above free tier, no hard cap |
| Card required | no | yes | not for Free | yes *(verify)* | yes for R2 activation *(verify)* |

## Decision

**Frontend: Vercel Hobby. Backend: one Fly.io `shared-cpu-1x` machine with 1 GB in `iad`. Data and models: the
pinned snapshot archive as a GitHub Release asset, with Cloudflare R2 as the private alternative.**

* **Why Vercel for the web app.** Next.js runs there natively with HTTPS, preview deployments and instant
  rollback at $0. The proxy route handlers need well under the 60 s limit (investigation 3.4 s).
  A portfolio project fits the Hobby plan's non-commercial terms; commercial use would need Pro.
* **Why Fly.io for the API.** It is the cheapest option measured that gives 1 GB with predictable per-second
  billing. Staging can auto-stop to near-zero cost. Render's $7 Starter has 512 MB, below a safe margin over the
  373 MiB peak. Railway is a close second: usage-based, similar cost, and no stop-to-zero mode needed.
* **Why a single instance.** Rate limits, the daily budget, the concurrency gate and the report store are
  process-local (`ARCHITECTURE.md`). One instance keeps them correct. Multiple replicas would need a shared store
  (e.g. Redis) first and are **not** configured.
* **Why a GitHub Release for the snapshot.** It is free, versioned, served over HTTPS and needs no new account.
  The snapshot is derived from public NYC Open Data, so a public asset is acceptable. Publishing it is still an
  outward-facing act and **needs your approval**. Use R2 (private bucket plus a pre-signed URL) if the snapshot
  should not be public.

### One host for both, considered and rejected

Running the web and API containers together on Fly.io (two machines on the private `.internal` network) would
keep the API off the public internet. It would cost roughly double, and HTTPS, previews and caching for the
frontend would be ours to manage. The chosen split exposes the API URL publicly, so the API runs in
**proxy-only mode**: every `/api/*` request must carry the proxy secret, and docs and schema are disabled. A
public URL is therefore not an open API.

## Recheck (2026-11-06, release preparation)

* **Fly.io.** shared-cpu-1x with 1 GB is about **$6.70/month** (RAM $6/GB-month since 2026-10-01; older pages say
  $5.70). There is **no confirmed hard spending cap**: staff forum replies say billing cannot be limited to $0,
  and a prepaid balance rolls over into normal billing. Budget or alert features could not be confirmed in
  Fly's own docs. Cost is bounded by configuration instead: one machine, `--ha=false`, a fixed 1 GB VM and
  auto-stop. The proposed config fits: the 373 MiB peak sits under 1 GB, one instance matches the process-local
  limits, and the HTTP check is `/health/ready` with a 60 s grace period. The region was changed to `iad`
  (same metro as Vercel `iad1`).
* **Vercel Hobby.** Officially restricted to non-commercial personal use; commercial use needs Pro. A personal
  portfolio that earns no money and serves no client fits, according to Vercel community answers, which are
  not an official ruling. A portfolio tied to paid client work would not fit. Functions default to 10 s, and the
  proxy sets `maxDuration = 60` (the Hobby maximum).

## Sources

[Render compute plans](https://render.com/docs/compute-plans) · [Render pricing](https://render.com/pricing) ·
[Fly.io pricing](https://fly.io/docs/about/pricing/) · [Fly.io cost management](https://fly.io/docs/about/cost-management/) ·
[Railway pricing](https://docs.railway.com/pricing) · [Railway plans](https://docs.railway.com/pricing/plans) ·
[Vercel Hobby plan](https://vercel.com/docs/plans/hobby) · [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing) · [Vercel fair use](https://vercel.com/docs/limits/fair-use-guidelines) · [Fly community: billing limit](https://community.fly.io/t/can-i-set-a-billing-limit-per-month/714) ·
third-party summaries used only where marked *(verify)*: [temps.sh Fly.io 2026](https://temps.sh/blog/fly-io-pricing-2026),
[budgetforge Fly.io 2026](https://www.budgetforge.dev/tools/fly-io-pricing-2026), [livemy.app Render](https://livemy.app/blog/render-pricing),
[activepieces Railway 2026](https://www.activepieces.com/blog/railway-pricing-2026-plans-usage-rates-and-costs).
