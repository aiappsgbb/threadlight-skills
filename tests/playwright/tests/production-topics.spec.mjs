import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const topics = ['platform-controls', 'model-controls', 'effect-authority',
  'quality-controls', 'information-controls', 'operating-controls'];

test('production presents a visual map and only one structured topic at a time', async ({ page }) => {
  await page.goto('/production.html');
  await expect(page.locator('.production-map')).toBeVisible();
  await expect(page.getByRole('tablist', { name: 'Production topics' })).toBeVisible();
  await expect(page.getByRole('tab')).toHaveCount(6);
  await expect(page.getByRole('tabpanel')).toHaveCount(1);
  await expect(page.locator('.topic-reference[open]')).toHaveCount(0);
  for (const id of topics) {
    await page.locator(`[data-topic-tab][href="#${id}"]`).click();
    await expect(page.getByRole('tabpanel')).toHaveCount(1);
    const panel = page.locator(`#${id}`);
    await expect(panel).toBeVisible();
    for (const part of ['problem', 'existing', 'proposal', 'mechanism', 'deeper']) {
      await expect(panel.locator(`[data-topic-part="${part}"]`)).toBeVisible();
    }
    expect((await panel.innerText()).split(/\s+/).length).toBeLessThan(650);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  }
});

test('tabs support keyboard selection and browser history without losing context', async ({ page }) => {
  await page.goto('/production.html#platform-controls');
  const first = page.locator('[data-topic-tab]').first();
  await first.focus();
  const orientation = await page.getByRole('tablist').getAttribute('aria-orientation');
  await first.press(orientation === 'vertical' ? 'ArrowDown' : 'ArrowRight');
  await expect(page.locator('[data-topic-tab]').nth(1)).toBeFocused();
  await expect(page.locator('#model-controls')).toBeVisible();
  await expect(page).toHaveURL(/#model-controls$/);
  await page.goBack();
  await expect(page.locator('#platform-controls')).toBeVisible();
  await page.goForward();
  await expect(page.locator('#model-controls')).toBeVisible();
  await page.locator('[data-topic-tab]').nth(1).press('End');
  await expect(page.locator('#operating-controls')).toBeVisible();
});

test('legacy deep links select the owning topic and open the necessary reference', async ({ page }) => {
  for (const [anchor, owner] of [['effect-authority', 'effect-authority'],
    ['target', 'platform-controls'], ['checks', 'operating-controls'],
    ['start', 'operating-controls'], ['evidence-boundaries', 'effect-authority']]) {
    await page.goto(`/production.html#${anchor}`);
    await expect(page.locator(`#${owner}`)).toBeVisible();
    await expect(page.getByRole('tabpanel')).toHaveCount(1);
    const target = page.locator(`#${anchor}`);
    await expect(target).toBeVisible();
    await expect.poll(() => target.evaluate(el => {
      const rect = el.getBoundingClientRect();
      return rect.top >= 0 && rect.top < innerHeight;
    })).toBe(true);
  }
});

test('selected topics retain accessible names, focus and contrast', async ({ page }) => {
  await page.goto('/production.html');
  for (const id of topics) {
    await page.locator(`[data-topic-tab][href="#${id}"]`).click();
    const violations = (await new AxeBuilder({ page }).include('#topic-explorer')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations, id).toEqual([]);
  }
});

test('the active mobile topic stays fully visible after deep links and resizing', async ({ page }) => {
  await page.goto('/production.html#operating-controls');
  for (const width of [768, 412, 600]) {
    await page.setViewportSize({ width, height: 915 });
    await expect.poll(() => page.locator('[data-topic-tab][aria-selected="true"]').evaluate(tab => {
      const rect = tab.getBoundingClientRect();
      const rail = tab.parentElement.getBoundingClientRect();
      return rect.left >= rail.left && rect.right <= rail.right;
    })).toBe(true);
  }
});

test('initial mobile fragments align after the shared header is enhanced', async ({ page }) => {
  await page.setViewportSize({ width: 412, height: 915 });
  await page.goto('/production.html#effect-authority');
  await expect.poll(() => page.locator('#effect-authority').evaluate(panel => {
    const offset = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop);
    return Math.abs(panel.getBoundingClientRect().top - offset);
  })).toBeLessThan(2);
});

test('without JavaScript all topic links and disclosure references remain usable', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    javaScriptEnabled: false, viewport: testInfo.project.use.viewport,
    baseURL: testInfo.project.use.baseURL,
  });
  try {
    const page = await context.newPage();
    await page.goto('/production.html');
    for (const id of topics) await expect(page.locator(`#${id}`)).toBeVisible();
    await expect(page.getByRole('tab')).toHaveCount(0);
    await page.locator('#readiness-reference > summary').click();
    await expect(page.locator('#checks')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});
