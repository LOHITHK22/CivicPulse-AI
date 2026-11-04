"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import { useApi } from "@/hooks/useApi";
import { type DashboardFilters, parseFilters, toQuery, withDefaults } from "@/lib/filters";
import type { FilterOptions } from "@/lib/types";

/** Resolved filters (URL + default window) shared by every component on the page. */
export function useFilters() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const options = useApi<FilterOptions>("/api/v1/meta/filters");

  const raw = useMemo(() => parseFilters(new URLSearchParams(sp.toString())), [sp]);
  const filters: DashboardFilters | null = useMemo(() => {
    if (options.status !== "success") return null;
    return withDefaults(raw, options.data.last_complete_date ?? options.data.max_date, options.data.min_date);
  }, [raw, options]);

  const setFilters = useCallback(
    (next: DashboardFilters) => {
      const q = toQuery(next);
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    },
    [router, pathname],
  );

  return { filters, setFilters, options };
}
