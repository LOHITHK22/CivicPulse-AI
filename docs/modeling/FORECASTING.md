# Daily volume forecasting

Milestone 2. Code: `packages/modeling/civicpulse_modeling/forecasting.py`. Config: `configs/modeling.yaml`
(`forecast:`). Every number below comes from run **`20261104T194523Z-daf75c`**
(`data/models/runs/<run_id>/forecast_evaluation.json`). The run trained on warehouse build
`20261104T194022Z-5da243`, which is the real snapshot of NYC 311 `erm2-nwe9`.

## Task
- **Target:** the number of service requests **created** per NYC-local calendar day.
- **Series:** 10 series, one per borough × category: 5 boroughs × {`noise_residential`, `heat_hot_water`}.
  Requests with an invalid or missing borough are excluded from series (DQ023 flags them).
- **Horizon:** days 1–7 after the forecast origin. The origin is the last complete day of data.
- **History used:** 2023-01-01 → 2026-10-31. This is a dense daily grid, so a day with no requests
  counts as 0. Days with no source data are listed in `source_gap_dates`, which is empty for this
  snapshot. Gaps are never silently filled.
- **Forecast published:** origin 2026-10-31, covering 2026-11-01 → 2026-11-07.

## Models
| Model | Definition |
|---|---|
| `naive` | ŷ(t+h) = y(origin) |
| `seasonal_naive` | ŷ(t+h) = y(t+h−7k), using the most recent same-weekday value at or before the origin |
| `rolling_mean` | ŷ = mean of the last 28 days at or before the origin |
| `lightgbm` | One global direct multi-horizon model: one row per (series, origin, horizon). Target is `log1p(y) − log1p(mean28)`, and the prediction is transformed back and clipped at ≥ 0. |

**LightGBM features.** All are computed from indices ≤ origin only:
- the last value
- the 7- and 28-day means and the 28-day standard deviation
- the same-weekday lags 1–4
- year-ago same-weekday value, year-ago week mean and year-ago shift
- the log level
- target day-of-week, month and day-of-year (sin/cos)
- horizon
- borough and category codes

Calendar features of the *target* date are known in advance, so they are legitimate.
Hyperparameters were fixed in the config **before** any test result was seen and were not tuned
afterwards: 15 leaves, learning rate 0.05, 300 trees, seed 42.

## Leakage controls (tested)
- **Features.** Feature rows are built from `y[:origin+1]` only. `test_forecasting.py`
  perturbs every value after the origin and asserts that the features are unchanged.
- **Training rows.** For a backtest origin *o*, the training rows are restricted to
  (origin', h) pairs with **target date ≤ o**. A test asserts this, so no training label can
  postdate the origin. The window expands from 2024-01-30, the first origin with ≥ 400 days of
  history.
- **Intervals.** These use only residuals whose target date ≤ origin (see below).
- **Model choice.** Selection uses **validation** WAPE. Test results are reported but never used
  to choose anything.

## Rolling-origin backtest
| Period | Target dates | Origins (weekly) | Forecast points per model |
|---|---|---|---|
| Validation (selection only) | 2025-05-01 → 2025-10-31 | 26 (2025-04-30 … 2025-10-22) | 1,820 |
| Test (reported) | 2025-11-01 → 2026-10-31 | 52 (2025-10-31 … 2026-10-23) | 3,640 |

Points per model = 10 series × 7 horizons × origins.

## Metrics
- **WAPE** = Σ|y−ŷ| / Σy, pooled over all points.
- **MAE** = mean |y−ŷ|.
- **Bias** = mean(ŷ−y).

### Results
| Model | Val WAPE | Test WAPE | Test MAE | Test bias | Test 90% PI coverage | Mean PI width |
|---|---|---|---|---|---|---|
| **lightgbm** (selected) | **0.283** | **0.318** | 67.1 | −14.2 | 86.4% | 210.9 |
| naive | 0.455 | 0.347 | 73.4 | −9.3 | 85.0% | 257.1 |
| seasonal_naive | 0.334 | 0.368 | 77.9 | +2.1 | 87.2% | 243.2 |
| rolling_mean | 0.477 | 0.432 | 91.3 | +3.5 | 86.0% | 291.2 |

**Selection.** The rule is lowest validation WAPE, with ties going to the simpler model.
LightGBM won. The rule was fixed before the run and would have kept a baseline if one had won.

**Test improvement.** The CI comes from a paired bootstrap over the 52 test origins
(2,000 resamples) of WAPE(baseline) − WAPE(lightgbm).

| vs baseline | Relative WAPE reduction | Difference | 95% CI | Significant? |
|---|---|---|---|---|
| naive | 8.5% | 0.029 | [−0.019, 0.079] | **No**: the CI includes 0 |
| seasonal_naive | 13.8% | 0.051 | [0.007, 0.094] | Yes |
| rolling_mean | 26.4% | 0.114 | [0.071, 0.161] | Yes |

### Where it does and doesn't help (test)
| Category | lightgbm WAPE | naive | seasonal_naive |
|---|---|---|---|
| noise_residential | **0.244** | 0.300 | 0.268 |
| heat_hot_water | 0.405 | **0.403** | 0.487 |

- **Heat/hot water is not improved over the naive baseline.** Heat volume is driven by outdoor
  temperature, and this model has no weather input, which is a deliberate scope limit. Cold
  snaps cause step changes that persistence tracks about as well as anything else.
- **WAPE by horizon (test, lightgbm):** 0.25, 0.29, 0.32, 0.37, 0.38, 0.32, 0.33 for h = 1…7.
- **Bias:** LightGBM under-forecasts on average (−14 requests per series-day), mostly on heat.

### Prediction intervals
- **Method:** sequential split-conformal at a nominal 90% level.
  - For each (model, category, horizon), the interval half-width is the empirical 90% quantile
    of the scaled residuals |y−ŷ| / √(ŷ+1).
  - Only residuals from earlier origins whose **target date ≤ the current origin** are used.
  - The result is rescaled by √(ŷ+1), and the lower bound is clipped at 0.
- **Too few residuals:** if fewer than 30 are available, **no interval is reported**. Early
  validation origins hit this, which is why validation has 1,400 of 1,820 points with intervals.
- **Empirical test coverage:** 86.4% for LightGBM (heat 83.6%, noise 89.2%), against a 90%
  nominal level. That is a 3–4 point **under-coverage**, consistent with drift between
  calibration and test periods. The UI and API label intervals "nominal 90%" and also show the
  measured coverage.

### Explainability (correlation, not causation)
- **Ranking:** gain importance from the production model, plus permutation importance (WAPE
  increase) on the test backtest.
- **Leading features:** year-ago same-weekday volume, the day-of-year terms, year-ago week
  mean, and last week's same-weekday value.
- **What this means:** these show what the model *uses* to predict. They do not show what
  *causes* complaints.

## Reproduce
```bash
.venv/Scripts/python -m pip install -e ".[dev,ml]"
.venv/Scripts/civicpulse-model train            # ~9 min on a laptop for all three tasks
```
A second full run on the same warehouse produced identical metrics; see
`docs/evaluation/MODEL_EVALUATION.md`. CI trains with the smaller `configs/modeling.ci.yaml`
on the committed sample, and **those numbers are never reported**.

## Limitations
- No weather, holidays (beyond the calendar terms), or policy and event inputs.
- 10 series. Borough-level only, with no finer geography.
- Intervals under-cover by about 4 points on test.
- 311 counts measure *reported complaints*, not incidents. Reporting behaviour changes over
  time (see `docs/research/SOURCES.md`).
- Snapshot data with source freshness lag (DQ041/DQ042). The forecast is not live.
