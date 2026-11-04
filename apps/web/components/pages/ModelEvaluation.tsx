"use client";

import { CalibrationChart, DataTable, ImportanceBars, ModelLines, modelLabel } from "@/components/predict";
import { Caveats, Section, Stat, SyntheticBadge } from "@/components/pages/predict-shared";
import { Card } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { fmtDate, fmtDateTime, fmtInt, fmtPct, shortDate } from "@/lib/format";
import type { AnomalyEvaluation, DelayEvaluation, ForecastEvaluation, Registry } from "@/lib/predict-types";

const FC_MODELS = ["naive", "seasonal_naive", "rolling_mean", "lightgbm"];
const DR_MODELS = ["majority_class", "logistic_regression", "lightgbm"];
const f3 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(3));
const f2 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

export function ModelEvaluation() {
  const reg = useApi<Registry>("/api/v1/models");
  const fc = useApi<ForecastEvaluation>("/api/v1/models/forecast/evaluation");
  const dr = useApi<DelayEvaluation>("/api/v1/models/delay_risk/evaluation");
  const an = useApi<AnomalyEvaluation>("/api/v1/models/anomaly/evaluation");

  return (
    <div className="space-y-6">
      <Section state={reg} height="h-24">
        {(r) => (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Model run" value={<span className="text-base">{r.run_id}</span>} note={`Trained ${fmtDateTime(r.created_at_utc)} UTC · seed ${r.seed}`} />
            <Stat label="Training data" value={fmtInt(r.data.rows)}
              note={`Requests created ${fmtDate(r.data.min_created_at)} → ${fmtDate(r.data.max_created_at)} · build ${r.data.build_id}`} />
            <Stat label="Code version" value={<span className="text-base">{r.git_commit.slice(0, 10)}</span>}
              note={`LightGBM ${r.versions.lightgbm} · scikit-learn ${r.versions["scikit-learn"]}`} />
            <Stat label="Data fingerprint" value={<span className="text-base">{r.data.fact_sha256.slice(0, 12)}…</span>}
              note="SHA-256 of the warehouse fact table the models were trained on" />
          </div>
        )}
      </Section>

      <Card id="fc" title="Demand forecasting: rolling-origin backtest"
        subtitle="Every model forecast the same series from the same weekly origins, using only data up to each origin.">
        <Section state={fc}>
          {(e) => (
            <div className="space-y-5">
              <p className="text-xs text-ink-2">
                Validation origins {fmtDate(e.periods.validation.first_origin)} → {fmtDate(e.periods.validation.last_origin)} ({e.periods.validation.origins}) ·
                Test origins {fmtDate(e.periods.test.first_origin)} → {fmtDate(e.periods.test.last_origin)} ({e.periods.test.origins}) ·
                Training: {e.periods.training}. Selected: <strong className="text-ink">{modelLabel(e.selection.selected)}</strong> ({e.selection.rule}).
              </p>
              <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
                <DataTable caption="Forecast accuracy by model" head={["Model", "Period", "MAE", "WAPE", "Bias", `${fmtPct(e.interval.level, 0)} coverage`, "Mean width", "Forecasts"]}
                  rows={["validation", "test"].flatMap((p) => FC_MODELS.map((m) => {
                    const r = e.overall.find((x) => x.model === m && x.period === p);
                    return [modelLabel(m) + (m === e.selection.selected && p === "test" ? " ✓" : ""), p, f2(r?.mae), fmtPct(r?.wape),
                      f2(r?.bias), fmtPct(r?.interval_coverage), r?.interval_mean_width?.toFixed(1) ?? "—", fmtInt(r?.n)];
                  }))} />
              </div>
              <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
                <DataTable caption="LightGBM vs baselines" head={["Comparison (test)", "Baseline WAPE", "LightGBM WAPE", "Relative change", "95% CI of WAPE difference", "Clear?"]}
                  rows={e.comparisons.map((c) => [`LightGBM vs ${modelLabel(c.baseline)}`, fmtPct(c.baseline_value), fmtPct(c.candidate_value),
                    `${c.relative_improvement >= 0 ? "−" : "+"}${fmtPct(Math.abs(c.relative_improvement))} error`,
                    `${(c.wape_diff_baseline_minus_candidate.ci_low * 100).toFixed(2)} to ${(c.wape_diff_baseline_minus_candidate.ci_high * 100).toFixed(2)} pts`,
                    c.significant_at_95 ? "Yes" : "No (CI includes 0)"])} />
                <p className="mt-2 text-[11px] text-ink-3">CI: paired bootstrap over test origins (2,000 resamples). Positive difference = LightGBM has lower error.</p>
              </div>
              <div className="grid gap-6 xl:grid-cols-2">
                <div>
                  <h3 className="mb-2 text-xs font-medium text-ink-2">Test WAPE per origin week</h3>
                  <ModelLines label="Test WAPE per origin" xKey="origin" models={FC_MODELS} xLabel="Origin" xFormat={shortDate}
                    rows={[...new Set(e.by_origin.filter((x) => x.period === "test").map((x) => x.origin as string))].sort().map((o) => {
                      const row: Record<string, string | number | null> = { origin: o };
                      for (const m of FC_MODELS) row[m] = e.by_origin.find((x) => x.period === "test" && x.origin === o && x.model === m)?.wape ?? null;
                      return row;
                    })} />
                </div>
                <div>
                  <h3 className="mb-2 text-xs font-medium text-ink-2">LightGBM permutation importance (test WAPE increase)</h3>
                  <ImportanceBars label="Forecast feature importance" valueLabel="WAPE increase" format={(v) => fmtPct(v, 2)}
                    rows={e.feature_importance.permutation_test_wape_increase.slice(0, 12).map((p) => ({ feature: p.feature, value: p.wape_increase_mean, err: p.wape_increase_std }))} />
                </div>
              </div>
              <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
                <DataTable caption="Test WAPE by category" head={["Category", ...FC_MODELS.map(modelLabel)]}
                  rows={[...new Set(e.by_category.map((x) => x.category_key as string))].map((c) => [c, ...FC_MODELS.map((m) =>
                    fmtPct(e.by_category.find((x) => x.period === "test" && x.model === m && x.category_key === c)?.wape))])} />
              </div>
            </div>
          )}
        </Section>
      </Card>

      <Card id="dr" title="Seven-day closure-delay risk: held-out evaluation"
        subtitle="Chronological train / validation / test with 7-day embargo gaps; only matured labels are evaluated.">
        <Section state={dr}>
          {(e) => (
            <div className="space-y-5">
              <p className="text-xs text-ink-2">
                Target <code className="text-ink">{e.target}</code>: {e.target_definition}. Train {fmtDate(e.splits.train?.created_from)} → {fmtDate(e.splits.train?.created_to)} ({fmtInt(e.splits.train?.n)}) ·
                validation {fmtDate(e.splits.validation?.created_from)} → {fmtDate(e.splits.validation?.created_to)} ({fmtInt(e.splits.validation?.n)}) ·
                test {fmtDate(e.splits.test?.created_from)} → {fmtDate(e.splits.test?.created_to)} ({fmtInt(e.splits.test?.n)}).
                Selected: <strong className="text-ink">{modelLabel(e.selection.selected)}</strong> ({e.selection.rule}).
              </p>
              <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
                <DataTable caption="Delay-risk metrics" head={["Model", "Split", "PR-AUC", "ROC-AUC", "Brier", "ECE", "Precision", "Recall", "Threshold"]}
                  rows={(["validation", "test"] as const).flatMap((s) => DR_MODELS.map((m) => {
                    const r = e.models[m][s];
                    return [modelLabel(m), s, f3(r.overall.pr_auc), f3(r.overall.roc_auc), f3(r.overall.brier), f3(r.overall.ece),
                      fmtPct(r.at_threshold.precision), fmtPct(r.at_threshold.recall), f3(e.models[m].threshold)];
                  }))} />
                <p className="mt-2 text-[11px] text-ink-3">Threshold chosen on validation (max F1) and frozen for test. Majority class predicts the training base rate ({fmtPct(e.train_prevalence)}) for everyone.</p>
              </div>
              <div className="grid gap-6 xl:grid-cols-2">
                <div>
                  <h3 className="mb-2 text-xs font-medium text-ink-2">Calibration (test)</h3>
                  <CalibrationChart label="Delay-risk calibration" series={["logistic_regression", "lightgbm"].map((m) => ({ model: m, bins: e.models[m].test.reliability }))} />
                </div>
                <div>
                  <h3 className="mb-2 text-xs font-medium text-ink-2">Largest logistic-regression coefficients (standardized log-odds)</h3>
                  <div className="max-h-[300px] overflow-auto" tabIndex={0} role="region" aria-label="Scrollable table">
                    <DataTable caption="Logistic regression coefficients" head={["Feature", "Level", "Coefficient"]}
                      rows={e.explanations.logistic_regression_coefficients.slice(0, 20).map((c) => [c.feature, c.level ?? "(numeric, per SD)", c.coefficient.toFixed(3)])} />
                  </div>
                </div>
              </div>
            </div>
          )}
        </Section>
      </Card>

      <Card id="an" title="Anomaly detection: injected-anomaly evaluation" actions={<SyntheticBadge />}>
        <Section state={an}>
          {(e) => (
            <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
              <DataTable caption="Recall by series size" head={["Detector", "z", "Series size", "Recall", "Injected"]}
                rows={e.by_series_size.filter((r) => r.threshold === Number(e.detector.z_threshold)).map((r) =>
                  [r.detector, r.threshold, r.series_size, fmtPct(r.recall), r.n])} />
            </div>
          )}
        </Section>
      </Card>

      <Caveats items={[
        "All metrics are out-of-sample: computed on periods the models never trained on. Periods and selection rules were fixed before looking at test results.",
        "Percent improvements are shown only as computed from the metrics above, with a bootstrap interval; a difference whose interval includes zero is not a reliable improvement.",
        "Feature importance and coefficients describe associations the models exploit, not causal effects.",
        "311 data records reported complaints. Reporting propensity varies by neighborhood, so models learn reporting patterns as well as conditions.",
      ]} />
    </div>
  );
}
