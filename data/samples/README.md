# Committed real-data sample

`nyc311_raw_sample.parquet` holds **3,785 genuine raw NYC 311 rows (3,606 distinct requests)** extracted from NYC Open
Data dataset `erm2-nwe9` on 2026-11-04. Regenerated in Milestone 2 to span 2023-01 to 2026-10 so the
models can be trained end-to-end in CI (the Milestone 1 version had 969 rows for 2025-11 to 2026-10).
Some requests appear more than once because the raw pages keep superseded versions; the warehouse
build deduplicates them, exactly as with the full data. It uses the same raw format as `data/raw` pages (all values
are strings, plus lineage columns).

- **Selection:** deterministic and stratified. Up to 8 records per (complaint_type, borough,
  month), chosen by a seeded hash of `unique_key`. It covers residential noise and heat/hot
  water, all boroughs, and 2023-01 through 2026-10.
- **Privacy:** street addresses are never ingested, and latitude/longitude are rounded to 3
  decimals (about 100 m).
- **Purpose:** offline tests, CI and the Docker demo. It is **not** representative of real
  volumes, because strata are capped equally. Models trained on it (CI smoke runs) produce real
  outputs from real records, but their metrics are meaningless and are never reported.
- **Regenerate:** `civicpulse sample --per-stratum 8` (requires a local backfill).

Source: NYC Open Data, *311 Service Requests from 2020 to Present*. Modified (filtered and
sampled) from the original source.
