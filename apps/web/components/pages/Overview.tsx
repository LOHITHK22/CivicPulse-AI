"use client";

import Link from "next/link";
import { BreakdownBars, ClosureHistogram, HourHeatmap, TimeSeriesChart } from "@/components/charts";
import { FilterBar } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { Card, EmptyState, ErrorState, Loading, QueryInfo } from "@/components/ui";
import { type ApiState, useApi } from "@/hooks/useApi";
import { useFilters } from "@/hooks/useFilters";
import { type DashboardFilters, toQuery } from "@/lib/filters";
import {
  BOROUGH_COLOR,
  CATEGORY_COLOR,
  CATEGORY_LABEL,
  STATUS_COLOR,
  fmtDate,
  fmtDateTime,
  fmtHours,
  fmtInt,
  fmtPct,
  fmtSignedPct,
  titleCase,
} from "@/lib/format";
import type { Breakdown, ClosureDistribution, HourlyPattern, Kpis, Series } from "@/lib/types";

function Section<T>({ state, children, height = "h-56" }: {
  state: ApiState<T>; children: (d: T) => React.ReactNode; height?: string;
}) {
  if (state.status === "loading") return <Loading className={height} />;
  if (state.status === "error") return <ErrorState error={state.error} />;
  return <>{children(state.data)}</>;
}

const monthFmt = (iso: string) =>
  new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });

export function Overview() {
  const { filters, setFilters, options } = useFilters();

  if (options.status === "loading") return <Loading label="Loading filters" className="h-96" />;
  if (options.status === "error") return <ErrorState error={options.error} />;
  if (!filters) return null;
  return (
    <>
      <FilterBar filters={filters} options={options.data} onChange={setFilters} />
      <Dashboard filters={filters} />
    </>
  );
}

function Dashboard({ filters }: { filters: DashboardFilters }) {
  const q = toQuery(filters);
  const kpis = useApi<Kpis>(`/api/v1/metrics/kpis?${q}`);
  const daily = useApi<Series>(`/api/v1/metrics/volume?${toQuery(filters, { grain: "day", split_by: "category_key" })}`);
  const weekly = useApi<Series>(`/api/v1/metrics/volume?${toQuery(filters, { grain: "week", split_by: "borough" })}`);
  const status = useApi<Series>(`/api/v1/metrics/volume?${toQuery(filters, { grain: "week", split_by: "status_group" })}`);
  // Seasonality uses the full loaded range (date filter intentionally not applied).
  const seasonal = useApi<Series>(
    `/api/v1/metrics/volume?${toQuery({ ...filters, start: null, end: null }, { grain: "month", split_by: "category_key" })}`,
  );
  const cats = useApi<Breakdown>(`/api/v1/metrics/breakdown/category_key?${q}`);
  const boroughs = useApi<Breakdown>(`/api/v1/metrics/breakdown/borough?${q}`);
  const closure = useApi<ClosureDistribution>(`/api/v1/metrics/closure-distribution?${q}`);
  const hourly = useApi<HourlyPattern>(`/api/v1/metrics/hourly?${q}`);

  const empty = kpis.status === "success" && kpis.data.total_requests === 0;

  return (
    <div className="space-y-6">
      <section aria-label="Key metrics">
        <Section state={kpis} height="h-28">{(k) => <KpiGrid k={k} />}</Section>
      </section>

      {empty ? <EmptyState /> : (
        <>
          <div className="grid gap-6 xl:grid-cols-3">
            <Card id="daily" title="Daily request volume" subtitle="By category · requests created per day"
              className="xl:col-span-2" actions={daily.status === "success" && <QueryInfo meta={daily.data.meta} />}>
              <Section state={daily}>
                {(s) => <TimeSeriesChart label="Daily request volume" points={s.points} colors={CATEGORY_COLOR} labels={CATEGORY_LABEL} />}
              </Section>
            </Card>
            <Card id="cats" title="Category distribution" subtitle="Share of requests in selection">
              <Section state={cats}>
                {(b) => <BreakdownBars label="Category distribution" data={b} colors={CATEGORY_COLOR} />}
              </Section>
            </Card>
          </div>

          <div className="grid gap-6 xl:grid-cols-2">
            <Card id="weekly" title="Weekly trend by borough" subtitle="Stacked weekly totals (weeks start Monday; partial weeks at the edges)">
              <Section state={weekly}>
                {(s) => <TimeSeriesChart label="Weekly trend by borough" kind="bar" stacked points={s.points}
                  colors={BOROUGH_COLOR} labels={{}} />}
              </Section>
            </Card>
            <Card id="boroughs" title="Borough comparison" subtitle="Request counts; open the table view for median closure times and 7-day closure rates">
              <Section state={boroughs}>
                {(b) => <BreakdownBars label="Borough comparison" data={b} colors={BOROUGH_COLOR} render={(r) => titleCase(r.key)} />}
              </Section>
            </Card>
          </div>

          <div className="grid gap-6 xl:grid-cols-2">
            <Card id="status" title="Open vs closed, by week created" subtitle="Current status (as of snapshot) of requests created each week">
              <Section state={status}>
                {(s) => <TimeSeriesChart label="Open vs closed" kind="bar" stacked points={s.points}
                  colors={STATUS_COLOR} labels={{ open: "Open", closed: "Closed", unknown: "Unknown" }} />}
              </Section>
            </Card>
            <Card id="closure" title="Time to recorded closure"
              subtitle={closure.status === "success"
                ? `${fmtInt(closure.data.still_open)} still open and ${fmtInt(closure.data.excluded_invalid)} with invalid closure records are excluded`
                : "Closed requests with a valid closed_date"}>
              <Section state={closure}>{(d) => <ClosureHistogram data={d} />}</Section>
            </Card>
          </div>

          <div className="grid gap-6 xl:grid-cols-2">
            <Card id="hourly" title="Activity by time of day" subtitle="Requests by day of week and hour created (NYC local time)">
              <Section state={hourly}>{(h) => <HourHeatmap data={h} />}</Section>
            </Card>
            <Card id="season" title="Monthly pattern" subtitle="Full loaded range, ignores date filter; first and last months may be partial. One year of data cannot separate seasonality from trend.">
              <Section state={seasonal}>
                {(s) => <TimeSeriesChart label="Monthly pattern" kind="bar" points={s.points} colors={CATEGORY_COLOR}
                  labels={CATEGORY_LABEL} periodFormat={monthFmt} />}
              </Section>
            </Card>
          </div>

          <Card id="predictions" title="Forecasts, anomaly flags and geography"
            subtitle="These views live on their own pages so their uncertainty and limitations are shown with them.">
            <ul className="flex flex-wrap gap-4 text-sm">
              <li><Link className="text-cyan hover:underline" href="/forecast">Forecast Lab: 7-day forecasts with intervals and baselines</Link></li>
              <li><Link className="text-cyan hover:underline" href="/anomalies">Anomaly Center: unverified statistical flags</Link></li>
              <li><Link className="text-cyan hover:underline" href="/geo">Geographic Intelligence: borough map and rankings</Link></li>
            </ul>
          </Card>
        </>
      )}
    </div>
  );
}

function KpiGrid({ k }: { k: Kpis }) {
  const c = k.comparison;
  const f = k.freshness;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <KpiCard label="Total requests" value={fmtInt(k.total_requests)} hint={k.definitions.total_requests}
        detail={<span className="text-ink-3">Created in selected range</span>} />
      <KpiCard label="Change vs prior period" value={fmtSignedPct(c.pct_change)} tone={c.pct_change == null ? "muted" : undefined}
        hint={k.definitions.comparison}
        detail={c.prior_total != null
          ? <span className="text-ink-3">{fmtInt(c.prior_total)} in {fmtDate(c.prior_start)} to {fmtDate(c.prior_end)}</span>
          : <span className="text-ink-3">{c.note}</span>} />
      <KpiCard label="Open requests" value={fmtInt(k.open_requests)} hint={k.definitions.open_requests}
        detail={<span className="text-ink-3">Status at snapshot · {fmtPct(k.total_requests ? k.open_requests / k.total_requests : null)} of total</span>} />
      <KpiCard label="Closed requests" value={fmtInt(k.closed_requests)}
        detail={<span className="text-ink-3">{fmtInt(k.unknown_status_requests)} with unspecified status</span>} />
      <KpiCard label="Median time to recorded closure" value={fmtHours(k.median_hours_to_close)} hint={k.definitions.median_hours_to_close}
        detail={<span className="text-ink-3">Over {fmtInt(k.closed_with_valid_duration)} closed requests; excludes open ones</span>} />
      <KpiCard label={`Not closed within ${k.threshold_days} days`} value={fmtPct(k.open_beyond_threshold_pct)}
        hint={k.definitions.open_beyond_threshold_pct}
        detail={<span className="text-ink-3">{fmtInt(k.open_beyond_threshold_numerator)} of {fmtInt(k.open_beyond_threshold_denominator)} requests old enough to judge</span>} />
      <KpiCard label="Data freshness" value={f.latest_created_at ? fmtDateTime(f.latest_created_at) : "—"}
        detail={<span className="text-ink-3">Latest request in snapshot · source updated {fmtDateTime(f.source_rows_updated_at)} UTC · not live</span>} />
      <KpiCard label="Categories with unusual increases" value="Not computed" tone="muted"
        detail={<span className="text-ink-3">{k.unusual_increase_note}</span>} />
    </div>
  );
}
