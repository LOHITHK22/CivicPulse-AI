import type { Metadata } from "next";

import { PlannedPage } from "@/components/PlannedPage";

export const metadata: Metadata = { title: "Geographic Intelligence" };

export default function Page() {
  return (
    <PlannedPage title="Geographic Intelligence" milestone={4}
      purpose="Aggregated geographic view of complaint activity using borough and community-district boundaries."
      scope={[
        "MapLibre map with borough / community-district boundaries (aggregated, never individual addresses)",
        "Category and time filters shared with the dashboard",
        "Trend comparisons and anomaly indicators per area",
        "Population-normalized rates only with a documented, period-matched denominator",
      ]} />
  );
}
