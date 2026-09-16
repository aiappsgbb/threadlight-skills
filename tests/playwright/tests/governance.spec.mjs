import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const url = '/governance.html';

test('governance chapter explains the boundary before the acronyms', async ({ page }) => {
  await page.goto(url);
  await expect(page).toHaveTitle(/Governance & AgentOps/);
  await expect(page.locator('h1')).toContainText('Wrong action?');
  await expect(page.locator('#chapter-top')).toContainText('per selected binding');
  await expect(page.locator('#agentops [data-release="preview"]')).toContainText('PR #128');
  await expect(page.locator('#agentops')).toContainText('not Azure attestation');
  await expect(page.locator('header nav.nav a[aria-current="location"]')).toHaveAttribute('href', './production.html');
});

test('hero control changes the concrete request outcome without leaving the page', async ({ page }) => {
  await page.goto(url);
  await page.locator('[data-hero-decision]').selectOption('missing');
  await expect(page.locator('[data-hero-outcome]')).toHaveText('Stop before the effect.');
  await page.locator('[data-hero-decision]').selectOption('drift');
  await expect(page.locator('[data-hero-outcome]')).toHaveText('Collect new evidence.');
  await page.locator('[data-hero-decision]').selectOption('allowed');
  await expect(page.locator('[data-hero-outcome]')).toHaveText('Proceed on this path.');
  await expect(page).toHaveURL(/governance\.html$/);
});

test('decision examples are keyboard-operable illustrations, not live proof', async ({ page }) => {
  await page.goto(url + '#before-action');
  const missing = page.locator('#case-missing');
  const summary = missing.locator('summary');
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(missing).toHaveAttribute('open', '');
  await expect(missing.locator('.decision-result')).toContainText('Stop before the effect');
  await expect(page.locator('#before-action')).toContainText('Illustration only');
  await page.keyboard.press('Enter');
  await expect(missing).not.toHaveAttribute('open', '');
  await page.locator('#case-drift summary').click();
  await expect(page.locator('#case-drift .decision-result')).toContainText('Collect new evidence');
});

test('decision theatre follows the selected scenario and clears a closed case', async ({ page }) => {
  await page.goto(url + '#before-action');
  const monitor = page.locator('.decision-monitor');
  await expect(monitor).toHaveAttribute('data-state', 'allowed');
  await page.locator('#case-missing summary').click();
  await expect(monitor).toHaveAttribute('data-state', 'missing');
  await expect(monitor.locator('[data-monitor-title]')).toHaveText('Stop before the effect.');
  await expect(monitor.locator('[data-check="approval"]')).toHaveText('Missing');
  await expect(page.locator('#case-allowed')).not.toHaveAttribute('open', '');
  await page.locator('#case-drift summary').click();
  await expect(monitor).toHaveAttribute('data-state', 'drift');
  await expect(monitor.locator('[data-monitor-title]')).toHaveText('Collect new evidence.');
  await page.locator('#case-drift summary').click();
  await expect(monitor).toHaveAttribute('data-state', 'idle');
  await expect(monitor.locator('[data-monitor-title]')).toHaveText('Choose a scenario.');
});

test('top chapter index stays reachable without obscuring the mobile result', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url);
  const dock = page.locator('.floating-toc');
  await expect(dock).toBeVisible();
  await expect(dock.locator('.cx-chapter-links a')).toHaveCount(4);
  await dock.locator('.cx-chapter-links a[href="#agentops"]').click();
  await expect(page).toHaveURL(/#agentops$/);
  await expect(dock.locator('.cx-chapter-links a[href="#agentops"]')).toHaveAttribute('aria-current', 'location');
  const box = await dock.boundingBox();
  expect(box.y + box.height).toBeLessThanOrEqual(844);
  await dock.locator('.cx-chapter-index summary').click();
  await dock.locator('.cx-chapter-index a[href="#before-action"]').focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#before-action$/);
  await expect(page.locator('#before-action h2')).toBeInViewport();
  await page.locator('#case-missing summary').click();
  const result = page.locator('#case-missing .decision-result');
  await result.scrollIntoViewIfNeeded();
  const resultBox = await result.boundingBox();
  const indexBox = await dock.boundingBox();
  expect(resultBox.y).toBeGreaterThanOrEqual(indexBox.y + indexBox.height);
});

test('chapter remains usable with no JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  try {
    await page.goto(url);
    await expect(page.locator('h1')).toBeVisible();
    await page.locator('#case-missing summary').click();
    await expect(page.locator('#case-missing .decision-result')).toBeVisible();
    await expect(page.locator('.decision-monitor')).toBeHidden();
    await expect(page.locator('.floating-toc')).toBeVisible();
    await expect(page.locator('#agentops')).toContainText('Preview');
  } finally {
    await context.close();
  }
});

for (const width of [1440, 1024, 390]) {
  test(`chapter visual review at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(url);
    await expect(page.locator('h1')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const clipped = await page.locator('main').evaluate((main) =>
      [...main.querySelectorAll('h1, h2, h3, p, summary, li, dt, dd, a')].filter((element) => {
        const box = element.getBoundingClientRect();
        return box.width > 0 && (box.left < -1 || box.right > innerWidth + 1);
      }).map((element) => element.textContent.trim()),
    );
    expect(clipped).toEqual([]);
    for (const control of await page.locator('main summary:visible, main .btn:visible, .cx-chapter-links a').all()) {
      const box = await control.boundingBox();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    const a11y = await new AxeBuilder({ page }).analyze();
    expect(a11y.violations.filter(({ impact }) => impact === 'serious' || impact === 'critical')).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`governance-${width}.png`), fullPage: true });
    await page.screenshot({ path: testInfo.outputPath(`governance-hero-${width}.png`) });
  });
}

test('chapter reflows at 200 percent CSS zoom without hiding content', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto(url);
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('#case-drift summary').click();
  await expect(page.locator('#case-drift .decision-result')).toBeVisible();
  await page.locator('#agentops').scrollIntoViewIfNeeded();
  await expect(page.locator('#agentops h2')).toBeVisible();
});

test('evidence levels show their mechanism and keep technical artifacts optional', async ({ page }) => {
  await page.goto(url + '#evidence');
  await expect(page.locator('.evidence-ladder .evidence-mechanism')).toHaveCount(3);
  for (const mechanism of await page.locator('.evidence-mechanism').all()) {
    await expect(mechanism.locator('svg')).toHaveCount(1);
    await expect(mechanism).toBeVisible();
  }
  await expect(page.locator('#evidence-artifacts')).not.toHaveAttribute('open', '');
  await page.locator('#evidence-artifacts > summary').click();
  await expect(page.locator('#evidence-artifacts')).toContainText('.threadlight/governance-live.json');
  await expect(page.locator('#evidence')).toContainText('governance_probe_noop');
});

for (const source of ['/production.html', '/funnel.html', '/self-improving.html']) {
  test(`${source} leads into the advanced story without footer discovery`, async ({ page }) => {
    await page.goto(source);
    const link = page.locator('main a[href^="./governance.html"]').first();
    await link.click();
    await expect(page).toHaveURL(/governance\.html(?:#agentops)?$/);
    await expect(page.locator('#agentops')).toContainText('Preview');
  });
}
