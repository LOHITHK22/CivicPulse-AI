export type InvestigationStatus = "answered" | "clarification" | "refused" | "fallback" | "error";

export interface TraceStep {
  step: number;
  kind: "llm" | "tool" | "validation" | "policy";
  name: string;
  status: string;
  arguments: Record<string, unknown> | null;
  duration_ms: number;
  error_kind: string | null;
  message: string | null;
  evidence_id: string | null;
  data_source: string | null;
  model_version: string | null;
  capability: string | null;
}

export interface EvidenceItem {
  evidence_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  status: string;
  capability: string;
  dataset_id: string | null;
  data_build_id: string | null;
  artifact: string | null;
  data_cutoff: string | null;
  last_complete_date: string | null;
  metric_definitions: Record<string, string>;
  summary: string;
  key_measurements: { name: string; value: unknown; unit?: string | null; definition?: string | null }[];
  model_version: string | null;
  model_run_id: string | null;
  limitations: string[];
  uncertainty: string[];
  duration_ms: number;
  coverage: { requested_start?: string; requested_end?: string; effective_start?: string; effective_end?: string; status?: string; note?: string } | null;
}

export interface ChartSpec {
  evidence_id: string;
  kind: "line" | "bar";
  title: string;
  y_label: string;
  series: { name: string; points: { x: string; y: number | null }[] }[];
}

export interface AnalystWarning {
  level: "info" | "caution" | "restricted";
  capability: string | null;
  tool: string | null;
  message: string;
}

export interface GroundingAttempt {
  ok: boolean;
  semantic?: SemanticValidation;
  citations_used: string[];
  invalid_citations: string[];
  numbers_checked: number;
  unsupported_numbers: string[];
  policy_violations: string[];
}

export interface ClaimRecord {
  id: string;
  type: "measurement" | "derived" | "comparison" | "evidence_text";
  status: string;
  reason: string;
  evidence_ids: string[];
  fact_id?: string | null;
  metric_id: string | null;
  geography_id: string | null;
  category_id: string | null;
  period_start: string | null;
  period_end: string | null;
  value: number | string | null;
  unit: string | null;
  snapshot_id?: string | null;
  model_version?: string | null;
  method?: string | null;
  operation?: string | null;
  operands?: string[];
  relation?: string | null;
}

export interface SemanticValidation {
  ok: boolean;
  structured: boolean;
  claims_total: number;
  claims_valid: number;
  narrative_violations: string[];
  parse_errors: string[];
}

export interface Investigation {
  investigation_id: string;
  question: string;
  status: InvestigationStatus;
  answer: string;
  evidence: EvidenceItem[];
  trace: TraceStep[];
  validation: { ok?: boolean; attempts?: GroundingAttempt[]; regenerations?: number; fallback?: GroundingAttempt; screening?: string };
  warnings: AnalystWarning[];
  periods: { phrase: string; start: string; end: string; basis: string; covered: string; note: string }[];
  freshness: { data_cutoff?: string; last_complete_date?: string; first_date?: string; build_id?: string; live?: boolean; today?: string; model_versions?: Record<string, string> };
  charts: ChartSpec[];
  claims?: ClaimRecord[];
  provider: { name: string; model: string | null; live_llm: boolean };
  usage: { input_tokens: number; output_tokens: number; tool_calls: number; provider_calls: number; estimated_cost_usd: number };
  mcp: { protocol_version?: string; server?: string; server_version?: string; transport?: string; tools_offered?: number };
  duration_ms: number;
}

export interface AnalystStatus {
  enabled: boolean;
  provider: string;
  model: string | null;
  live_llm: boolean;
  auth_required: boolean;
  disabled_reason?: string | null;
  paid_ai?: { enabled: boolean; blockers: string[] };
  limits: { max_tool_calls: number; max_output_tokens: number; request_timeout_s: number; max_question_chars: number };
  tools: string[];
  capabilities: { capability: string; class: string }[];
}
