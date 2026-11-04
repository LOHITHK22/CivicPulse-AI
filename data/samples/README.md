# Committed real-data sample

`nyc311_raw_sample.parquet` holds **969 genuine NYC 311 records** extracted from NYC Open Data
dataset `erm2-nwe9` on 2026-11-04. It uses the same raw format as `data/raw` pages (all values
are strings, plus lineage columns).

- **Selection:** deterministic and stratified. Up to 8 records per (complaint_type, borough,
  month), chosen by a seeded hash of `unique_key`. It covers residential noise and heat/hot
  water, all boroughs, and 2025-11 through 2026-10.
- **Privacy:** street addresses are never ingested, and latitude/longitude are rounded to 3
  decimals (about 100 m).
- **Purpose:** offline tests, CI and the Docker demo. It is **not** representative of real
  volumes, because strata are capped equally.
- **Regenerate:** `civicpulse sample --per-stratum 8` (requires a local backfill).

Source: NYC Open Data, *311 Service Requests from 2020 to Present*. Modified (filtered and
sampled) from the original source.
