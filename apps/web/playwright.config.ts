import { defineConfig, devices } from "@playwright/test";

// E2E tests run against the REAL stack: FastAPI over a DuckDB/Parquet warehouse + the Next.js
// production server. Locally they reuse already-running servers (full real snapshot). In CI the
// warehouse is built from the committed real-data sample (see .github/workflows/ci.yml).
// Tests compare what the UI renders with what the API returns, so they are data-agnostic.
const WEB_PORT = Number(process.env.WEB_PORT ?? 3000);
const API_PORT = Number(process.env.API_PORT ?? 8000);

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } }, testIgnore: /mobile.spec.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
  webServer: [
    {
      command: process.env.API_COMMAND ?? `python -m uvicorn civicpulse_api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: !process.env.CI,
      cwd: "../..",
      // The analyst's per-client limit (6/min by default) would throttle parallel e2e workers on 127.0.0.1.
      env: { CIVICPULSE_AI_RATE_LIMIT_PER_MINUTE: "120", CIVICPULSE_LLM_PROVIDER: "fake" },
      timeout: 60_000,
    },
    {
      command: `npx next start -p ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/about`,
      reuseExistingServer: !process.env.CI,
      env: { API_BASE_URL: `http://127.0.0.1:${API_PORT}` },
      timeout: 120_000,
    },
  ],
});
