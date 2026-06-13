// End-to-end checks for docs/index.html (landing) — public release scope.
// Numbers in the cost widget come from the v0.1.0 golden manifest.
// The skills catalog page (docs/skills.html) is deliberately out of scope
// for the initial release — it lives untracked under .gitignore.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const LANDING = '/index.html';

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
    // Zero (removed) or one (kept) is fine; two or more is a regression.
    await expect(await setupLinks.count()).toBeLessThan(2);
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
    await expect(cost.locator('a[href*="/skills/threadlight-consumption-iq"]').first()).toBeVisible();
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

test.describe('catalog page (skills.html) — DEFERRED out of initial release', () => {
  test.skip('the docs/skills.html catalog is intentionally not shipped yet; its prior tests live in git history', () => {
    // The catalog page lives untracked under .gitignore and may come back in
    // a follow-up release. Reinstate the prior describe block when it does.
  });
});

test.describe('deck-spine additions (evolution / funnel / industries / channels)', () => {
  test('evolution band renders all five waves with Skills marked active', async ({ page }) => {
    await page.goto(LANDING);
    const band = page.locator('.evolution-band');
    await expect(band).toBeVisible();
    const chips = band.locator('.evo-chip');
    await expect(chips).toHaveCount(5);
    await expect(chips.nth(0)).toContainText('LLMs');
    await expect(chips.nth(1)).toContainText('RAG');
    await expect(chips.nth(2)).toContainText('Multi-agent');
    await expect(chips.nth(3)).toContainText('MCP');
    await expect(chips.nth(4)).toContainText('Skills');
    await expect(chips.nth(4)).toHaveClass(/is-now/);
  });

  test('funnel hero shows exactly 5 stages with valid internal targets', async ({ page }) => {
    await page.goto(LANDING);
    const funnel = page.locator('#scene-funnel');
    await funnel.scrollIntoViewIfNeeded();
    const steps = funnel.locator('.funnel-step');
    await expect(steps).toHaveCount(5);
    const expected = ['01 · Conversation', '02 · Co-design', '03 · Deploy', '04 · Safe-check', '05 · Production'];
    for (let i = 0; i < expected.length; i++) {
      await expect(steps.nth(i)).toContainText(expected[i]);
    }
    // every step links to a real scene on this page
    const hrefs = await steps.evaluateAll(els => els.map(e => e.getAttribute('href')));
    for (const h of hrefs) {
      expect(h, 'funnel step should anchor to a scene').toMatch(/^#scene-/);
      await expect(page.locator(h)).toHaveCount(1);
    }
  });

  test('industries strip renders all 6 sectors with non-empty pilots', async ({ page }) => {
    await page.goto(LANDING);
    const ind = page.locator('#scene-industries');
    await ind.scrollIntoViewIfNeeded();
    const tiles = ind.locator('.industry-tile');
    await expect(tiles).toHaveCount(6);
    const sectors = await ind.locator('.industry-tile .it-sector').allTextContents();
    for (const s of ['Financial services', 'Healthcare', 'Manufacturing', 'Retail', 'Public sector', 'Telco']) {
      expect(sectors.join(' | ').toLowerCase()).toContain(s.toLowerCase());
    }
    // each tile has a one-line pilot description, not empty
    for (let i = 0; i < 6; i++) {
      const line = await tiles.nth(i).locator('.it-line').textContent();
      expect((line || '').trim().length, `industry ${i} should have copy`).toBeGreaterThan(20);
    }
  });

  test('kratos showcase links out to live demo + GitHub repo', async ({ page }) => {
    await page.goto(LANDING);
    const ch = page.locator('#scene-channels');
    await ch.scrollIntoViewIfNeeded();
    await expect(ch.getByRole('heading', { name: /Kratos Agent/i })).toBeVisible();
    const live = ch.locator('a[href="https://aka.ms/kratos"]');
    await expect(live).toBeVisible();
    await expect(live).toHaveAttribute('target', '_blank');
    await expect(live).toHaveAttribute('rel', /noopener/);
    const repo = ch.locator('a[href="https://github.com/kmavrodis/kratos-agent"]');
    await expect(repo).toBeVisible();
    // Screenshot loads from the kratos repo, not embedded in this repo
    const shot = ch.locator('img.kc-shot');
    await expect(shot).toHaveAttribute('src', /raw\.githubusercontent\.com\/kmavrodis\/kratos-agent/);
    await expect(shot).toHaveAttribute('alt', /kratos/i);
  });

  test('other channels strip lists exactly 3 surfaces', async ({ page }) => {
    await page.goto(LANDING);
    const tiles = page.locator('#scene-channels .channels-other .co-tile');
    await expect(tiles).toHaveCount(3);
    const text = (await tiles.allTextContents()).join(' | ').toLowerCase();
    expect(text).toContain('copilot studio');
    expect(text).toContain('sre agent');
    expect(text).toContain('mcp');
  });

  test('nav contains the new Funnel + Channels anchors and stays under 9 links', async ({ page }) => {
    await page.goto(LANDING);
    const links = page.locator('header.masthead nav.nav a');
    const count = await links.count();
    expect(count).toBeLessThanOrEqual(8);
    const labels = await links.allTextContents();
    expect(labels.join(' | ')).toMatch(/Funnel/);
    expect(labels.join(' | ')).toMatch(/Channels/);
  });
});

test.describe('public-safety audit (no leaks of internal-only phrasing)', () => {
  test('no obvious leak terms anywhere on landing', async ({ page }) => {
    for (const path of [LANDING]) {
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
