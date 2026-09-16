import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('AgentOps keeps the original pipeline and concise checks in one readable area', async ({ page }) => {
  await page.goto('/production.html#operating-controls');
  const area = page.locator('#readiness-topic');
  await expect(area.locator('details, .posture-trio-svg, .scorecard-preview, [data-agentops-view]')).toHaveCount(0);
  await expect(area.locator('.pipe-svg')).toBeVisible();
  await expect(area.locator('.release-checks > li')).toHaveCount(3);
  for (const anchor of ['quality-controls', 'runtime-controls']) {
    await expect(area.locator(`#${anchor}`)).toBeVisible();
  }
  const violations = (await new AxeBuilder({ page }).include('#readiness-topic')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
  expect(violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
});

test('the deployment visual separates deployed resources from subsequent verdict checks', async ({ page }) => {
  await page.goto('/production.html#ship');
  const deployment = page.locator('#readiness-topic');
  for (const boundary of ['after deployment', 'Soft', 'partial', 'echo', 'does not roll back']) {
    await expect(deployment).toContainText(boundary);
  }
  await expect(deployment.locator('.pipe-svg')).toContainText('CI RESULT');
  await expect(deployment).not.toContainText(/all gates green.*ships|hard-block the merge|nothing calls home/i);
});

test('AgentOps history and retained deep links keep the relevant subsection selected', async ({ page }) => {
  for (const anchor of ['operating-controls', 'legs', 'ship', 'runtime-controls']) {
    await page.goto(`/production.html#${anchor}`);
    await expect(page.getByRole('tabpanel')).toHaveAttribute('id', 'readiness-topic');
    await expect(page.locator(`#${anchor}`)).toBeVisible();
    await expect(page.locator('[data-area-navigation="readiness-topic"] a[aria-current="location"]')).toHaveCount(1);
  }
  await page.goto('/production.html#quality-controls');
  await page.locator('[data-area-navigation="readiness-topic"] a[href="#operating-controls"]').click();
  await page.goBack();
  await expect(page).toHaveURL(/#quality-controls$/);
  await page.goForward();
  await expect(page).toHaveURL(/#operating-controls$/);
  await expect(page.locator('.pipe-svg')).toBeVisible();
});

test('AgentOps diagrams and checks remain readable without JavaScript', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    javaScriptEnabled: false, viewport: testInfo.project.use.viewport,
    baseURL: testInfo.project.use.baseURL,
  });
  try {
    const page = await context.newPage();
    await page.goto('/production.html');
    await expect(page.locator('.pipe-svg')).toBeVisible();
    await expect(page.locator('.release-checks > li')).toHaveCount(3);
    await expect(page.locator('main details')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});
