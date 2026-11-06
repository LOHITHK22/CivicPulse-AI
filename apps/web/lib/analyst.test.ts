// Unit tests for the AI Analyst helpers. Run: npm run test:unit (Node's built-in runner with type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { Investigation } from "./ai-types.ts";
import {
  DEMO_BANNER,
  dashboardLinks,
  fmtClaimValue,
  isDemoProvider,
  isVerified,
  modelNotes,
  progressLabel,
  reportUrl,
  verifiedAnswerText,
} from "./analyst.ts";

function inv(over: Partial<Investigation> = {}): Investigation {
  return {
    investigation_id: "0123456789abcdef",
    question: "Which borough had the most residential noise complaints last month?",
    status: "answered",
    answer: "BROOKLYN had the most: 11,861 requests [E1].",
    evidence: [
      {
        evidence_id: "E1", tool: "compare_boroughs", arguments: {}, status: "ok", capability: "MEASURED",
        dataset_id: "erm2-nwe9", data_build_id: "b1", artifact: null, data_cutoff: "2026-11-01", last_complete_date: "2026-10-31",
        metric_definitions: {}, summary: "Borough totals", key_measurements: [], model_version: null, model_run_id: null,
        limitations: [], uncertainty: [], duration_ms: 12, coverage: null,
      },
    ],
    trace: [],
    validation: { ok: true },
    warnings: [],
    periods: [],
    freshness: { data_cutoff: "2026-11-01", build_id: "b1" },
    charts: [],
    claims: [
      { id: "C1", type: "measurement", status: "valid", reason: "", evidence_ids: ["E1"], metric_id: "requests", geography_id: "brooklyn",
        category_id: "noise_residential", period_start: "2026-10-01", period_end: "2026-10-31", value: 11861, unit: "requests" },
    ],
    provider: { name: "fake", model: "deterministic-fake-v1", live_llm: false },
    usage: { input_tokens: 0, output_tokens: 0, tool_calls: 1, provider_calls: 2, estimated_cost_usd: 0 },
    mcp: {},
    duration_ms: 100,
    ...over,
  };
}

test("demo banner text is exact", () => {
  assert.equal(DEMO_BANNER, "Demonstration Mode — deterministic analytical assistant, not a live LLM.");
});

test("fake or non-live providers are disclosed; live and disabled are not", () => {
  assert.equal(isDemoProvider({ name: "fake", live_llm: false }), true);
  assert.equal(isDemoProvider({ provider: "fake", live_llm: false }), true);
  assert.equal(isDemoProvider({ name: "openai", live_llm: true }), false);
  assert.equal(isDemoProvider({ name: "disabled", live_llm: false }), false);
  assert.equal(isDemoProvider(undefined), false);
});

test("verification mirrors the server rule", () => {
  assert.equal(isVerified(inv()), true);
  assert.equal(isVerified(inv({ validation: { ok: false } })), false);
  assert.equal(isVerified(inv({ status: "fallback", validation: { ok: false, fallback: { ok: true } as never } })), true);
  assert.equal(isVerified(inv({ status: "fallback", validation: { ok: false, fallback: { ok: false } as never } })), false);
  assert.equal(isVerified(inv({ status: "refused", validation: {} })), false);
  assert.equal(isVerified(inv({ status: "error", validation: { ok: true } })), false);
});

test("copy text holds only verified content and discloses demo mode", () => {
  const text = verifiedAnswerText(inv());
  assert.ok(text);
  assert.match(text, /C1: requests · brooklyn · noise_residential · 2026-10-01 to 2026-10-31 = 11,861 \[E1\]/);
  assert.ok(text.includes(DEMO_BANNER));
  assert.ok(text.includes("not live"));
  assert.equal(verifiedAnswerText(inv({ validation: { ok: false } })), null);
  const failedClaim = inv({ claims: [{ ...inv().claims![0], status: "value_mismatch", id: "C9" }] });
  assert.ok(!verifiedAnswerText(failedClaim)!.includes("C9"));
  const live = verifiedAnswerText(inv({ provider: { name: "openai", model: "m", live_llm: true } }));
  assert.ok(live && !live.includes("Demonstration Mode"));
});

test("report URLs are built only from valid ids", () => {
  assert.equal(reportUrl("0123456789abcdef", "markdown"), "/api/v1/ai/investigations/0123456789abcdef/report?format=markdown&download=true");
  assert.equal(reportUrl("0123456789abcdef", "html", false), "/api/v1/ai/investigations/0123456789abcdef/report?format=html");
  assert.throws(() => reportUrl("../../etc/passwd", "json"));
});

test("claim values format like the server renderer", () => {
  assert.equal(fmtClaimValue(0.25, "fraction"), "25%");
  assert.equal(fmtClaimValue(0.1234, "fraction"), "12.3%");
  assert.equal(fmtClaimValue(11861, "requests"), "11,861");
  assert.equal(fmtClaimValue("BROOKLYN", "label"), "BROOKLYN");
  assert.equal(fmtClaimValue(null, "requests"), "—");
});

test("dashboard links follow the tools used, without duplicates", () => {
  const base = inv().evidence[0];
  const r = inv({ evidence: [base, { ...base, evidence_id: "E2", tool: "get_category_breakdown" }, { ...base, evidence_id: "E3", tool: "get_service_request_trends" }, { ...base, evidence_id: "E4", tool: "forecast_request_volume" }] });
  assert.deepEqual(dashboardLinks(r).map((l) => l.href), ["/geo", "/explorer", "/forecast"]);
});

test("model notes explain baselines, unverified anomalies and research-only delay model", () => {
  const base = inv().evidence[0];
  const notes = modelNotes({ evidence: ["forecast_request_volume", "detect_unusual_spikes", "predict_closure_delay"].map((tool) => ({ ...base, tool })) });
  assert.equal(notes.length, 3);
  assert.match(notes[0], /baselines/);
  assert.match(notes[1], /unverified/);
  assert.match(notes[2], /research-only/);
  assert.deepEqual(modelNotes(inv()), []);
});

test("progress label reports elapsed time only", () => {
  assert.match(progressLabel(3400), /^Investigating · 3 s elapsed\./);
  assert.match(progressLabel(-5), /0 s elapsed/);
});
