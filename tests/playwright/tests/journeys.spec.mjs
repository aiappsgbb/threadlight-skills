import { test, expect } from '@playwright/test';

test('Build follows six navigable phases and names execution boundaries', async ({ page }) => {
  await page.goto('/funnel.html');
  await expect(page).toHaveTitle(/^Build —/);
  const rail = page.getByRole('navigation', { name: 'Six phases from brief to handoff' });
  await expect(rail.locator('[data-phase-name]')).toHaveText(['Enter', 'Build', 'Integrate', 'Assure', 'Ship', 'Improve']);
  for (const link of await rail.locator('a').all()) {
    const href = await link.getAttribute('href');
    await expect(page.locator(href)).toHaveCount(1);
  }
  await expect(page.locator('[data-visual-anchor]')).toContainText('Illustrative starter');
  await expect(page.locator('[data-execution-boundary]')).toContainText('Auto plans');
  await expect(page.locator('[data-execution-boundary]')).toContainText('coding agent executes');
  await expect(page.locator('a[href="./downloads/threadlight-qualify.zip"]')).toBeVisible();
  await expect(page.locator('main a[href="./governance.html"]').first()).toBeVisible();
  await expect(page.locator('main')).not.toContainText(/Every Threadlight engagement|23-skill|never fails|One prompt\. One session/);
});

test('Workbook offers a short working-session map before technical prompts', async ({ page }) => {
  await page.goto('/workbook.html');
  await expect(page).toHaveTitle('Hands-on workbook — Threadlight');
  const map = page.getByRole('navigation', { name: 'Your working session' });
  await expect(map.locator('[data-session-step]')).toHaveText(['Prepare', 'Build', 'Check', 'Next boundary']);
  const entry = page.locator('[data-first-action]');
  await expect(entry).toBeVisible();
  const box = await entry.boundingBox();
  expect(box.y).toBeLessThan(844);
  await entry.click();
  await expect.poll(async () => (await page.locator('#prereqs').boundingBox()).y).toBeLessThan(400);
  const prompt = page.locator('details[data-technical-prompt="design"]');
  await expect(prompt).not.toHaveAttribute('open', '');
  await prompt.locator('summary').click();
  await expect(prompt.locator('.wb-term')).toContainText('BRIEF');
  await expect(page.locator('main')).toContainText('not production approval');
  await expect(page.locator('main')).not.toContainText(/prompt runs all three stages|Every other skill.*runs underneath/);
});

test('Workbook copies the bounded starter without deploying anything', async ({ page }) => {
  await page.goto('/workbook.html');
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async text => { window.copiedPrompt = text; } }
    });
  });
  await page.getByRole('button', { name: 'Copy starter', exact: true }).click();
  await expect(page.locator('#stage-design [role="status"]')).toContainText('Copied.');
  expect(await page.evaluate(() => window.copiedPrompt)).toContain('Stop before cloud deployment');
});

test('Workbook offers a manual-copy fallback when clipboard access is denied', async ({ page }) => {
  await page.goto('/workbook.html');
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async () => { throw new Error('Clipboard denied'); } }
    });
  });
  await page.getByRole('button', { name: 'Copy pre-flight', exact: true }).click();
  await expect(page.locator('#prereqs [role="status"]')).toContainText('copy it manually');
  await expect(page.locator('#preflight-prompt')).toBeVisible();
});

for (const path of ['funnel', 'workbook']) {
  test(`${path} keeps mobile entry, anchors and layout usable`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/${path}.html`);
    const anchor = await page.locator('[data-visual-anchor]').first().boundingBox();
    expect(anchor.y).toBeLessThanOrEqual(600);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    const current = page.locator('.masthead .nav a[href="./funnel.html"]');
    await expect(current).toHaveAttribute('aria-current', path === 'funnel' ? 'page' : 'location');
    const ids = await page.locator('[id]').evaluateAll(nodes => nodes.map(node => node.id));
    expect(new Set(ids).size).toBe(ids.length);
    await expect(page.locator('a[href^="#"]')).not.toHaveCount(0);
    for (const href of await page.locator('main a[href^="#"]').evaluateAll(nodes => nodes.map(n => n.getAttribute('href')))) {
      await expect(page.locator(href)).toHaveCount(1);
    }
    await page.locator('main details').evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  });
}
