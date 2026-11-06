import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";

interface GeoRow { borough: string; requests: number; pct_change: number | null; rank: number }
interface Geo { rows: GeoRow[]; total_requests: number; start: string; end: string }

const fmt = (n: number) => new Intl.NumberFormat("en-US").format(n);

async function geoApi(page: Page, query: string): Promise<Geo> {
  const r = await page.request.get(`/api/v1/geo/boroughs?${query}`);
  expect(r.ok()).toBeTruthy();
  return (await r.json()) as Geo;
}

async function tableRequests(page: Page): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const row of await page.getByTestId("geo-table").locator("tbody tr").all()) {
    out[(await row.getAttribute("data-borough")) ?? ""] = (await row.locator('[data-col="requests"]').innerText()).trim();
  }
  return out;
}

test.describe("geographic intelligence", () => {
  test("map, table and API agree for the default window", async ({ page }) => {
    await page.goto("/geo");
    const start = await page.getByLabel("From", { exact: true }).inputValue();
    const end = await page.getByLabel("To", { exact: true }).inputValue();
    const api = await geoApi(page, `start=${start}&end=${end}`);
    await expect(page.getByTestId("geo-table")).toBeVisible();
    const table = await tableRequests(page);
    for (const r of api.rows) expect(table[r.borough]).toBe(fmt(r.requests));
    const map = page.getByTestId("borough-map");
    await expect(map).toHaveAttribute("data-map-state", /ready|unavailable/, { timeout: 20_000 });
    const mapValues = JSON.parse((await map.getAttribute("data-map-values")) ?? "[]") as GeoRow[];
    expect(mapValues.map((v) => [v.borough, v.requests])).toEqual(api.rows.map((r) => [r.borough, r.requests]));
    expect(await map.getAttribute("aria-label")).toContain(`had the most requests (${fmt(api.rows[0].requests)})`);
    await expect(page.getByText("Historical snapshot, not live")).toBeVisible();
    await expect(page.getByText(/raw counts, not population-adjusted rates/)).toBeVisible();
  });

  test("the WebGL map renders a canvas without console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/geo");
    const map = page.getByTestId("borough-map");
    await expect(map).toHaveAttribute("data-map-state", /ready|unavailable/, { timeout: 20_000 });
    test.skip((await map.getAttribute("data-map-state")) !== "ready", "WebGL unavailable in this browser");
    await expect(map.locator("canvas")).toBeVisible();
    expect(errors.filter((e) => /worker|maplibre|webgl/i.test(e))).toEqual([]);
  });

  test("category filter changes the mapped values consistently", async ({ page }) => {
    await page.goto("/geo?start=2026-10-01&end=2026-10-31");
    await page.getByRole("button", { name: "Heat / hot water" }).click();
    await expect(page).toHaveURL(/category=heat_hot_water/);
    const api = await geoApi(page, "start=2026-10-01&end=2026-10-31&category=heat_hot_water");
    await expect(page.getByTestId("geo-table").locator(`tr[data-borough="${api.rows[0].borough}"] [data-col="requests"]`))
      .toHaveText(fmt(api.rows[0].requests));
    const table = await tableRequests(page);
    for (const r of api.rows) expect(table[r.borough]).toBe(fmt(r.requests));
  });

  test("selecting a borough drills down to its details and trend, and comparison works", async ({ page }) => {
    await page.goto("/geo?start=2026-10-01&end=2026-10-31&category=noise_residential");
    const api = await geoApi(page, "start=2026-10-01&end=2026-10-31&category=noise_residential");
    const brooklyn = api.rows.find((r) => r.borough === "BROOKLYN")!;
    await page.getByTestId("geo-table").getByRole("button", { name: "Brooklyn" }).click();
    await expect(page).toHaveURL(/focus=BROOKLYN/);
    const detail = page.getByTestId("borough-detail");
    await expect(detail).toContainText(fmt(brooklyn.requests));
    await expect(detail).toContainText(`rank ${brooklyn.rank} of 5`);
    await expect(page.getByRole("figure", { name: /Requests per day, Brooklyn/ }).or(page.locator(".recharts-wrapper").first())).toBeVisible();
    await page.getByLabel("Compare with").selectOption("QUEENS");
    await expect(page).toHaveURL(/compare=QUEENS/);
    const queens = api.rows.find((r) => r.borough === "QUEENS")!;
    await expect(page.getByTestId("comparison-text")).toContainText(fmt(Math.abs(brooklyn.requests - queens.requests)));
    await expect(page.getByRole("link", { name: "Open these requests in the Explorer" }))
      .toHaveAttribute("href", /borough=BROOKLYN/);
  });

  test("table is sortable and keyboard operable", async ({ page }) => {
    await page.goto("/geo?start=2026-10-01&end=2026-10-31");
    const header = page.getByRole("columnheader", { name: /Borough/ });
    await header.getByRole("button").click();
    await expect(header).toHaveAttribute("aria-sort", "ascending");
    const first = page.getByTestId("geo-table").locator("tbody tr").first();
    await expect(first).toHaveAttribute("data-borough", "BRONX");
    const pick = page.getByRole("group", { name: "Select a borough" }).getByRole("button", { name: "Queens" });
    await pick.focus();
    await page.keyboard.press("Enter");
    await expect(pick).toHaveAttribute("aria-pressed", "true");
    await expect(page).toHaveURL(/focus=QUEENS/);
  });

  test("change mode shows a fixed diverging legend and prior-period values", async ({ page }) => {
    await page.goto("/geo?start=2026-10-01&end=2026-10-31&mode=change");
    await expect(page.getByLabel("Legend: change versus prior period")).toContainText("≥ +50%");
    const api = await geoApi(page, "start=2026-10-01&end=2026-10-31");
    const r = api.rows[0];
    const pct = `${r.pct_change! > 0 ? "+" : ""}${(r.pct_change! * 100).toFixed(1)}%`;
    await expect(page.getByTestId("geo-table").locator(`tr[data-borough="${r.borough}"]`)).toContainText(pct);
  });

  test("API errors show a retryable error state", async ({ page }) => {
    await page.route("**/api/v1/geo/boroughs**", (route) => route.fulfill({ status: 500, body: "{}" }));
    await page.goto("/geo");
    await expect(page.locator("main").getByRole("alert")).toContainText("Couldn’t load this data");
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  test("selected-borough view has no serious axe violations", async ({ page }) => {
    await page.goto("/geo?start=2026-10-01&end=2026-10-31&focus=BRONX&compare=MANHATTAN");
    await expect(page.getByTestId("borough-detail")).toBeVisible();
    await expect(page.getByTestId("borough-map")).toHaveAttribute("data-map-state", /ready|unavailable/, { timeout: 20_000 });
    await expect(page.locator("#detail-title").locator("xpath=../../..").locator(".recharts-wrapper")).toBeVisible();
    const results = await new AxeBuilder({ page }).analyze();
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious.map((v) => `${v.id}: ${v.nodes.length} nodes - ${v.help}`)).toEqual([]);
  });
});
