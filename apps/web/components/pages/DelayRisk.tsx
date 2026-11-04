"use client";

import { useState } from "react";

import { CalibrationChart, DataTable, ImportanceBars, RiskHistogram, modelLabel } from "@/components/predict";
import { BOROUGHS, CATEGORIES, Caveats, Section, Select, Stat } from "@/components/pages/predict-shared";
import { Card, EmptyState, QueryInfo } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { CATEGORY_LABEL, fmtDate, fmtDateTime, fmtInt, fmtPct, titleCase } from "@/lib/format";
import type { DelayEvaluation, RiskDetail, RiskList, RiskSummary } from "@/lib/predict-types";

const ALL = "ALL";

export function DelayRisk() {
  const [category, setCategory] = useState(ALL);
  const [borough, setBorough] = useState(ALL);
  const [selected, setSelected] = useState<string | null>(null);
  const q = new URLSearchParams({ limit: "25" });
  if (category !== ALL) q.set("category", category);
  if (borough !== ALL) q.set("borough", borough);
  const list = useApi<RiskList>(`/api/v1/predictions/delay-risk?${q.toString()}`);
  const summary = useApi<RiskSummary>("/api/v1/predictions/delay-risk/summary");
  const ev = useApi<DelayEvaluation>("/api/v1/models/delay_risk/evaluation");

  return (
    <div className="space-y-6">
      <Section state={ev} height="h-24">
        {(e) => {
          const sel = e.models[e.selection.selected];
          return (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <Stat label="Model in use" value={modelLabel(e.selection.selected)} note="Highest validation PR-AUC" />
              <Stat label="Test PR-AUC" value={sel.test.overall.pr_auc?.toFixed(3) ?? "—"}
                note={`Base rate ${fmtPct(sel.test.overall.positive_rate)} (a random ranking scores about this)`} />
              <Stat label="Test Brier score" value={sel.test.overall.brier.toFixed(3)}
                note={`Majority-class baseline: ${e.models.majority_class.test.overall.brier.toFixed(3)} (lower is better)`} />
              <Stat label="Labels mature up to" value={fmtDate(e.snapshot.maturity_cutoff)}
                note={`Newer requests are scored but their 7-day outcome is not known yet`} />
            </div>
          );
        }}
      </Section>

      <Card id="list" title="Recent requests most at risk of staying open past 7 days"
        subtitle="Requests created after the label-maturity cutoff, so the outcome is still unknown. Select a row to see what drives its score."
        actions={list.status === "success" && <QueryInfo meta={list.data.meta} />}>
        <div className="mb-4 flex flex-wrap gap-3">
          <Select label="Category" value={category} options={[ALL, ...CATEGORIES]} onChange={setCategory}
            render={(v) => (v === ALL ? "All categories" : CATEGORY_LABEL[v] ?? v)} />
          <Select label="Borough" value={borough} options={[ALL, ...BOROUGHS]} onChange={setBorough}
            render={(v) => (v === ALL ? "All boroughs" : titleCase(v))} />
        </div>
        <Section state={list}>
          {(l) => l.rows.length === 0 ? <EmptyState message="No scored recent requests match." /> : (
            <div className="grid gap-6 xl:grid-cols-5">
              <div className="min-w-0 overflow-x-auto xl:col-span-3" tabIndex={0} role="region" aria-label="Scrollable table">
                <table className="w-full text-left text-xs">
                  <caption className="sr-only">Highest-risk recent requests</caption>
                  <thead className="text-ink-3"><tr>{["Request", "Created", "Category", "Borough", "Descriptor", "Risk"].map((h) =>
                    <th key={h} scope="col" className="py-1.5 pr-3 font-medium">{h}</th>)}</tr></thead>
                  <tbody className="tabular text-ink-2">
                    {l.rows.map((r) => (
                      <tr key={r.unique_key} className={`border-t border-border ${selected === r.unique_key ? "bg-surface-3" : ""}`}>
                        <td className="py-1.5 pr-3">
                          <button type="button" onClick={() => setSelected(r.unique_key)} aria-pressed={selected === r.unique_key}
                            className="text-teal underline-offset-2 hover:underline">{r.unique_key}</button>
                        </td>
                        <td className="pr-3">{fmtDateTime(r.created_at)}</td>
                        <td className="pr-3">{CATEGORY_LABEL[r.category_key]}</td>
                        <td className="pr-3">{titleCase(r.borough)}</td>
                        <td className="pr-3">{r.descriptor}</td>
                        <td className="pr-3 font-medium text-ink">{fmtPct(r.risk)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 text-[11px] text-ink-3">Showing {l.rows.length} of {fmtInt(l.total)} scored recent requests.</p>
              </div>
              <div className="min-w-0 xl:col-span-2">
                {selected ? <RiskExplanation id={selected} /> :
                  <EmptyState message="Select a request to see its explanation." />}
              </div>
            </div>
          )}
        </Section>
      </Card>

      <div className="grid gap-6 [&>*]:min-w-0 xl:grid-cols-2">
        <Card id="cal" title="Calibration on the held-out test period"
          subtitle="Each point is 10% of test requests: mean predicted risk vs share actually not closed within 7 days. On the dashed line = well calibrated.">
          <Section state={ev}>
            {(e) => <CalibrationChart label="Calibration (test)" series={["logistic_regression", "lightgbm"].map((m) => ({
              model: m, bins: e.models[m].test.reliability }))} />}
          </Section>
        </Card>
        <Card id="hist" title="Predicted risk of recent requests" subtitle="Distribution of scores for requests whose outcome is not known yet">
          <Section state={summary}>{(s) => <RiskHistogram rows={s.live_risk_histogram} label="Recent risk distribution" />}</Section>
        </Card>
      </div>

      <div className="grid gap-6 [&>*]:min-w-0 xl:grid-cols-2">
        <Card id="seg" title="Test performance by segment" subtitle="Selected model; segments with at least 200 test requests">
          <Section state={ev}>
            {(e) => {
              const t = e.models[e.selection.selected].test;
              const rows = [
                ...t.by_category.map((r) => [CATEGORY_LABEL[r.category_key] ?? r.category_key, r]),
                ...t.by_borough.map((r) => [titleCase(r.borough), r]),
              ] as [string, typeof t.overall][];
              return (
                <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
                  <DataTable caption="Performance by segment" head={["Segment", "Requests", "Base rate", "PR-AUC", "ROC-AUC", "Brier", "ECE"]}
                    rows={rows.map(([name, r]) => [name, fmtInt(r.n), fmtPct(r.positive_rate, 2), r.pr_auc?.toFixed(4) ?? "—",
                      r.roc_auc?.toFixed(3) ?? "—", r.brier.toFixed(3), r.ece?.toFixed(3) ?? "—"])} />
                </div>
              );
            }}
          </Section>
        </Card>
        <Card id="imp" title="What the model relies on" subtitle="Drop in test PR-AUC when one feature is shuffled (permutation importance)">
          <Section state={ev}>
            {(e) => {
              const perm = e.explanations.permutation_test_pr_auc_drop[e.selection.selected === "logistic_regression" ? "logistic_regression" : "lightgbm"];
              return <ImportanceBars label="Permutation importance" valueLabel="PR-AUC drop"
                rows={perm.slice(0, 12).map((p) => ({ feature: p.feature, value: p.pr_auc_drop_mean, err: p.pr_auc_drop_std }))} />;
            }}
          </Section>
        </Card>
      </div>

      <Caveats items={[
        "The label uses each request's closure as it appears in the latest snapshot. The source keeps no status history, so a request closed, re-opened and closed again after day 7 counts as delayed even if it looked closed on day 7.",
        "Requests with invalid closure records (closed before created, closed in the future, or status Closed with no date) are excluded because their real closure time is unknown.",
        "Features use only information available when the request was created. Status, closure, resolution and due-date fields are never used.",
        "Scores rank requests by risk; they are associations learned from history, not causes. Agency workload, policy and staffing are not observed.",
      ]} />
    </div>
  );
}

function RiskExplanation({ id }: { id: string }) {
  const d = useApi<RiskDetail>(`/api/v1/predictions/delay-risk/${id}`);
  return (
    <Section state={d}>
      {(r) => (
        <div aria-live="polite">
          <p className="text-sm font-medium text-ink">Request {r.unique_key}: {fmtPct(r.risk)} risk</p>
          <p className="mt-0.5 text-xs text-ink-3">{r.status} · {modelLabel(r.model)}</p>
          {r.explanation ? (
            <>
              <p className="mt-3 text-xs text-ink-3">Largest contributions (log-odds; positive pushes risk up)</p>
              <ul className="mt-2 space-y-1.5">
                {r.explanation.map((c) => (
                  <li key={c.feature} className="flex items-center gap-2 text-xs">
                    <span className="w-44 shrink-0 truncate text-ink-2" title={`${c.feature} = ${c.value ?? "missing"}`}>
                      {c.feature} = <span className="text-ink">{c.value ?? "missing"}</span>
                    </span>
                    <span className="relative h-2.5 flex-1 rounded bg-surface-3">
                      <span className="absolute top-0 h-2.5 rounded" style={{
                        left: c.contribution >= 0 ? "50%" : `${50 - Math.min(50, Math.abs(c.contribution) * 25)}%`,
                        width: `${Math.min(50, Math.abs(c.contribution) * 25)}%`,
                        background: c.contribution >= 0 ? "var(--series-2)" : "var(--series-1)",
                      }} />
                    </span>
                    <span className="tabular w-14 text-right text-ink">{c.contribution > 0 ? "+" : ""}{c.contribution.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : <p className="mt-3 text-xs text-ink-3">No per-request explanation stored for this request.</p>}
          <p className="mt-3 text-[11px] leading-snug text-ink-3">{r.causality_note}</p>
        </div>
      )}
    </Section>
  );
}
