import { Suspense } from "react";

import { Overview } from "@/components/pages/Overview";
import { Loading, PageHeader } from "@/components/ui";

export default function Page() {
  return (
    <>
      <PageHeader
        title="Operations overview"
        subtitle="NYC 311 residential noise and heat/hot-water service requests. Every figure is computed from the loaded NYC Open Data snapshot."
      />
      <Suspense fallback={<Loading label="Loading dashboard" className="h-96" />}>
        <Overview />
      </Suspense>
    </>
  );
}
