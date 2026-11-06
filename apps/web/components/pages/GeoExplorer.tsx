"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useId, useMemo, useState } from "react";

import { BoroughMap, CHANGE_DOMAIN, CHANGE_STOPS, MAX_RADIUS, type MapMode, radiusFor } from "@/components/BoroughMap";
import { TimeSeriesChart } from "@/components/charts";
import { Card, EmptyState, ErrorState, Loading, QueryInfo } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useFilters } from "@/hooks/useFilters";
import { type DashboardFilters, toQuery } from "@/lib/filters";
import { BOROUGH_COLOR, CATEGORY_LABEL, fmtDate, fmtDateTime, fmtInt, fmtPct, fmtSignedPct, titleCase } from "@/lib/format";
import type { GeoBoroughRow, GeoSummary, Series } from "@/lib/types";

const BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"];
type SortKey = "rank" | "borough" | "requests" | "share_of_known" | "prior_requests" | "pct_change";

function categoryText(cats: string[]): string {
  if (cats.length !== 1) return "all loaded categories (residential noise and heat/hot water)";
  return (CATEGORY_LABEL[cats[0]] ?? cats[0]).toLowerCase();
}

export function GeoExplorer() {
  const { filters, setFilters, options } = useFilters();
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const mode: MapMode = sp.get("mode") === "change" ? "change" : "volume";
  const focus = BOROUGHS.includes(sp.get("focus") ?? "") ? sp.get("focus") : null;
  const compare = BOROUGHS.includes(sp.get("compare") ?? "") && sp.get("compare") !== focus ? sp.get("compare") : null;

  const setView = useCallback(
    (next: { filters?: DashboardFilters; mode?: MapMode; focus?: string | null; compare?: string | null }) => {
      if (!filters) return;
      const f = next.filters ?? filters;
      const q = toQuery({ ...f, borough: [] }, {
        mode: (next.mode ?? mode) === "change" ? "change" : undefined,
        focus: (next.focus === undefined ? focus : next.focus) ?? undefined,
        compare: (next.compare === undefined ? compare : next.compare) ?? undefined,
      });
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    },
    [filters, mode, focus, compare, router, pathname],
  );

  if (options.status === "loading") return <Loading label="Loading filters" className="h-96" />;
  if (options.status === "error") return <ErrorState error={options.error} />;
  if (!filters) return null;
  return (
    <GeoView
      filters={filters}
      minDate={options.data.min_date}
      maxDate={options.data.last_complete_date ?? options.data.max_date}
      categories={options.data.categories.map((c) => c.key)}
      mode={mode}
      focus={focus}
      compare={compare}
      setFilters={(f) => (focus || compare || mode === "change" ? setView({ filters: f }) : setFilters({ ...f, borough: [] }))}
      setView={setView}
    />
  );
}

function GeoView({ filters, minDate, maxDate, categories, mode, focus, compare, setFilters, setView }: {
  filters: DashboardFilters; minDate: string | null; maxDate: string | null; categories: string[]; mode: MapMode;
  focus: string | null; compare: string | null; setFilters: (f: DashboardFilters) => void;
  setView: (v: { mode?: MapMode; focus?: string | null; compare?: string | null }) => void;
}) {
  const [reload, setReload] = useState(0);
  const q = toQuery({ ...filters, borough: [], agency: [], status: [] });
  const geo = useApi<GeoSummary>(`/api/v1/geo/boroughs?${q}`, reload);
  const startId = useId();
  const endId = useId();

  return (
    <div className="space-y-6">
      <section aria-label="Map filters" className="rounded-xl border border-border bg-surface-1 p-4">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label htmlFor={startId} className="mb-1.5 block text-xs font-medium text-ink-3">From</label>
              <input id={startId} type="date" value={filters.start ?? ""} min={minDate ?? undefined}
                max={filters.end ?? maxDate ?? undefined}
                onChange={(e) => e.target.value && setFilters({ ...filters, start: e.target.value })}
                className="rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-sm text-ink [color-scheme:dark]" />
            </div>
            <div>
              <label htmlFor={endId} className="mb-1.5 block text-xs font-medium text-ink-3">To</label>
              <input id={endId} type="date" value={filters.end ?? ""} min={filters.start ?? minDate ?? undefined}
                max={maxDate ?? undefined}
                onChange={(e) => e.target.value && setFilters({ ...filters, end: e.target.value })}
                className="rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-sm text-ink [color-scheme:dark]" />
            </div>
          </div>
          <Segmented label="Category" value={filters.category.length === 1 ? filters.category[0] : "all"}
            options={[["all", "All loaded"], ...categories.map((c) => [c, CATEGORY_LABEL[c] ?? c] as [string, string])]}
            onChange={(v) => setFilters({ ...filters, category: v === "all" ? [] : [v] })} />
          <Segmented label="Map shows" value={mode}
            options={[["volume", "Request volume"], ["change", "Change vs prior period"]]}
            onChange={(v) => setView({ mode: v as MapMode })} />
        </div>
        <p className="mt-3 border-t border-border pt-3 text-xs text-ink-3">
          The map always shows all five boroughs. Counts are 311 reports by the borough recorded on each request,
          aggregated from the warehouse; no addresses or coordinates of individual requests are used.
        </p>
      </section>

      {geo.status === "loading" && <Loading label="Loading borough totals" className="h-[460px]" />}
      {geo.status === "error" && <ErrorState error={geo.error} onRetry={() => setReload((n) => n + 1)} />}
      {geo.status === "success" && (
        geo.data.total_requests === 0
          ? <EmptyState message="No requests match this period and category." />
          : <GeoContent g={geo.data} filters={filters} mode={mode} focus={focus} compare={compare} setView={setView} />
      )}
    </div>
  );
}

function Segmented({ label, value, options, onChange }: {
  label: string; value: string; options: [string, string][]; onChange: (v: string) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-1.5 text-xs font-medium text-ink-3">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map(([v, text]) => (
          <button key={v} type="button" aria-pressed={value === v} onClick={() => onChange(v)}
            className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
              value === v ? "border-teal bg-teal/15 text-ink" : "border-border text-ink-2 hover:border-border-strong hover:text-ink"
            }`}>
            {text}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function summaryText(g: GeoSummary, cats: string[]): string {
  const top = g.rows[0];
  const low = g.rows[g.rows.length - 1];
  const parts = [
    `Map of New York City boroughs: ${categoryText(cats)}, ${g.start} to ${g.end}.`,
    `${titleCase(top.borough)} had the most requests (${fmtInt(top.requests)}); ${titleCase(low.borough)} had the fewest (${fmtInt(low.requests)}).`,
  ];
  if (g.comparison_available) {
    const ch = g.rows.filter((r) => r.pct_change != null);
    const up = [...ch].sort((a, b) => (b.pct_change ?? 0) - (a.pct_change ?? 0))[0];
    if (up) parts.push(`Largest change versus the prior period: ${titleCase(up.borough)} ${fmtSignedPct(up.pct_change)}.`);
  }
  parts.push("The table below lists every value shown on the map.");
  return parts.join(" ");
}

function GeoContent({ g, filters, mode, focus, compare, setView }: {
  g: GeoSummary; filters: DashboardFilters; mode: MapMode; focus: string | null; compare: string | null;
  setView: (v: { focus?: string | null; compare?: string | null }) => void;
}) {
  const summary = summaryText(g, filters.category);
  const selectedRow = g.rows.find((r) => r.borough === focus) ?? null;
  return (
    <>
      <Freshness g={g} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card id="map" title="Borough map"
          subtitle={`${categoryText(filters.category)} · ${fmtDate(g.start)} – ${fmtDate(g.end)} (${g.days} days)`}
          actions={<QueryInfo meta={g.meta} />}>
          <p className="sr-only" aria-live="polite">{summary}</p>
          <BoroughMap rows={g.rows} maxRequests={g.max_requests} mode={mode} selected={focus} summary={summary}
            onSelect={(b) => setView({ focus: b, compare: compare === b ? null : compare })} />
          <Legend g={g} mode={mode} />
          <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label="Select a borough">
            {g.rows.map((r) => (
              <button key={r.borough} type="button" aria-pressed={r.borough === focus}
                onClick={() => setView({ focus: r.borough === focus ? null : r.borough })}
                className={`rounded-full border px-2.5 py-1 text-xs ${r.borough === focus ? "border-teal bg-teal/15 text-ink" : "border-border text-ink-2 hover:text-ink"}`}>
                {titleCase(r.borough)}
              </button>
            ))}
          </div>
        </Card>
        <Detail g={g} row={selectedRow} filters={filters} compare={compare} setView={setView} />
      </div>
      <RankingTable g={g} focus={focus} onSelect={(b) => setView({ focus: b })} />
      <Interpretation g={g} />
    </>
  );
}

function Freshness({ g }: { g: GeoSummary }) {
  return (
    <div role="note" className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-surface-1 px-4 py-2.5 text-xs text-ink-2">
      <span className="font-medium text-ink">Historical snapshot, not live</span>
      <span>Newest request: {fmtDateTime(g.freshness.latest_created_at)} (NYC)</span>
      <span>Warehouse built: {fmtDateTime(g.freshness.snapshot_at_local)}</span>
      <span>Build {g.meta.data_build_id ?? "unknown"}</span>
      {g.unspecified_requests > 0 && (
        <span>{fmtInt(g.unspecified_requests)} requests with no valid borough are counted in totals but not mapped.</span>
      )}
    </div>
  );
}

function niceSteps(max: number): number[] {
  if (max <= 0) return [];
  const mag = 10 ** Math.floor(Math.log10(max));
  const top = Math.floor(max / mag) * mag;
  return [top, top / 4, top / 16].filter((v) => v >= 1).map((v) => Math.round(v));
}

function Legend({ g, mode }: { g: GeoSummary; mode: MapMode }) {
  if (mode === "change") {
    const grad = `linear-gradient(to right, ${CHANGE_STOPS.map(([, c]) => c).join(", ")})`;
    return (
      <div className="mt-3 text-xs text-ink-2" aria-label="Legend: change versus prior period">
        <p className="mb-1 font-medium text-ink">Change vs the preceding {g.days} days {g.comparison_available ? "" : "(not available for this period)"}</p>
        <div className="h-2.5 w-full max-w-sm rounded" style={{ background: grad }} />
        <div className="flex max-w-sm justify-between tabular text-ink-3">
          <span>≤ −{CHANGE_DOMAIN * 100}%</span><span>0%</span><span>≥ +{CHANGE_DOMAIN * 100}%</span>
        </div>
        <p className="mt-1 text-ink-3">Fixed scale in every view; colors encode the rate of change, not volume.</p>
      </div>
    );
  }
  const steps = niceSteps(g.max_requests);
  return (
    <div className="mt-3 flex flex-wrap items-end gap-4 text-xs text-ink-2" aria-label="Legend: circle area is proportional to request count">
      <span className="font-medium text-ink">Circle area ∝ requests</span>
      {steps.map((v) => {
        const r = radiusFor(v, g.max_requests);
        return (
          <span key={v} className="inline-flex items-center gap-1.5">
            <svg aria-hidden="true" width={2 * MAX_RADIUS + 2} height={2 * r + 2} viewBox={`0 0 ${2 * MAX_RADIUS + 2} ${2 * r + 2}`}>
              <circle cx={MAX_RADIUS + 1} cy={r + 1} r={r} fill="#22d3ee" fillOpacity={0.55} stroke="#22d3ee" />
            </svg>
            <span className="tabular">{fmtInt(v)}</span>
          </span>
        );
      })}
      <span className="text-ink-3">Borough shapes are neutral so land area is not read as volume.</span>
    </div>
  );
}

function Detail({ g, row, filters, compare, setView }: {
  g: GeoSummary; row: GeoBoroughRow | null; filters: DashboardFilters; compare: string | null;
  setView: (v: { focus?: string | null; compare?: string | null }) => void;
}) {
  const compareId = useId();
  const sel = row ? [row.borough, ...(compare ? [compare] : [])] : [];
  const grain = g.days <= 120 ? "day" : "week";
  const trendQ = row ? toQuery({ ...filters, borough: sel, agency: [], status: [] }, { grain, split_by: "borough" }) : null;
  const trend = useApi<Series>(trendQ ? `/api/v1/metrics/volume?${trendQ}` : null);
  if (!row) {
    return (
      <Card id="detail" title="Borough details" subtitle="Select a borough on the map, in the buttons or in the table">
        <EmptyState message="No borough selected." />
      </Card>
    );
  }
  const other = g.rows.find((r) => r.borough === compare) ?? null;
  const explorerQ = toQuery({ ...filters, borough: [row.borough], agency: [], status: [] });
  return (
    <Card id="detail" title={`${titleCase(row.borough)}${other ? ` vs ${titleCase(other.borough)}` : ""}`}
      subtitle={`${categoryText(filters.category)} · ${fmtDate(g.start)} – ${fmtDate(g.end)}`}
      actions={<button type="button" className="text-xs text-cyan hover:underline" onClick={() => setView({ focus: null, compare: null })}>Clear</button>}>
      <dl className="grid grid-cols-2 gap-3 text-sm" data-testid="borough-detail">
        <Stat label="Requests" value={fmtInt(row.requests)} sub={`rank ${row.rank} of 5`} />
        <Stat label="Share of mapped requests" value={fmtPct(row.share_of_known)} />
        <Stat label="Per day (mean)" value={fmtInt(row.daily_mean)} />
        <Stat label="Change vs prior period" value={fmtSignedPct(row.pct_change)}
          sub={row.prior_requests != null ? `${fmtInt(row.prior_requests)} in ${fmtDate(g.prior_start)} – ${fmtDate(g.prior_end)}` : "not available"} />
      </dl>
      <div className="mt-4">
        <label htmlFor={compareId} className="mb-1.5 block text-xs font-medium text-ink-3">Compare with</label>
        <select id={compareId} value={compare ?? ""} onChange={(e) => setView({ compare: e.target.value || null })}
          className="rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-sm text-ink">
          <option value="">No comparison</option>
          {BOROUGHS.filter((b) => b !== row.borough).map((b) => <option key={b} value={b}>{titleCase(b)}</option>)}
        </select>
        {other && (
          <p className="mt-2 text-xs text-ink-2" data-testid="comparison-text">
            {titleCase(row.borough)}: {fmtInt(row.requests)} · {titleCase(other.borough)}: {fmtInt(other.requests)} ·
            difference {fmtInt(Math.abs(row.requests - other.requests))} ({row.requests >= other.requests ? titleCase(row.borough) : titleCase(other.borough)} higher). Raw counts, not per capita.
          </p>
        )}
      </div>
      <div className="mt-4">
        <h3 className="mb-2 text-xs font-medium text-ink-3">Requests per {grain}</h3>
        {trend.status === "loading" && <Loading className="h-56" />}
        {trend.status === "error" && <ErrorState error={trend.error} />}
        {trend.status === "success" && (
          <TimeSeriesChart label={`Requests per ${grain}, ${sel.map(titleCase).join(" and ")}`} points={trend.data.points}
            colors={BOROUGH_COLOR} labels={Object.fromEntries(BOROUGHS.map((b) => [b, titleCase(b)]))} />
        )}
      </div>
      <div className="mt-4 flex flex-wrap gap-3 text-xs">
        <Link className="text-cyan hover:underline" href={`/explorer?${explorerQ}`}>Open these requests in the Explorer</Link>
        <Link className="text-cyan hover:underline" href={`/?${explorerQ}`}>Overview for {titleCase(row.borough)}</Link>
        <Link className="text-cyan hover:underline" href={`/anomalies`}>Anomaly flags</Link>
      </div>
    </Card>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-2 p-3">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="tabular mt-1 text-lg font-semibold text-ink">{value}</dd>
      {sub && <dd className="mt-0.5 text-[11px] text-ink-3">{sub}</dd>}
    </div>
  );
}

const COLUMNS: [SortKey, string, boolean][] = [
  ["rank", "Rank", true],
  ["borough", "Borough", false],
  ["requests", "Requests", true],
  ["share_of_known", "Share of mapped", true],
  ["prior_requests", "Prior period", true],
  ["pct_change", "Change", true],
];

function RankingTable({ g, focus, onSelect }: { g: GeoSummary; focus: string | null; onSelect: (b: string) => void }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "rank", dir: "asc" });
  const rows = useMemo(() => {
    const out = [...g.rows];
    out.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      const cmp = av == null ? 1 : bv == null ? -1 : typeof av === "string" ? av.localeCompare(String(bv)) : Number(av) - Number(bv);
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return out;
  }, [g.rows, sort]);
  return (
    <Card id="ranking" title="Borough ranking (same data as the map)"
      subtitle={`Totals: ${fmtInt(g.known_borough_requests)} mapped${g.unspecified_requests ? ` + ${fmtInt(g.unspecified_requests)} unspecified` : ""} = ${fmtInt(g.total_requests)} requests${g.prior_start ? ` · prior period ${g.prior_start} to ${g.prior_end}` : ""}`}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm" data-testid="geo-table">
          <caption className="sr-only">Requests by borough, sortable. Select a borough name to show its details.</caption>
          <thead>
            <tr className="border-b border-border text-left text-xs text-ink-3">
              {COLUMNS.map(([key, label, numeric]) => (
                <th key={key} scope="col" aria-sort={sort.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                  className={`py-2 pr-3 font-medium ${numeric ? "text-right" : ""}`}>
                  <button type="button" className="hover:text-ink"
                    onClick={() => setSort((s) => ({ key, dir: s.key === key && s.dir === (numeric ? "desc" : "asc") ? (numeric ? "asc" : "desc") : numeric && key !== "rank" ? "desc" : "asc" }))}>
                    {label}{sort.key === key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.borough} data-borough={r.borough}
                className={`border-b border-border/60 ${r.borough === focus ? "bg-teal/10" : ""}`}>
                <td className="tabular py-2 pr-3 text-right">{r.rank}</td>
                <th scope="row" className="py-2 pr-3 text-left font-normal">
                  <button type="button" className="text-cyan hover:underline" aria-pressed={r.borough === focus}
                    onClick={() => onSelect(r.borough)}>{titleCase(r.borough)}</button>
                </th>
                <td className="tabular py-2 pr-3 text-right" data-col="requests">{fmtInt(r.requests)}</td>
                <td className="tabular py-2 pr-3 text-right">{fmtPct(r.share_of_known)}</td>
                <td className="tabular py-2 pr-3 text-right">{fmtInt(r.prior_requests)}</td>
                <td className="tabular py-2 pr-3 text-right">
                  {r.pct_change == null ? "—" : (
                    <span className="inline-flex items-center gap-1">
                      <span aria-hidden="true">{r.pct_change > 0 ? "▲" : r.pct_change < 0 ? "▼" : "•"}</span>
                      {fmtSignedPct(r.pct_change)}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {g.comparison_note && <p className="mt-2 text-xs text-ink-3">{g.comparison_note}</p>}
    </Card>
  );
}

function Interpretation({ g }: { g: GeoSummary }) {
  return (
    <Card id="interpretation" title="How to read this map">
      <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink-2">
        <li>Counts are <strong className="text-ink">311 reports</strong>, not measured incidents. Reporting habits differ between
          neighborhoods, so differences in counts do not show differences in how often problems occur.</li>
        <li>These are <strong className="text-ink">raw counts, not population-adjusted rates</strong>. Boroughs have very different
          populations and housing stock; no per-capita rate is shown because no validated, period-matched denominator is loaded.</li>
        <li>Geographic differences do not show differences in service quality or responsiveness.</li>
        <li>Change compares the same category over the immediately preceding {g.days} days. Seasonal effects (for example the
          October start of the heating season) produce large changes that are not unusual events.</li>
        <li>Borough is taken from the borough recorded on each request; boundaries are drawn only for orientation.
          Source: NYC Department of City Planning, Borough Boundaries (NYC Open Data gthc-hcne), simplified for display.</li>
      </ul>
    </Card>
  );
}
