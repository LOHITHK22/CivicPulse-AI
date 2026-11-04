import type { Metadata } from "next";
import { Suspense } from "react";

import { Explorer } from "@/components/pages/Explorer";
import { Loading, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Service Request Explorer" };

export default function Page() {
  return (
    <>
      <PageHeader title="Service Request Explorer"
        subtitle="Search, sort and drill into individual requests from the loaded snapshot. Exports are bounded to 10,000 rows." />
      <Suspense fallback={<Loading label="Loading explorer" className="h-96" />}>
        <Explorer />
      </Suspense>
    </>
  );
}
