// Continuous Production chapter (October 2026 revision): one reading path, a compact
// shared chapter index, retained legacy fragments and depth only in disclosures.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const parts = ['production-domains', 'platform-controls', 'operating-controls',
  'effect-authority', 'evidence-review', 'production-review'];
const legacy = ['why', 'target', 'model-controls', 'platform-postures', 'delivery-controls', 'ship', 'legs',
  'quality-controls', 'runtime-controls', 'returns-walkthrough', 'action-architecture', 'workflow-in-action',
  'evidence-boundaries', 'information-controls', 'checks', 'chapter-recap', 'proof', 'start'];

const desktop = page => page.viewportSize().width > 900;

test('six parts read in one order and the single rail index lists exactly those parts', async ({ page }) => {
  await page.goto('/production.html');
  expect(await page.locator('[data-toc-id]').evaluateAll(nodes => nodes.map(node => node.id))).toEqual(parts);
  expect(await page.locator('.chapter-rail a').evaluateAll(links => links.map(a => a.hash.slice(1)))).toEqual(parts);
  await expect(page.locator('nav[aria-label="Production chapter"]')).toHaveCount(1);
  await expect(page.locator('.floating-toc, .cx-chapter-links, .cx-chapter-index')).toHaveCount(0);
  await expect(page.locator('[data-topic-tab], [data-topic-panel], [data-area-navigation]')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
});

test('desktop rail sits beside the reading column and stays visible while scrolling', async ({ page }) => {
  test.skip(!desktop(page), 'desktop rail');
  await page.goto('/production.html#evidence-review');
  const rail = page.locator('.chapter-rail');
  await expect(rail.locator('#chapter-rail-list')).toBeVisible();
  await expect(rail.locator('.rail-toggle')).toBeHidden();
  await expect.poll(() => page.evaluate(() => document.querySelector('.chapter-rail').getBoundingClientRect().top
    - document.querySelector('.masthead').getBoundingClientRect().bottom)).toBeLessThan(80);
  const geometry = await page.evaluate(() => {
    const r = document.querySelector('.chapter-rail').getBoundingClientRect();
    const c = document.querySelector('#evidence-review h2').getBoundingClientRect();
    return { railRight: r.right, contentLeft: c.left, railTop: r.top,
      header: document.querySelector('.masthead').getBoundingClientRect().bottom };
  });
  expect(geometry.railRight).toBeLessThanOrEqual(geometry.contentLeft);
  expect(geometry.railTop).toBeGreaterThanOrEqual(geometry.header);
  expect(geometry.railTop).toBeLessThan(geometry.header + 80);
  // Reduced top chrome: only the shared masthead is sticky on desktop.
  expect(geometry.header).toBeLessThanOrEqual(90);
});

test('scroll-spy marks the part being read, in both directions', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/production.html');
  const current = () => page.locator('.chapter-rail a[aria-current="location"]').evaluateAll(links => links.map(a => a.hash.slice(1)));
  for (const id of [...parts, ...parts.slice(0, -1).reverse()]) {
    await page.locator(`#${id} h2`).evaluate(el => {
      const offset = parseFloat(getComputedStyle(el.closest('section')).scrollMarginTop) || 0;
      scrollTo(0, el.closest('section').getBoundingClientRect().top + scrollY - offset + 40);
    });
    await expect.poll(current, { message: id }).toEqual([id]);
  }
});

test('rail navigation lands each part below the sticky chrome and moves focus', async ({ page }) => {
  await page.goto('/production.html');
  for (const id of parts.slice(1)) {
    if (!desktop(page)) await page.locator('.rail-toggle').click();
    await page.locator(`.chapter-rail a[href="#${id}"]`).click();
    await expect(page).toHaveURL(new RegExp(`#${id}$`));
    await expect(page.locator(`#${id}`)).toBeFocused();
    if (!desktop(page)) await expect(page.locator('#chapter-rail-list')).toBeHidden();
    await expect.poll(() => page.locator(`#${id} h2`).evaluate(el => {
      const rect = el.getBoundingClientRect();
      const chrome = Math.max(...['.masthead', '.chapter-rail'].map(selector => {
        const box = document.querySelector(selector).getBoundingClientRect();
        return box.width > innerWidth / 2 ? box.bottom : 0;
      }));
      const atEnd = scrollY + innerHeight >= document.documentElement.scrollHeight - 2;
      return rect.top >= chrome - 1 && (rect.top < innerHeight / 2 || atEnd);
    })).toBe(true);
    await expect(page.locator(`.chapter-rail a[href="#${id}"]`)).toHaveAttribute('aria-current', 'location');
  }
  await page.goBack();
  await expect(page).toHaveURL(/#evidence-review$/);
});

test('mobile keeps one compact, opaque index bar and an opaque header', async ({ page }) => {
  test.skip(desktop(page), 'mobile only');
  await page.goto('/production.html#effect-authority');
  const toggle = page.locator('.rail-toggle');
  await expect(toggle).toBeVisible();
  await expect(page.locator('#chapter-rail-list')).toBeHidden();
  await expect(toggle.locator('[data-rail-current]')).toHaveText('Authorize');
  const chrome = await page.evaluate(() => document.querySelector('.chapter-rail').getBoundingClientRect().bottom);
  expect(chrome).toBeLessThanOrEqual(120);
  for (const selector of ['.masthead', '.chapter-rail']) {
    const background = await page.locator(selector).evaluate(el => getComputedStyle(el).backgroundColor);
    expect(background, selector).toMatch(/^rgb\(/);
  }
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#chapter-rail-list')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#chapter-rail-list')).toBeHidden();
  await expect(toggle).toBeFocused();
});

for (const width of [1440, 1920]) {
  test(`the walkthrough architecture keeps the pre-revision scale at ${width}`, async ({ page }) => {
    test.skip(!desktop(page), 'desktop widths');
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/production.html#workflow-in-action');
    const metrics = await page.evaluate(() => ({
      diagram: document.querySelector('#workflow-in-action .wf-diagram').getBoundingClientRect().width,
      actors: document.querySelector('[data-action-primary]').getBoundingClientRect().width,
      label: Math.min(...[...document.querySelectorAll('#workflow-in-action [data-node-layer] .wf-node:not([data-used="false"]) .wf-label')]
        .map(text => text.getBoundingClientRect().height)),
    }));
    // ba94a00 rendered 1060px at 1440 and 1340px at 1920 in the same reading column.
    expect(metrics.diagram).toBeGreaterThanOrEqual(width === 1440 ? 1040 : 1300);
    expect(metrics.label, 'rendered node label height').toBeGreaterThanOrEqual(15);
    expect(Math.abs(metrics.actors - metrics.diagram)).toBeLessThan(80);
  });
}

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
  const index = (await new AxeBuilder({ page }).include('.chapter-rail')
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
    await expect(page.locator('.chapter-rail a[href="#evidence-review"]')).toBeVisible();
    await expect(page.locator('.release-checks > li')).toHaveCount(3);
    await expect(page.locator('.owner-matrix tbody tr')).toHaveCount(6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});

test('part transitions read as chapter openers: wide openers and a generous divider at 1440', async ({ page }) => {
  test.skip(!desktop(page), 'desktop layout');
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/production.html');
  const metrics = await page.evaluate(() => {
    const column = document.querySelector('.chapter-body').getBoundingClientRect().width;
    const openers = [...document.querySelectorAll('section[data-toc-id] > .wrap > .section-head')].map(head => ({
      head: head.getBoundingClientRect().width,
      lede: head.querySelector('.section-lede').getBoundingClientRect().width,
      h2: parseFloat(getComputedStyle(head.querySelector('h2')).fontSize),
    }));
    const subhead = Math.max(...[...document.querySelectorAll('.part-subhead')].map(el => parseFloat(getComputedStyle(el).fontSize)));
    const dividers = [...document.querySelectorAll('.part-divider')].map(divider => {
      const next = divider.nextElementSibling.querySelector('h2') || divider.nextElementSibling;
      const rules = ['::before', '::after'].map(pseudo => parseFloat(getComputedStyle(divider, pseudo).width));
      return { above: divider.getBoundingClientRect().top - divider.previousElementSibling.getBoundingClientRect().bottom,
        below: next.getBoundingClientRect().top - divider.getBoundingClientRect().bottom,
        span: rules[0] + rules[1] + divider.querySelector('span').getBoundingClientRect().width };
    });
    return { column, openers, subhead, dividers };
  });
  expect(metrics.openers).toHaveLength(6);
  for (const opener of metrics.openers) {
    expect(opener.head).toBeGreaterThanOrEqual(metrics.column * 0.9);
    expect(opener.lede).toBeGreaterThanOrEqual(metrics.column * 0.75);
    expect(opener.h2).toBeGreaterThanOrEqual(metrics.subhead * 1.8);
  }
  expect(metrics.dividers).toHaveLength(5);
  for (const divider of metrics.dividers) {
    expect(divider.above).toBeGreaterThanOrEqual(96);
    expect(divider.below).toBeGreaterThanOrEqual(96);
    expect(divider.span).toBeGreaterThanOrEqual(metrics.column * 0.6);
  }
});
