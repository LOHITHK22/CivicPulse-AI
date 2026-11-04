"use client";

import { AnomalyChart, DataTable } from "@/components/predict";
import { BOROUGHS, Caveats, Section, SeriesPicker, Stat, SyntheticBadge, useSeriesParams } from "@/components/pages/predict-shared";
import { Card, EmptyState, QueryInfo } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { CATEGORY_LABEL, fmtDate, fmtInt, fmtPct, titleCase } from "@/lib/format";
import type { AnomalyEvaluation, AnomalyList, AnomalySeries } from "@/lib/predict-types";

const WITH_CITY = ["CITYWIDE", ...BOROUGHS];

export function Anomalies() {
  const { category, borough, set } = useSeriesParams({ category: "noise_residential", borough: "CITYWIDE" }, WITH_CITY);
  const sq = `category=${category}&borough=${encodeURIComponent(borough)}`;
  const series = useApi<AnomalySeries>(`/api/v1/anomalies/series?${sq}`);
  const list = useApi<AnomalyList>(`/api/v1/anomalies?${sq}&limit=50`);
  const ev = useApi<AnomalyEvaluation>("/api/v1/models/anomaly/evaluation");
  const place = borough === "CITYWIDE" ? "Citywide" : titleCase(borough);

  return (
    <>
      <SeriesPicker category={category} borough={borough} set={set} boroughs={WITH_CITY} />
      <div className="space-y-6">
        <Card id="series" title={`Daily volume vs expected · ${CATEGORY_LABEL[category]} · ${place}`}
          subtitle="Last 180 days. Expected = median of the same weekday over the previous 8 weeks. Marked days have a robust z-score of at least 3.5."
          actions={series.status === "success" && <QueryInfo meta={series.data.meta} />}>
          <Section state={series}>
            {(s) => s.points.length === 0 ? <EmptyState message="No scored days for this series." /> :
              <AnomalyChart points={s.points} label={`Anomalies, ${CATEGORY_LABEL[category]}, ${place}`} />}
          </Section>
        </Card>

        <Card id="list" title="Detected anomalies (unverified)"
          subtitle="Newest first. These are statistical alerts on real data; no confirmed labels exist for real events.">
          <Section state={list}>
            {(l) => l.rows.length === 0 ? <EmptyState message="No anomalies detected for this series." /> : (
              <div className="max-h-[360px] overflow-auto">
                <DataTable caption="Detected anomalies" head={["Date", "Direction", "Actual", "Expected", "Difference", "Robust z"]}
                  rows={l.rows.map((r) => [fmtDate(r.date), r.direction === "spike" ? "▲ Spike" : "▼ Drop", fmtInt(r.actual),
                    r.expected.toFixed(1), `${r.residual > 0 ? "+" : ""}${r.residual.toFixed(0)}`, r.score.toFixed(1)])} />
                <p className="mt-2 text-[11px] text-ink-3">{fmtInt(l.total)} alerts for this series across the scored history.</p>
              </div>
            )}
          </Section>
        </Card>

        <Card id="eval" title="How well does the detector find known anomalies?" actions={<SyntheticBadge />}
          subtitle="Known spikes and drops were injected into the real series (seeded), then the detector was re-run. Recall = share of injected days flagged with the right direction.">
          <Section state={ev}>
            {(e) => {
              const prim = e.summary.find((r) => r.detector === "seasonal_mad" && r.threshold === Number(e.detector.z_threshold));
              return (
                <div className="space-y-5">
                  <div className="grid gap-4 sm:grid-cols-3">
                    <Stat label="Recall at the production threshold" value={fmtPct(prim?.recall)}
                      note={`${fmtInt(prim?.n_injected)} injected days, ${e.window_start} → ${e.window_end}`} />
                    <Stat label="Alert rate on uninjected days" value={fmtPct(prim?.alert_rate_uninjected_days, 2)}
                      note="Not a false-positive rate: real anomalies exist in these days too" />
                    <Stat label="Real-data alerts (unverified)" value={fmtInt(e.real_data.alerts)}
                      note={`Across ${fmtInt(e.real_data.days_scored)} scored series-days`} />
                  </div>
                  <DataTable caption="Detector comparison" head={["Detector", "z threshold", "Recall", "Injected", "Alert rate (uninjected)"]}
                    rows={e.summary.map((r) => [r.detector === "seasonal_mad" ? "Seasonal median + MAD" : "28-day median + MAD (no weekday)",
                      r.threshold, fmtPct(r.recall), fmtInt(r.n_injected), fmtPct(r.alert_rate_uninjected_days, 2)])} />
                  <DataTable caption="Recall by injected magnitude" head={["Direction", "Magnitude (× expected)", "Recall", "Cases"]}
                    rows={e.by_magnitude.filter((r) => r.detector === "seasonal_mad" && r.threshold === Number(e.detector.z_threshold))
                      .map((r) => [r.direction, r.direction === "drop" ? `−${Math.min(r.magnitude, 0.9)}` : `+${r.magnitude}`, fmtPct(r.recall), r.n])} />
                </div>
              );
            }}
          </Section>
        </Card>

        <Caveats items={[
          "An alert means the count was unusual for that weekday given recent weeks. It does not say why: weather, outages, reporting changes or seasonal onset (heat season starts in October) all produce alerts.",
          "Injected anomalies are synthetic and are used only to measure detection. They are never shown as real events.",
          "Small series (Staten Island, low-volume days) need larger relative changes to be detected; see the recall-by-size results in Model Evaluation.",
        ]} />
      </div>
    </>
  );
}
