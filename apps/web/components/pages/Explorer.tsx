"use client";

import { useEffect, useState } from "react";

import { FilterBar } from "@/components/FilterBar";
import { Card, EmptyState, ErrorState, Loading, QueryInfo, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useFilters } from "@/hooks/useFilters";
import { toQuery } from "@/lib/filters";
import { CATEGORY_LABEL, fmtDateTime, fmtHours, fmtInt, titleCase } from "@/lib/format";
import type { RequestDetail, RequestPage } from "@/lib/types";

const COLUMNS: { key: string; label: string; sortable: boolean }[] = [
  { key: "unique_key", label: "Request #", sortable: true },
  { key: "created_at", label: "Created", sortable: true },
  { key: "category_key", label: "Category", sortable: true },
  { key: "descriptor", label: "Descriptor", sortable: false },
  { key: "borough", label: "Borough", sortable: true },
  { key: "community_board", label: "Community board", sortable: false },
  { key: "status", label: "Status", sortable: true },
  { key: "hours_to_close", label: "Time to close", sortable: true },
];
const PAGE_SIZE = 25;

export function Explorer() {
  const { filters, setFilters, options } = useFilters();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("-created_at");
  const [pageState, setPageState] = useState({ key: "", page: 1 });
  const [selected, setSelected] = useState<string | null>(null);

  // Debounce free-text search.
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 350);
    return () => clearTimeout(t);
  }, [search]);
  // Any change to filters, search or sort returns to page 1 (derived, not an effect).
  const resultKey = `${filters ? toQuery(filters) : ""}|${q}|${sort}`;
  const page = pageState.key === resultKey ? pageState.page : 1;
  const setPage = (update: (p: number) => number) => setPageState({ key: resultKey, page: update(page) });

  const path = filters ? `/api/v1/requests?${toQuery(filters, { q, sort, page, page_size: PAGE_SIZE })}` : null;
  const data = useApi<RequestPage>(path);

  if (options.status === "loading") return <Loading label="Loading filters" className="h-96" />;
  if (options.status === "error") return <ErrorState error={options.error} />;
  if (!filters) return null;

  const exportHref = `/api/v1/requests/export.csv?${toQuery(filters, { q })}`;
  const totalPages = data.status === "success" ? Math.max(1, Math.ceil(data.data.total / PAGE_SIZE)) : 1;

  const toggleSort = (key: string) => setSort((s) => (s === `-${key}` ? key : `-${key}`));
  const ariaSort = (key: string) => (sort === key ? "ascending" : sort === `-${key}` ? "descending" : "none");

  return (
    <>
      <FilterBar filters={filters} options={options.data} onChange={setFilters} />
      <div className="grid gap-6 2xl:grid-cols-[1fr_380px]">
        <Card id="requests" className="min-w-0" title="Service requests"
          subtitle={data.status === "success" ? `${fmtInt(data.data.total)} matching requests` : "Loading…"}
          actions={
            <div className="flex items-center gap-3">
              {data.status === "success" && <QueryInfo meta={data.data.meta} />}
              <a href={exportHref} className="rounded-md border border-border-strong px-3 py-1.5 text-xs text-ink hover:bg-surface-2"
                download>
                Export CSV (max 10,000 rows)
              </a>
            </div>
          }>
          <div className="mb-3">
            <label htmlFor="search" className="sr-only">Search requests</label>
            <input id="search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={100}
              placeholder="Search request number, descriptor, ZIP or community board"
              className="w-full rounded-md border border-border bg-surface-2 px-3 py-2 text-sm text-ink placeholder:text-ink-3" />
          </div>
          {data.status === "loading" && <Loading className="h-96" label="Loading requests" />}
          {data.status === "error" && <ErrorState error={data.error} />}
          {data.status === "success" && data.data.rows.length === 0 && <EmptyState />}
          {data.status === "success" && data.data.rows.length > 0 && (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] text-left text-sm">
                  <caption className="sr-only">Service requests, sortable by column</caption>
                  <thead className="text-xs text-ink-3">
                    <tr>
                      {COLUMNS.map((c) => (
                        <th key={c.key} scope="col" aria-sort={c.sortable ? ariaSort(c.key) : undefined} className="py-2 pr-3 font-medium">
                          {c.sortable ? (
                            <button type="button" onClick={() => toggleSort(c.key)} className="inline-flex items-center gap-1 hover:text-ink">
                              {c.label}
                              <span aria-hidden="true">{sort === c.key ? "↑" : sort === `-${c.key}` ? "↓" : "↕"}</span>
                            </button>
                          ) : c.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="tabular">
                    {data.data.rows.map((r) => (
                      <tr key={r.unique_key}
                        className={`border-t border-border ${selected === r.unique_key ? "bg-surface-3" : "hover:bg-surface-2"}`}>
                        <td className="py-2 pr-3">
                          <button type="button" onClick={() => setSelected(r.unique_key)} className="text-cyan hover:underline"
                            aria-label={`View details for request ${r.unique_key}`}>
                            {r.unique_key}
                          </button>
                        </td>
                        <td className="py-2 pr-3 text-ink-2">{fmtDateTime(r.created_at)}</td>
                        <td className="py-2 pr-3 text-ink-2">{CATEGORY_LABEL[r.category_key] ?? r.category_key}</td>
                        <td className="py-2 pr-3 text-ink-2">{r.descriptor ?? "—"}</td>
                        <td className="py-2 pr-3 text-ink-2">{titleCase(r.borough)}</td>
                        <td className="py-2 pr-3 text-ink-2">{r.community_board ?? "—"}</td>
                        <td className="py-2 pr-3 text-ink-2">{r.status}</td>
                        <td className="py-2 pr-3 text-ink-2">{fmtHours(r.hours_to_close)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <nav aria-label="Pagination" className="mt-4 flex items-center justify-between text-xs text-ink-2">
                <span className="tabular">Page {page} of {fmtInt(totalPages)}</span>
                <div className="flex gap-2">
                  <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
                    className="rounded-md border border-border px-3 py-1.5 disabled:opacity-40 enabled:hover:bg-surface-2">Previous</button>
                  <button type="button" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}
                    className="rounded-md border border-border px-3 py-1.5 disabled:opacity-40 enabled:hover:bg-surface-2">Next</button>
                </div>
              </nav>
            </>
          )}
        </Card>
        <DetailPanel uniqueKey={selected} onClose={() => setSelected(null)} />
      </div>
    </>
  );
}

function DetailPanel({ uniqueKey, onClose }: { uniqueKey: string | null; onClose: () => void }) {
  const detail = useApi<RequestDetail>(uniqueKey ? `/api/v1/requests/${encodeURIComponent(uniqueKey)}` : null);
  if (!uniqueKey) {
    return (
      <Card title="Request detail">
        <p className="text-sm text-ink-3">Select a request number to see its full record, quality flags and lineage.</p>
      </Card>
    );
  }
  return (
    <Card id="detail" title={`Request ${uniqueKey}`}
      actions={<button type="button" onClick={onClose} className="text-xs text-ink-3 hover:text-ink">Close</button>}>
      {detail.status === "loading" && <Loading className="h-64" />}
      {detail.status === "error" && <ErrorState error={detail.error} />}
      {detail.status === "success" && (
        <div className="space-y-4 text-sm">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
            {([
              ["Complaint", `${detail.data.complaint_type}: ${detail.data.descriptor ?? "—"}`],
              ["Agency", detail.data.agency_name ?? detail.data.agency ?? "—"],
              ["Status", detail.data.status],
              ["Created", fmtDateTime(detail.data.created_at)],
              ["Closed", fmtDateTime(detail.data.closed_at)],
              ["Time to close", fmtHours(detail.data.hours_to_close)],
              ["Borough", titleCase(detail.data.borough)],
              ["Community board", detail.data.community_board ?? "—"],
              ["ZIP", detail.data.incident_zip ?? "—"],
              ["Location type", detail.data.location_type ?? "—"],
              ["Channel", detail.data.channel ?? "—"],
            ] as const).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-ink-3">{k}</dt>
                <dd className="text-ink">{v}</dd>
              </div>
            ))}
          </dl>
          <div>
            <h3 className="mb-1.5 text-xs font-medium text-ink-3">Data-quality flags</h3>
            {detail.data.quality_flags.length === 0
              ? <p className="text-ink-2">None</p>
              : <ul className="flex flex-wrap gap-1.5">{detail.data.quality_flags.map((f) => (
                <li key={f}><StatusBadge status="warn" /> <span className="text-ink-2">{f.replaceAll("_", " ")}</span></li>
              ))}</ul>}
          </div>
          <div>
            <h3 className="mb-1.5 text-xs font-medium text-ink-3">Lineage</h3>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 break-all font-mono text-[11px] text-ink-2">
              {Object.entries(detail.data.lineage).map(([k, v]) => (
                <div key={k} className="contents"><dt className="text-ink-3">{k}</dt><dd>{v ?? "—"}</dd></div>
              ))}
            </dl>
          </div>
          <p className="text-xs text-ink-3">Street addresses and exact coordinates are intentionally not shown.</p>
        </div>
      )}
    </Card>
  );
}
