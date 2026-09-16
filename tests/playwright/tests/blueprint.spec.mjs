// End-to-end checks for docs/blueprint.html — the composer on-ramp.
// Verifies the picker loads the static library, filtering works, and a
// selected/described process yields a derived arc + copy-paste prompts.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const BLUEPRINT = '/blueprint.html';

test.describe('blueprint composer (blueprint.html)', () => {
  test('renders hero + title', async ({ page }) => {
    await page.goto(BLUEPRINT);
    await expect(page).toHaveTitle(/Blueprint/);
    await expect(page.getByRole('heading', { level: 1 })).toContainText(/process.*starter/i);
  });

  test('puts the working composer before the library and bounds its promise', async ({ page }) => {
    const mobile = page.viewportSize().width <= 600;
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BLUEPRINT);
    await expect(page.locator('body')).toHaveClass(/chapter-experience/);
    const intro = page.locator('[data-chapter-intro]');
    await expect(intro).toBeVisible();
    expect((await intro.innerText()).trim().split(/\s+/).length).toBeLessThanOrEqual(60);
    const composer = page.locator('#bp-custom[data-visual-anchor]');
    await expect(composer).toBeVisible();
    expect((await composer.boundingBox()).y).toBeLessThanOrEqual(mobile ? 600 : 760);
    const submit = await composer.locator('button[type="submit"]').boundingBox();
    expect(submit.y + submit.height).toBeLessThanOrEqual(page.viewportSize().height);
    expect((await composer.boundingBox()).y).toBeLessThan((await page.locator('#bp-grid').boundingBox()).y);
    await expect(page.locator('.chapter-hero .stat-strip')).toHaveCount(0);
    await expect(page.locator('#bp-handoff')).toContainText(/manual.*live/i);
    await expect(page.locator('main')).not.toContainText(/exact build|you run nothing|only thing you type/i);
    await expect(page.locator('header nav.nav a[href="./funnel.html"]')).toHaveAttribute('aria-current', 'location');
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

  test('maps signals to a starter and a separate later handoff without hiding the tool', async ({ page }) => {
    const mobile = page.viewportSize().width <= 600;
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BLUEPRINT + '#how');
    const map = page.locator('figure[data-starter-map]');
    await expect(map).toBeVisible();
    await expect(map.locator('figcaption')).toContainText(/illustrative.*not.*execution/i);
    await expect(map.locator('[data-starter-step]')).toHaveCount(3);
    await expect(map.locator('[data-starter-step="signals"]')).toContainText(/domain.*complexity/i);
    await expect(map.locator('[data-starter-step="starter"]')).toContainText(/deterministic.*prompt/is);
    await expect(map.locator('[data-starter-step="handoff"]')).toContainText(/manual.*live/is);
    await expect(map).toContainText(/no model call.*no deployment/is);
    await expect(page.locator('.bp-method')).toHaveCount(0);
    const boxes = await map.locator('[data-starter-step]').evaluateAll(els =>
      els.map(el => { const { x, y, width, height } = el.getBoundingClientRect(); return { x, y, width, height }; }));
    if (mobile) expect(boxes[1].y).toBeGreaterThanOrEqual(boxes[0].y + boxes[0].height);
    else expect(boxes[1].x).toBeGreaterThanOrEqual(boxes[0].x + boxes[0].width);
    expect(await map.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await map.getByRole('link', { name: /readiness contract/i }).focus();
    await expect(map.getByRole('link', { name: /readiness contract/i })).toBeFocused();
    const accessibility = await new AxeBuilder({ page }).include('[data-starter-map]').analyze();
    expect(accessibility.violations).toEqual([]);
  });

  test('filtering by domain narrows the grid; reset restores it', async ({ page }) => {
    await page.goto(BLUEPRINT);
    await page.locator('#bp-grid .bp-card').first().waitFor();
    await page.selectOption('#bp-domain', 'healthcare');
    await expect(page.locator('#bp-count')).toContainText(/10 of 89/);
    await page.click('#bp-reset');
    await expect(page.locator('#bp-count')).toContainText(/89 of 89/);
  });

  test('selecting a scenario derives an arc and prompt with a conditional execution plan', async ({ page }) => {
    await page.goto(BLUEPRINT);
    await page.locator('#bp-grid .bp-card').first().click();
    const result = page.locator('#bp-result');
    await expect(result).toBeVisible();
    await expect(page.locator('#bp-arc .bp-skill').first()).toContainText('threadlight-design');
    await expect(page.locator('#bp-prompt')).toContainText('threadlight-auto');
    await page.locator('#bp-execution summary').click();
    // Generated instructions remain intact; the page labels prerequisites separately.
    await expect(page.locator('#bp-auto')).toContainText('CI/CD');
    await expect(page.locator('#bp-auto')).not.toContainText('azd up');
    await expect(page.locator('#bp-handoff')).toContainText(/not.*production/i);
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

  test('nav exposes Build as the Blueprint parent', async ({ page }) => {
    await page.goto(BLUEPRINT);
    const current = page.locator('header.masthead nav.nav a[aria-current="location"]');
    await expect(current).toHaveAttribute('href', './funnel.html');
  });

  test('without JavaScript provides a route to the source instructions', async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    await page.goto(BLUEPRINT);
    await expect(page.locator('noscript p')).toBeVisible();
    await expect(page.locator('noscript p')).toContainText(/JavaScript/i);
    await expect(page.locator('noscript a')).toHaveAttribute('href', /THREADLIGHT\.md/);
    await expect(page.locator('figure[data-starter-map]')).toBeVisible();
    await context.close();
  });
});
