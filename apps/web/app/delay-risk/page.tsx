import type { Metadata } from "next";

import { PlannedPage } from "@/components/PlannedPage";

export const metadata: Metadata = { title: "Delay Risk" };

export default function Page() {
  return (
    <PlannedPage title="Delay Risk" milestone={2}
      purpose="Estimated probability that a newly created request remains open beyond seven days."
      scope={[
        "Target request_not_closed_within_7_days, labeled only once a request is at least 7 days old",
        "Majority-class, logistic regression and LightGBM compared on chronological splits",
        "PR-AUC, Brier score and calibration checked before any probability is displayed",
        "Framed as risk of remaining open, never as a guaranteed service failure",
      ]} />
  );
}
