# AI readiness audit (Milestone 3, Stage A)

Date: 2026-11-04. Branch `feature/milestone-3-mcp-analyst`. Model run audited:
`20261104T195715Z-27ea6f` (the run `data/models/current.json` points to; it is the
second of the two identical Milestone 2 runs). Warehouse build `20261104T194022Z-5da243`.

**Purpose.** Before an AI assistant is allowed to present any Milestone 2 output, check that
every reported number can be reproduced from the saved artifacts, and decide what the assistant
may say about each capability.

**Method.** `scripts/ai_readiness_audit.py` re-derives the reported metrics directly from the
saved backtest and score files and from the warehouse. It deliberately does **not** import the
modeling package, so a bug in the original code cannot also hide in the check. Raw output:
[`ai_readiness_audit.json`](ai_readiness_audit.json).

```bash
.venv/Scripts/python scripts/ai_readiness_audit.py     # exit code 0 = every check matches
```

**Result: 42 of 42 checks reproduce the reported values** (tolerance 1e-6, exact for counts).
No correctness defect was found in the modeling code, so **no model was retrained or modified**.

## 1. Forecasting

| Check | Reported | Re-derived from `forecast_backtest.parquet` | |
|---|---|---|---|
| Test WAPE: lightgbm, naive, seasonal_naive, rolling_mean | 0.3178, 0.3472, 0.3685, 0.4320 | identical | ✅ |
| Validation WAPE (all 4 models) and point counts (1,820 val, 3,640 test) | as reported | identical | ✅ |
| Model selection uses lowest **validation** WAPE | lightgbm | lightgbm (0.283 vs 0.334 next) | ✅ |
| Every backtest row has target = origin + horizon and target > origin | — | 0 violations | ✅ |
| Validation targets end before test targets begin | — | 2025-10-29 < 2025-11-01 | ✅ |
| Backtest `actual` equals warehouse daily counts | — | 0 mismatches over all points | ✅ |
| 90% interval coverage, test, all 4 models | lightgbm 86.4% | identical | ✅ |
| 90% interval coverage, validation (only points with an interval) | lightgbm 84.6% on 1,400 points | identical | ✅ |

### Baseline comparisons
An independent paired bootstrap was run over the 52 test origins, with 4,000 resamples and a
different seed from training. The bootstrap unit and statistic are the same as in Milestone 2.

| LightGBM vs | Reported 95% CI of WAPE difference | Independent CI | Significant? |
|---|---|---|---|
| naive | [−0.019, 0.079] | [−0.018, 0.079] | **No**, in both. The CI includes 0. |
| seasonal_naive | [0.007, 0.094] | [0.006, 0.095] | Yes, in both |
| rolling_mean (28 d) | [0.071, 0.161] | [0.072, 0.164] | Yes, in both |

By category (test WAPE), heat/hot water is **not improved**: LightGBM 0.405 vs naive 0.403.
Residential noise is improved: 0.244 vs naive 0.300 and seasonal 0.268.

### What was observable at prediction time
- **Features.** They use only daily counts at or before the origin. The leakage tests from
  Milestone 2 cover this.
- **Not verifiable: point-in-time counts.** The counts come from the latest snapshot of each
  record. Records published or deleted after a given day (see the bulk source rewrite in §2)
  could make historical daily counts differ from what was visible on that day. The source
  publishes no history, so this is **NOT VERIFIED**. It mainly affects how a backtest should be
  read. It does not affect the descriptive counts.

### Documentation imprecisions found (corrected in `MODEL_EVALUATION.md` in Milestone 3)
These are not code defects.
- `MODEL_EVALUATION.md` gives the validation targets as "2025-05-01 → 2025-10-31". The last
  validation origin is 2025-10-22, so the latest scored target is **2025-10-29**.
- The test targets end **2026-10-30**, not 10-31.
- The periods in the config are bounds, and no metric is affected. The MCP tools report the
  **actual** first and last target dates.

## 2. Closure-delay risk: label construction and the 2026 drift

**Label construction is internally correct.**
- Recomputing `closed_at IS NULL OR closed_at > created_at + 7 days` from the warehouse for all
  544,834 test rows gives **0 mismatches**.
- No test row was created after the maturity cutoff (2026-10-25 02:04:40).
- The split counts match.

**However, the label is "the status in the latest published version of the record". It is not
"the status observable 7 days after creation".** Four findings show these differ in ways that
cannot be reconstructed:

1. **Bulk source rewrite on 2025-12-26.** Every 2023 and 2024 record carries a source
   `:updated_at` on 2025-12-26 between 17:22 and 19:20 UTC. The only exceptions are 10 heat
   records.

   | Created | Records rewritten 2025-12-26 |
   |---|---|
   | 2025, heat/hot water | 160,014 of 315,946 |
   | 2025, residential noise | 450,113 of 463,349 |
   | 2026 | **0** |

   The publisher reprocessed historical records, and the source keeps no field history. So
   whether that rewrite set or changed `closed_date` or `status` **cannot be determined**.
   Training, validation and test therefore come from records in **different processing
   states**: the older ones were rewritten, the 2026 ones were not.
2. **Closure timestamp semantics changed for heat around September 2025.** The median of
   `resolution_updated_at − closed_at` was −13.6 to −16.5 h for heat requests created through
   August 2025. From September 2025 onward it is exactly 0 h. Noise was 0 h throughout. How
   HPD closure times are recorded changed mid-history.
3. **The 2026 positives are almost all still open, not closed late.** Of the 14,824 mature 2026
   heat positives, **14,089 (95.0%)** have no `closed_at`, and every one has status `Open`.
   Closures recorded more than 30 days after creation are rare: **203** heat requests in almost
   four years. So a later closure would not turn these into negatives. But if a future
   publisher reprocess writes closure dates the way the 2025-12-26 rewrite may have, the 2026
   labels would change.
4. **Open-request backlogs are not new, just larger.**

   | Heat, created | Still open in the snapshot |
   |---|---|
   | October 2024 | 6.3% |
   | May 2025 | 5.8% |
   | 2026 | 3–14% every month (13.6% in October 2026) |

   Part of the drift may be a real change in HPD closure behaviour. That cannot be separated
   from differences in record-processing state.

**Field availability and schema** were checked as well:
- DQ060 schema check: no added, removed or retyped fields.
- `resolution_updated_at` is present on every open heat record.
- `due_at` is never populated.

So the drift is **not** explained by missing timestamps or schema changes. The candidate
explanations are source-side processing (findings 1 and 2) and genuine backlog (finding 4).

### Decision
Following the Milestone 3 rule, **the target's historical status cannot be reliably
reconstructed**. **Numerical closure-delay predictions are therefore DISABLED through the AI
agent and the MCP server.** That covers:
- per-request probabilities
- risk lists and rankings
- per-request SHAP contributions

`predict_closure_delay` returns only the research methodology, the measured evaluation and the
limitations. The **experimental evaluation page in the dashboard is preserved unchanged**, as
the milestone requires. The disable is a code constant (`DELAY_NUMERIC_PREDICTIONS_ENABLED =
False` in `services/mcp/civicpulse_mcp/capabilities.py`) with no environment override. Enabling
it needs a code change that cites a new audit.

## 3. Anomaly detection
| Check | Result |
|---|---|
| Real-data alerts in `anomaly_scores.parquet` | 1,272: 1,190 spikes, 82 drops. Matches the report. |
| Heat alerts in October | 469 (of 718 heat alerts). Matches the report. |
| `actual` in anomaly scores equals warehouse daily counts (borough series) | 0 mismatches |
| Synthetic recall (29.9% at z = 3.5) | **Not re-derivable from artifacts.** The injected days are not persisted. It reproduces only by re-running the seeded pipeline (`test_pipeline_is_reproducible`, and the two identical full runs in Milestone 2). |

**Methodology.**
- The detectors are causal: a test asserts that changing future days leaves past scores
  unchanged.
- The "alert rate on uninjected days" (7.7%) is correctly described as **not** a
  false-positive rate. Uninjected real days can contain real anomalies, and no real labels
  exist.
- The agent must therefore never call a flag a confirmed incident, and never give it a cause.

## 4. Descriptive analytics used by the agent
- **Verification.**
  - Daily counts, breakdowns and KPIs were verified against an independent Polars
    implementation in Milestone 1 (30 tests).
  - Backtest and anomaly actuals match warehouse counts exactly (above).
  - Milestone 3 adds MCP-vs-API consistency tests (`services/mcp/tests`).
- **Finding:** `breakdown.closed_within_7d_pct` (an analytics-service field used in the Explorer
  table) counts requests created in the last 7 days in its denominator, so it is biased low near
  the snapshot date.
  - The maturity-aware KPI `open_beyond_threshold_pct` handles this correctly.
  - The MCP tools expose **only** the maturity-aware metric. The Milestone 1 dashboard field is
    left unchanged and documented here. Changing it is out of scope for this milestone.
- **Comparisons.** Raw counts are **not normalized by population or housing stock**, and 311
  counts measure *reported complaints*, not incidents. Every comparison tool returns this
  caveat.

## 5. Capability classification
| Capability | Class | What the agent may say |
|---|---|---|
| Historical request counts, trends, borough comparisons, category/descriptor breakdowns | **VALIDATED** | Exact counts and shares with filters, period and dataset cutoff. These are reported complaints: raw, not per-capita. |
| Median hours to close; share open beyond 7 days (maturity-aware) | **VALIDATED** (descriptive) | Values as of the snapshot. The median excludes open requests and is biased low. Status reflects the latest record version. |
| Dataset freshness, record counts, duplicates, quality rules | **VALIDATED** | Exact values. Always state that this is a snapshot, not live. |
| Saved model evaluation metrics (all three families) | **VALIDATED** (reproduced, 42/42) | Exact artifact values, with period and model version. |
| 7-day forecast values (LightGBM, borough × category) | **EXPERIMENTAL** | Always give the model, origin, horizon, interval and test WAPE. Never claim it beats naive. Heat is not improved. |
| Forecast prediction intervals | **EXPERIMENTAL** | "Nominal 90%, measured 86.4% on test (heat 83.6%)." |
| Anomaly flags | **EXPERIMENTAL** | "Statistical flags requiring investigation." Never incidents, never causes. Mention the October heating-season effect. |
| Forecast feature importance | **EXPERIMENTAL** | Associations the model uses. Not causes. |
| Synthetic anomaly recall | **NOT VERIFIED** (by this audit) | "Reported 29.9% on injected anomalies, reproducible by re-running the seeded pipeline." |
| Point-in-time historical counts | **NOT VERIFIED** | Snapshot counts may differ from what was visible on the day. |
| Closure-delay methodology and measured evaluation | **EXPERIMENTAL** model, numbers **VALIDATED** | May explain the method, metrics, calibration failure and drift. |
| Closure-delay numerical predictions, rankings, per-request explanations | **DISABLED** | Refuse. The model is not validated for prioritization, allocation or ranking. |
| Causal claims about why volumes changed | **DISABLED** | Never produced. No causal analysis exists. |
| Live operational monitoring | **DISABLED** | The warehouse is a snapshot: newest request 2026-11-01 02:04 NYC. |

These classes are encoded once in `services/mcp/civicpulse_mcp/capabilities.py`:
- every MCP tool result carries its class and limitations
- the AI analyst shows them in the warning panel
- the agent evaluation suite checks that the agent respects them
