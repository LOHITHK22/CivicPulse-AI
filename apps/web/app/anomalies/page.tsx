import type { Metadata } from "next";
import { Suspense } from "react";

import { Anomalies } from "@/components/pages/Anomalies";
import { Loading, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Anomaly Center" };

export default function Page() {
  return (
    <>
      <PageHeader title="Anomaly Center"
        subtitle="Days when request volume was unusually high or low for that weekday, by category and borough. Part of the Prediction Lab." />
      <Suspense fallback={<Loading label="Loading Anomaly Center" className="h-96" />}>
        <Anomalies />
      </Suspense>
    </>
  );
}
