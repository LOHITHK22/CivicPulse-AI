"use client";

import { useState } from "react";

import { KpiCard } from "@/components/KpiCard";
import { Card, ErrorState, Loading, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { fmtDateTime, fmtInt, fmtPct } from "@/lib/format";
import type { QualityReport, QualityRule } from "@/lib/types";

const CATEGORY_ORDER = ["uniqueness", "completeness", "validity", "missingness", "freshness", "consistency", "schema"];

export function Quality() {
  const [reload, setReload] = useState(0);
  const report = useApi<QualityReport>("/api/v1/quality", reload);
  if (report.status === "loading") return <Loading label="Loading quality report" className="h-96" />;
  if (report.status === "error") return <ErrorState error={report.error} onRetry={() => setReload((r) => r + 1)} />;
  const r = report.data;
  const b = r.build ?? {};
  const counts = r.status_counts;
  const schema = r.source?.schema_report;
  const lastRun = r.runs[0];

  return (
    <div className="space-y-6">
      <section aria-label="Data health summary" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard label="Records accepted" value={fmtInt(Number(b.accepted_rows ?? 0))}
          detail={<span className="text-ink-3">{fmtInt(Number(b.rejected_rows ?? 0))} rejected · {fmtInt(Number(b.raw_rows ?? 0))} raw rows</span>} />
        <KpiCard label="Superseded duplicates" value={fmtInt(Number(b.duplicate_raw_rows ?? 0))}
          detail={<span className="text-ink-3">Older versions of the same request, resolved by dedup</span>} />
        <KpiCard label="Rule outcomes" value={`${counts.pass ?? 0} pass`}
          detail={<span className="text-ink-3">{counts.warn ?? 0} warning · {counts.fail ?? 0} fail · {counts.info ?? 0} informational</span>} />
        <KpiCard label="Last successful ingestion"
          value={lastRun ? fmtDateTime(r.runs.find((x) => x.status === "succeeded")?.finished_at ?? null) : "—"}
          detail={<span className="text-ink-3">UTC · source updated {fmtDateTime(r.freshness.source_rows_updated_at)} UTC</span>} />
      </section>

      <Card id="freshness" title="Freshness" subtitle="This application serves a versioned snapshot. It is not a live feed.">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
          {([
            ["Latest request created", fmtDateTime(r.freshness.latest_created_at) + " (NYC local)"],
            ["Snapshot built", fmtDateTime(r.freshness.last_build_at_utc) + " UTC"],
            ["Lag at build time", r.freshness.data_lag_hours != null ? `${r.freshness.data_lag_hours} h` : "—"],
            ["Source dataset last updated", fmtDateTime(r.freshness.source_rows_updated_at) + " UTC"],
          ] as const).map(([k, v]) => (
            <div key={k}><dt className="text-xs text-ink-3">{k}</dt><dd className="tabular text-ink">{v}</dd></div>
          ))}
        </dl>
      </Card>

      <Card id="rules" title="Validation rules" subtitle={`Build ${String(b.build_id ?? "—")} · thresholds in docs/data/QUALITY_RULES.md`}>
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
          <table className="w-full min-w-[760px] text-left text-sm">
            <caption className="sr-only">Data quality rule results for the latest build</caption>
            <thead className="text-xs text-ink-3">
              <tr>
                {["Rule", "Check", "Severity", "Failed / checked", "Rate", "Threshold", "Status"].map((h) => (
                  <th key={h} scope="col" className="py-2 pr-3 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            {CATEGORY_ORDER.filter((c) => r.rules.some((x) => x.category === c)).map((cat) => (
              <tbody key={cat} className="tabular">
                <tr><th colSpan={7} scope="rowgroup" className="pt-4 pb-1 text-left text-xs font-semibold uppercase tracking-wide text-ink-3">{cat}</th></tr>
                {r.rules.filter((x) => x.category === cat).map((rule) => <RuleRow key={rule.rule_id} rule={rule} />)}
              </tbody>
            ))}
          </table>
        </div>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card id="runs" title="Ingestion runs" subtitle="Most recent first">
          <ul className="divide-y divide-border text-sm">
            {r.runs.map((run) => (
              <li key={run.run_id} className="py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-xs text-ink-2">{run.run_id}</span>
                  <StatusBadge status={run.status} />
                </div>
                <p className="tabular mt-1 text-xs text-ink-3">
                  {run.mode} · {fmtInt(run.rows_fetched)} rows · {run.pages_written} pages · {run.total_attempts} HTTP attempts · started {fmtDateTime(run.started_at)} UTC
                </p>
                {run.error && <p className="mt-1 text-xs text-[color:var(--status-serious)]">{run.error}</p>}
              </li>
            ))}
          </ul>
        </Card>
        <Card id="schema" title="Source schema & failed batches">
          <h3 className="text-xs font-medium text-ink-3">Schema drift vs committed snapshot</h3>
          {schema ? (
            schema.added.length + schema.removed.length + Object.keys(schema.type_changed).length === 0
              ? <p className="mt-1 text-sm text-ink-2"><StatusBadge status="pass" /> No drift detected (checked {fmtDateTime(r.source?.checked_at)} UTC)</p>
              : <ul className="mt-1 list-disc pl-5 text-sm text-ink-2">
                  {schema.added.map((c) => <li key={c}>Added at source: {c}</li>)}
                  {schema.removed.map((c) => <li key={c}>Removed at source: {c}</li>)}
                  {Object.entries(schema.type_changed).map(([c, t]) => <li key={c}>{c}: {t.join(" → ")}</li>)}
                </ul>
          ) : <p className="mt-1 text-sm text-ink-3">Schema has not been checked yet.</p>}
          <h3 className="mt-5 text-xs font-medium text-ink-3">Failed batches</h3>
          {r.failed_batches.length === 0
            ? <p className="mt-1 text-sm text-ink-2">None recorded.</p>
            : <ul className="mt-1 space-y-1 font-mono text-xs text-ink-2">
                {r.failed_batches.map((f, i) => <li key={i}>{JSON.stringify(f)}</li>)}
              </ul>}
          <h3 className="mt-5 text-xs font-medium text-ink-3">Build history</h3>
          <ul className="mt-1 space-y-1 text-xs text-ink-2">
            {r.build_history.map((h) => (
              <li key={h.build_id} className="tabular">{h.build_id}: {h.pass} pass · {h.warn} warn · {h.fail} fail</li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

function RuleRow({ rule }: { rule: QualityRule }) {
  return (
    <tr className="border-t border-border align-top">
      <td className="py-2 pr-3 font-mono text-xs text-ink-3">{rule.rule_id}</td>
      <td className="py-2 pr-3 text-ink-2">
        {rule.description}
        {rule.detail && <span className="mt-0.5 block break-all text-xs text-ink-3">{rule.detail}</span>}
      </td>
      <td className="py-2 pr-3 text-ink-3">{rule.severity}</td>
      <td className="py-2 pr-3 text-ink-2">{fmtInt(rule.records_failed)} / {fmtInt(rule.records_checked)}</td>
      <td className="py-2 pr-3 text-ink-2">{fmtPct(rule.failure_rate, 3)}</td>
      <td className="py-2 pr-3 text-ink-3">{rule.threshold == null ? "—" : fmtPct(rule.threshold, 2)}</td>
      <td className="py-2 pr-3"><StatusBadge status={rule.status} /></td>
    </tr>
  );
}
