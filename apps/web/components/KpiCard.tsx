import type { ReactNode } from "react";

export function KpiCard({ label, value, detail, hint, tone }: {
  label: string; value: ReactNode; detail?: ReactNode; hint?: string; tone?: "muted";
}) {
  return (
    <div className="rounded-xl border border-border bg-surface-1 p-4" title={hint}>
      <p className="text-xs font-medium text-ink-3">{label}</p>
      <p className={`tabular mt-1.5 text-2xl font-semibold tracking-tight ${tone === "muted" ? "text-ink-3" : "text-ink"}`}>
        {value}
      </p>
      {detail && <div className="mt-1 text-xs text-ink-2">{detail}</div>}
    </div>
  );
}
