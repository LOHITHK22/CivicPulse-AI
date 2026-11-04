# Volume anomaly detection

Milestone 2. Code: `packages/modeling/civicpulse_modeling/anomalies.py`. Config: `configs/modeling.yaml`
(`anomaly:`). Numbers are from run **`20261104T194523Z-daf75c`**, files
`anomaly_evaluation.json` and `anomaly_scores.parquet`.

> **Ground-truth labels for real 311 anomalies do not exist, and none are invented.** Detector
> quality is measured only on **synthetic anomalies injected into the real series**. Alerts on
> the real data are reported as **unverified**, never as true or false positives.

## Series
There are 12 daily count series:
- **10** borough × category series, with categories `noise_residential` and `heat_hot_water`
- **2** citywide series, one per category

Scored days run from 2023-03-26, after the warm-up history, to 2026-10-31. That gives **15,792**
series-days.

## Detectors
Each detector uses **only days before d** for day d. A test asserts that changing future days
leaves past scores unchanged.

| | `seasonal_mad` (primary) | `rolling_median_mad` (comparator) |
|---|---|---|
| expected(d) | median of the same weekday over the previous 8 weeks | median of the previous 28 days |
| residual | y(d) − expected(d) | same |
| scale | max(1.4826 · MAD of residuals over the previous 56 days, √max(expected, 1)) | same |
| score | residual / scale (robust z) | same |
| flag | \|score\| ≥ 3.5 **and** \|residual\| ≥ 5 requests | same |

The √expected floor is a Poisson-style minimum scale. It stops low-count series from flagging
±2 requests. The API returns, for each flagged day:
- date
- geography (borough or CITYWIDE)
- category
- actual
- expected
- residual
- score
- direction (spike or drop)

## Synthetic evaluation (injected anomalies)
**Window.** 2024-01-01 → 2026-10-31.

**Injection.** Per series, 12 days are chosen with a seeded RNG (seed 42), at least 21 days
apart. Each chosen day is altered relative to the clean seasonal expected value e:
- a **spike** adds round(m · e)
- a **drop** subtracts round(min(m, 0.9) · e), floored at 0
- the magnitude m is drawn from {0.25, 0.5, 1.0, 2.0}

That gives **144 injected days**. Detection is run on the modified series. An injection counts
as detected only if it is flagged **in the injected direction**. Four injected days were
flagged in the opposite direction, so 47 injected days raised an alert but only 43 count as
recall. The result is labelled `synthetic: true` everywhere it appears.

### Recall on injected days and alert rate on uninjected days
| Detector | Threshold | Recall (of 144) | Alert rate on uninjected days (12,276) |
|---|---|---|---|
| seasonal_mad | 3.0 | 36.1% | 9.4% |
| **seasonal_mad** | **3.5** (configured) | **29.9%** | **7.7%** |
| seasonal_mad | 4.0 | 25.7% | 6.4% |
| rolling_median_mad | 3.0 | 26.4% | 10.0% |
| rolling_median_mad | 3.5 | 23.6% | 8.0% |
| rolling_median_mad | 4.0 | 20.1% | 6.5% |

**How to read the alert rate.** It is **not** a false-positive rate, because uninjected real
days may contain real anomalies. It is the share of ordinary days that would generate an alert.

**Comparison.** The seasonal baseline beats the 28-day rolling median at every threshold: it
has higher recall at a slightly lower alert rate.

### Recall by injected magnitude (seasonal_mad, z ≥ 3.5)
| Magnitude m | Spike (+m) recall | Drop (−min(m, 0.9)) recall |
|---|---|---|
| 0.25 | 1 / 18 (6%) | 1 / 23 (4%), a 25% drop |
| 0.5 | 5 / 18 (28%) | 4 / 17 (24%), a 50% drop |
| 1.0 | 11 / 20 (55%) | 7 / 13 (54%), a 90% drop |
| 2.0 | 12 / 18 (67%) | 2 / 17 (12%), also a 90% drop |

**Overall recall.** 43 / 144 (29.9%). The m = 1.0 and m = 2.0 drops are the **same**
transformation, a 90% drop, and their combined recall is 9 / 30 (30%). The difference between
those two cells (54% vs 12%) is therefore sampling noise, from which days the RNG happened to
pick. It shows how unstable the per-cell numbers are with 13–23 injections each.

**By series size.** Recall is 33% on series averaging ≥ 100 requests per day (n = 120), 25% on
20–100 per day (n = 12), and 0% on < 20 per day (n = 12).

**Interpretation.** The detector catches about half of +100% spikes, but few changes under 50%.
Noisy real series have a wide robust scale (MAD over 56 days), which costs sensitivity in
exchange for fewer alerts.

## Real-data alerts (unverified)
- **1,272** alerts on 15,792 series-days (8.1%): 1,190 spikes and 82 drops.
- **By category:** heat 718, noise 554.
- **Heat timing:** **469** of the heat alerts fall in **October**, at the start of the heating
  season. An 8-week same-weekday baseline lags a seasonal step change, so many of these are
  *expected seasonal transitions*, not operational incidents. This is a known weakness of the
  detector. A year-over-year baseline would address it and is noted for later work.
- **Largest scores:**
  - Bronx residential-noise days with over 3,000 requests, against an expected ~200–350
    (2024-09-15, 2024-12-16/17, 2024-12-29, 2024-11-20). These are real values in the source.
    Whether they are bulk or duplicate submissions or genuine surges is **not verified**.
  - Citywide heat on 2025-10-16/17: 2,432 and 2,028 requests, against an expected ~120.

## Explainability
Each alert carries its expected value, residual and robust scale. The *explanation* is
therefore "the count was N robust standard deviations above the typical same-weekday count".
That is a statistical statement, not a cause.

## Limitations
- No ground truth. Synthetic injections test sensitivity only to multiplicative single-day
  shocks.
- Seasonal transitions, especially heat in October, produce many alerts.
- Single-day detection only. Gradual level shifts are out of scope.
- Counts reflect *reported complaints*, and bulk submissions can look like surges.
