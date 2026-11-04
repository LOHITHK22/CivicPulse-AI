"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

import { Card, ErrorState, Loading } from "@/components/ui";
import type { ApiState } from "@/hooks/useApi";
import { CATEGORY_LABEL, titleCase } from "@/lib/format";

export const BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"];
export const CATEGORIES = ["heat_hot_water", "noise_residential"];

/** URL-backed selection (?category=&borough=) validated against allow-lists. */
export function useSeriesParams(defaults: { category: string; borough: string }, boroughs = BOROUGHS) {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const cat = sp.get("category");
  const bor = sp.get("borough");
  const category = cat && CATEGORIES.includes(cat) ? cat : defaults.category;
  const borough = bor && boroughs.includes(bor) ? bor : defaults.borough;
  const set = useCallback((next: Record<string, string>) => {
    const q = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(next)) q.set(k, v);
    router.replace(`${pathname}?${q.toString()}`, { scroll: false });
  }, [sp, router, pathname]);
  return { category, borough, set };
}

export function Select({ label, value, options, onChange, render = (v) => v }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void; render?: (v: string) => string;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-3">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="min-w-40 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-sm text-ink focus:border-teal focus:outline-none">
        {options.map((o) => <option key={o} value={o}>{render(o)}</option>)}
      </select>
    </label>
  );
}

export function SeriesPicker({ category, borough, set, boroughs = BOROUGHS, extra }: {
  category: string; borough: string; set: (n: Record<string, string>) => void; boroughs?: string[]; extra?: React.ReactNode;
}) {
  return (
    <div role="group" aria-label="Series filters" className="mb-6 flex flex-wrap items-end gap-3 rounded-xl border border-border bg-surface-1 p-4">
      <Select label="Category" value={category} options={CATEGORIES} onChange={(v) => set({ category: v })}
        render={(v) => CATEGORY_LABEL[v] ?? v} />
      <Select label="Borough" value={borough} options={boroughs} onChange={(v) => set({ borough: v })}
        render={(v) => (v === "CITYWIDE" ? "Citywide" : titleCase(v))} />
      {extra}
    </div>
  );
}

export function Section<T>({ state, children, height = "h-56" }: {
  state: ApiState<T>; children: (d: T) => React.ReactNode; height?: string;
}) {
  if (state.status === "loading") return <Loading className={height} />;
  if (state.status === "error") return <ErrorState error={state.error} />;
  return <>{children(state.data)}</>;
}

export function Stat({ label, value, note }: { label: string; value: React.ReactNode; note?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-surface-1 p-4">
      <p className="text-xs text-ink-3">{label}</p>
      <p className="tabular mt-1 text-xl font-semibold text-ink">{value}</p>
      {note && <p className="mt-1 text-[11px] leading-snug text-ink-3">{note}</p>}
    </div>
  );
}

export function Caveats({ items, title = "Limitations" }: { items: React.ReactNode[]; title?: string }) {
  return (
    <Card title={title}>
      <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink-2">
        {items.map((it, i) => <li key={i}>{it}</li>)}
      </ul>
    </Card>
  );
}

export function SyntheticBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--accent-violet)] px-2 py-0.5 text-[11px] text-ink">
      <svg aria-hidden="true" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--accent-violet)" strokeWidth="2.5"><path d="M9 3h6M10 3v6L4 19a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2l-6-10V3" /></svg>
      Synthetic injected anomalies
    </span>
  );
}
