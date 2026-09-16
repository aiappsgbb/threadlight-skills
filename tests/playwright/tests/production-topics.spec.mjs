import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const topics = ['platform-controls', 'model-controls', 'effect-authority',
  'quality-controls', 'information-controls', 'operating-controls'];
const areas = ['platform-controls', 'operating-controls', 'effect-authority'];
const subsections = [...topics.filter(id => id !== 'information-controls'), 'delivery-controls'];

test('three areas own the correct subsections and expose one platform architecture', async ({ page }) => {
  await page.goto('/production.html');
  await expect(page.getByRole('tab')).toHaveCount(3);
  for (const [id, owner] of [['model-controls', 'platform-topic'],
    ['quality-controls', 'readiness-topic'], ['delivery-controls', 'readiness-topic'],
    ['effect-authority', 'actions-topic']]) {
    await page.goto(`/production.html#${id}`);
    await expect(page.getByRole('tabpanel')).toHaveAttribute('id', owner);
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
  await page.goto('/production.html#platform-controls');
  await expect(page.locator('#target')).toBeVisible();
  await expect(page.locator('#platform-reference')).toHaveCount(0);
  await expect(page.locator('[data-visual="citadel-hub"]')).toHaveCount(1);
  await expect(page.locator('#model-controls')).toContainText('Citadel');
  await expect(page.locator('#information-controls')).toContainText('not supplied by the new action-governance runtime');
  expect(await page.locator('#information-controls').evaluate(el => el.closest('[data-topic-panel]'))).toBeNull();
  await expect(page.locator('[data-agentops-view]')).toHaveCount(0);
});

test('subsection navigation preserves the selected area and keeps headings unobscured', async ({ page }) => {
  for (const id of areas) {
    await page.goto(`/production.html#${id}`);
    const links = page.locator('[data-area-navigation]:not([hidden]) a');
    expect(await links.count()).toBeGreaterThan(0);
    for (const link of await links.all()) {
      await link.click();
      const target = page.locator(await link.getAttribute('href'));
      await expect(target).toBeVisible();
      await expect.poll(() => target.evaluate(el => {
        const rect = el.getBoundingClientRect();
        const offset = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop);
        const atEnd = scrollY + innerHeight >= document.documentElement.scrollHeight - 2;
        return rect.top >= offset - 2 && (Math.abs(rect.top - offset) < 2 || atEnd);
      })).toBe(true);
    }
  }
});

test('the general overview opens independent topics and keeps the return inside agent governance', async ({ page }) => {
  await page.goto('/production.html');
  const links = page.locator('.production-map a');
  await expect(links).toHaveCount(3);
  await expect(page.locator('[data-journey-step], [data-journey-next]')).toHaveCount(0);
  for (const link of await links.all()) {
    await link.click();
    const panel = page.locator(await link.getAttribute('href'));
    await expect(panel).toBeVisible();
    await expect(page.getByRole('tabpanel').locator('[data-visual]').first()).toBeVisible();
    await expect(page.getByRole('tabpanel')).toHaveCount(1);
    if (await panel.getAttribute('id') === 'effect-authority') {
      await expect(panel.locator('#returns-walkthrough')).toContainText('Can I return this order');
    } else {
      await expect(panel.locator('[data-case-context]')).toHaveCount(0);
    }
  }
});

test('concise controls remain directly readable without disclosures', async ({ page }) => {
  await page.goto('/production.html');
  for (const id of subsections) {
    await page.goto(`/production.html#${id}`);
    await expect(page.locator('main details, main summary')).toHaveCount(0);
    await expect(page.locator(`#${id}`)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
    const violations = (await new AxeBuilder({ page }).include(`#${id}`)
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations, id).toEqual([]);
  }
});

test('production presents a visual map and only one structured topic at a time', async ({ page }) => {
  await page.goto('/production.html');
  await expect(page.locator('.production-map')).toBeVisible();
  await expect(page.getByRole('tablist', { name: 'Production topics' })).toBeVisible();
  await expect(page.getByRole('tab')).toHaveCount(3);
  await expect(page.getByRole('tabpanel')).toHaveCount(1);
  await expect(page.locator('.topic-reference[open]')).toHaveCount(0);
  for (const id of subsections) {
    await page.goto(`/production.html#${id}`);
    await expect(page.getByRole('tabpanel')).toHaveCount(1);
    const panel = page.locator(`#${id}`);
    await expect(panel).toBeVisible();
    await expect(page.getByRole('tabpanel').locator('[data-visual]').first()).toBeVisible();
    expect((await panel.innerText()).split(/\s+/).length).toBeLessThan(700);
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
  await expect(page.locator('#operating-controls')).toBeVisible();
  await expect(page).toHaveURL(/#operating-controls$/);
  await page.goBack();
  await expect(page.locator('#platform-controls')).toBeVisible();
  await page.goForward();
  await expect(page.locator('#operating-controls')).toBeVisible();
  await page.locator('[data-topic-tab]').nth(1).press('End');
  await expect(page.locator('#effect-authority')).toBeVisible();
});

test('legacy deep links select the owning topic or a relevant common note', async ({ page }) => {
  for (const [anchor, owner] of [['effect-authority', 'effect-authority'],
    ['target', 'platform-controls'], ['checks', null],
    ['start', null], ['returns-walkthrough', 'effect-authority'],
    ['evidence-boundaries', 'effect-authority']]) {
    await page.goto(`/production.html#${anchor}`);
    if (owner) await expect(page.locator(`#${owner}`)).toBeVisible();
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
  for (const id of areas) {
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

test('selected fragments track delayed layout without taking over manual scrolling', async ({ page }) => {
  await page.goto('/production.html#effect-authority');
  await page.addStyleTag({ content: 'html { overflow-anchor: none; }' });
  const distance = () => page.locator('#effect-authority').evaluate(panel =>
    Math.abs(panel.getBoundingClientRect().top - parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop)));
  await expect.poll(distance).toBeLessThan(2);
  await page.locator('#production-domains').evaluate(section => { section.style.paddingTop = '180px'; });
  await expect.poll(distance, { timeout: 1000 }).toBeLessThan(2);
  const aligned = await page.evaluate(() => scrollY);
  await page.mouse.wheel(0, 400);
  await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(aligned + 300);
  await page.locator('#production-domains').evaluate(section => { section.style.paddingTop = '200px'; });
  await expect.poll(distance).toBeGreaterThan(250);
});

test('without JavaScript all topic links and concise sections remain usable', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    javaScriptEnabled: false, viewport: testInfo.project.use.viewport,
    baseURL: testInfo.project.use.baseURL,
  });
  try {
    const page = await context.newPage();
    await page.goto('/production.html');
    for (const id of topics) await expect(page.locator(`#${id}`)).toBeVisible();
    await expect(page.getByRole('tab')).toHaveCount(0);
    await expect(page.locator('#checks')).toBeVisible();
    await expect(page.locator('#readiness-topic details')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});
