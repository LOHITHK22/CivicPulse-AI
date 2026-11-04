// Dashboard filters live in the URL so every component reads the same state and views are shareable.

export interface DashboardFilters {
  start: string | null;
  end: string | null;
  borough: string[];
  category: string[];
  agency: string[];
  status: string[];
}

export const FILTER_KEYS = ["borough", "category", "agency", "status"] as const;
export const DEFAULT_WINDOW_DAYS = 28;

export function parseFilters(sp: URLSearchParams): DashboardFilters {
  return {
    start: sp.get("start"),
    end: sp.get("end"),
    borough: sp.getAll("borough"),
    category: sp.getAll("category"),
    agency: sp.getAll("agency"),
    status: sp.getAll("status"),
  };
}

export function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Apply the default window (last N complete days of loaded data) when no dates are in the URL. */
export function withDefaults(f: DashboardFilters, maxDate: string | null, minDate: string | null): DashboardFilters {
  if (f.start || f.end || !maxDate) return f;
  let start = addDays(maxDate, -(DEFAULT_WINDOW_DAYS - 1));
  if (minDate && start < minDate) start = minDate;
  return { ...f, start, end: maxDate };
}

export function toQuery(f: DashboardFilters, extra: Record<string, string | number | undefined> = {}): string {
  const sp = new URLSearchParams();
  if (f.start) sp.set("start", f.start);
  if (f.end) sp.set("end", f.end);
  for (const k of FILTER_KEYS) for (const v of f[k]) sp.append(k, v);
  for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== "") sp.set(k, String(v));
  return sp.toString();
}
