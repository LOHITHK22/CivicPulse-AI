"use client";

import { type ReactNode, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { fmtHours, fmtInt, fmtPct, shortDate, titleCase } from "@/lib/format";
import type { Breakdown, ClosureDistribution, HourlyPattern, SeriesPoint } from "@/lib/types";

const AXIS = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false, axisLine: false } as const;
const GRID = { stroke: "var(--grid)", strokeDasharray: "0", vertical: false } as const;
const TOOLTIP = {
  contentStyle: {
    background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8,
    color: "var(--text-primary)", fontSize: 12,
  },
  labelStyle: { color: "var(--text-secondary)", marginBottom: 4 },
  itemStyle: { color: "var(--text-primary)", padding: 0 },
  cursor: { fill: "rgba(148,163,184,0.08)", stroke: "var(--border-strong)" },
} as const;
const LEGEND = { wrapperStyle: { fontSize: 12, color: "var(--text-secondary)", paddingTop: 8 }, iconType: "circle" as const, iconSize: 8 };
const compact = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : String(v));

/** Card body that toggles between a chart and an accessible data table. */
export function ChartFrame({ chart, table, height = 260, label }: {
  chart: ReactNode; table: ReactNode; height?: number; label: string;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <div>
      <div className="mb-2 flex justify-end">
        <div role="group" aria-label={`${label} view`} className="inline-flex rounded-md border border-border p-0.5 text-[11px]">
          {(["chart", "table"] as const).map((v) => (
            <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}
              className={`rounded px-2 py-0.5 ${view === v ? "bg-surface-3 text-ink" : "text-ink-3 hover:text-ink"}`}>
              {v === "chart" ? "Chart" : "Table"}
            </button>
          ))}
        </div>
      </div>
      {view === "chart" ? (
        <div style={{ height }} role="img" aria-label={`${label} chart. Switch to table view for values.`}>
          {chart}
        </div>
      ) : (
        <div className="max-h-[320px] overflow-auto">{table}</div>
      )}
    </div>
  );
}

function DataTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <table className="w-full text-left text-xs">
      <thead className="sticky top-0 bg-surface-1 text-ink-3">
        <tr>{head.map((h) => <th key={h} scope="col" className="py-1.5 pr-3 font-medium">{h}</th>)}</tr>
      </thead>
      <tbody className="tabular text-ink-2">
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-border">
            {r.map((c, j) => <td key={j} className="py-1.5 pr-3">{c}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Pivot long series points into recharts rows: { period, [key]: value }. */
function pivot(points: SeriesPoint[]): { rows: Record<string, number | string>[]; keys: string[] } {
  const keys = [...new Set(points.map((p) => p.key))].sort();
  const byPeriod = new Map<string, Record<string, number | string>>();
  for (const p of points) {
    const row = byPeriod.get(p.period) ?? { period: p.period };
    row[p.key] = p.value;
    byPeriod.set(p.period, row);
  }
  const rows = [...byPeriod.values()].sort((a, b) => String(a.period).localeCompare(String(b.period)));
  for (const r of rows) for (const k of keys) r[k] ??= 0;
  return { rows, keys };
}

export function TimeSeriesChart({ points, colors, labels, kind = "line", stacked = false, label, periodFormat = shortDate }: {
  points: SeriesPoint[]; colors: Record<string, string>; labels: Record<string, string>; kind?: "line" | "bar";
  stacked?: boolean; label: string; periodFormat?: (iso: string) => string;
}) {
  const { rows, keys } = pivot(points);
  const name = (k: string) => labels[k] ?? titleCase(k);
  const common = { data: rows, margin: { top: 4, right: 8, bottom: 0, left: -8 } };
  const axes = (
    <>
      <CartesianGrid {...GRID} />
      <XAxis dataKey="period" tickFormatter={periodFormat} minTickGap={24} {...AXIS} />
      <YAxis tickFormatter={compact} width={48} {...AXIS} />
      <Tooltip {...TOOLTIP} labelFormatter={(l) => periodFormat(String(l))}
        formatter={(v, n) => [fmtInt(Number(v)), name(String(n))]} />
      {keys.length > 1 && <Legend {...LEGEND} formatter={(v) => <span style={{ color: "var(--text-secondary)" }}>{name(String(v))}</span>} />}
    </>
  );
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      {kind === "line" ? (
        <LineChart {...common}>
          {axes}
          {keys.map((k) => (
            <Line key={k} dataKey={k} name={k} type="monotone" stroke={colors[k] ?? "var(--series-1)"}
              strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }}
              isAnimationActive={false} />
          ))}
        </LineChart>
      ) : (
        <BarChart {...common}>
          {axes}
          {keys.map((k, i) => (
            <Bar key={k} dataKey={k} name={k} stackId={stacked ? "s" : undefined} fill={colors[k] ?? "var(--series-1)"}
              stroke="var(--surface-1)" strokeWidth={stacked ? 1 : 0} maxBarSize={28}
              radius={!stacked || i === keys.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />
          ))}
        </BarChart>
      )}
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label={label} chart={chart}
      table={<DataTable head={["Period", ...keys.map(name)]}
        rows={rows.map((r) => [String(r.period), ...keys.map((k) => fmtInt(Number(r[k])))])} />} />
  );
}

export function BreakdownBars({ data, colors, label, render = (r) => r.label }: {
  data: Breakdown; colors: Record<string, string>; label: string; render?: (r: Breakdown["rows"][number]) => string;
}) {
  const rows = data.rows.map((r) => ({ ...r, name: render(r) }));
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
        <CartesianGrid {...GRID} horizontal={false} vertical />
        <XAxis type="number" tickFormatter={compact} {...AXIS} />
        <YAxis type="category" dataKey="name" width={112} {...AXIS} />
        <Tooltip {...TOOLTIP} formatter={(v) => [fmtInt(Number(v)), "Requests"]} />
        <Bar dataKey="requests" maxBarSize={22} radius={[0, 4, 4, 0]} isAnimationActive={false}>
          {rows.map((r) => <Cell key={r.key} fill={colors[r.key] ?? "var(--series-1)"} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label={label} height={Math.max(140, rows.length * 40)} chart={chart}
      table={<DataTable head={["Group", "Requests", "Share", "Open", "Median to close", "Closed ≤7d"]}
        rows={rows.map((r) => [r.name, fmtInt(r.requests), fmtPct(r.share), fmtInt(r.open_requests),
          fmtHours(r.median_hours_to_close), fmtPct(r.closed_within_7d_pct)])} />} />
  );
}

export function ClosureHistogram({ data }: { data: ClosureDistribution }) {
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data.buckets} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="label" interval={0} angle={-30} textAnchor="end" height={52} {...AXIS} />
        <YAxis tickFormatter={compact} width={48} {...AXIS} />
        <Tooltip {...TOOLTIP} formatter={(v) => [fmtInt(Number(v)), "Closed requests"]} />
        <Bar dataKey="count" fill="var(--series-1)" maxBarSize={36} radius={[4, 4, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label="Time to recorded closure" chart={chart}
      table={<DataTable head={["Time to closure", "Requests"]}
        rows={data.buckets.map((b) => [b.label, fmtInt(b.count)])} />} />
  );
}

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// Sequential single hue (blue), dark->light on the dark surface: low values recede toward the surface.
const SEQ = ["#14284a", "#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#86b6ef", "#b7d3f6"];

export function HourHeatmap({ data }: { data: HourlyPattern }) {
  const [hover, setHover] = useState<{ dow: number; hour: number; count: number } | null>(null);
  const max = Math.max(1, ...data.cells.map((c) => c.count));
  const lookup = new Map(data.cells.map((c) => [`${c.dow}-${c.hour}`, c.count]));
  const color = (n: number) => SEQ[Math.min(SEQ.length - 1, Math.floor((n / max) * (SEQ.length - 1)))];
  const chart = (
    <div className="flex h-full flex-col">
      <div className="grid flex-1 gap-[2px]" style={{ gridTemplateColumns: "36px repeat(24, minmax(0, 1fr))" }}
        onMouseLeave={() => setHover(null)}>
        {DOW.map((d, di) => (
          <div key={d} className="contents">
            <div className="flex items-center text-[11px] text-ink-3">{d}</div>
            {Array.from({ length: 24 }, (_, h) => {
              const n = lookup.get(`${di + 1}-${h}`) ?? 0;
              return (
                <div key={h} className="rounded-[3px]" style={{ background: color(n), minHeight: 18 }}
                  onMouseEnter={() => setHover({ dow: di + 1, hour: h, count: n })} />
              );
            })}
          </div>
        ))}
      </div>
      <div className="mt-1 grid text-[10px] text-ink-3" style={{ gridTemplateColumns: "36px repeat(24, minmax(0, 1fr))" }}>
        <span />
        {Array.from({ length: 24 }, (_, h) => <span key={h} className="text-center">{h % 3 === 0 ? h : ""}</span>)}
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-ink-3">
        <span aria-live="polite" className="tabular text-ink-2">
          {hover ? `${DOW[hover.dow - 1]} ${String(hover.hour).padStart(2, "0")}:00 — ${fmtInt(hover.count)} requests`
            : "Hover a cell for its count"}
        </span>
        <span className="flex items-center gap-1.5">
          Fewer
          {SEQ.map((c) => <span key={c} className="h-2.5 w-3 rounded-sm" style={{ background: c }} />)}
          More
        </span>
      </div>
    </div>
  );
  return (
    <ChartFrame label="Requests by day of week and hour" height={230} chart={chart}
      table={<DataTable head={["Day", ...Array.from({ length: 24 }, (_, h) => String(h))]}
        rows={DOW.map((d, di) => [d, ...Array.from({ length: 24 }, (_, h) => fmtInt(lookup.get(`${di + 1}-${h}`) ?? 0))])} />} />
  );
}
