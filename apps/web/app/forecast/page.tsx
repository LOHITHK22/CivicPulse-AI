import type { Metadata } from "next";

import { PlannedPage } from "@/components/PlannedPage";

export const metadata: Metadata = { title: "Forecast Lab" };

export default function Page() {
  return (
    <PlannedPage title="Forecast Lab" milestone={2}
      purpose="Seven-day request-volume forecasts by category and borough."
      scope={[
        "Naive, seasonal-naive and rolling-average baselines",
        "Gradient-boosted model, kept only if it beats the baselines in rolling-origin backtests",
        "MAE, WAPE and RMSE by borough, category and season, with prediction-interval coverage",
        "Training cutoff, model version and limitations shown with every forecast",
      ]} />
  );
}
