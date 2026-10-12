import { test, expect } from '@playwright/test';

// Baseline measured before the October 2026 Build revision: 5841px at 1440, 9187px at 390.
const BASELINE = { 1440: 5841, 390: 9187 };
const PHASES = ['scene-show', 'scene-chain', 'phase-integrate', 'scene-prod-ready', 'scene-customize', 'phase-improve'];
const ANCHORS = ['scene-hero', 'scene-funnel', ...PHASES, 'scene-industries', 'scene-kratos', 'scene-cta', 'route-try', 'route-expert'];

test('Build reads in about a third of its former length', async ({ page }) => {
  for (const [width, height] of [[1440, 900], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await page.goto('/funnel.html');
    await expect(page.locator('.build-stepper.is-stepped')).toHaveCount(1);
    await page.evaluate(() => document.fonts.ready);
    const total = await page.evaluate(() => document.documentElement.scrollHeight);
    expect(total, `height at ${width}`).toBeLessThanOrEqual(Math.round(BASELINE[width] * 0.36));
  }
});

test('two routes are offered above the fold', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/funnel.html');
  for (const route of ['try', 'expert']) {
    const card = page.locator(`[data-build-route="${route}"]`);
    await expect(card).toBeVisible();
    expect((await card.boundingBox()).y).toBeLessThan(900);
  }
  await expect(page.locator('[data-build-route="try"]')).toContainText(/try it/i);
  await expect(page.locator('[data-build-route="expert"]')).toContainText(/workshop/i);
  for (const href of ['WORKSHOP-1H-QUICKSTART.md', 'IDEA-TO-PRODUCTION-WORKBOOK.md', 'THREADLIGHT.md']) {
    await expect(page.locator(`[data-build-route="expert"] a[href*="${href}"]`)).toHaveCount(1);
  }
  await expect(page.locator('[data-build-route="try"] a[href^="./workbook.html"]').first()).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/funnel.html');
  for (const href of ['#route-try', '#route-expert']) {
    const link = page.locator(`#scene-hero a[href="${href}"]`);
    await expect(link).toBeVisible();
    const box = await link.boundingBox();
    expect(box.y + box.height).toBeLessThanOrEqual(844);
  }
});

test('the phase rail shows one phase at a time and follows links and hashes', async ({ page }) => {
  await page.goto('/funnel.html');
  const rail = page.getByRole('navigation', { name: 'Six phases from brief to handoff' });
  await expect(rail).toBeVisible();
  for (const id of PHASES) {
    await rail.locator(`a[href="#${id}"]`).click();
    await expect(page.locator(`#${id}`)).toBeVisible();
    await expect(page.locator(`#${id} [data-phase-visual]`)).toBeVisible();
    await expect(rail.locator(`a[href="#${id}"]`)).toHaveAttribute('aria-current', 'step');
    for (const other of PHASES.filter(item => item !== id)) await expect(page.locator(`#${other}`)).toBeHidden();
  }
  await page.goto('/funnel.html#scene-prod-ready');
  await expect(page.locator('#scene-prod-ready')).toBeVisible();
  await expect(page.locator('#scene-show')).toBeHidden();
  await page.locator('#scene-prod-ready [data-phase-next]').click();
  await expect(page.locator('#scene-customize')).toBeVisible();
  await page.getByRole('button', { name: /show all six phases/i }).click();
  for (const id of PHASES) await expect(page.locator(`#${id}`)).toBeVisible();
});

test('existing anchors still land on visible content', async ({ page }) => {
  for (const id of ANCHORS) {
    await page.goto(`/funnel.html#${id}`);
    await expect(page.locator(`#${id}`)).toHaveCount(1);
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
});

test('without JavaScript every phase stays readable', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto('/funnel.html');
    for (const id of PHASES) await expect(page.locator(`#${id}`)).toBeVisible();
  } finally {
    await context.close();
  }
});

test('closing blocks are separated by a divider and gutters on desktop', async ({ page }) => {
  for (const width of [1440, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/funnel.html');
    const blocks = await page.locator('.build-closing-grid > div').evaluateAll(nodes => nodes.map(node => {
      const box = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const content = [...node.children].map(child => child.getBoundingClientRect());
      return {
        left: box.left, right: box.right,
        border: parseFloat(style.borderLeftWidth),
        contentLeft: Math.min(...content.map(r => r.left)),
        contentRight: Math.max(...content.map(r => r.right)),
      };
    }));
    expect(blocks).toHaveLength(3);
    for (let i = 1; i < blocks.length; i++) {
      expect(blocks[i].border, `divider before block ${i} at ${width}`).toBeGreaterThanOrEqual(1);
      expect(blocks[i].contentLeft - blocks[i].left, `left gutter of block ${i} at ${width}`).toBeGreaterThanOrEqual(24);
      expect(blocks[i - 1].right - blocks[i - 1].contentRight, `right gutter of block ${i - 1} at ${width}`).toBeGreaterThanOrEqual(24);
    }
  }
});
