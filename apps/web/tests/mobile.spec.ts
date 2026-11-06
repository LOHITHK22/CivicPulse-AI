import { expect, test } from "@playwright/test";

test("mobile: menu opens navigation and pages have no horizontal overflow", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Menu" }).click();
  const nav = page.getByRole("navigation", { name: "Primary" });
  await nav.getByRole("link", { name: /^Service Request Explorer/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Service Request Explorer" })).toBeVisible();

  for (const path of ["/", "/explorer", "/quality", "/about", "/forecast", "/delay-risk", "/anomalies", "/models", "/analyst", "/geo"]) {
    await page.goto(path);
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(1);
  }
});

test("mobile: KPI cards render", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Total requests", { exact: true })).toBeVisible();
});
