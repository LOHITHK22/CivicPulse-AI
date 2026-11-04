"use client";

import type { ReactNode } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartFrame } from "@/components/charts";
import { fmtInt, fmtPct, shortDate } from "@/lib/format";

const AXIS = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false, axisLine: false } as const;
const GRID = { stroke: "var(--grid)", strokeDasharray: "0", vertical: false } as const;
const TOOLTIP = {
  contentStyle: {
    background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8,
    color: "var(--text-primary)", fontSize: 12,
  },
  labelStyle: { color: "var(--text-secondary)", marginBottom: 4 },
  itemStyle: { color: "var(--text-primary)", padding: 0 },
  cursor: { stroke: "var(--border-strong)" },
} as const;
const LEGEND = { wrapperStyle: { fontSize: 12, color: "var(--text-secondary)", paddingTop: 8 }, iconType: "circle" as const, iconSize: 8 };
function legendText(label: (v: string) => string) {
  const render = (v: string) => <span style={{ color: "var(--text-secondary)" }}>{label(v)}</span>;
  return render;
}
const num1 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(1));

// Fixed identity colors for models (color follows the entity, never its rank).
export const MODEL_COLOR: Record<string, string> = {
  naive: "var(--series-1)",
  seasonal_naive: "var(--series-3)",
  rolling_mean: "var(--series-4)",
  lightgbm: "var(--series-2)",
  majority_class: "var(--series-1)",
  logistic_regression: "var(--series-3)",
};
export const MODEL_LABEL: Record<string, string> = {
  naive: "Naive",
  seasonal_naive: "Seasonal naive",
  rolling_mean: "28-day mean",
  lightgbm: "LightGBM",
  majority_class: "Majority class",
  logistic_regression: "Logistic regression",
};
export const modelLabel = (m: string) => MODEL_LABEL[m] ?? m;

export function DataTable({ head, rows, caption }: { head: string[]; rows: ReactNode[][]; caption?: string }) {
  return (
    <table className="w-full text-left text-xs">
      {caption && <caption className="sr-only">{caption}</caption>}
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

/** Actuals, point predictions and a shaded prediction interval on one count axis. */
export function IntervalChart({ rows, label, actualLabel = "Actual", predLabel = "Predicted", intervalLabel, splitAt }: {
  rows: { date: string; actual?: number | null; predicted?: number | null; lower?: number | null; upper?: number | null }[];
  label: string; actualLabel?: string; predLabel?: string; intervalLabel: string; splitAt?: string;
}) {
  const data = rows.map((r) => ({
    ...r,
    band: r.lower != null && r.upper != null ? [r.lower, r.upper] : null,
  }));
  const names: Record<string, string> = { actual: actualLabel, predicted: predLabel, band: intervalLabel };
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="date" tickFormatter={shortDate} minTickGap={24} {...AXIS} />
        <YAxis width={48} {...AXIS} />
        <Tooltip {...TOOLTIP} labelFormatter={(l) => shortDate(String(l))}
          formatter={(v, n) => {
            const key = String(n);
            if (Array.isArray(v)) return [`${num1(v[0] as number)} – ${num1(v[1] as number)}`, names[key] ?? key];
            return [key === "actual" ? fmtInt(Number(v)) : num1(Number(v)), names[key] ?? key];
          }} />
        <Legend {...LEGEND} formatter={legendText((v) => names[v] ?? v)} />
        <Area dataKey="band" name="band" stroke="none" fill="var(--series-2)" fillOpacity={0.18} isAnimationActive={false}
          connectNulls={false} activeDot={false} />
        <Line dataKey="actual" name="actual" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false}
          connectNulls={false} activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} />
        <Line dataKey="predicted" name="predicted" stroke="var(--series-2)" strokeWidth={2} strokeDasharray="5 3"
          dot={{ r: 2.5, fill: "var(--series-2)", strokeWidth: 0 }} isAnimationActive={false} connectNulls={false}
          activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} />
        {splitAt && <ReferenceLine x={splitAt} stroke="var(--border-strong)" strokeDasharray="3 3"
          label={{ value: "Forecast origin", fill: "var(--text-muted)", fontSize: 10, position: "insideTopLeft" }} />}
      </ComposedChart>
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label={label} height={280} chart={chart}
      table={<DataTable caption={label} head={["Date", actualLabel, predLabel, intervalLabel]}
        rows={rows.map((r) => [r.date, r.actual == null ? "—" : fmtInt(r.actual), num1(r.predicted),
          r.lower == null || r.upper == null ? "—" : `${num1(r.lower)} – ${num1(r.upper)}`])} />} />
  );
}

/** Several models' metric over a shared x (e.g. WAPE by horizon or by origin). */
export function ModelLines({ rows, xKey, models, label, xFormat = String, yFormat = (v: number) => fmtPct(v, 0), xLabel }: {
  rows: Record<string, number | string | null>[]; xKey: string; models: string[]; label: string;
  xFormat?: (v: string) => string; yFormat?: (v: number) => string; xLabel: string;
}) {
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -4 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey={xKey} tickFormatter={(v) => xFormat(String(v))} minTickGap={20} {...AXIS} />
        <YAxis width={48} tickFormatter={(v) => yFormat(Number(v))} {...AXIS} />
        <Tooltip {...TOOLTIP} labelFormatter={(l) => `${xLabel} ${xFormat(String(l))}`}
          formatter={(v, n) => [yFormat(Number(v)), modelLabel(String(n))]} />
        <Legend {...LEGEND} formatter={legendText(modelLabel)} />
        {models.map((m) => (
          <Line key={m} dataKey={m} name={m} stroke={MODEL_COLOR[m] ?? "var(--series-5)"} strokeWidth={2}
            dot={rows.length <= 10 ? { r: 3, strokeWidth: 0, fill: MODEL_COLOR[m] } : false} isAnimationActive={false}
            activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label={label} chart={chart}
      table={<DataTable caption={label} head={[xLabel, ...models.map(modelLabel)]}
        rows={rows.map((r) => [xFormat(String(r[xKey])), ...models.map((m) => (r[m] == null ? "—" : yFormat(Number(r[m]))))])} />} />
  );
}

/** Horizontal bars for feature importance (single measure, one hue). */
export function ImportanceBars({ rows, label, valueLabel, format = (v: number) => v.toFixed(4) }: {
  rows: { feature: string; value: number; err?: number }[]; label: string; valueLabel: string; format?: (v: number) => string;
}) {
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
        <CartesianGrid {...GRID} horizontal={false} vertical />
        <XAxis type="number" tickFormatter={(v) => format(Number(v))} {...AXIS} />
        <YAxis type="category" dataKey="feature" width={168} {...AXIS} />
        <ReferenceLine x={0} stroke="var(--border-strong)" />
        <Tooltip {...TOOLTIP} formatter={(v) => [format(Number(v)), valueLabel]} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
        <Bar dataKey="value" fill="var(--series-1)" maxBarSize={16} radius={[0, 4, 4, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label={label} height={Math.max(160, rows.length * 26)} chart={chart}
      table={<DataTable caption={label} head={["Feature", valueLabel, "± std"]}
        rows={rows.map((r) => [r.feature, format(r.value), r.err == null ? "—" : format(r.err)])} />} />
  );
}

/** Reliability diagram: mean predicted vs observed rate per equal-frequency bin, with the diagonal. */
/** Smallest "nice" upper bound (…, 2%, 5%, 10%, 20%, 50%, 100%) covering every value. */
export function calibrationTop(values: number[]): number {
  const max = Math.max(0, ...values.filter((v) => Number.isFinite(v)));
  for (const t of [0.01, 0.02, 0.05, 0.1, 0.2, 0.5]) if (max <= t) return t;
  return 1;
}

export function CalibrationChart({ series, label }: {
  series: { model: string; bins: { mean_predicted: number; observed_rate: number; n: number }[] }[]; label: string;
}) {
  // Both axes share one scale (so the diagonal still means "calibrated"), zoomed to the data:
  // with low base rates a fixed 0–100% axis squeezes every point into the corner.
  const top = calibrationTop(series.flatMap((s) => s.bins.flatMap((b) => [b.mean_predicted, b.observed_rate])));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * top);
  // Enough decimals that every tick (multiples of top/4) is printed exactly, e.g. 2.5%, 7.5%.
  const exact = (d: number) => ticks.every((t) => Math.abs(t * 100 * 10 ** d - Math.round(t * 100 * 10 ** d)) < 1e-6);
  const digits = [0, 1, 2].find(exact) ?? 2;
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart margin={{ top: 4, right: 12, bottom: 16, left: -4 }}>
        <CartesianGrid {...GRID} vertical />
        <XAxis type="number" dataKey="mean_predicted" domain={[0, top]} ticks={ticks} allowDataOverflow
          tickFormatter={(v) => fmtPct(Number(v), digits)} {...AXIS}
          label={{ value: "Mean predicted risk", position: "insideBottom", offset: -10, fill: "var(--text-muted)", fontSize: 11 }} />
        <YAxis type="number" dataKey="observed_rate" domain={[0, top]} ticks={ticks} width={52} allowDataOverflow
          tickFormatter={(v) => fmtPct(Number(v), digits)} {...AXIS} />
        <ReferenceLine segment={[{ x: 0, y: 0 }, { x: top, y: top }]} stroke="var(--border-strong)" strokeDasharray="4 4" />
        <Tooltip {...TOOLTIP} formatter={(v, n) => [fmtPct(Number(v)), String(n)]} />
        <Legend {...LEGEND} formatter={legendText(modelLabel)} />
        {series.map((s) => (
          <Line key={s.model} data={s.bins} dataKey="observed_rate" name={s.model} stroke={MODEL_COLOR[s.model]}
            strokeWidth={2} dot={{ r: 4, fill: MODEL_COLOR[s.model], stroke: "var(--surface-1)", strokeWidth: 2 }}
            isAnimationActive={false} />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label={label} height={300} chart={chart}
      table={<DataTable caption={label} head={["Model", "Bin", "Mean predicted", "Observed rate", "Requests"]}
        rows={series.flatMap((s) => s.bins.map((b, i) => [modelLabel(s.model), i + 1, fmtPct(b.mean_predicted),
          fmtPct(b.observed_rate), fmtInt(b.n)]))} />} />
  );
}

/** Histogram of predicted risk in 10 bins (0–10%, …, 90–100%). */
export function RiskHistogram({ rows, label }: { rows: { bin: number; requests: number }[]; label: string }) {
  const data = Array.from({ length: 10 }, (_, b) => ({
    bin: b, name: `${b * 10}–${b * 10 + 10}%`, requests: rows.find((r) => r.bin === b)?.requests ?? 0,
  }));
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="name" interval={0} angle={-30} textAnchor="end" height={48} {...AXIS} />
        <YAxis width={48} {...AXIS} />
        <Tooltip {...TOOLTIP} formatter={(v) => [fmtInt(Number(v)), "Requests"]} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
        <Bar dataKey="requests" maxBarSize={36} radius={[4, 4, 0, 0]} isAnimationActive={false}>
          {data.map((d) => <Cell key={d.bin} fill="var(--series-2)" />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label={label} chart={chart}
      table={<DataTable caption={label} head={["Predicted risk", "Requests"]} rows={data.map((d) => [d.name, fmtInt(d.requests)])} />} />
  );
}

/** Actual vs expected daily counts with anomalies marked (shape + color, never color alone). */
export function AnomalyChart({ points, label }: {
  points: { date: string; actual: number; expected: number; is_anomaly: boolean; direction: string | null; score: number }[];
  label: string;
}) {
  const data = points.map((p) => ({
    ...p,
    spike: p.is_anomaly && p.direction === "spike" ? p.actual : null,
    drop: p.is_anomaly && p.direction === "drop" ? p.actual : null,
  }));
  const names: Record<string, string> = { actual: "Actual", expected: "Expected (seasonal median)", spike: "Anomaly: spike", drop: "Anomaly: drop" };
  const chart = (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="date" tickFormatter={shortDate} minTickGap={24} {...AXIS} />
        <YAxis width={48} {...AXIS} />
        <Tooltip {...TOOLTIP} labelFormatter={(l) => shortDate(String(l))}
          formatter={(v, n) => [n === "expected" ? num1(Number(v)) : fmtInt(Number(v)), names[String(n)] ?? String(n)]} />
        <Legend {...LEGEND} formatter={legendText((v) => names[v] ?? v)} />
        <Line dataKey="expected" name="expected" stroke="var(--text-muted)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
        <Line dataKey="actual" name="actual" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false}
          activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} />
        <Scatter dataKey="spike" name="spike" fill="var(--status-critical)" shape="triangle" isAnimationActive={false} />
        <Scatter dataKey="drop" name="drop" fill="var(--status-warning)" shape="diamond" isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
  return (
    <ChartFrame label={label} height={280} chart={chart}
      table={<DataTable caption={label} head={["Date", "Actual", "Expected", "Score", "Anomaly"]}
        rows={points.map((p) => [p.date, fmtInt(p.actual), num1(p.expected), p.score.toFixed(2), p.is_anomaly ? (p.direction ?? "yes") : "—"])} />} />
  );
}
