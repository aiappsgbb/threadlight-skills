// Continuous Production chapter (October 2026 revision): one reading path, a compact
// shared chapter index, retained legacy fragments and depth only in disclosures.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const parts = ['production-domains', 'platform-controls', 'operating-controls',
  'effect-authority', 'evidence-review', 'production-review'];
const legacy = ['why', 'target', 'model-controls', 'platform-postures', 'delivery-controls', 'ship', 'legs',
  'quality-controls', 'runtime-controls', 'returns-walkthrough', 'action-architecture', 'workflow-in-action',
  'evidence-boundaries', 'information-controls', 'checks', 'chapter-recap', 'proof', 'start'];

test('six parts read in one order and the index lists exactly those parts', async ({ page }) => {
  await page.goto('/production.html');
  expect(await page.locator('[data-toc-id]').evaluateAll(nodes => nodes.map(node => node.id))).toEqual(parts);
  expect(await page.locator('.cx-chapter-index a').evaluateAll(links => links.map(a => a.hash.slice(1)))).toEqual(parts);
  await expect(page.locator('[data-topic-tab], [data-topic-panel], [data-area-navigation]')).toHaveCount(0);
  await expect(page.locator('[role="tab"]:not(#workflow-in-action [role="tab"])')).toHaveCount(0);
  for (const id of parts) await expect(page.locator(`#${id}`)).toBeAttached();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
});

test('index navigation lands each part below the sticky chrome and moves focus', async ({ page }) => {
  await page.goto('/production.html');
  const compact = page.viewportSize().width <= 720;
  for (const id of parts.slice(1)) {
    if (compact) {
      await page.locator('.cx-chapter-index summary').click();
      await page.locator(`.cx-chapter-index a[href="#${id}"]`).click();
    } else {
      await page.locator(`.cx-chapter-links a[href="#${id}"]`).click();
    }
    await expect(page).toHaveURL(new RegExp(`#${id}$`));
    await expect(page.locator(`#${id}`)).toBeFocused();
    const heading = page.locator(`#${id} h2`);
    await expect.poll(() => heading.evaluate(el => {
      const rect = el.getBoundingClientRect();
      const chrome = Math.max(...['.masthead', '.floating-toc'].map(selector =>
        document.querySelector(selector).getBoundingClientRect().bottom));
      const atEnd = scrollY + innerHeight >= document.documentElement.scrollHeight - 2;
      return rect.top >= chrome - 1 && (rect.top < innerHeight / 2 || atEnd);
    })).toBe(true);
  }
  await page.goBack();
  await expect(page).toHaveURL(/#evidence-review$/);
});

test('sticky chrome stays compact and the mobile index names the current part', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  const height = await page.evaluate(() => document.querySelector('.floating-toc').getBoundingClientRect().bottom);
  expect(height).toBeLessThanOrEqual(page.viewportSize().width <= 720 ? 130 : 150);
  if (page.viewportSize().width <= 720) {
    await expect(page.locator('.cx-chapter-links')).toBeHidden();
    await expect(page.locator('[data-index-current]')).toHaveText('Authorize');
  }
});

test('legacy fragments still resolve into the visible chapter', async ({ page }) => {
  for (const id of legacy) {
    await page.goto(`/production.html#${id}`);
    const target = page.locator(`#${id}`);
    await expect(target).toBeVisible();
    await expect.poll(() => target.evaluate(el => {
      const rect = el.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < innerHeight;
    }), id).toBe(true);
  }
  await page.goto('/production.html#user-confirmation');
  await expect(page.locator('#wf-tab-confirmation')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#user-confirmation')).toBeVisible();
});

test('the skip link is the first stop and moves focus into the chapter', async ({ page }) => {
  await page.goto('/production.html');
  await page.keyboard.press('Tab');
  const skip = page.locator('.skip-link');
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main-content')).toBeFocused();
});

test('every part passes axe with the index, matrix and disclosures', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/production.html');
  await page.locator('details.depth').evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
  for (const id of ['chapter-top', ...parts, 'delivery-controls', 'evidence-boundaries']) {
    const violations = (await new AxeBuilder({ page }).include(`#${id}`)
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations.map(v => `${v.id}: ${v.nodes.map(n => n.target).join(' ')}`), id).toEqual([]);
  }
  const index = (await new AxeBuilder({ page }).include('.floating-toc')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
  expect(index).toEqual([]);
});

test('mobile replaces wide diagrams with readable sequences and stacked rows', async ({ page }) => {
  test.skip(page.viewportSize().width > 720, 'mobile layout only');
  await page.goto('/production.html#ship');
  await expect(page.locator('.pipe-svg')).toBeHidden();
  await expect(page.locator('.pipe-mobile > li')).toHaveCount(6);
  expect(await page.locator('#ship').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await page.goto('/production.html#checks');
  await expect(page.locator('.owner-matrix td[data-label="Limit"]').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
});

test('without JavaScript every part, the index and the pipeline remain usable', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    javaScriptEnabled: false, viewport: testInfo.project.use.viewport, baseURL: testInfo.project.use.baseURL,
  });
  try {
    const page = await context.newPage();
    await page.goto('/production.html');
    for (const id of parts) await expect(page.locator(`#${id} h2`)).toBeVisible();
    await page.locator('.cx-chapter-index summary').click();
    await expect(page.locator('.cx-chapter-index a[href="#evidence-review"]')).toBeVisible();
    await expect(page.locator('.release-checks > li')).toHaveCount(3);
    await expect(page.locator('.owner-matrix tbody tr')).toHaveCount(6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});
