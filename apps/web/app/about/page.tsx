import type { Metadata } from "next";

import { Card, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "About the Project" };

export default function Page() {
  return (
    <>
      <PageHeader title="About CivicPulse AI"
        subtitle="A personal engineering portfolio project: predictive city operations intelligence on public NYC 311 data." />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="What it does today (Milestone 1)">
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink-2">
            <li>Extracts NYC 311 residential-noise and heat/hot-water requests from the Socrata API using keyset pagination, retries, checkpoints and incremental watermarks.</li>
            <li>Builds cleaned, typed Parquet tables with DuckDB, with data-quality rules and a full audit of each run.</li>
            <li>Serves reproducible metrics through a read-only FastAPI service, used by this dashboard.</li>
          </ul>
          <p className="mt-3 text-sm text-ink-3">Forecasting, delay risk, anomaly detection, the MCP server and the AI analyst are planned for later milestones and are not implemented yet.</p>
        </Card>
        <Card title="Data source & attribution">
          <p className="text-sm text-ink-2">
            Data: <a className="text-cyan underline underline-offset-2" href="https://data.cityofnewyork.us/d/erm2-nwe9" rel="noreferrer" target="_blank">311 Service Requests from 2020 to Present</a>,
            NYC Open Data (dataset erm2-nwe9), published by the NYC Office of Technology and Innovation. The data has been
            filtered, cleaned and aggregated by this project and is not an official City product. The City makes no warranty
            as to its accuracy or completeness.
          </p>
        </Card>
        <Card title="How to read these numbers">
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink-2">
            <li>311 data records <em>reported complaints</em>, not the true prevalence of noise or heating problems. Reporting propensity differs between neighborhoods and populations.</li>
            <li>Closure times reflect when an agency recorded a request as closed. That does not always mean the underlying problem was fixed.</li>
            <li>Median closure time excludes requests that are still open, so it understates delays when many requests remain open.</li>
            <li>Data is a versioned snapshot. Its timestamp is shown on the dashboard and the Data Quality page.</li>
          </ul>
        </Card>
        <Card title="Privacy">
          <p className="text-sm text-ink-2">
            Street addresses, cross streets, BBL and free-text resolution descriptions are never ingested. Coordinates are
            used only for validation and future aggregated geography, and are never returned by the API.
          </p>
        </Card>
      </div>
    </>
  );
}
