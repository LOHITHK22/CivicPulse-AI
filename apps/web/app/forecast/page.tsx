import type { Metadata } from "next";
import { Suspense } from "react";

import { ForecastLab } from "@/components/pages/ForecastLab";
import { Loading, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Forecast Lab" };

export default function Page() {
  return (
    <>
      <PageHeader title="Forecast Lab"
        subtitle="Seven-day forecasts of daily request volume by category and borough, with prediction intervals and out-of-sample backtests. Part of the Prediction Lab." />
      <Suspense fallback={<Loading label="Loading Forecast Lab" className="h-96" />}>
        <ForecastLab />
      </Suspense>
    </>
  );
}
