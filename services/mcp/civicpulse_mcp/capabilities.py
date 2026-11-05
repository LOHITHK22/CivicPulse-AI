"""Capability classification and limitation texts, from docs/evaluation/AI_READINESS_AUDIT.md.

This module is the single source of what the AI layer may claim about each capability. Every MCP
tool result carries its class and limitations; the agent and UI only relay them.
"""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    VALIDATED = "VALIDATED"
    EXPERIMENTAL = "EXPERIMENTAL"
    DISABLED = "DISABLED"
    NOT_VERIFIED = "NOT_VERIFIED"


# The audit found that the closure-delay label's historical status cannot be reconstructed
# (bulk source rewrite of all pre-2026 records on 2025-12-26, closure-timestamp semantics change in
# Sep 2025). Numerical predictions are therefore disabled for the agent. Deliberately a constant,
# not an environment variable: re-enabling requires a code change that cites a new audit.
DELAY_NUMERIC_PREDICTIONS_ENABLED = False

AUDIT_DOC = "docs/evaluation/AI_READINESS_AUDIT.md"
DATASET_ID = "erm2-nwe9"
DATASET_NAME = "NYC 311 Service Requests from 2020 to Present (NYC Open Data)"
MANIFEST_DOC = "docs/data/MANIFEST.md"

SNAPSHOT = (
    "The warehouse is a historical snapshot, not live monitoring. Answers describe data up to the dataset cutoff only."
)
REPORTED_NOT_INCIDENTS = (
    "311 counts measure complaints reported to 311, not the true number of incidents; reporting "
    "behaviour differs between places and over time."
)
RAW_COUNTS = "Counts are raw totals, not normalized by population, housing units or building stock."
POINT_IN_TIME = (
    "Counts come from the latest version of each record; what was visible on a past day cannot be "
    "reconstructed (NOT VERIFIED point-in-time)."
)
MEDIAN_CENSORING = (
    "Median hours to close excludes requests that are still open, so it is biased low when many "
    "requests remain open (right-censoring)."
)
STATUS_SNAPSHOT = (
    "Open/closed status reflects the latest published record version; the publisher bulk-rewrote all "
    "2023-2024 records on 2025-12-26, so status is not comparable across that boundary."
)
FORECAST_LIMITS = (
    "LightGBM test WAPE 0.318 (2025-11-01 to 2026-10-30) improves on seasonal naive and the 28-day "
    "mean, but its advantage over the plain naive baseline is not statistically established "
    "(bootstrap 95% CI of the WAPE difference includes 0).",
    "Heat/hot-water forecasts do not improve on the naive baseline (test WAPE 0.405 vs 0.403); the "
    "model has no weather input.",
    "Intervals are nominal 90% but covered 86.4% of test outcomes (heat 83.6%, noise 89.2%).",
    "Forecasts are made from the dataset cutoff, not from today's date.",
)
ANOMALY_LIMITS = (
    "Flags are statistical deviations requiring investigation, not confirmed incidents. No real-world "
    "incident labels exist.",
    "Reported recall (29.9% at |z| >= 3.5) was measured on synthetic injected anomalies only; about "
    "half of +100% one-day spikes and few changes under 50% are caught.",
    "The 8-week same-weekday baseline lags seasonal step changes: 469 of 718 historical heat flags fall "
    "in October (heating-season onset).",
    "A flag says a count was unusual, not why. No cause is inferred.",
)
DELAY_LIMITS = (
    "Experimental research model. Not validated for prioritizing requests, allocating services, "
    "ranking communities or any real-world intervention decision.",
    "Probabilities are badly under-calibrated on 2026 data (mean predicted 0.57% vs 2.74% observed).",
    "Within a category the ranking is at chance level (heat ROC-AUC 0.518).",
    "The label's historical status cannot be reconstructed (see AI readiness audit), so numerical "
    "predictions are disabled for the AI analyst.",
)
ATTRIBUTION_NOT_CAUSAL = (
    "Feature attributions describe associations the model uses to predict. They are not causal effects."
)

PROHIBITED_DELAY_USES = (
    "prioritizing individual requests",
    "recommending service or inspector allocation",
    "ranking boroughs or communities by operational need",
    "treating individual probabilities as reliable",
    "recommending real-world interventions",
)

# Capability class per MCP tool (and per sub-capability where a tool mixes classes).
TOOL_CAPABILITY: dict[str, Capability] = {
    "get_service_request_trends": Capability.VALIDATED,
    "compare_boroughs": Capability.VALIDATED,
    "get_category_breakdown": Capability.VALIDATED,
    "forecast_request_volume": Capability.EXPERIMENTAL,
    "predict_closure_delay": Capability.DISABLED,
    "detect_unusual_spikes": Capability.EXPERIMENTAL,
    "explain_model_prediction": Capability.EXPERIMENTAL,
    "get_dataset_quality": Capability.VALIDATED,
    "get_model_evaluation": Capability.VALIDATED,
    "generate_investigation_report": Capability.VALIDATED,
}

CAPABILITY_TABLE: list[dict[str, str]] = [
    {"capability": "Historical counts, trends, comparisons, breakdowns", "class": "VALIDATED"},
    {"capability": "Median hours to close; open beyond 7 days (maturity-aware)", "class": "VALIDATED"},
    {"capability": "Dataset freshness and quality", "class": "VALIDATED"},
    {"capability": "Saved model evaluation metrics", "class": "VALIDATED"},
    {"capability": "7-day forecast values and intervals", "class": "EXPERIMENTAL"},
    {"capability": "Anomaly flags", "class": "EXPERIMENTAL"},
    {"capability": "Forecast feature importance", "class": "EXPERIMENTAL"},
    {"capability": "Synthetic anomaly recall", "class": "NOT_VERIFIED"},
    {"capability": "Point-in-time historical counts", "class": "NOT_VERIFIED"},
    {"capability": "Closure-delay methodology and evaluation", "class": "EXPERIMENTAL"},
    {"capability": "Closure-delay numerical predictions, rankings, per-request explanations", "class": "DISABLED"},
    {"capability": "Causal explanations", "class": "DISABLED"},
    {"capability": "Live operational monitoring", "class": "DISABLED"},
]
