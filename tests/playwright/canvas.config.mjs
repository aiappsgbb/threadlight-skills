import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./canvas-tests",
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:4187",
    viewport: { width: 430, height: 860 },
    trace: "on-first-retry",
  },
  webServer: {
    command: "node ../canvas/serve-canvas-fixture.mjs",
    url: "http://127.0.0.1:4187/?token=canvas-test",
    timeout: 15_000,
    reuseExistingServer: !process.env.CI,
  },
});
