import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const views = [
  ['operating-controls', 'release', '.pipe-svg'],
  ['quality-controls', 'offline', '[data-visual="evaluation-matrix"]'],
  ['runtime-controls', 'verification', '[data-visual="deployment-verification"]'],
];

test('AgentOps preserves the original visuals in focused views, not nested disclosures', async ({ page }) => {
  await page.goto('/production.html#operating-controls');
  await expect(page.locator('#readiness-reference, #delivery-reference')).toHaveCount(0);
  await expect(page.locator('#readiness-topic details')).toHaveCount(0);
  await expect(page.locator('#readiness-topic .posture-trio-svg')).toHaveCount(0);
  for (const [anchor, view, visual] of views) {
    await page.locator(`[data-area-navigation="readiness-topic"] a[href="#${anchor}"]`).click();
    const selected = page.locator(`[data-agentops-view="${view}"]`);
    await expect(selected).toBeVisible();
    await expect(page.locator('[data-agentops-view]:visible')).toHaveCount(1);
    await expect(selected.locator(visual).first()).toBeVisible();
    await expect(selected.locator(visual).first().locator('xpath=ancestor::details')).toHaveCount(0);
    expect((await selected.innerText()).split(/\s+/).length).toBeLessThan(1000);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
    const violations = (await new AxeBuilder({ page }).include(`[data-agentops-view="${view}"]`)
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations, view).toEqual([]);
  }
});

test('the deployment visual separates deployed resources from subsequent verdict checks', async ({ page }) => {
  await page.goto('/production.html#ship');
  const deployment = page.locator('[data-agentops-view="release"]');
  await expect(deployment).toBeVisible();
  await expect(deployment).toContainText('after deployment');
  await expect(deployment).toContainText('soft');
  await expect(deployment).toContainText('partial');
  await expect(deployment).toContainText('echo');
  await expect(deployment).toContainText('does not roll back');
  await expect(deployment.locator('.pipe-svg')).toContainText('CI RESULT');
  await expect(deployment).not.toContainText(/all gates green.*ships|hard-block the merge|nothing calls home/i);
});

test('AgentOps history and retained deep links open the correct view', async ({ page }) => {
  for (const [anchor, view] of [['operating-controls', 'release'], ['legs', 'offline'],
    ['ship', 'release'], ['runtime-controls', 'verification']]) {
    await page.goto(`/production.html#${anchor}`);
    await expect(page.locator(`[data-agentops-view="${view}"]`)).toBeVisible();
    await expect(page.locator(`#${anchor}`)).toBeVisible();
    await expect(page.locator('[data-area-navigation="readiness-topic"] a[aria-current="location"]')).toHaveCount(1);
  }
  await page.goto('/production.html#quality-controls');
  await page.locator('[data-area-navigation="readiness-topic"] a[href="#operating-controls"]').click();
  await page.goBack();
  await expect(page.locator('[data-agentops-view="offline"]')).toBeVisible();
  await page.goForward();
  await expect(page.locator('[data-agentops-view="release"]')).toBeVisible();
});

test('all AgentOps views and visuals remain readable without JavaScript', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    javaScriptEnabled: false, viewport: testInfo.project.use.viewport,
    baseURL: testInfo.project.use.baseURL,
  });
  try {
    const page = await context.newPage();
    await page.goto('/production.html');
    await expect(page.locator('[data-agentops-view]:visible')).toHaveCount(3);
    for (const [, view, visual] of views) {
      await expect(page.locator(`[data-agentops-view="${view}"] ${visual}`).first()).toBeVisible();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});
