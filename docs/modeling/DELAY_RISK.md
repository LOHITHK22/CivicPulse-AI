# Closure-delay risk

Milestone 2. Code: `packages/modeling/civicpulse_modeling/delay_risk.py`. Config: `configs/modeling.yaml`
(`delay_risk:`). Numbers are from run **`20261104T194523Z-daf75c`**, in
`data/models/runs/<run_id>/delay_evaluation.json`.

> **Bottom line.** The model ranks requests better than chance (test ROC-AUC 0.79, PR-AUC 0.062
> vs a 0.027 base rate). Most of that comes from separating **categories and boroughs**, not
> individual requests. Probabilities are **badly under-calibrated on the test period**, because
> the base rate rose about 7× in 2026. Treat the scores as a **relative ranking aid**, not as
> calibrated probabilities. They are **not** a basis for operational decisions.

## Target
`request_not_closed_within_7_days`:
- **= 1** if the request has **no recorded closure within 7 × 24 h** of `created_at`. This
  covers both requests closed late and requests still open.
- **= 0** otherwise.

### Label maturity and censoring
- `observed_until` = min(newest `created_at` in the snapshot, the source's `rowsUpdatedAt` in
  NYC time). For this snapshot that is **2026-11-01 02:04:40**.
- `maturity_cutoff` = `observed_until` − 7 days = **2026-10-25 02:04:40**.
  - Requests created **after** the cutoff are **immature**: whether they close within 7 days
    is not yet observable (right-censored). There are **15,153** such requests.
  - Immature requests are **excluded from training and evaluation**. They are the only rows the
    Prediction Lab shows as live predictions.
- **Missing closures.** For a mature request, "no closure date" means it was not closed within
  7 days, so y = 1. That is a real outcome, not missing data.
- **Unknown labels.** Invalid closures (closed before creation, closed in the future, or a
  closed status with no date; rules DQ020–DQ022) make the label unknowable. These **127**
  requests are excluded, not imputed.
- **Status changes.** The label uses `closed_at` as it appears in the **snapshot**. A request
  closed on day 3, reopened, and closed again on day 20 shows only its final closure. See the
  limitation below.

## Snapshot vs prediction-time information
The model must use only what is known **when the request is created**.

| Used (known at creation) | Never used (outcome or later information) |
|---|---|
| category, descriptor, borough, community board, channel, location type | `closed_at`, `hours_to_close`, `status`, `status_group`, `resolution_updated_at`, `due_at` |
| created hour, day-of-week, month, weekend flag | anything from after `created_at` |
| `load_prev_7d`: same category × borough requests created in the previous 7 days | |
| `load_same_day_before`: same category × borough requests created earlier the same day | |
| `trailing_delay_rate_{borough,cb}` + `trailing_n_*`: share not closed within 7 days among requests **created 35 to 8 days earlier** | |

**Why 8 days.** A request created on D−8 has had 7 full days by D−1, so its label was knowable
before D. A test asserts that changing any outcome created after D−8 does not change D's
features. Another test asserts the forbidden columns never appear in the feature matrix.

**Limitation that cannot be reconstructed.** The warehouse stores the **latest version** of
each request, not a history of its fields. Descriptor, location type or community board could
have been edited after creation, and `closed_at` could have been rewritten by a later
re-closure. The source publishes no field-level history, so true point-in-time records **cannot
be reconstructed**. The model therefore uses snapshot values for creation-time fields, on the
assumption that these fields rarely change. This is stated, not hidden, and no point-in-time
records are invented.

## Splits (chronological, by `created_at`, NYC time)
| Split | Created | Rows | Positive rate |
|---|---|---|---|
| train | 2023-01-01 → 2025-06-23 | 1,544,783 | 0.40% |
| embargo | 2025-06-24 → 06-30, 2025-12-25 → 12-31 | 27,901 | excluded |
| validation | 2025-07-01 → 2025-12-24 | 378,495 | 0.053% |
| test | 2026-01-01 → 2026-10-25 02:04 | 544,834 | **2.74%** |
| immature | after 2026-10-25 02:04:40 | 15,153 | excluded (scored only) |
| label unknown | various | 127 | excluded |

**Embargo.** The 7-day embargo before each boundary means every training label had matured
before the next split begins.

**Train-only statistics.**
- Categorical levels seen fewer than 200 times **in train** become `__other__`.
- Unseen levels map to `__other__`.
- Numeric medians (used for imputation) come from train.

Tests cover all three.

## Models
| Model | Details |
|---|---|
| `majority_class` | Predicts the train prevalence (0.40%) for every request. Its ranking is uninformative (ROC 0.5). |
| `logistic_regression` | One-hot categoricals plus standardized numerics (log1p for counts), L2 penalty with C = 1, lbfgs solver, no class weighting, so probabilities stay on the prevalence scale. Exported as portable JSON coefficients. |
| `lightgbm` | Native categoricals, 63 leaves, learning rate 0.05, early stopping on validation log-loss. Best iteration: 600. |

**Selection rule:** highest **validation** PR-AUC, fixed in advance. Validation scores were
LightGBM 0.0045, logistic regression 0.0030 and majority 0.0005, so **LightGBM** was selected.
Validation has only about 200 positives, so this comparison is noisy. On **test**, logistic
regression is marginally better on every metric (below). That was **not** used to switch
models, because switching would mean selecting on test.

## Test results (2026-01-01 → 2026-10-25, n = 544,834, positives 2.74%)
| Model | PR-AUC | ROC-AUC | Brier | ECE | Log loss | Mean predicted |
|---|---|---|---|---|---|---|
| majority_class | 0.027 | 0.500 | 0.02720 | 0.0240 | 0.155 | 0.40% |
| logistic_regression | **0.065** | **0.796** | **0.02697** | 0.0224 | **0.146** | 0.50% |
| **lightgbm** (selected) | 0.062 | 0.789 | 0.02717 | **0.0218** | 0.170 | 0.57% |

**Operating points.** The threshold is chosen on validation (max F1), then applied to test.

| Model | Threshold | Flagged | Precision | Recall |
|---|---|---|---|---|
| lightgbm | 0.047 | 3.2% | 0.054 | 0.063 |
| logistic_regression | 0.052 | 1.4% | 0.038 | 0.019 |
| lightgbm, top 10% by score | — | 10% | 0.064 | 0.234 |
| logistic_regression, top 10% by score | — | 10% | 0.070 | 0.255 |

**Calibration is poor.** The models predict a mean of about 0.5–0.6%, but the observed test
rate is 2.7%. The reliability tables in the evaluation JSON, also plotted in the UI, show
observed rates above predicted ones in every decile. Brier for LightGBM is **not better than
majority class** (0.02717 vs 0.02720). No post-hoc recalibration on test was done, since that
would leak.

### By category (test)
| Category | n | Positive rate | LightGBM ROC | LR ROC | LightGBM PR-AUC |
|---|---|---|---|---|---|
| heat_hot_water | 235,649 | 6.29% | 0.518 | 0.520 | 0.063 |
| noise_residential | 309,185 | 0.034% | 0.357 | 0.527 | 0.0003 |

Within a category, the ranking is at **chance level**. For noise, which has about 106
positives, LightGBM's ranking is **worse** than chance. The overall ROC of 0.79 comes mainly
from heat requests having a far higher base rate than noise requests.

### By borough (test, LightGBM)
| Borough | n | Positive rate | ROC | PR-AUC |
|---|---|---|---|---|
| Bronx | 161,934 | 4.61% | 0.777 | 0.101 |
| Brooklyn | 162,173 | 1.47% | 0.832 | 0.044 |
| Manhattan | 110,711 | 3.19% | 0.741 | 0.057 |
| Queens | 97,591 | 1.54% | 0.838 | 0.044 |
| Staten Island | 12,417 | 0.31% | 0.891 | 0.019 |

## Why the test period looks so different (label drift)
**What changed.** The monthly share of HEAT/HOT WATER requests not closed within 7 days moved
sharply:
- **2023–2025 heating seasons:** mostly **0.1–0.5%**.
- **Throughout 2026:** **3–14%**, reaching **13.6% in October 2026**.
- **What the extra positives are:** almost all of them are still open in the snapshot, not
  closed late.

**Earlier spikes.** These line up with known HPD backlog or reopen patterns:
- summer 2024
- October–November 2024
- spring 2025

**What it means.** No creation-time feature in the training period can anticipate a regime
change of this size, so the test metrics measure performance **under drift**.
`trailing_delay_rate_borough` is the top feature by permutation importance (PR-AUC drop 0.010):
it lets the model partly track the new regime.

## Explainability (associations, not causes)
- **Logistic regression:** coefficients on standardized or one-hot inputs. The strongest
  negatives, i.e. *lower* delay risk, are noise category, "Residential Building/House"
  location, Staten Island, the mobile and online channels, and "Loud Music/Party".
- **LightGBM:** gain importance, plus **permutation importance** on a 100k test sample with 3
  repeats, measured as PR-AUC drop. The leaders are `trailing_delay_rate_borough`,
  `category_key`, `created_month` and `trailing_delay_rate_cb`.
- **Per-request explanations:** TreeSHAP contributions (`pred_contrib`), served for immature,
  scored requests.

All of these describe what the model **associates** with delay in this data. They do not show
that a channel or borough **causes** delay. The API returns this caveat (`causality_note`)
with every explanation.

## Limitations
- Label drift in 2026 makes the probabilities under-calibrated. Re-training on recent data would
  change scores substantially, and no on-line recalibration exists.
- Snapshot fields, not point-in-time history (see above).
- "Closed" means *recorded as closed by the agency*, which does not necessarily mean resolved.
- Within-category discrimination is at chance. The model is mostly a category and borough
  prior plus a trailing-rate signal.
- Request-level scores are served only for immature requests, keyed by `unique_key`. No address
  or coordinates are exposed.
