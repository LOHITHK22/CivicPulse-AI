import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const fmt = (n: number) => new Intl.NumberFormat("en-US").format(n);

async function kpiValue(page: Page, label: string) {
  const card = page.locator("div").filter({ has: page.getByText(label, { exact: true }) }).last();
  return card.locator("p").nth(1);
}

async function filterOptions(page: Page) {
  return (await page.request.get("/api/v1/meta/filters")).json();
}

test.describe("navigation", () => {
  test("every primary nav item is reachable and labelled", async ({ page }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Primary" });
    const expected: [string, RegExp][] = [
      ["Overview", /Operations overview/],
      ["Service Request Explorer", /Service Request Explorer/],
      ["Geographic Intelligence", /Geographic Intelligence/],
      ["Forecast Lab", /Forecast Lab/],
      ["Delay Risk", /Delay Risk/],
      ["Anomaly Center", /Anomaly Center/],
      ["AI Analyst", /AI Analyst/],
      ["Data Quality", /Data quality/],
      ["Model Evaluation", /Model Evaluation/],
      ["About the Project", /About CivicPulse AI/],
    ];
    for (const [link, heading] of expected) {
      await nav.getByRole("link", { name: new RegExp(`^${link}`) }).click();
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      await expect(nav.getByRole("link", { name: new RegExp(`^${link}`) })).toHaveAttribute("aria-current", "page");
    }
  });

});

test.describe("overview dashboard", () => {
  test("KPIs match the analytics API for the default window", async ({ page }) => {
    const opts = await filterOptions(page);
    await page.goto("/");
    const start = await page.getByLabel("From", { exact: true }).inputValue();
    const end = await page.getByLabel("To", { exact: true }).inputValue();
    expect(end).toBe(opts.last_complete_date);
    const api = await (await page.request.get(`/api/v1/metrics/kpis?start=${start}&end=${end}`)).json();
    await expect(await kpiValue(page, "Total requests")).toHaveText(fmt(api.total_requests));
    await expect(await kpiValue(page, "Open requests")).toHaveText(fmt(api.open_requests));
    await expect(await kpiValue(page, "Categories with unusual increases")).toHaveText("Not computed");
  });

  test("a borough filter updates the URL and every KPI consistently", async ({ page }) => {
    await page.goto("/");
    await expect(await kpiValue(page, "Total requests")).not.toHaveText("");
    await page.getByRole("button", { name: "Brooklyn", exact: true }).click();
    await expect(page).toHaveURL(/borough=BROOKLYN/);
    await expect(page.getByRole("button", { name: "Brooklyn", exact: true })).toHaveAttribute("aria-pressed", "true");
    const start = await page.getByLabel("From", { exact: true }).inputValue();
    const end = await page.getByLabel("To", { exact: true }).inputValue();
    const api = await (await page.request.get(`/api/v1/metrics/kpis?start=${start}&end=${end}&borough=BROOKLYN`)).json();
    await expect(await kpiValue(page, "Total requests")).toHaveText(fmt(api.total_requests));
    await expect(await kpiValue(page, "Closed requests")).toHaveText(fmt(api.closed_requests));
  });

  test("date range filter is applied", async ({ page }) => {
    const opts = await filterOptions(page);
    await page.goto(`/?start=${opts.max_date}&end=${opts.max_date}`);
    const api = await (await page.request.get(`/api/v1/metrics/kpis?start=${opts.max_date}&end=${opts.max_date}`)).json();
    await expect(await kpiValue(page, "Total requests")).toHaveText(fmt(api.total_requests));
  });

  test("charts render and expose an accessible table view", async ({ page }) => {
    await page.goto("/");
    const daily = page.locator("section", { has: page.getByRole("heading", { name: "Daily request volume" }) });
    await expect(daily.locator(".recharts-wrapper > svg.recharts-surface")).toBeVisible();
    await expect(daily.locator(".recharts-line")).toHaveCount(2);
    await daily.getByRole("button", { name: "Table" }).click();
    await expect(daily.getByRole("table")).toBeVisible();
    expect(await daily.getByRole("row").count()).toBeGreaterThan(5);
    const heat = page.locator("section", { has: page.getByRole("heading", { name: "Activity by time of day" }) });
    await expect(heat.getByText("Hover a cell for its count")).toBeVisible();
  });

  test("shows a useful error when the API fails", async ({ page }) => {
    await page.route("**/api/v1/metrics/kpis**", (r) =>
      r.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "x", code: "data_unavailable" }) }));
    await page.goto("/");
    await expect(page.getByRole("alert").first()).toContainText("analytics warehouse is not available");
  });

  test("shows a loading state while data is in flight", async ({ page }) => {
    await page.route("**/api/v1/metrics/kpis**", async (r) => {
      await new Promise((res) => setTimeout(res, 1500));
      await r.continue();
    });
    await page.goto("/");
    await expect(page.getByRole("status").first()).toBeAttached();
  });

  test("shows an empty state when no requests match", async ({ page }) => {
    await page.route("**/api/v1/metrics/kpis**", async (r) => {
      const real = await (await r.fetch()).json();
      await r.fulfill({ json: { ...real, total_requests: 0 } });
    });
    await page.goto("/");
    await expect(page.getByText("No requests match the current filters.")).toBeVisible();
  });
});

test.describe("service request explorer", () => {
  test("search, sort, paginate and drill down", async ({ page }) => {
    await page.goto("/explorer");
    const table = page.getByRole("table", { name: /Service requests/ });
    await expect(table).toBeVisible();
    const firstKey = await table.getByRole("button", { name: /View details/ }).first().innerText();

    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByText(/^Page 2 of/)).toBeVisible();
    await expect(table.getByRole("button", { name: /View details/ }).first()).not.toHaveText(firstKey);

    await page.getByRole("button", { name: /Time to close/ }).click();
    await expect(page.getByText(/^Page 1 of/)).toBeVisible();
    await expect(page.getByRole("columnheader", { name: /Time to close/ })).toHaveAttribute("aria-sort", "descending");

    await page.getByLabel("Search requests").fill(firstKey);
    await expect(page.getByText("1 matching requests")).toBeVisible();
    await table.getByRole("button", { name: `View details for request ${firstKey}` }).click();
    const detail = page.locator("section", { has: page.getByRole("heading", { name: `Request ${firstKey}` }) });
    await expect(detail.getByText("Lineage")).toBeVisible();
    await expect(detail.getByText("erm2-nwe9")).toBeVisible();
    await expect(detail.getByText(/Street addresses and exact coordinates are intentionally not shown/)).toBeVisible();
  });

  test("export link carries the active filters", async ({ page }) => {
    await page.goto("/explorer?borough=QUEENS");
    const href = await page.getByRole("link", { name: /Export CSV/ }).getAttribute("href");
    expect(href).toContain("borough=QUEENS");
    const res = await page.request.get(href!);
    expect(res.headers()["content-type"]).toContain("text/csv");
    const body = await res.text();
    expect(body.split("\n")[0]).toContain("unique_key");
    expect(body).not.toContain("latitude");
  });
});

test.describe("data quality", () => {
  test("rules come from the latest build and show icon+label statuses", async ({ page }) => {
    const report = await (await page.request.get("/api/v1/quality")).json();
    await page.goto("/quality");
    const rules = page.getByRole("table", { name: /Data quality rule results/ });
    await expect(rules).toBeVisible();
    await expect(rules.getByText("DQ001")).toBeVisible();
    await expect(rules.getByRole("row")).toHaveCount(report.rules.length + 1 + new Set(report.rules.map((r: { category: string }) => r.category)).size);
    await expect(page.getByText(`Build ${report.build.build_id}`)).toBeVisible();
  });
});

test.describe("accessibility", () => {
  for (const path of ["/", "/explorer", "/quality", "/about", "/forecast", "/delay-risk", "/anomalies", "/models", "/analyst", "/geo"]) {
    test(`no serious or critical axe violations on ${path}`, async ({ page }) => {
      await page.goto(path);
      await page.getByRole("heading", { level: 1 }).waitFor();
      await page.waitForLoadState("networkidle");
      const results = await new AxeBuilder({ page }).analyze();
      const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${v.id}: ${v.nodes.length} nodes - ${v.help}`)).toEqual([]);
    });
  }
});
