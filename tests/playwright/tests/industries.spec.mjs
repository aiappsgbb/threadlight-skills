// End-to-end checks for docs/industries.html — the process gallery.
// Verifies the gallery loads the static library, industry + search filters
// work, cards deep-link into the Blueprint composer, and that the deep-link
// actually preselects the process on the other side.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const INDUSTRIES = '/industries.html';

test('catalogue counts, complexity labels and actions have readable contrast', async ({ page }) => {
  await page.goto(INDUSTRIES);
  await expect(page.locator('#ind-grid .ind-card')).toHaveCount(89);
  const results = await new AxeBuilder({ page }).include('#library').withRules(['color-contrast']).analyze();
  expect(results.violations).toEqual([]);
});

test.describe('industries gallery (industries.html)', () => {
  test('renders hero + title', async ({ page }) => {
    await page.goto(INDUSTRIES);
    await expect(page).toHaveTitle(/industries/i);
    await expect(page.getByRole('heading', { level: 1 })).toContainText(/Loans.*Claims.*Operations/i);
  });

  test('puts the searchable catalogue in the opening screen', async ({ page }) => {
    const mobile = page.viewportSize().width <= 600;
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(INDUSTRIES);
    await expect(page.locator('body')).toHaveClass(/chapter-experience/);
    const intro = page.locator('[data-chapter-intro]');
    await expect(intro).toBeVisible();
    expect((await intro.innerText()).trim().split(/\s+/).length).toBeLessThanOrEqual(60);
    await expect(page.locator('[data-visual-anchor] #ind-search')).toBeVisible();
    expect((await page.locator('[data-visual-anchor]').boundingBox()).y).toBeLessThanOrEqual(mobile ? 600 : 760);
    expect((await page.locator('#ind-search').boundingBox()).y).toBeLessThanOrEqual(mobile ? 600 : 760);
    await expect(page.locator('#ind-grid .ind-card').first()).toBeVisible();
    const firstTitle = await page.locator('#ind-grid .ind-card h3').first().boundingBox();
    expect(firstTitle.y + firstTitle.height).toBeLessThanOrEqual(page.viewportSize().height);
    await expect(page.locator('.stat-strip, .metric-card')).toHaveCount(0);
    await expect(page.locator('header nav.nav a[href="./funnel.html"]')).toHaveAttribute('aria-current', 'location');
  });

  test('loads the process library and reports the count', async ({ page }) => {
    await page.goto(INDUSTRIES);
    const cards = page.locator('#ind-grid .ind-card');
    await expect(cards).toHaveCount(89);
    await expect(page.locator('#ind-count')).toContainText(/89 of 89 processes/);
  });

  test('shows how a library brief becomes a reviewed SPEC, not production proof', async ({ page }) => {
    const mobile = page.viewportSize().width <= 600;
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(INDUSTRIES + '#ind-spec');
    const map = page.locator('figure[data-spec-map]');
    await expect(map).toBeVisible();
    await expect(map.locator('figcaption')).toContainText(/illustrative.*not.*generated spec/i);
    await expect(map.locator('[data-spec-step]')).toHaveCount(3);
    await expect(map.locator('[data-spec-step="brief"]')).toContainText('89');
    await expect(map.locator('[data-spec-step="review"]')).toContainText(/rules.*owners.*systems/is);
    await expect(map.locator('[data-spec-step="later"]')).toContainText(/manual.*live/is);
    await expect(map).toContainText(/not production evidence/i);
    expect(await map.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    const first = await map.locator('[data-spec-step="brief"]').boundingBox();
    const second = await map.locator('[data-spec-step="review"]').boundingBox();
    if (mobile) expect(second.y).toBeGreaterThanOrEqual(first.y + first.height);
    else expect(second.x).toBeGreaterThanOrEqual(first.x + first.width);
    const accessibility = await new AxeBuilder({ page }).include('[data-spec-map]').analyze();
    expect(accessibility.violations).toEqual([]);
    await page.locator('.ind-technical summary').click();
    await expect(page.locator('.spec-shape .ss-cell')).toHaveCount(13);
    await expect(page.locator('.spec-shape')).toContainText('Value model');
  });

  test('builds the industry pill row busiest-first with counts', async ({ page }) => {
    await page.goto(INDUSTRIES);
    const pills = page.locator('#ind-pills .ind-pill');
    // All + 15 industries
    await expect(pills).toHaveCount(16);
    await expect(pills.first()).toContainText(/All/);
    await expect(pills.nth(1)).toContainText(/Financial Services/);
  });

  test('filtering by an industry pill narrows the grid; All restores it', async ({ page }) => {
    await page.goto(INDUSTRIES);
    await page.locator('#ind-grid .ind-card').first().waitFor();
    await page.locator('.ind-filter-panel summary').click();
    await page.getByRole('button', { name: /^Healthcare/ }).click();
    await expect(page.locator('#ind-count')).toContainText(/10 of 89/);
    await page.getByRole('button', { name: /^All/ }).click();
    await expect(page.locator('#ind-count')).toContainText(/89 of 89/);
  });

  test('industry filters expand without horizontal scrolling on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(INDUSTRIES);
    await page.locator('.ind-filter-panel summary').click();
    await expect(page.locator('#ind-pills .ind-pill')).toHaveCount(16);
    expect(await page.locator('#ind-pills').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.getByRole('button', { name: /^Healthcare/ }).click();
    await expect(page.locator('.ind-filter-panel summary')).toContainText('Healthcare');
  });

  test('search narrows by process name', async ({ page }) => {
    await page.goto(INDUSTRIES);
    await page.locator('#ind-grid .ind-card').first().waitFor();
    await page.fill('#ind-search', 'loan');
    await expect(page.locator('#ind-count')).toContainText(/3 of 89/);
    await expect(page.locator('#ind-grid .ind-card')).toHaveCount(3);
  });

  test('every card is a deep-link into the Blueprint composer', async ({ page }) => {
    await page.goto(INDUSTRIES);
    const first = page.locator('#ind-grid .ind-card').first();
    await expect(first).toHaveAttribute('href', /^blueprint\.html\?s=[a-z0-9-]+$/);
  });

  test('the deep-link preselects that process in the composer', async ({ page }) => {
    await page.goto('/blueprint.html?s=commercial-loan-origination');
    await expect(page.locator('#bp-result')).toBeVisible();
    await expect(page.locator('#bp-result-name')).toContainText('Commercial Loan Origination');
    await expect(page.locator('#bp-arc .bp-skill').first()).toContainText('threadlight-design');
  });

  test('industries belongs to Build without crowding the primary nav', async ({ page }) => {
    await page.goto(INDUSTRIES);
    const hrefs = await page.locator('header.masthead nav.nav a').evaluateAll(
      els => els.map(e => e.getAttribute('href') || '')
    );
    expect(hrefs).toContain('./funnel.html');
    expect(hrefs).not.toContain('./industries.html');
    // The group entry owns the primary active state, not the child page.
    await expect(page.locator('header.masthead nav.nav a[aria-current="page"]')).toHaveCount(0);
    await expect(page.locator('header.masthead nav.nav a[aria-current="location"]')).toHaveAttribute('href', './funnel.html');
  });

  test('empty search retains a recovery path', async ({ page }) => {
    await page.goto(INDUSTRIES);
    await expect(page.locator('#ind-count')).toContainText('89 of 89');
    await page.fill('#ind-search', 'no-such-process-xyz');
    await expect(page.locator('.ind-empty')).toContainText('Clear the search');
    await page.fill('#ind-search', '');
    await expect(page.locator('#ind-grid .ind-card')).toHaveCount(89);
  });

  test('without JavaScript links to the process source library', async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    await page.goto(INDUSTRIES);
    await expect(page.locator('noscript p')).toBeVisible();
    await expect(page.locator('noscript p')).toContainText(/JavaScript/i);
    await expect(page.locator('noscript a')).toHaveAttribute('href', /process-library/);
    await expect(page.locator('figure[data-spec-map]')).toBeVisible();
    await context.close();
  });
});
