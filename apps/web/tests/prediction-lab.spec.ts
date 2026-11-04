import { expect, test } from "@playwright/test";

// Every assertion compares the UI with the API response, so these run against any trained model run
// (the full real snapshot locally, or the committed real-data sample in CI).

test.describe("forecast lab", () => {
  test("renders the API forecast and its interval in the table view", async ({ page }) => {
    const api = await (await page.request.get("/api/v1/predictions/forecast?category=heat_hot_water&borough=BROOKLYN&history_days=56")).json();
    await page.goto("/forecast");
    const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Next seven days" }) });
    await expect(card.locator(".recharts-wrapper > svg")).toBeVisible();
    await card.getByRole("button", { name: "Table" }).click();
    const first = api.forecast[0];
    const row = card.getByRole("row").filter({ hasText: first.target_date });
    await expect(row).toContainText(first.predicted.toFixed(1));
    if (first.lower != null) await expect(row).toContainText(`${first.lower.toFixed(1)} – ${first.upper.toFixed(1)}`);
    await expect(page.getByText(`Forecasts days 1–7 after this date`)).toBeVisible();
  });

  test("changing borough updates the URL and the forecast", async ({ page }) => {
    await page.goto("/forecast");
    await page.getByLabel("Borough").selectOption("QUEENS");
    await expect(page).toHaveURL(/borough=QUEENS/);
    const api = await (await page.request.get("/api/v1/predictions/forecast?category=heat_hot_water&borough=QUEENS&history_days=56")).json();
    const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Next seven days" }) });
    await expect(card.getByText(/Queens/)).toBeVisible();
    await card.getByRole("button", { name: "Table" }).click();
    await expect(card.getByRole("row").filter({ hasText: api.forecast[6].target_date })).toContainText(api.forecast[6].predicted.toFixed(1));
  });

  test("shows an error state when the forecast API fails", async ({ page }) => {
    await page.route("**/api/v1/predictions/forecast?*", (r) => r.fulfill({ status: 503, body: JSON.stringify({ detail: "x" }) }));
    await page.goto("/forecast");
    await expect(page.getByRole("alert").first()).toContainText("Couldn’t load this data");
  });
});

test.describe("delay risk", () => {
  test("lists the API's highest-risk open-outcome requests and explains one", async ({ page }) => {
    const api = await (await page.request.get("/api/v1/predictions/delay-risk?limit=25")).json();
    await page.goto("/delay-risk");
    if (api.rows.length === 0) {
      await expect(page.getByText("No scored recent requests match.")).toBeVisible();
      return;
    }
    const top = api.rows[0];
    const button = page.getByRole("button", { name: top.unique_key, exact: true });
    await expect(button).toBeVisible();
    await button.click();
    await expect(page.getByText(`Request ${top.unique_key}:`)).toBeVisible();
    await expect(page.getByText("prediction: outcome not yet known")).toBeVisible();
  });
});

test.describe("anomaly center", () => {
  test("renders the series chart and labels the evaluation as synthetic", async ({ page }) => {
    await page.goto("/anomalies");
    await expect(page.locator("section").filter({ has: page.getByRole("heading", { name: /Daily volume vs expected/ }) })
      .locator(".recharts-wrapper > svg")).toBeVisible();
    await expect(page.getByText("Synthetic injected anomalies").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Detected anomalies (unverified)" })).toBeVisible();
  });
});

test.describe("model evaluation", () => {
  test("shows the current run and the selected models from the registry", async ({ page }) => {
    const reg = await (await page.request.get("/api/v1/models")).json();
    await page.goto("/models");
    await expect(page.getByText(reg.run_id, { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Demand forecasting: rolling-origin backtest" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Forecast accuracy by model" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Delay-risk metrics" })).toBeVisible();
  });
});
