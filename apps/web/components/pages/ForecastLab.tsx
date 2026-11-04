"use client";

import { useState } from "react";

import { IntervalChart, ModelLines, modelLabel } from "@/components/predict";
import { Caveats, Section, SeriesPicker, Select, Stat, useSeriesParams } from "@/components/pages/predict-shared";
import { Card } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { CATEGORY_LABEL, fmtDate, fmtPct, titleCase } from "@/lib/format";
import type { BacktestResponse, ForecastEvaluation, ForecastResponse } from "@/lib/predict-types";

const MODELS = ["naive", "seasonal_naive", "rolling_mean", "lightgbm"];

export function ForecastLab() {
  const { category, borough, set } = useSeriesParams({ category: "heat_hot_water", borough: "BROOKLYN" });
  const [horizon, setHorizon] = useState("1");
  const sq = `category=${category}&borough=${encodeURIComponent(borough)}`;
  const fc = useApi<ForecastResponse>(`/api/v1/predictions/forecast?${sq}&history_days=56`);
  const ev = useApi<ForecastEvaluation>("/api/v1/models/forecast/evaluation");
  const bt = useApi<BacktestResponse>(`/api/v1/predictions/forecast/backtest?${sq}&horizon=${horizon}`);
  const seriesName = `${CATEGORY_LABEL[category]} · ${titleCase(borough)}`;

  return (
    <>
      <SeriesPicker category={category} borough={borough} set={set}
        extra={<Select label="Backtest horizon" value={horizon} options={["1", "3", "7"]} onChange={setHorizon}
          render={(v) => `${v} day${v === "1" ? "" : "s"} ahead`} />} />
      <div className="space-y-6">
        <Section state={fc} height="h-24">
          {(f) => {
            const sel = f.test_metrics.find((m) => m.model === f.model);
            const bestBase = f.test_metrics.filter((m) => m.model !== "lightgbm")
              .sort((a, b) => (a.wape ?? Infinity) - (b.wape ?? Infinity))[0];
            return (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <Stat label="Model in use" value={f.model ? modelLabel(f.model) : "—"}
                  note="Chosen by lowest validation WAPE across all series" />
                <Stat label="Forecast origin (last complete day)" value={fmtDate(f.origin)} note="Forecasts days 1–7 after this date" />
                <Stat label={`Test WAPE, this series (${f.model ? modelLabel(f.model) : "—"})`} value={fmtPct(sel?.wape)}
                  note={bestBase ? `Best baseline here: ${modelLabel(bestBase.model)} ${fmtPct(bestBase.wape)}` : undefined} />
                <Stat label={`${fmtPct(f.interval_level, 0)} interval coverage (test)`} value={fmtPct(sel?.interval_coverage)}
                  note={`Share of test days inside the interval; target ${fmtPct(f.interval_level, 0)}`} />
              </div>
            );
          }}
        </Section>

        <Card id="fc" title="Next seven days" subtitle={`${seriesName} · last 8 weeks of actuals, then the forecast with its prediction interval`}>
          <Section state={fc}>
            {(f) => {
              const rows = [
                ...f.history.map((h) => ({ date: h.date, actual: h.actual, predicted: null, lower: null, upper: null })),
                ...f.forecast.map((p) => ({ date: p.target_date, actual: null, predicted: p.predicted, lower: p.lower, upper: p.upper })),
              ];
              return <IntervalChart rows={rows} label={`Seven-day forecast, ${seriesName}`} splitAt={f.origin ?? undefined}
                intervalLabel={`${fmtPct(f.interval_level, 0)} prediction interval`} predLabel="Forecast" />;
            }}
          </Section>
        </Card>

        <Card id="bt" title="Backtest: predicted vs actual"
          subtitle={`Out-of-sample forecasts made ${horizon} day(s) ahead from weekly origins (validation and test periods). Each prediction used only data available at its origin.`}>
          <Section state={bt}>
            {(b) => <IntervalChart label={`Backtest, ${seriesName}`} predLabel={`${modelLabel(b.model)} (${horizon}-day ahead)`}
              intervalLabel="Prediction interval"
              rows={b.points.map((p) => ({ date: p.target_date, actual: p.actual, predicted: p.predicted, lower: p.lower, upper: p.upper }))} />}
          </Section>
        </Card>

        <div className="grid gap-6 xl:grid-cols-2">
          <Card id="hz" title="Error by horizon (test period, all series)" subtitle="WAPE = Σ|actual − forecast| / Σ actual. Lower is better.">
            <Section state={ev}>
              {(e) => {
                const rows = [1, 2, 3, 4, 5, 6, 7].map((h) => {
                  const r: Record<string, number | string | null> = { horizon: h };
                  for (const m of MODELS) r[m] = e.by_horizon.find((x) => x.period === "test" && x.horizon === h && x.model === m)?.wape ?? null;
                  return r;
                });
                return <ModelLines rows={rows} xKey="horizon" models={MODELS} label="WAPE by forecast horizon" xLabel="Day" xFormat={(v) => `+${v}`} />;
              }}
            </Section>
          </Card>
          <Card id="cat" title="Test accuracy for this series by model" subtitle="Same test origins for every model">
            <Section state={fc}>
              {(f) => (
                <table className="w-full text-left text-xs">
                  <thead className="text-ink-3"><tr>{["Model", "MAE", "WAPE", "Bias", "Interval coverage", "Mean width"].map((h) =>
                    <th key={h} scope="col" className="py-1.5 pr-3 font-medium">{h}</th>)}</tr></thead>
                  <tbody className="tabular text-ink-2">
                    {MODELS.map((m) => {
                      const r = f.test_metrics.find((x) => x.model === m);
                      return (
                        <tr key={m} className="border-t border-border">
                          <th scope="row" className="py-1.5 pr-3 font-normal text-ink">{modelLabel(m)}{m === f.model ? " (in use)" : ""}</th>
                          <td className="pr-3">{r?.mae.toFixed(2) ?? "—"}</td><td className="pr-3">{fmtPct(r?.wape)}</td>
                          <td className="pr-3">{r?.bias.toFixed(2) ?? "—"}</td><td className="pr-3">{fmtPct(r?.interval_coverage)}</td>
                          <td className="pr-3">{r?.interval_mean_width?.toFixed(1) ?? "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </Section>
          </Card>
        </div>

        <Caveats items={[
          "Forecasts are counts of requests created (reported complaints), not incidents. Weather drives heat complaints but no weather data is used, so cold snaps are not anticipated.",
          "Intervals come from past forecast errors (split-conformal), not from a probability model. Coverage on the test period is shown above; it can drift when conditions change.",
          "Daily counts come from the latest snapshot. Counts as they were known on each historical day (data vintages) cannot be reconstructed from the source.",
          "Accuracy figures are from backtests over fixed validation and test periods; see Model Evaluation for the full comparison.",
        ]} />
      </div>
    </>
  );
}
