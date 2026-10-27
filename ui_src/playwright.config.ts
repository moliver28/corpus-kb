import { defineConfig } from "@playwright/test";

// The SPA smoke runs against a REAL corpus-kb server on a throwaway
// database (scripts/ui_e2e.py boots it, preloads the demo corpus, and
// tears both down). baseURL points at that server; /app/ serves the
// built SPA. No webServer block: Python owns the server lifecycle.
export default defineConfig({
  testDir: "./e2e",
  // 15 minutes per test: the CI runner generates answers on CPU with the
  // 4B model, so the ask step can take several minutes by itself.
  timeout: 900_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:8010",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
