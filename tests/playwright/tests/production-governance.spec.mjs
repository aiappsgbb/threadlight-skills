import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

async function capture(locator, name, testInfo) {
  const directory = process.env.THREADLIGHT_SCREENSHOT_DIR;
  if (!directory) return;
  if (!path.isAbsolute(directory)) throw new Error('Screenshot artifacts need an absolute external directory');
  await mkdir(directory, { recursive: true });
  await locator.screenshot({
    path: path.join(directory, `${testInfo.project.name}-${name}.png`),
    animations: 'disabled',
  });
}

for (const name of ['index', 'funnel', 'production']) {
  test(`${name}: scoped copy, responsive chapter navigation and no overflow`, async ({ page }, testInfo) => {
    await page.goto(`/${name}.html`);
    await expect(page.locator('header.masthead .brand-name')).toContainText('Threadlight');
    await expect(page.locator('header nav.nav a')).toHaveCount(5);
    const toggle = page.locator('[data-mobile-nav-toggle]');
    if (await toggle.isVisible()) {
      await toggle.click();
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    }
    await page.locator('header nav.nav a[href="./production.html"]').click();
    await expect(page).toHaveURL(/production\.html$/);
    await expect(page.locator('header nav.nav a[aria-current="page"]')).toContainText('Production-ready');
    await page.goto(`/${name}.html`);
    await expect(page.locator('main')).not.toContainText(/Foundry runs & governs it|ship with (?:two|2) waivers|ready in ~7m|92[–/]100/);
    const overflowing = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    expect(overflowing, `${name} horizontal overflow`).toBe(false);
    await capture(page.locator('body'), name, testInfo);
  });
}

test('authority and evidence are named, readable without a diagram runtime and keyboard-linked', async ({ page }, testInfo) => {
  await page.goto('/production.html');
  const authority = page.locator('#effect-authority');
  const evidence = page.locator('#evidence-boundaries');
  await expect(authority.getByRole('heading', { level: 2 })).toContainText('Access to a system');
  await expect(authority.getByRole('list', { name: 'Selected effect authorization sequence' }).locator(':scope > li')).toHaveCount(6);
  if (page.viewportSize().width <= 680) {
    await expect(authority.locator('.authority-mobile')).toBeVisible();
    await expect(authority.locator('.authority-mobile')).toContainText('Outlook');
    await expect(authority.locator('.authority-map')).toBeHidden();
  } else {
    await expect(authority.getByRole('img', { name: 'Propose, authorize, execute: the controlled action path' })).toBeVisible();
  }
  await expect(authority.getByRole('link', { name: /Read the architecture/ })).toHaveAttribute('href', /agent-governance-deep-dive\.md$/);
  await expect(evidence.locator('#workflow-design-value')).toContainText('Make autonomy useful');
  await expect(evidence.locator('#human-decision-value')).toContainText('Human judgement, connected');
  await expect(evidence.locator('#operating-evidence-value')).toContainText('Connect actions to outcomes');
  await expect(evidence).not.toContainText(/HISTORICAL|NOT PROVED|EXPIRED|4\/4|2026-09-/);
  await expect(evidence.locator('a[href*="governed-returns-validation.md"]')).toBeVisible();
  for (const section of [authority, evidence]) {
    await section.scrollIntoViewIfNeeded();
    await expect(section).toBeVisible();
    await expect(page.locator('.floating-toc')).toHaveCSS('opacity', '1');
    const violations = (await new AxeBuilder({ page }).include(`#${await section.getAttribute('id')}`)
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations).toEqual([]);
  }
  const next = authority.getByRole('link', { name: 'Inspect the evidence boundaries' });
  await next.focus();
  await expect(next).toBeFocused();
  await next.press('Enter');
  // Existing shared smooth-scroll deliberately preserves the URL fragment.
  await expect.poll(() => evidence.evaluate((element) => {
    const top = element.getBoundingClientRect().top;
    return top >= 0 && top < 150;
  })).toBe(true);
  await capture(authority, 'effect-authority', testInfo);
  await capture(evidence, 'evidence-boundaries', testInfo);
});

test('new authority and evidence remain readable when JavaScript is disabled', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    javaScriptEnabled: false, viewport: testInfo.project.use.viewport, baseURL: testInfo.project.use.baseURL,
  });
  const page = await context.newPage();
  try {
    await page.goto('/production.html');
    await expect(page.locator('#effect-authority')).toBeVisible();
    await expect(page.locator('#production-domains .production-domain')).toHaveCount(6);
    await expect(page.locator('#evidence-boundaries')).toBeVisible();
    await expect(page.getByRole('list', { name: 'Selected effect authorization sequence' }).locator(':scope > li')).toHaveCount(6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});

test('production responsibilities are distinct, navigable and accessible before the action deep dive', async ({ page }, testInfo) => {
  await page.goto('/production.html');
  const domains = page.locator('#production-domains');
  await expect(page.locator('main > section').first()).toHaveAttribute('id', 'chapter-top');
  await expect(domains.locator('.production-domain')).toHaveCount(6);
  const links = domains.getByRole('navigation', { name: 'Production responsibilities' }).getByRole('link');
  await expect(links).toHaveCount(6);
  for (const link of await links.all()) {
    const target = page.locator(await link.getAttribute('href'));
    await expect(target).toHaveCount(1);
    const heading = target.getByRole('heading').first();
    await link.focus();
    await link.press('Enter');
    await expect.poll(() => heading.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return hit !== null && element.contains(hit);
    })).toBe(true);
  }
  const order = await page.evaluate(() => {
    const domains = document.querySelector('#production-domains');
    return Boolean(domains.compareDocumentPosition(document.querySelector('#effect-authority'))
      & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(order).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  const violations = (await new AxeBuilder({ page }).include('#production-domains')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
  expect(violations).toEqual([]);
  await capture(domains, 'production-responsibilities', testInfo);
});
