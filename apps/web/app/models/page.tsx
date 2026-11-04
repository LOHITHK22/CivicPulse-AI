import type { Metadata } from "next";

import { PlannedPage } from "@/components/PlannedPage";

export const metadata: Metadata = { title: "Model Evaluation" };

export default function Page() {
  return (
    <PlannedPage title="Model Evaluation" milestone={2}
      purpose="Backtests, baseline comparisons, calibration and drift for every model."
      scope={[
        "Reads the model_evaluations table (schema exists; deliberately empty in Milestone 1)",
        "Forecast error vs baselines, classification calibration and threshold analysis",
        "Training date, training cutoff, test period and known limitations per model version",
      ]} />
  );
}
