// End-to-end checks for docs/case-study.html — the guided Wizard.
// The case study is presented as a single-screen deck: one chapter fills the
// viewport, and the reader advances with Next/Prev, arrow keys, or a chapter
// menu. All content stays in the DOM (hidden slides are display:none, never
// deleted) so the page keeps its full reference value and degrades to a plain
// scrollable page when JS is off. Content is re-presented, never rewritten.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const CS = '/case-study.html';

// The 15 top-level steps, in DOM order (hero + 10 chapters + 4 Act cards).
const STEPS = [
  'chapter-top', 'glance', 'act-1', 'setup', 'act-2', 'arch', 'run',
  'act-3', 'proof', 'demos', 'cost', 'act-4', 'limits', 'shipped', 'verdict',
];

// The wizard boots on load (adds html.cs-wiz and injects its chrome). site.js
// also runs scroll-reveal that force-reveals every `.reveal` within ~1.2s; wait
// for the wizard to be live before interacting so nothing races the boot.
async function ready(page) {
  await page.waitForFunction(
    () => document.documentElement.classList.contains('cs-wiz')
      && !!document.querySelector('.cs-wiz-next'),
    null, { timeout: 5000 },
  );
  await page.waitForFunction(
    () => document.querySelectorAll('.reveal:not(.in)').length === 0,
    null, { timeout: 5000 },
  ).catch(() => {});
}

// Fire the element's real click handler without Playwright pointer actionability
// (fixed chrome + the ambient slide transition can otherwise race the assertion).
// A dispatched click still triggers the wizard's button/rail listeners exactly
// like a user click. Keyboard tests below prove real key handling.
async function tap(page, selector) {
  await page.locator(selector).dispatchEvent('click');
}

test.describe('case study wizard (case-study.html)', () => {
  test('renders + title intact', async ({ page }) => {
    await page.goto(CS);
    await expect(page).toHaveTitle(/Case study/);
  });

  test('boots as a wizard: exactly one slide, hero first, no page scroll', async ({ page }) => {
    await page.goto(CS);
    await ready(page);
    await expect(page.locator('main > section.is-current')).toHaveCount(1);
    await expect(page.locator('#chapter-top')).toHaveClass(/is-current/);
    await expect(page.locator('#verdict')).toBeHidden();
    // body/page does not scroll — the deck owns the viewport
    const noScroll = await page.evaluate(() => {
      const el = document.scrollingElement || document.documentElement;
      return el.scrollHeight <= el.clientHeight + 2;
    });
    expect(noScroll).toBe(true);
  });

  test('Next / Prev walk the top-level steps and disable at the ends', async ({ page }) => {
    await page.goto(CS);
    await ready(page);
    await expect(page.locator('.cs-wiz-prev')).toBeDisabled();
    await tap(page, '.cs-wiz-next');
    await expect(page.locator('#glance')).toHaveClass(/is-current/);
    await expect(page.locator('#chapter-top')).not.toHaveClass(/is-current/);
    await tap(page, '.cs-wiz-prev');
    await expect(page.locator('#chapter-top')).toHaveClass(/is-current/);
    // jump to the end → Next disabled on the last step
    await tap(page, '.cs-wiz-menu-btn');
    await tap(page, '.cs-wiz-jump[data-jump="verdict"]');
    await expect(page.locator('#verdict')).toHaveClass(/is-current/);
    await expect(page.locator('.cs-wiz-next')).toBeDisabled();
  });

  test('keyboard: arrows step, Home/End jump to first/last', async ({ page }) => {
    await page.goto(CS);
    await ready(page);
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('#glance')).toHaveClass(/is-current/);
    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#chapter-top')).toHaveClass(/is-current/);
    await page.keyboard.press('End');
    await expect(page.locator('#verdict')).toHaveClass(/is-current/);
    await page.keyboard.press('Home');
    await expect(page.locator('#chapter-top')).toHaveClass(/is-current/);
  });

  test('chapter menu lists chapters and jumps', async ({ page }) => {
    await page.goto(CS);
    await ready(page);
    await tap(page, '.cs-wiz-menu-btn');
    await expect(page.locator('.cs-wiz-menu')).toHaveClass(/is-open/);
    // 11 labelled chapters (hero + 10), Act cards are stepped-through, not listed
    await expect(page.locator('.cs-wiz-jump')).toHaveCount(11);
    await tap(page, '.cs-wiz-jump[data-jump="demos"]');
    await expect(page.locator('#demos')).toHaveClass(/is-current/);
    await expect(page.locator('.cs-wiz-menu')).not.toHaveClass(/is-open/);
  });

  test('deep links: #proof opens proof; #stage-07 opens the run at stage 7', async ({ page }) => {
    await page.goto(CS + '#proof');
    await ready(page);
    await expect(page.locator('#proof')).toHaveClass(/is-current/);

    await page.goto(CS + '#stage-07');
    await ready(page);
    await expect(page.locator('#run')).toHaveClass(/is-current/);
    await expect(page.locator('#stage-07')).toHaveClass(/is-shown/);
    await expect(page.locator('.cs-rail-item[data-target="stage-07"]')).toHaveClass(/is-active/);
    await expect(page.locator('.cs-run-pos')).toContainText('7');
  });

  test('sub-stepper: one stage at a time, Next cycles 1..14 then exits, rail jumps, Prev re-enters at 14', async ({ page }) => {
    await page.goto(CS + '#run');
    await ready(page);
    await expect(page.locator('#run')).toHaveClass(/is-current/);
    await expect(page.locator('#run .cs-stage.is-shown')).toHaveCount(1);
    await expect(page.locator('#stage-01')).toHaveClass(/is-shown/);

    // Next advances the stage but stays on the run step
    await tap(page, '.cs-wiz-next');
    await expect(page.locator('#stage-02')).toHaveClass(/is-shown/);
    await expect(page.locator('#run')).toHaveClass(/is-current/);

    // rail click jumps directly to a stage
    await tap(page, '.cs-rail-item[data-target="stage-14"]');
    await expect(page.locator('#stage-14')).toHaveClass(/is-shown/);

    // Next from the last stage exits to the next top-level step (Act III)
    await tap(page, '.cs-wiz-next');
    await expect(page.locator('#act-3')).toHaveClass(/is-current/);

    // Prev re-enters the run at the LAST stage (continuous backward reading)
    await tap(page, '.cs-wiz-prev');
    await expect(page.locator('#run')).toHaveClass(/is-current/);
    await expect(page.locator('#stage-14')).toHaveClass(/is-shown/);
  });

  test('content integrity: every stage + the load-run receipts stay in the DOM', async ({ page }) => {
    await page.goto(CS);
    await ready(page);
    for (let i = 1; i <= 14; i++) {
      await expect(page.locator('#stage-' + String(i).padStart(2, '0'))).toHaveCount(1);
    }
    const html = await page.content();
    for (const token of ['2,306', 'Meridian Commercial Bank', 'Citadel', 'Claude Opus 4.8', '14 / 15']) {
      expect(html).toContain(token);
    }
  });

  test('reduced motion: still fully navigable', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(CS);
    await ready(page);
    await tap(page, '.cs-wiz-next');
    await expect(page.locator('#glance')).toHaveClass(/is-current/);
  });

  test('no serious accessibility violations mid-deck', async ({ page }) => {
    await page.goto(CS + '#proof');
    await ready(page);
    const serious = (r) =>
      r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');

    // Structural a11y across the whole deck view: roles, names, focus order, ARIA.
    // color-contrast is asserted separately (below), scoped to the chrome the
    // wizard injects — the case study's pre-existing content chips, eyebrows and
    // masthead carry known light-mode contrast that predates and is independent
    // of the wizard (verbatim on main), so this change does not police it.
    const structural = await new AxeBuilder({ page }).disableRules(['color-contrast']).analyze();
    expect(serious(structural)).toEqual([]);

    // Contrast of the wizard's own chrome (top bar + bottom nav) must be clean.
    const chrome = await new AxeBuilder({ page })
      .include('.cs-wiz-bar')
      .include('.cs-wiz-nav')
      .withRules(['color-contrast'])
      .analyze();
    expect(serious(chrome)).toEqual([]);
  });
});

test.describe('case study fallback (no JS)', () => {
  test.use({ javaScriptEnabled: false });
  test('degrades to a plain scrollable page with all content shown', async ({ page }) => {
    await page.goto(CS);
    await expect(page.locator('html')).not.toHaveClass(/cs-wiz/);
    await expect(page.locator('#chapter-top')).toBeVisible();
    await expect(page.locator('#verdict')).toBeVisible();
    // all 14 build stages render (nothing hidden behind the wizard)
    await expect(page.locator('#run .cs-stage')).toHaveCount(14);
  });
});
