// End-to-end checks for docs/blueprint.html — the composer on-ramp.
// Verifies the picker loads the static library, filtering works, and a
// selected/described process yields a derived arc + copy-paste prompts.
import { test, expect } from '@playwright/test';

const BLUEPRINT = '/blueprint.html';

test.describe('blueprint composer (blueprint.html)', () => {
  test('renders hero + title', async ({ page }) => {
    await page.goto(BLUEPRINT);
    await expect(page).toHaveTitle(/Blueprint/);
    await expect(page.getByRole('heading', { level: 1 })).toContainText(/build prompt/i);
  });

  test('loads the library capped so the describe-your-own form stays reachable', async ({ page }) => {
    await page.goto(BLUEPRINT);
    const cards = page.locator('#bp-grid .bp-card');
    // Only a preview renders on load — the full 89 would bury the freeform textbox.
    await expect(cards).toHaveCount(12);
    // The count still reports the true total, not the number shown.
    await expect(page.locator('#bp-count')).toContainText(/89 of 89 scenarios/);
    // A show-all control reveals the rest and then disappears.
    const showAll = page.locator('#bp-grid .bp-showall');
    await expect(showAll).toContainText(/Show all 89/);
    await showAll.click();
    await expect(cards).toHaveCount(89);
    await expect(page.locator('#bp-grid .bp-showall')).toHaveCount(0);
  });

  test('filtering by domain narrows the grid; reset restores it', async ({ page }) => {
    await page.goto(BLUEPRINT);
    await page.locator('#bp-grid .bp-card').first().waitFor();
    await page.selectOption('#bp-domain', 'healthcare');
    await expect(page.locator('#bp-count')).toContainText(/10 of 89/);
    await page.click('#bp-reset');
    await expect(page.locator('#bp-count')).toContainText(/89 of 89/);
  });

  test('selecting a scenario derives an arc + build prompt + hands-off automation', async ({ page }) => {
    await page.goto(BLUEPRINT);
    await page.locator('#bp-grid .bp-card').first().click();
    const result = page.locator('#bp-result');
    await expect(result).toBeVisible();
    await expect(page.locator('#bp-arc .bp-skill').first()).toContainText('threadlight-design');
    await expect(page.locator('#bp-prompt')).toContainText('threadlight-auto');
    // Deploy is a hands-off CI/CD outcome — never a manual azd command block.
    await expect(page.locator('#bp-auto')).toContainText('CI/CD');
    await expect(page.locator('#bp-auto li.is-key')).toContainText('never run a deploy command');
    await expect(page.locator('#bp-auto')).not.toContainText('azd up');
  });

  test('describe-your-own with integrations + approvals adds the right skills', async ({ page }) => {
    await page.goto(BLUEPRINT);
    await page.fill('#bp-desc', 'Triage inbound claims and route to an adjuster');
    await page.check('#bp-cint');
    await page.check('#bp-capp');
    await page.click('#bp-custom button[type="submit"]');
    const arc = page.locator('#bp-arc');
    await expect(arc).toContainText('threadlight-demo-data-factory');
    await expect(arc).toContainText('threadlight-hitl-patterns');
  });

  test('copy button gives feedback', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
    await page.goto(BLUEPRINT);
    await page.locator('#bp-grid .bp-card').first().click();
    await page.click('.bp-copy[data-copy="bp-prompt"]');
    await expect(page.locator('.bp-copy[data-copy="bp-prompt"]')).toContainText(/Copied/);
  });

  test('nav exposes Blueprint as the current page', async ({ page }) => {
    await page.goto(BLUEPRINT);
    const current = page.locator('header.masthead nav.nav a[aria-current="page"]');
    await expect(current).toHaveAttribute('href', './blueprint.html');
  });
});
