import type { Metadata } from "next";

import { PlannedPage } from "@/components/PlannedPage";

export const metadata: Metadata = { title: "Anomaly Center" };

export default function Page() {
  return (
    <PlannedPage title="Anomaly Center" milestone={2}
      purpose="Statistically unusual changes in complaint volume by category and area."
      scope={[
        "Interpretable methods: seasonal baselines, robust z-scores and median absolute deviation",
        "Actual vs expected counts, anomaly score and method for each flag",
        "Evaluation on clearly labeled synthetic injected anomalies",
        "No causal claims without supporting evidence",
      ]} />
  );
}
