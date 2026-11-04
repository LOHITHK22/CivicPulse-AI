import type { ReactNode } from "react";

import type { ApiError } from "@/hooks/useApi";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink-2">{subtitle}</p>}
      </div>
      {actions}
    </header>
  );
}

export function Card({ title, subtitle, actions, children, className = "", id }: {
  title?: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; id?: string;
}) {
  return (
    <section aria-labelledby={id && title ? `${id}-title` : undefined}
      className={`rounded-xl border border-border bg-surface-1 p-5 ${className}`}>
      {(title || actions) && (
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 id={id ? `${id}-title` : undefined} className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-3">{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Skeleton({ className = "h-40" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}

export function Loading({ label = "Loading", className = "h-56" }: { label?: string; className?: string }) {
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">{label}…</span>
      <Skeleton className={className} />
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-[color:var(--status-critical)]/50 bg-[color:var(--status-critical)]/10 p-4 text-sm">
      <p className="font-medium text-ink">Couldn’t load this data</p>
      <p className="mt-1 text-ink-2">{error.message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}
          className="mt-3 rounded-md border border-border-strong px-3 py-1.5 text-xs text-ink hover:bg-surface-2">
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message = "No requests match the current filters." }: { message?: string }) {
  return (
    <div className="grid h-40 place-items-center rounded-lg border border-dashed border-border text-sm text-ink-3">
      {message}
    </div>
  );
}

const STATUS_STYLE: Record<string, { color: string; icon: string; label: string }> = {
  pass: { color: "var(--status-good)", icon: "M5 12l4 4 10-10", label: "Pass" },
  warn: { color: "var(--status-warning)", icon: "M12 4 2 20h20L12 4zM12 10v4M12 17h.01", label: "Warning" },
  fail: { color: "var(--status-critical)", icon: "M6 6l12 12M18 6 6 18", label: "Fail" },
  info: { color: "var(--text-muted)", icon: "M12 11v6M12 7h.01", label: "Info" },
  succeeded: { color: "var(--status-good)", icon: "M5 12l4 4 10-10", label: "Succeeded" },
  failed: { color: "var(--status-critical)", icon: "M6 6l12 12M18 6 6 18", label: "Failed" },
  paused: { color: "var(--status-warning)", icon: "M9 6v12M15 6v12", label: "Paused" },
  running: { color: "var(--accent-cyan)", icon: "M12 4v4M12 16v4M4 12h4M16 12h4", label: "Running" },
};

/** Status is always icon + label + color — never color alone. */
export function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.info;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs text-ink">
      <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={s.color} strokeWidth="3"
        strokeLinecap="round" strokeLinejoin="round"><path d={s.icon} /></svg>
      {s.label}
    </span>
  );
}

export function QueryInfo({ meta }: { meta?: { elapsed_ms: number; rows: number } }) {
  if (!meta) return null;
  return <span className="tabular text-[11px] text-ink-3">{meta.rows} rows · {meta.elapsed_ms.toFixed(0)} ms</span>;
}
