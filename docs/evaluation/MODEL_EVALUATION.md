# Model evaluation (Milestone 2)

This page summarizes every reported number and how to reproduce it. Method details are in
[FORECASTING](../modeling/FORECASTING.md), [DELAY_RISK](../modeling/DELAY_RISK.md) and
[ANOMALIES](../modeling/ANOMALIES.md). Every value is copied from the run outputs. Nothing is
estimated or illustrative.

## Data and runs
| | |
|---|---|
| Source | NYC Open Data `erm2-nwe9`: `Noise - Residential`, `HEAT/HOT WATER` |
| Warehouse build | `20261104T194022Z-5da243`. 2,511,293 requests, created 2023-01-01 00:00:42 → 2026-11-01 02:04:40 (NYC). Fact sha256 `5dd5799e7f74bc29…` |
| Model runs | `20261104T194523Z-daf75c` and `20261104T195715Z-27ea6f`, both at git `749f817` with the same config (sha256 match) and seed 42 |
| Libraries | LightGBM 4.7.0, scikit-learn 1.9.1, Python 3.12 |

**Rules fixed before training.** Periods, hyperparameters and selection rules are set in
`configs/modeling.yaml` before any run:
- **Selection** uses validation only.
- **Test** is reported, never used to choose or tune.
- **A baseline would have been kept** if it had won validation.

## Reproducibility (verified)
The full pipeline (`civicpulse-model train`) was run twice on the same warehouse.

**Result:** after removing run ids and timings, the two runs produced **identical** outputs:
- `forecast_evaluation.json`, `delay_evaluation.json` and `anomaly_evaluation.json` are equal
  as parsed JSON.
- `forecast.parquet`, `forecast_backtest.parquet`, `delay_scores.parquet` and
  `anomaly_scores.parquet` are equal frame by frame.

**What makes this deterministic:**
- LightGBM is run with `deterministic=True`, `force_col_wise=True`, a fixed thread count and a
  fixed seed.
- Every random step (bootstrap, permutation, injections) uses seeded generators.

The suite also contains a reproducibility test on a synthetic warehouse
(`test_pipeline_is_reproducible`).

## 1. Forecasting (7-day daily volume, 10 borough × category series)
**Training:** an expanding window. For each origin, use every (series, origin', horizon) row
whose target date ≤ origin, from 2024-01-30 onwards.

| Period | Configured bounds | Actual scored targets | Origins | Points per model |
|---|---|---|---|---|
| Validation | 2025-05-01 → 2025-10-31 | → 2025-10-29 (last origin 2025-10-22) | 26 weekly | 1,820 |
| Test | 2025-11-01 → 2026-10-31 | 2025-11-01 → 2026-10-30 | 52 weekly | 3,640 |

Actual target dates corrected on 2026-11-04 by the AI readiness audit; no metric changes.

| Model | Val WAPE | Test WAPE | Test MAE | Test 90% PI coverage |
|---|---|---|---|---|
| naive | 0.455 | 0.347 | 73.4 | 85.0% |
| seasonal_naive | 0.334 | 0.368 | 77.9 | 87.2% |
| rolling_mean (28 d) | 0.477 | 0.432 | 91.3 | 86.0% |
| **lightgbm** (selected) | **0.283** | **0.318** | **67.1** | 86.4% |

**Verified improvements on test.** Relative WAPE reduction, with a paired bootstrap over 52
origins (2,000 resamples):
- vs seasonal_naive: **13.8%**, CI of the WAPE difference [0.007, 0.094]
- vs rolling_mean: **26.4%**, CI [0.071, 0.161]
- vs naive: 8.5%, but CI [−0.019, 0.079] **includes 0**, so this is **not** a demonstrated
  improvement.

**By category:**
- Noise: LightGBM 0.244 vs naive 0.300 vs seasonal 0.268.
- **Heat: LightGBM 0.405 vs naive 0.403**, i.e. no improvement.

**Intervals:** nominal 90%, empirical test coverage 86.4%. This is **under-coverage of about
4 points**.

## 2. Delay risk (`request_not_closed_within_7_days`)
| Split | Created | n | Positive rate |
|---|---|---|---|
| Train | 2023-01-01 → 2025-06-23 | 1,544,783 | 0.40% |
| Validation | 2025-07-01 → 2025-12-24 | 378,495 | 0.053% |
| Test | 2026-01-01 → 2026-10-25 02:04 | 544,834 | 2.74% |
| Excluded | embargo 27,901; immature 15,153; label unknown 127 | | |

**Maturity.** `observed_until` = 2026-11-01 02:04:40 and the maturity cutoff is 2026-10-25
02:04:40.

| Model | Val PR-AUC | Test PR-AUC | Test ROC-AUC | Test Brier | Test ECE |
|---|---|---|---|---|---|
| majority_class | 0.0005 | 0.0274 | 0.500 | 0.02720 | 0.0240 |
| logistic_regression | 0.0030 | **0.0646** | **0.796** | **0.02697** | 0.0224 |
| **lightgbm** (selected on validation) | **0.0045** | 0.0622 | 0.789 | 0.02717 | **0.0218** |

**Selection.** LightGBM won on validation, which has only about 200 positives. Logistic
regression is marginally better on test. Switching on that basis would be selecting on test,
so it was not done.

**Calibration (quality: poor).**
- Mean predicted risk is 0.57% against 2.74% observed.
- Every reliability decile under-predicts.
- LightGBM's Brier is not better than the majority baseline.
- **Cause:** label drift. The heat not-closed-in-7-days rate went from 0.1–0.5% (2023–2025) to
  3–14% (2026).

**Operating points.** These use a validation max-F1 threshold, applied to test.

| Model | Precision | Recall |
|---|---|---|
| LightGBM | 0.054 | 0.063 |
| LightGBM, top 10% by score | 0.064 | 0.234 |

**Segments.**
- **Within a category, ranking is at chance:** heat ROC 0.518; noise ROC 0.357, from about
  106 positives.
- **Borough ROC** ranges from 0.74 (Manhattan) to 0.89 (Staten Island).

**Verdict:** a weak ranking signal. It is useful for showing *where* risk concentrates
(category or borough, trailing delay rate), but it is **not decision-grade**.

## 3. Anomaly detection
- **Ground truth:** none exists. Evaluation uses 144 synthetic injections into the real
  series, 2024-01-01 → 2026-10-31, seed 42.
- **seasonal_mad at z = 3.5:**
  - recall **29.9%** (43 / 144)
  - alert rate on uninjected days 7.7%
  - this beats the rolling-median comparator (23.6% at 8.0%)
- **By injection size:** recall for a +100% spike is 55%, for a +200% spike 67%, and for ±25%
  changes it is 4–6%.
- **Real data:** 1,272 alerts are **unverified**. 469 of the 718 heat alerts fall in October,
  at the heating-season onset.

## 4. Explainability
| Model | Method |
|---|---|
| Forecast LightGBM | gain, plus permutation (test WAPE increase) |
| Delay logistic regression | standardized coefficients |
| Delay LightGBM | gain, permutation (test PR-AUC drop), and per-request TreeSHAP |
| Anomalies | expected / residual / robust scale per alert |

Every explanation endpoint returns a `causality_note`. These are associations, not causes.

## 5. Tests (run 2026-11-04, local Windows 11, Python 3.12, Node 22)
| Suite | Result |
|---|---|
| `pytest` (offline: ingestion 27, analytics 30, modeling 25, API 44) | **126 passed, 0 failed, 0 skipped**, 3 live tests deselected by default |
| Playwright on the **real snapshot** (desktop + mobile, axe) | **28 passed, 0 failed** |
| Playwright on the **committed sample + CI-config models** (mirrors CI) | **28 passed, 0 failed** |
| ruff, ruff format, mypy --strict (29 files), tsc, eslint, `next build` | clean |

**Test coverage of the Milestone 2 requirements:**
- temporal leakage: features and labels after the origin or cutoff are perturbed and features
  stay unchanged
- split chronology and embargo
- label maturity and immature exclusion
- creation-time-only features
- train-only encoder statistics
- model serialization round trips
- probability and forecast validity
- conformal intervals using only past residuals
- hand-computed metrics
- the seeded bootstrap
- detector causality and injection reproducibility
- the end-to-end pipeline and its reproducibility
- API contracts and input bounds
- Prediction Lab interactions
- all Milestone 1 tests (still passing)

## 6. Infrastructure
- **Docker (verified 2026-11-04, Docker 27.5.1):**
  - `services/api/Dockerfile` builds.
  - The runtime image has **no LightGBM or scikit-learn** and runs as uid 10001.
  - The health check reports healthy, and all prediction endpoints returned 200.
  - `docker compose up --build` serves every page through the web proxy, the API is not
    published on the host, and `POST` returns 405.
  - The first build failed on a real bug: a non-editable install resolved `REPO_ROOT` inside
    site-packages. This is fixed with `CIVICPULSE_REPO_ROOT` and covered by tests.
- **Secret scan:** detect-secrets 1.5.0 found 0 findings in tracked files and in every commit.
- **CI:** updated (`.[dev,ml]`, sample models, modeling coverage). It has **not been run on
  GitHub** because the repository has no remote.

## Reproduce
```bash
.venv/Scripts/python -m pip install -e ".[dev,ml]"
bash scripts/backfill_history.sh && .venv/Scripts/civicpulse extract --mode incremental   # network
.venv/Scripts/civicpulse build
.venv/Scripts/civicpulse-model train
```
**Caveat:** the source is live and records are edited over time, so a fresh extraction will
not reproduce these numbers exactly. The fact-table sha256 identifies the snapshot used here.
