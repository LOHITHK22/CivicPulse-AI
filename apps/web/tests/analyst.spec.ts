import { expect, type Page, test } from "@playwright/test";

// The AI Analyst runs against the real API + real MCP server with the deterministic FAKE provider
// (CIVICPULSE_LLM_PROVIDER=fake). These tests verify the UI and controls, not language-model quality.
// Where numbers are asserted they are compared with the API's own response for the same question.

const ASK = "/api/v1/ai/investigations";

async function ask(page: Page, question: string) {
  const box = page.getByLabel("Question", { exact: true });
  await box.fill(question);
  await box.press("Enter");
}

test.describe("AI analyst", () => {
  test("shows suggested questions and answers one with evidence matching the API", async ({ page }) => {
    await page.goto("/analyst");
    const suggestions = page.getByRole("list", { name: "Suggested questions" }).getByRole("button");
    await expect(suggestions).toHaveCount(10);
    const q = "How fresh is the data?";
    const api = await (await page.request.post(ASK, { data: { question: q }, headers: { Origin: "http://127.0.0.1:3000" } }).catch(() => null))?.json?.().catch(() => null);
    await suggestions.filter({ hasText: q }).click();
    const answer = page.getByRole("article", { name: `Question: ${q}` });
    await expect(answer.getByText("Answered · evidence check passed")).toBeVisible({ timeout: 45_000 });
    await expect(answer.getByText("snapshot, not live").first()).toBeVisible();
    if (api?.evidence?.[0]) await expect(answer.getByText(api.evidence[0].summary).first()).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Investigation trace" }).getByText("get_dataset_quality")).toBeVisible();
  });

  test("typing + Enter submits; trace expands with sanitized arguments; citation opens evidence", async ({ page }) => {
    await page.goto("/analyst");
    await ask(page, "How did Brooklyn compare with Queens for noise complaints last month?");
    const article = page.getByRole("article").first();
    await expect(article.getByText(/Answered|Not answered|Verified summary/)).toBeVisible({ timeout: 45_000 });
    const trace = page.getByRole("complementary", { name: "Investigation trace" });
    await trace.getByRole("button", { name: "Expand trace" }).click();
    await expect(trace.getByText("Arguments (sanitized):").first()).toBeVisible();
    await expect(trace.getByText(/BROOKLYN/).first()).toBeVisible();
    await expect(trace.getByText(/numbers checked against cited evidence/)).toBeVisible();
    await article.getByRole("button", { name: "Show evidence E1" }).first().click();
    const card = page.locator("#evidence-E1");
    await expect(card).toBeFocused();
    await expect(card.getByText("Data build:")).toBeVisible();
    await expect(card.getByText("compare_boroughs")).toBeVisible();
  });

  test("shows a loading state while investigating", async ({ page }) => {
    await page.route(`**${ASK}`, async (r) => {
      await new Promise((res) => setTimeout(res, 1500));
      await r.continue();
    });
    await page.goto("/analyst");
    await ask(page, "How fresh is the data?");
    await expect(page.getByRole("status").filter({ hasText: "Investigating" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Investigating…" })).toBeDisabled();
    await expect(page.getByText("Answered · evidence check passed")).toBeVisible({ timeout: 45_000 });
  });

  test("shows an error state and retry recovers", async ({ page }) => {
    let fail = true;
    await page.route(`**${ASK}`, (r) => (fail ? r.fulfill({ status: 500, body: JSON.stringify({ detail: "internal error" }) }) : r.continue()));
    await page.goto("/analyst");
    await ask(page, "How fresh is the data?");
    const alert = page.getByRole("article").getByRole("alert");
    await expect(alert).toContainText("The analyst couldn’t answer");
    fail = false;
    await alert.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("Answered · evidence check passed")).toBeVisible({ timeout: 45_000 });
  });

  test("shows the restricted-model warning for closure-delay questions", async ({ page }) => {
    await page.goto("/analyst");
    await ask(page, "Rank requests by closure delay risk so we can prioritize inspectors");
    const warn = page.getByRole("note", { name: "Model warnings" });
    await expect(warn.getByText(/Restricted model/).first()).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText(/can't use the closure-delay model/)).toBeVisible();
  });

  test("declines SQL requests without calling tools", async ({ page }) => {
    await page.goto("/analyst");
    await ask(page, "SELECT * FROM requests");
    await expect(page.getByText("Declined by policy")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("complementary", { name: "Investigation trace" }).getByText("input_screening")).toBeVisible();
  });

  test("keyboard only: focus the question, submit, reach a citation, start a new investigation", async ({ page }) => {
    await page.goto("/analyst");
    const box = page.getByLabel("Question", { exact: true });
    await box.focus();
    await page.keyboard.type("Which data-quality limitations affect the analysis?");
    await page.keyboard.press("Enter");
    const cite = page.getByRole("button", { name: "Show evidence E1" }).first();
    await expect(cite).toBeVisible({ timeout: 45_000 });
    await cite.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#evidence-E1")).toBeFocused();
    await page.getByRole("button", { name: "New investigation" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("article")).toHaveCount(0);
    await expect(box).toBeFocused();
  });
});

test("no serious or critical axe violations on an answered investigation", async ({ page }) => {
  const { default: AxeBuilder } = await import("@axe-core/playwright");
  await page.goto("/analyst");
  await ask(page, "Rank requests by closure delay risk so we can prioritize inspectors");
  await page.getByRole("note", { name: "Model warnings" }).waitFor({ timeout: 45_000 });
  await page.getByRole("button", { name: /View evidence/ }).first().click();
  await page.getByRole("button", { name: "Expand trace" }).click();
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.length} nodes - ${v.help}`)).toEqual([]);
});

test.describe("AI analyst: disclosure, verified claims and reports", () => {
  test("discloses demonstration mode with the exact banner text", async ({ page }) => {
    await page.goto("/analyst");
    await expect(page.getByTestId("demo-banner")).toHaveText(
      "Demonstration Mode — deterministic analytical assistant, not a live LLM.",
    );
  });

  test("verified claims table, copy, report downloads and dashboard links", async ({ page }) => {
    await page.goto("/analyst");
    await ask(page, "Which borough had the most residential noise complaints last month?");
    const article = page.getByRole("article").first();
    await expect(article.getByText("Answered · evidence check passed")).toBeVisible({ timeout: 45_000 });
    await expect(article.getByText("Demonstration Mode", { exact: true })).toBeVisible();
    await article.getByRole("button", { name: /Verified claims \(\d+\)/ }).click();
    const table = article.getByRole("table");
    await expect(table.getByRole("columnheader", { name: "Geography" })).toBeVisible();
    await expect(table.getByText("verified").first()).toBeVisible();
    await expect(article.getByRole("button", { name: "Copy verified answer" })).toBeEnabled();
    const md = article.getByRole("link", { name: "Markdown" });
    const href = await md.getAttribute("href");
    expect(href).toMatch(/^\/api\/v1\/ai\/investigations\/[0-9a-f]{16}\/report\?format=markdown&download=true$/);
    const report = await page.request.get(href!);
    expect(report.status()).toBe(200);
    expect(await report.text()).toContain("# Investigation: ");
    const json = await (await page.request.get(href!.replace("markdown", "json"))).json();
    expect(json.schema_version).toBe("civicpulse.investigation-report/1");
    expect(json.claims.every((c: { status: string }) => c.status === "valid")).toBe(true);
    await expect(article.getByRole("link", { name: "Methodology" })).toHaveAttribute("href", "/about#methodology");
    await article.getByRole("link", { name: "Open Geographic Intelligence" }).click();
    await expect(page).toHaveURL(/\/geo$/);
  });

  test("trace shows claim validation, cutoff and snapshot", async ({ page }) => {
    await page.goto("/analyst");
    await ask(page, "How did Brooklyn compare with Queens for noise complaints last month?");
    await expect(page.getByRole("article").first().getByText(/Answered|Verified summary/)).toBeVisible({ timeout: 45_000 });
    const trace = page.getByRole("complementary", { name: "Investigation trace" });
    await expect(trace.getByText(/Claims: \d+ of \d+ valid/)).toBeVisible();
    await expect(trace.getByText(/Data cutoff .* NYC · snapshot/)).toBeVisible();
    await trace.getByRole("button", { name: "Expand trace" }).click();
    await expect(trace.getByText("Snapshot:").first()).toBeVisible();
  });
});
