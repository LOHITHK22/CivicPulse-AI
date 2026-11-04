// TypeScript mirrors of the Milestone 2 model endpoints (packages/analytics/civicpulse_analytics/predictions.py).
// Numbers that can be undefined in the evaluation (e.g. no interval yet) are typed `number | null`.

export interface PredMeta { elapsed_ms: number; rows: number; model_run_id: string | null }

export interface Period { target_start: string; target_end: string; origins: number; first_origin: string; last_origin: string }

export interface Registry {
  run_id: string;
  created_at_utc: string;
  git_commit: string;
  config_sha256: string;
  seed: number;
  versions: Record<string, string>;
  data: {
    build_id: string; fact_sha256: string; rows: number; min_created_at: string; max_created_at: string;
    last_complete_date: string; source_rows_updated_at: string | null;
  };
  models: {
    forecast: {
      version: string; selected: string; selection_rule: string; candidates: string[];
      periods: { validation: Period; test: Period; training: string };
      interval: { method: string; level: number; min_residuals: number };
      production_origin: string;
    };
    delay_risk: {
      version: string; selected: string; selection_rule: string; candidates: string[]; target: string;
      target_definition: string;
      snapshot: { observed_until: string; maturity_cutoff: string; threshold_days: number };
      splits: Record<string, { n: number; positive_rate: number | null; created_from: string; created_to: string }>;
    };
    anomaly: { version: string; detector: Record<string, string | number> };
  };
}

export interface ForecastPoint { target_date: string; horizon: number; predicted: number; lower: number | null; upper: number | null }

export interface SeriesMetric {
  model: string; period: string; category_key?: string; borough?: string; horizon?: number; origin?: string;
  n: number; actual_total: number; mae: number; wape: number | null; bias: number;
  interval_coverage: number | null; interval_mean_width: number | null; n_with_interval: number;
}

export interface ForecastResponse {
  category_key: string; borough: string; origin: string | null; model: string | null; interval_level: number | null;
  forecast: ForecastPoint[]; history: { date: string; actual: number }[]; test_metrics: SeriesMetric[]; meta: PredMeta;
}

export interface BacktestResponse {
  category_key: string; borough: string; horizon: number; model: string;
  points: { target_date: string; origin: string; period: string; actual: number; predicted: number; lower: number | null; upper: number | null }[];
  meta: PredMeta;
}

export interface Comparison {
  candidate: string; baseline: string; metric: string; period: string; baseline_value: number; candidate_value: number;
  relative_improvement: number;
  wape_diff_baseline_minus_candidate: { diff: number; ci_low: number; ci_high: number; n_groups: number; n_boot: number };
  significant_at_95: boolean;
}

export interface ForecastEvaluation {
  target: string; horizon_days: number;
  history: { start: string; end: string; source_gap_dates: string[] };
  periods: { validation: Period; test: Period; training: string };
  interval: { method: string; level: number; min_residuals: number };
  overall: SeriesMetric[]; by_horizon: SeriesMetric[]; by_category: SeriesMetric[]; by_series: SeriesMetric[];
  by_origin: SeriesMetric[];
  selection: { rule: string; validation_wape: Record<string, number>; selected: string };
  comparisons: Comparison[];
  feature_importance: {
    permutation_test_wape_increase: { feature: string; wape_increase_mean: number; wape_increase_std: number }[];
    gain_production_model: { feature: string; gain: number }[];
  };
}

export interface ClassSummary {
  n: number; positive_rate: number; pr_auc: number | null; roc_auc: number | null; brier: number; log_loss: number;
  ece: number | null; mean_predicted: number;
}
export interface ReliabilityBin { mean_predicted: number; observed_rate: number; p_min: number; p_max: number; n: number }
export interface SplitEval {
  overall: ClassSummary;
  at_threshold: { threshold: number; precision: number | null; recall: number | null; f1: number | null; flagged_share: number };
  top_10pct: { share: number; k: number; precision: number; recall: number };
  reliability: ReliabilityBin[];
  by_category: (ClassSummary & { category_key: string })[];
  by_borough: (ClassSummary & { borough: string })[];
}
export interface DelayEvaluation {
  target: string; target_definition: string;
  snapshot: { observed_until: string; maturity_cutoff: string; threshold_days: number };
  splits: Record<string, { n: number; positive_rate: number | null; created_from: string; created_to: string }>;
  split_rules: Record<string, unknown>;
  features: { categorical: string[]; numeric: string[]; never_used: string[] };
  models: Record<string, { threshold_rule: string; threshold: number; validation: SplitEval; test: SplitEval }>;
  selection: { rule: string; validation_pr_auc: Record<string, number>; selected: string };
  explanations: {
    logistic_regression_coefficients: { feature: string; level: string | null; coefficient: number }[];
    lightgbm_gain: { feature: string; gain: number }[];
    permutation_test_pr_auc_drop: Record<string, { feature: string; pr_auc_drop_mean: number; pr_auc_drop_std: number }[]>;
    permutation_sample: number;
  };
  train_prevalence: number;
}

export interface RiskRow {
  unique_key: string; created_at: string; category_key: string; descriptor: string; borough: string;
  community_board: string; channel: string; risk: number;
}
export interface RiskList { total: number; rows: RiskRow[]; meta: PredMeta }
export interface RiskSummary {
  live_window: { first: string | null; last: string | null; n: number };
  live_by_segment: { category_key: string; borough: string; requests: number; mean_risk: number; risk_at_least_50pct: number }[];
  test_risk_histogram: { bin: number; requests: number; observed_rate: number }[];
  live_risk_histogram: { bin: number; requests: number }[];
  meta: PredMeta;
}
export interface RiskDetail extends RiskRow {
  split: string; label: number | null; model: string; status: string; causality_note: string;
  explanation: { feature: string; value: string | null; contribution: number }[] | null;
}

export interface AnomalyRow {
  date: string; category_key: string; borough: string; actual: number; expected: number; residual: number;
  score: number; direction: "spike" | "drop";
}
export interface AnomalyList { total: number; rows: AnomalyRow[]; label_status: string; meta: PredMeta }
export interface AnomalySeries {
  category_key: string; borough: string;
  points: { date: string; actual: number; expected: number; score: number; is_anomaly: boolean; direction: string | null }[];
  available: { lo: string; hi: string }; meta: PredMeta;
}
export interface AnomalyEvaluation {
  synthetic: boolean; window_start: string; window_end: string; series: number;
  summary: { detector: string; threshold: number; recall: number; n_injected: number; alerts_uninjected: number;
    days_uninjected: number; alerts_injected: number; alert_rate_uninjected_days: number }[];
  by_magnitude: { detector: string; threshold: number; magnitude: number; direction: string; recall: number; n: number }[];
  by_series_size: { detector: string; threshold: number; series_size: string; recall: number; n: number }[];
  real_data: { label_status: string; days_scored: number; alerts: number; alerts_by_direction: { direction: string; len: number }[] };
  detector: Record<string, string | number>;
}
