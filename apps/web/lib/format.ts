const nf = new Intl.NumberFormat("en-US");

export const fmtInt = (n: number | null | undefined) => (n == null ? "—" : nf.format(Math.round(n)));

export const fmtPct = (p: number | null | undefined, digits = 1) =>
  p == null ? "—" : `${(p * 100).toFixed(digits)}%`;

export const fmtSignedPct = (p: number | null | undefined) =>
  p == null ? "—" : `${p > 0 ? "+" : ""}${(p * 100).toFixed(1)}%`;

export function fmtHours(h: number | null | undefined): string {
  if (h == null) return "—";
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} d`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16);
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

export const shortDate = (iso: string) =>
  new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });

export const titleCase = (s: string) => s.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

export const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)"];

// Color follows the entity, never its rank: fixed assignments.
export const CATEGORY_COLOR: Record<string, string> = {
  noise_residential: "var(--series-1)",
  heat_hot_water: "var(--series-2)",
};
export const BOROUGH_COLOR: Record<string, string> = {
  BRONX: "var(--series-1)",
  BROOKLYN: "var(--series-2)",
  MANHATTAN: "var(--series-3)",
  QUEENS: "var(--series-4)",
  "STATEN ISLAND": "var(--series-5)",
  UNSPECIFIED: "var(--text-muted)",
};
export const STATUS_COLOR: Record<string, string> = {
  closed: "var(--series-1)",
  open: "var(--series-2)",
  unknown: "var(--text-muted)",
};
export const CATEGORY_LABEL: Record<string, string> = {
  noise_residential: "Residential noise",
  heat_hot_water: "Heat / hot water",
};
