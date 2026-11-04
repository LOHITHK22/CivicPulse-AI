// TypeScript mirrors of the API's Pydantic models (packages/analytics/civicpulse_analytics/models.py).

export interface QueryMeta {
  elapsed_ms: number;
  rows: number;
  data_build_id: string | null;
  snapshot_at_local: string | null;
}

export interface CategoryOption {
  key: string;
  label: string;
  complaint_types: string[];
  requests: number;
}

export interface FilterOptions {
  min_date: string | null;
  max_date: string | null;
  last_complete_date: string | null;
  categories: CategoryOption[];
  boroughs: string[];
  agencies: string[];
  statuses: string[];
  total_records: number;
}

export interface Freshness {
  latest_created_at: string | null;
  snapshot_at_local: string | null;
  data_lag_hours: number | null;
  source_rows_updated_at: string | null;
  last_build_at_utc: string | null;
  is_live: boolean;
}

export interface PeriodComparison {
  current_total: number;
  prior_total: number | null;
  prior_start: string | null;
  prior_end: string | null;
  pct_change: number | null;
  note: string | null;
}

export interface Kpis {
  total_requests: number;
  comparison: PeriodComparison;
  open_requests: number;
  closed_requests: number;
  unknown_status_requests: number;
  median_hours_to_close: number | null;
  closed_with_valid_duration: number;
  open_beyond_threshold_pct: number | null;
  open_beyond_threshold_numerator: number;
  open_beyond_threshold_denominator: number;
  threshold_days: number;
  freshness: Freshness;
  categories_with_unusual_increase: number | null;
  unusual_increase_note: string;
  definitions: Record<string, string>;
  meta: QueryMeta;
}

export interface SeriesPoint {
  period: string;
  key: string;
  value: number;
}

export interface Series {
  grain: string;
  points: SeriesPoint[];
  meta: QueryMeta;
}

export interface BreakdownRow {
  key: string;
  label: string;
  requests: number;
  share: number;
  open_requests: number;
  closed_requests: number;
  median_hours_to_close: number | null;
  closed_within_7d_pct: number | null;
}

export interface Breakdown {
  dimension: string;
  rows: BreakdownRow[];
  meta: QueryMeta;
}

export interface Bucket {
  label: string;
  lower_hours: number | null;
  upper_hours: number | null;
  count: number;
}

export interface ClosureDistribution {
  buckets: Bucket[];
  still_open: number;
  excluded_invalid: number;
  meta: QueryMeta;
}

export interface HourlyPattern {
  cells: { dow: number; hour: number; count: number }[];
  meta: QueryMeta;
}

export interface RequestRow {
  unique_key: string;
  created_at: string;
  closed_at: string | null;
  category_key: string;
  complaint_type: string;
  descriptor: string | null;
  agency: string | null;
  status: string;
  status_group: string;
  borough: string;
  community_board: string | null;
  incident_zip: string | null;
  hours_to_close: number | null;
}

export interface RequestPage {
  rows: RequestRow[];
  total: number;
  page: number;
  page_size: number;
  sort: string;
  meta: QueryMeta;
}

export interface RequestDetail extends RequestRow {
  agency_name: string | null;
  location_type: string | null;
  channel: string | null;
  council_district: string | null;
  police_precinct: string | null;
  due_at: string | null;
  resolution_updated_at: string | null;
  quality_flags: string[];
  lineage: Record<string, string | null>;
}

export interface QualityRule {
  rule_id: string;
  description: string;
  category: string;
  severity: string;
  records_checked: number;
  records_failed: number;
  failure_rate: number;
  threshold: number | null;
  status: "pass" | "warn" | "fail" | "info";
  detail: string;
}

export interface RunSummary {
  run_id: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  pages_written: number;
  rows_fetched: number;
  total_attempts: number;
  error: string | null;
  schema_warnings: string[];
}

export interface QualityReport {
  build: Record<string, string | number | null> | null;
  rules: QualityRule[];
  status_counts: Record<string, number>;
  runs: RunSummary[];
  failed_batches: Record<string, unknown>[];
  source: { checked_at?: string; rows_updated_at?: string; name?: string; attribution?: string;
            schema_report?: { added: string[]; removed: string[]; type_changed: Record<string, string[]>;
                              missing_selected: string[] } } | null;
  freshness: Freshness;
  build_history: { build_id: string; built_at_utc: string; pass: number; warn: number; fail: number }[];
}
