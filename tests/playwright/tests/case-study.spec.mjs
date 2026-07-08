// End-to-end checks for docs/case-study.html — the Run Explorer.
// The 14-stage run is progressively disclosed: each stage is a native
// <details> (collapsed by default, skimmable summary always visible) and a
// sticky rail navigates + deep-links them. Content is never deleted — every
// headline and finding stays in the DOM so the page keeps its reference value.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const CS = '/case-study.html';

// site.js runs scroll-reveal animations that grow the page for ~1.2s after load
// (there's a safety net that force-adds `.in` to every `.reveal` by then). Until
// those settle, content below keeps shifting, so clicks on off-screen stages can
// race the layout. Wait for reveals to finish before interacting.
async function settle(page) {
  await page.waitForFunction(
    () => document.querySelectorAll('.reveal:not(.in)').length === 0,
    null,
    { timeout: 5000 }
  );
}

// Fire the element's real click handler without Playwright's pointer actionability
// (scroll/stability/intercept) getting entangled with the page's ambient reveal
// animations and stacked mobile layout. Desktop/dark/light prove real pointer
// clicks; this keeps the behaviour assertions deterministic on the tall mobile
// layout too. A dispatched click toggles native <details> and fires the rail/
// expand/collapse listeners just like a user click.
async function tap(page, selector) {
  await page.locator(selector).dispatchEvent('click');
}

test.describe('case study run explorer (case-study.html)', () => {
  test('renders + title intact', async ({ page }) => {
    await page.goto(CS);
    await expect(page).toHaveTitle(/Case study/);
  });

  test('all 14 stages present, each a details, collapsed by default', async ({ page }) => {
    await page.goto(CS);
    const stages = page.locator('.cs-timeline .cs-stage');
    await expect(stages).toHaveCount(14);
    // Each stage is a native disclosure.
    await expect(page.locator('.cs-timeline .cs-stage details.cs-card')).toHaveCount(14);
    // Collapsed by default — nothing open on load.
    await expect(page.locator('.cs-timeline details[open]')).toHaveCount(0);
    // The skimmable header stays visible even while collapsed…
    await expect(page.locator('#stage-01 summary h3')).toBeVisible();
    // …but the body is hidden until opened.
    await expect(page.locator('#stage-01 .cs-body')).toBeHidden();
  });

  test('no content lost — every headline and finding stays in the DOM', async ({ page }) => {
    await page.goto(CS);
    // Present regardless of open/closed state (reference value / Ctrl-F).
    await expect(page.locator('.cs-timeline .cs-stage h3')).toHaveCount(14);
    await expect(page.locator('.cs-timeline .cs-finding')).toHaveCount(14);
  });

  test('the rail lists all 14 stages with deep-link anchors', async ({ page }) => {
    await page.goto(CS);
    const rail = page.locator('.cs-rail');
    await expect(rail).toBeVisible();
    const links = rail.locator('a[href^="#stage-"]');
    await expect(links).toHaveCount(14);
    await expect(links.first()).toHaveAttribute('href', '#stage-01');
    await expect(links.last()).toHaveAttribute('href', '#stage-14');
  });

  test('clicking a rail item opens and reveals that stage', async ({ page }) => {
    await page.goto(CS);
    await settle(page);
    await tap(page, '.cs-rail a[href="#stage-04"]');
    await expect(page.locator('#stage-04 details[open]')).toHaveCount(1);
    await expect(page.locator('#stage-04 .cs-body')).toBeVisible();
  });

  test('clicking a stage summary toggles it open then closed', async ({ page }) => {
    await page.goto(CS);
    await settle(page);
    const open = page.locator('#stage-02 details[open]');
    await expect(open).toHaveCount(0);
    await tap(page, '#stage-02 summary');
    await expect(open).toHaveCount(1);
    await tap(page, '#stage-02 summary');
    await expect(open).toHaveCount(0);
  });

  test('deep-link opens the target stage on load', async ({ page }) => {
    await page.goto(CS + '#stage-05');
    await expect(page.locator('#stage-05 details[open]')).toHaveCount(1);
    await expect(page.locator('#stage-05 summary')).toBeInViewport();
  });

  test('expand-all opens every stage, collapse-all closes them', async ({ page }) => {
    await page.goto(CS);
    await settle(page);
    await tap(page, '.cs-rail [data-cs="expand"]');
    await expect(page.locator('.cs-timeline details[open]')).toHaveCount(14);
    await tap(page, '.cs-rail [data-cs="collapse"]');
    await expect(page.locator('.cs-timeline details[open]')).toHaveCount(0);
  });

  test('scrolling marks the active stage in the rail', async ({ page }) => {
    await page.goto(CS);
    // The page scrolls inside document.body (overflow:auto); scroll the real
    // container so stage-08's top lands just under the reading line (180).
    await page.evaluate(() => {
      const s = document.getElementById('stage-08');
      const delta = s.getBoundingClientRect().top - 160;
      document.body.scrollTop += delta;
      (document.scrollingElement || document.documentElement).scrollTop += delta;
    });
    await expect(page.locator('.cs-rail a[href="#stage-08"]')).toHaveClass(/is-active/);
  });

  test('axe: no serious/critical a11y violations', async ({ page }) => {
    await page.goto(CS);
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa'])
      .disableRules(['color-contrast'])
      .analyze();
    const offenders = results.violations.filter(v => ['serious', 'critical'].includes(v.impact));
    expect(offenders, JSON.stringify(offenders, null, 2)).toEqual([]);
  });
});
