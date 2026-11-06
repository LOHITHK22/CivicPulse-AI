import type { Metadata } from "next";
import { Suspense } from "react";

import { GeoExplorer } from "@/components/pages/GeoExplorer";
import { Loading, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Geographic Intelligence" };

export default function Page() {
  return (
    <>
      <PageHeader title="Geographic Intelligence"
        subtitle="Borough-level 311 request activity from the warehouse: volume, period-over-period change, rankings and trends. Aggregated counts only; boroughs are the finest geography matched reliably in this dataset." />
      <Suspense fallback={<Loading label="Loading Geographic Intelligence" className="h-96" />}>
        <GeoExplorer />
      </Suspense>
    </>
  );
}
