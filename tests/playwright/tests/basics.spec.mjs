import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('skill-family diagram distinguishes the pipeline from its product', async ({ page }) => {
  await page.goto('/basics.html#two-agents');
  const diagram = page.locator('[data-pipeline-product]');
  await expect(diagram).toBeVisible();
  await expect(diagram.locator('[data-skill-family="engineering"] li')).toHaveCount(4);
  await expect(diagram.locator('[data-skill-family="business"] li')).toHaveCount(4);
  await expect(diagram.locator('[data-flow-output="pipeline"]')).toContainText('Working pilot');
  await expect(diagram.locator('[data-flow-output="business"]')).toContainText('Case outcome');
  await expect(diagram).toContainText('Output of the first flow');
  await expect(diagram).toContainText('not an automatic execution guarantee');
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await diagram.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test('Basics distinguishes construction, runtime and host capability', async ({ page }) => {
  await page.goto('/basics.html');
  await expect(page.locator('h1')).toContainText('Agents use skills');
  await expect(page.locator('[data-actor="builder"]')).toContainText('Coding agent');
  await expect(page.locator('[data-actor="process"]')).toContainText('Process agent');
  await expect(page.locator('[data-visual-anchor]')).toContainText(/engineering skills/i);
  await expect(page.locator('[data-visual-anchor]')).toContainText(/business skills/i);
  await expect(page.locator('#building-blocks')).not.toHaveAttribute('open', '');
  await expect(page.locator('[data-basic-term]')).toHaveCount(4);
  await expect(page.locator('[data-host]')).toHaveCount(3);
  await expect(page.locator('#where-skills-run')).toContainText('not a deployment');
  await expect(page.locator('#next-step a[href="./funnel.html"]')).toBeVisible();
  await page.locator('#runtime-detail > summary').click();
  await expect(page.locator('#runtime-detail')).toContainText('skill_directories');
  await expect(page.locator('#runtime-detail')).toContainText('SkillsProvider.from_paths');
});

test('Basics remains diagram-led at desktop, tablet, mobile and 200% zoom', async ({ page }, testInfo) => {
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/basics.html');
    expect((await page.locator('[data-chapter-intro]').innerText()).split(/\s+/).length).toBeLessThanOrEqual(60);
    expect((await page.locator('[data-visual-anchor]').boundingBox()).y).toBeLessThan(width === 390 ? 600 : 760);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const summary of await page.locator('main summary').all()) {
      const box = await summary.boundingBox();
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    await page.screenshot({ path: testInfo.outputPath(`basics-${width}.png`) });
  }
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('main details').evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('Basics has static explanations without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    await page.goto('/basics.html');
    await expect(page.locator('[data-visual-anchor]')).toBeVisible();
    await page.locator('#runtime-detail > summary').click();
    await expect(page.locator('#runtime-detail')).toHaveAttribute('open', '');
  } finally {
    await context.close();
  }
});

test('Basics explanations and expanded runtime references are accessible', async ({ page }) => {
  await page.goto('/basics.html');
  await page.locator('#runtime-detail > summary').click();
  const result = await new AxeBuilder({ page }).include('main').analyze();
  expect(result.violations.filter(v => ['serious', 'critical'].includes(v.impact))).toEqual([]);
});
