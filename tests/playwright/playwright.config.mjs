// Playwright config — serves docs/ on :4173 via python http.server.
// Run from this folder (tests/playwright) with `npx playwright test`.
import { defineConfig, devices } from '@playwright/test';

const PORT = process.env.PORT || 4173;

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    actionTimeout: 5_000,
    navigationTimeout: 8_000
  },
  webServer: {
    // python3 -m http.server, rooted at ../../docs, no caching for fresh runs
    command: `python3 -m http.server ${PORT} --bind 127.0.0.1 --directory ../../docs`,
    url: `http://127.0.0.1:${PORT}/index.html`,
    timeout: 15_000,
    reuseExistingServer: !process.env.CI
  },
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'chromium-mobile',  use: { ...devices['Pixel 7'] } },
    {
      name: 'chromium-dark',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        colorScheme: 'dark'
      }
    },
    {
      name: 'chromium-light',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        colorScheme: 'light'
      }
    }
  ]
});
