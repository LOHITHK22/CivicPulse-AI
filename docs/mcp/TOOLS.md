# MCP tools (`civicpulse-mcp` 0.3.0)

All tools are **read-only** (`readOnlyHint: true`), take typed arguments (enums, ISO dates, bounded
integers) and return one structured envelope (`outputSchema`):

| Field | Meaning |
|---|---|
| `tool`, `status` | `ok`, `no_data` (period outside coverage: never a silent zero) or `restricted` |
| `capability` | `VALIDATED`, `EXPERIMENTAL`, `DISABLED` or `NOT_VERIFIED` (from the AI readiness audit) |
| `summary`, `key_measurements` | Short deterministic summary and named values with units and definitions |
| `data` | Bounded result rows/series |
| `metric_definitions` | Definitions of the metrics used |
| `provenance` | Dataset id, data build id, data cutoff, last complete date, `live: false`, model run/version, artifact |
| `coverage` | Requested vs effective period and a note when clamped or partial |
| `uncertainty`, `limitations` | Mandatory caveats |
| `query` | Elapsed ms and row counts |

Results are capped at 96 KB. Invalid arguments come back as MCP tool errors (`isError: true`) that
name the offending field; unknown tools return `Unknown tool: <name>`.

| # | Tool | Arguments | Capability | Notes |
|---|---|---|---|---|
| 1 | `get_service_request_trends` | `start_date`, `end_date`, `category` (all / noise_residential / heat_hot_water), `borough` (ALL, 5 boroughs, UNSPECIFIED), `granularity` (day/week/month) | VALIDATED | Daily granularity ≤ 400 days; ranges ≤ 1098 days. Partial buckets marked; prior-period comparison from the KPI service. |
| 2 | `compare_boroughs` | dates, `boroughs` (2–5 distinct), `category` | VALIDATED | Counts, share of the selected boroughs, pairwise difference/ratio, median hours to close, maturity-aware share open beyond 7 days. Raw counts, not per capita. |
| 3 | `get_category_breakdown` | dates, `borough`, `level` (category/descriptor), `category` (descriptor level only) | VALIDATED | Top 20 descriptors plus `__other__`; descriptor text is sanitized (external, untrusted). |
| 4 | `forecast_request_volume` | `category`, `borough` (ALL or one) | EXPERIMENTAL | Precomputed 7-day forecast from the dataset cutoff with nominal 90% intervals, test evaluation, baseline comparisons with bootstrap significance, interval coverage. `ALL` sums point forecasts **without** an interval. |
| 5 | `predict_closure_delay` | `request` (research_summary / predict_request / rank_requests), optional `unique_key` | **DISABLED** | Always `status: restricted`. Returns methodology, measured test metrics, audit findings and prohibited uses. No numeric prediction, ranking or per-request explanation. |
| 6 | `detect_unusual_spikes` | dates, `category`, `geography` (ALL_SERIES / CITYWIDE / borough), `direction`, `limit` ≤ 50 | EXPERIMENTAL | Robust z vs the median of the previous 8 same weekdays. Flags are unverified, carry no cause; October windows add a heating-season note. |
| 7 | `explain_model_prediction` | `model` (forecast / delay_risk / anomaly), anomaly: `category`, `geography`, `date`; `top_k` | VALIDATED (global) / EXPERIMENTAL | Global importances (gain, permutation). Anomaly explanations recompute the 8-week same-weekday median and verify it against the stored value. Associations, not causes. |
| 8 | `get_dataset_quality` | none | VALIDATED | Records, coverage, cutoff, hours since cutoff, rule status, known warnings (publisher bulk rewrite, status not point-in-time). |
| 9 | `get_model_evaluation` | `model` | VALIDATED | Saved evaluation artifact values (reproduced by the AI readiness audit, 42/42 checks). |
| 10 | `generate_investigation_report` | `question`, `steps` (1–6 tool calls) | as per steps | Re-executes the listed tools through the server and returns a structured report with evidence IDs R1…; it writes no files. |

Resources: `civicpulse://capabilities`, `civicpulse://metric-definitions`, `civicpulse://dataset`.

The orchestrator offers all ten tools to the provider but validates each call against the server's
published input schema with `additionalProperties: false` before sending it.
