import type { Metadata } from "next";
import { Suspense } from "react";

import { DelayRisk } from "@/components/pages/DelayRisk";
import { Loading, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Delay Risk" };

export default function Page() {
  return (
    <>
      <PageHeader title="Delay Risk"
        subtitle="Probability that a new request is not closed within 7 days, estimated from information available when it was created. Part of the Prediction Lab." />
      <Suspense fallback={<Loading label="Loading Delay Risk" className="h-96" />}>
        <DelayRisk />
      </Suspense>
    </>
  );
}
