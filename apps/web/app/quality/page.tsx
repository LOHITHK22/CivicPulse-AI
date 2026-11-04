import type { Metadata } from "next";

import { Quality } from "@/components/pages/Quality";
import { PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Data Quality" };

export default function Page() {
  return (
    <>
      <PageHeader title="Data quality"
        subtitle="Ingestion runs, validation rules, missingness, freshness and schema drift for the loaded snapshot. Every status here comes from an evaluated rule." />
      <Quality />
    </>
  );
}
