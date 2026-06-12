// End-to-end checks for docs/ — landing + catalog + a11y.
// Numbers in the cost widget come from the v0.1.0 golden manifest.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const LANDING = '/index.html';
const SKILLS  = '/skills.html';

test.describe('landing page (index.html)', () => {
  test('renders the hero + has working title', async ({ page }) => {
    await page.goto(LANDING);
    await expect(page).toHaveTitle(/Threadlight/);
    await expect(page.getByRole('heading', { name: /Business process/i })).toBeVisible();
    await expect(page.locator('.hero-cta-row .btn-primary').first()).toContainText(/Try the live preview/i);
  });

  test('nav has no duplicate Setup link', async ({ page }) => {
    await page.goto(LANDING);
    const setupLinks = page.locator('header.masthead nav.nav a', { hasText: /^Setup$/ });
    await expect(setupLinks).toHaveCount(1);
  });

  test('footer is public-safe (no "internal use" leak)', async ({ page }) => {
    await page.goto(LANDING);
    const footer = page.locator('footer').first();
    await expect(footer).toBeVisible();
    await expect(footer).not.toContainText(/internal use/i);
    await expect(footer).toContainText(/open guidance/i);
  });

  test('cost intelligence section is present and animates to the golden numbers', async ({ page }) => {
    await page.goto(LANDING);
    const cost = page.locator('#scene-cost');
    await cost.scrollIntoViewIfNeeded();
    // Wait for counter animation (~1.1s + buffer)
    await page.waitForTimeout(1500);
    const current     = cost.locator('.ctr-value.is-current');
    const recommended = cost.locator('.ctr-value.is-recommended');
    await expect(current).toContainText('$397.57 /mo');
    await expect(recommended).toContainText('$341.19 /mo');
    await expect(cost.locator('.cost-savings-pill')).toContainText(/56\.38/);
    await expect(cost.locator('.cost-recs .cost-rec')).toHaveCount(3);
    await expect(cost.locator('.cost-table tbody tr')).toHaveCount(7);
    await expect(cost.locator('a[href$="/skills.html#threadlight-consumption-iq"]')).toBeVisible();
  });

  test('all internal anchors resolve', async ({ page }) => {
    await page.goto(LANDING);
    const hrefs = await page.locator('header.masthead nav.nav a[href^="#"]').evaluateAll(
      (els) => els.map((e) => e.getAttribute('href'))
    );
    for (const h of hrefs) {
      const id = h.slice(1);
      await expect(page.locator('#' + id), `nav target ${h} should exist`).toHaveCount(1);
    }
  });

  test('has favicon + OpenGraph tags', async ({ page }) => {
    await page.goto(LANDING);
    const icon = await page.locator('link[rel="icon"]').getAttribute('href');
    expect(icon).toMatch(/^data:image\/svg\+xml/);
    await expect(page.locator('meta[property="og:title"]')).toHaveCount(1);
    await expect(page.locator('meta[property="og:description"]')).toHaveCount(1);
    await expect(page.locator('meta[name="twitter:card"]')).toHaveCount(1);
  });

  test('axe-core: no serious or critical a11y violations on landing', async ({ page }) => {
    await page.goto(LANDING);
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa'])
      .disableRules(['color-contrast'])
      .analyze();
    const offenders = results.violations.filter(v => ['serious', 'critical'].includes(v.impact));
    expect(offenders, JSON.stringify(offenders, null, 2)).toEqual([]);
  });
});

test.describe('catalog page (skills.html)', () => {
  test('renders all 11 skill cards initially', async ({ page }) => {
    await page.goto(SKILLS);
    await expect(page).toHaveTitle(/skills catalog/i);
    await expect(page.locator('#grid .card')).toHaveCount(11);
    await expect(page.locator('#count-meta')).toContainText('Showing 11 of 11');
  });

  test('exposes consumption-iq as new', async ({ page }) => {
    await page.goto(SKILLS);
    const card = page.locator('#threadlight-consumption-iq');
    await expect(card).toBeVisible();
    await expect(card.locator('.card-version.is-new')).toContainText(/new/i);
    await expect(card.locator('.card-link')).toHaveAttribute('href', /consumption-iq\/SKILL\.md$/);
  });

  test('phase filter narrows the grid', async ({ page }) => {
    await page.goto(SKILLS);
    await page.getByRole('button', { name: 'Cost' }).click();
    await expect(page.locator('#grid .card')).toHaveCount(1);
    await expect(page.locator('#count-meta')).toContainText('Showing 1 of 11');
    await page.getByRole('button', { name: 'Cost' }).click();
    await expect(page.locator('#grid .card')).toHaveCount(11);
  });

  test('search filter is case-insensitive and substring', async ({ page }) => {
    await page.goto(SKILLS);
    await page.getByLabel('Search skills').fill('cosmos');
    await expect(page.locator('#grid .card').first()).toBeVisible();
    const visible = await page.locator('#grid .card').count();
    expect(visible).toBeGreaterThan(0);
    expect(visible).toBeLessThan(11);
    await page.getByLabel('Search skills').fill('completely-nonexistent-zzz');
    await expect(page.locator('#grid .empty')).toBeVisible();
  });

  test('deep-link anchor scrolls to the right card', async ({ page }) => {
    await page.goto(SKILLS + '#threadlight-consumption-iq');
    const card = page.locator('#threadlight-consumption-iq');
    await expect(card).toBeInViewport();
  });

  test('axe-core: no serious or critical a11y violations on catalog', async ({ page }) => {
    await page.goto(SKILLS);
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa'])
      .disableRules(['color-contrast'])
      .analyze();
    const offenders = results.violations.filter(v => ['serious', 'critical'].includes(v.impact));
    expect(offenders, JSON.stringify(offenders, null, 2)).toEqual([]);
  });
});

test.describe('public-safety audit (no leaks of internal-only phrasing)', () => {
  test('no obvious leak terms anywhere on landing or catalog', async ({ page }) => {
    for (const path of [LANDING, SKILLS]) {
      await page.goto(path);
      const body = (await page.locator('body').textContent()) || '';
      const forbidden = [
        /internal use/i,
        /confidential/i,
        /do not share/i,
        /microsoft only/i,
        /tier-1 european telco/i
      ];
      for (const re of forbidden) {
        expect(body, `${path} should not contain ${re}`).not.toMatch(re);
      }
    }
  });
});
