import { defineConfig } from "@playwright/test";

const port = Number(process.env.CANVAS_FIXTURE_PORT ?? 4187);

export default defineConfig({
  testDir: "./canvas-tests",
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 430, height: 860 },
    trace: "on-first-retry",
  },
  webServer: {
    command: "node ../canvas/serve-canvas-fixture.mjs",
    url: `http://127.0.0.1:${port}/?token=canvas-test`,
    timeout: 15_000,
    reuseExistingServer: !process.env.CI,
  },
});
