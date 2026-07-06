// End-to-end checks for docs/industries.html — the process gallery.
// Verifies the gallery loads the static library, industry + search filters
// work, cards deep-link into the Blueprint composer, and that the deep-link
// actually preselects the process on the other side.
import { test, expect } from '@playwright/test';

const INDUSTRIES = '/industries.html';

test.describe('industries gallery (industries.html)', () => {
  test('renders hero + title', async ({ page }) => {
    await page.goto(INDUSTRIES);
    await expect(page).toHaveTitle(/industries/i);
    await expect(page.getByRole('heading', { level: 1 })).toContainText(/Any business process/i);
  });

  test('loads the process library and reports the count', async ({ page }) => {
    await page.goto(INDUSTRIES);
    const cards = page.locator('#ind-grid .ind-card');
    await expect(cards).toHaveCount(89);
    await expect(page.locator('#ind-count')).toContainText(/89 of 89 processes/);
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
    await page.getByRole('button', { name: /^Healthcare/ }).click();
    await expect(page.locator('#ind-count')).toContainText(/10 of 89/);
    await page.getByRole('button', { name: /^All/ }).click();
    await expect(page.locator('#ind-count')).toContainText(/89 of 89/);
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

  test('nav is the 5-chapter set; industries is an off-nav sub-page', async ({ page }) => {
    await page.goto(INDUSTRIES);
    const hrefs = await page.locator('header.masthead nav.nav a').evaluateAll(
      els => els.map(e => e.getAttribute('href') || '')
    );
    // Industries is reached from Blueprint, not the primary nav
    expect(hrefs).toContain('./blueprint.html');
    expect(hrefs).not.toContain('./industries.html');
    // A non-chapter sub-page marks no nav item as current
    await expect(page.locator('header.masthead nav.nav a[aria-current="page"]')).toHaveCount(0);
  });
});
