// Pure, dependency-free helpers for the AI Analyst UI (unit-tested with `node --test`, no browser needed).
// Only type imports here so the file runs under Node's type stripping.
import type { ClaimRecord, Investigation } from "./ai-types.ts";

export const DEMO_BANNER = "Demonstration Mode — deterministic analytical assistant, not a live LLM.";

export type ReportFormat = "markdown" | "html" | "json";

/** The deterministic fake provider (or any non-live provider) must always be disclosed. */
export function isDemoProvider(p: { name?: string | null; provider?: string | null; live_llm?: boolean | null } | null | undefined): boolean {
  if (!p) return false;
  const name = p.name ?? p.provider ?? "";
  if (name === "disabled") return false;
  return name === "fake" || p.live_llm !== true;
}

/** Mirrors the server's report rule: only answers that passed deterministic validation count as verified. */
export function isVerified(r: Pick<Investigation, "status" | "validation">): boolean {
  if (r.status === "answered" || r.status === "clarification") return r.validation.ok === true;
  if (r.status === "fallback") return r.validation.fallback?.ok === true;
  return false;
}

export function reportUrl(investigationId: string, format: ReportFormat, download = true): string {
  if (!/^[0-9a-f]{16}$/.test(investigationId)) throw new Error("invalid investigation id");
  const q = new URLSearchParams({ format });
  if (download) q.set("download", "true");
  return `/api/v1/ai/investigations/${investigationId}/report?${q.toString()}`;
}

export function fmtClaimValue(value: unknown, unit: string | null | undefined): string {
  if (typeof value === "string") return value;
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (unit === "fraction" || unit === "percent") {
    const pct = unit === "fraction" ? value * 100 : value;
    return Math.abs(pct - Math.round(pct)) < 1e-9 ? `${Math.round(pct)}%` : `${pct.toFixed(1)}%`;
  }
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  return value.toLocaleString("en-US", { maximumFractionDigits: 3 });
}

export function fmtPeriod(c: Pick<ClaimRecord, "period_start" | "period_end">): string {
  if (c.period_start && c.period_end) return c.period_start === c.period_end ? c.period_start : `${c.period_start} to ${c.period_end}`;
  return c.period_start ?? c.period_end ?? "—";
}

/** Text placed on the clipboard: the verified answer and its claims only, or null when nothing was verified. */
export function verifiedAnswerText(r: Investigation): string | null {
  if (!isVerified(r) || !r.answer.trim()) return null;
  const claims = (r.claims ?? []).filter((c) => c.status === "valid");
  const lines = [`Q: ${r.question}`, "", r.answer];
  if (claims.length) {
    lines.push("", "Verified claims:");
    for (const c of claims) {
      lines.push(`- ${c.id}: ${c.metric_id ?? "value"} · ${c.geography_id ?? "—"} · ${c.category_id ?? "—"} · ${fmtPeriod(c)} = ${fmtClaimValue(c.value, c.unit)} [${(c.evidence_ids ?? []).join(", ")}]`);
    }
  }
  if (r.evidence.length) {
    lines.push("", "Evidence:", ...r.evidence.map((e) => `[${e.evidence_id}] ${e.tool}: ${e.summary}`));
  }
  lines.push("", `Data cutoff ${r.freshness.data_cutoff ?? "unknown"} (historical snapshot ${r.freshness.build_id ?? ""}, not live).`.replace(" ,", ","));
  if (isDemoProvider(r.provider)) lines.push(DEMO_BANNER);
  return lines.join("\n");
}

export const TOOL_PAGES: Record<string, { href: string; label: string }> = {
  get_service_request_trends: { href: "/explorer", label: "Service Request Explorer" },
  compare_boroughs: { href: "/geo", label: "Geographic Intelligence" },
  get_category_breakdown: { href: "/explorer", label: "Service Request Explorer" },
  forecast_request_volume: { href: "/forecast", label: "Forecast Lab" },
  predict_closure_delay: { href: "/delay-risk", label: "Delay Risk" },
  detect_unusual_spikes: { href: "/anomalies", label: "Anomaly Center" },
  explain_model_prediction: { href: "/models", label: "Model Evaluation" },
  get_dataset_quality: { href: "/quality", label: "Data Quality" },
  get_model_evaluation: { href: "/models", label: "Model Evaluation" },
};

/** Dashboard pages that show the same data the investigation used, in first-use order, without duplicates. */
export function dashboardLinks(r: Pick<Investigation, "evidence">): { href: string; label: string }[] {
  const out: { href: string; label: string }[] = [];
  for (const e of r.evidence) {
    const page = TOOL_PAGES[e.tool];
    if (page && !out.some((o) => o.href === page.href)) out.push(page);
  }
  return out;
}

/** Fixed interpretation notes for the model-backed tools an answer used (independent of the provider's text). */
export function modelNotes(r: Pick<Investigation, "evidence">): string[] {
  const tools = new Set(r.evidence.map((e) => e.tool));
  const notes: string[] = [];
  if (tools.has("forecast_request_volume") || tools.has("get_model_evaluation")) {
    notes.push("Forecasts are judged against seasonal-naive and moving-average baselines; Model Evaluation shows where the model does and does not beat them.");
  }
  if (tools.has("detect_unusual_spikes")) {
    notes.push("Anomaly flags are statistical outliers against an expected baseline, not confirmed incidents; they are unverified.");
  }
  if (tools.has("predict_closure_delay") || tools.has("explain_model_prediction")) {
    notes.push("The closure-delay model is experimental and research-only: it must not be used to prioritize, allocate or rank.");
  }
  return notes;
}

/** Honest progress text: the request is a single call, so only elapsed time is known, not the current step. */
export function progressLabel(elapsedMs: number): string {
  const s = Math.max(0, Math.floor(elapsedMs / 1000));
  return `Investigating · ${s} s elapsed. The server plans, calls read-only MCP tools, then validates every claim before replying.`;
}
