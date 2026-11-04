import type { Metadata } from "next";
import { Suspense } from "react";

import { ModelEvaluation } from "@/components/pages/ModelEvaluation";
import { Loading, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Model Evaluation" };

export default function Page() {
  return (
    <>
      <PageHeader title="Model Evaluation"
        subtitle="How every model was trained, validated and tested, compared against simple baselines. All numbers are computed offline from the current model run." />
      <Suspense fallback={<Loading label="Loading Model Evaluation" className="h-96" />}>
        <ModelEvaluation />
      </Suspense>
    </>
  );
}
