"use client";

import { useId } from "react";

import type { DashboardFilters } from "@/lib/filters";
import { titleCase } from "@/lib/format";
import type { FilterOptions } from "@/lib/types";

interface Props {
  filters: DashboardFilters;
  options: FilterOptions;
  onChange: (f: DashboardFilters) => void;
}

function MultiSelect({ label, values, selected, render, onChange }: {
  label: string; values: string[]; selected: string[]; render?: (v: string) => string;
  onChange: (next: string[]) => void;
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="mb-1.5 text-xs font-medium text-ink-3">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {values.map((v) => {
          const on = selected.includes(v);
          return (
            <button key={v} type="button" aria-pressed={on}
              onClick={() => onChange(on ? selected.filter((s) => s !== v) : [...selected, v])}
              className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                on ? "border-teal bg-teal/15 text-ink" : "border-border text-ink-2 hover:border-border-strong hover:text-ink"
              }`}>
              {render ? render(v) : v}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

export function FilterBar({ filters, options, onChange }: Props) {
  const startId = useId();
  const endId = useId();
  const catLabel = (k: string) => options.categories.find((c) => c.key === k)?.label ?? k;
  const active = filters.borough.length + filters.category.length + filters.agency.length + filters.status.length;

  return (
    <section aria-label="Filters" className="mb-6 rounded-xl border border-border bg-surface-1 p-4">
      <div className="grid gap-4 md:grid-cols-[auto_1fr]">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={startId} className="mb-1.5 block text-xs font-medium text-ink-3">From</label>
            <input id={startId} type="date" value={filters.start ?? ""}
              min={options.min_date ?? undefined} max={filters.end ?? options.max_date ?? undefined}
              onChange={(e) => e.target.value && onChange({ ...filters, start: e.target.value })}
              className="rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-sm text-ink [color-scheme:dark]" />
          </div>
          <div>
            <label htmlFor={endId} className="mb-1.5 block text-xs font-medium text-ink-3">To</label>
            <input id={endId} type="date" value={filters.end ?? ""}
              min={filters.start ?? options.min_date ?? undefined} max={options.max_date ?? undefined}
              onChange={(e) => e.target.value && onChange({ ...filters, end: e.target.value })}
              className="rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-sm text-ink [color-scheme:dark]" />
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MultiSelect label="Category" values={options.categories.map((c) => c.key)} selected={filters.category}
            render={catLabel} onChange={(category) => onChange({ ...filters, category })} />
          <MultiSelect label="Borough" values={options.boroughs} selected={filters.borough} render={titleCase}
            onChange={(borough) => onChange({ ...filters, borough })} />
          <MultiSelect label="Agency" values={options.agencies} selected={filters.agency}
            onChange={(agency) => onChange({ ...filters, agency })} />
          <MultiSelect label="Status" values={options.statuses} selected={filters.status} render={titleCase}
            onChange={(status) => onChange({ ...filters, status })} />
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 text-xs text-ink-3">
        <span>
          Loaded data covers {options.min_date} to {options.max_date} · {options.total_records.toLocaleString("en-US")} requests.
          Dates are NYC local time of request creation. No selection in a group means all values.
        </span>
        {active > 0 && (
          <button type="button" className="text-cyan hover:underline"
            onClick={() => onChange({ ...filters, borough: [], category: [], agency: [], status: [] })}>
            Clear {active} filter{active > 1 ? "s" : ""}
          </button>
        )}
      </div>
    </section>
  );
}
