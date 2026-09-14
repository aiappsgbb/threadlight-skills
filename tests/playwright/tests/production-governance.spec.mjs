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
  await expect(authority.getByRole('heading', { level: 2 })).toContainText('The model proposes');
  await expect(authority.getByRole('list', { name: 'Selected effect authorization sequence' }).locator(':scope > li')).toHaveCount(6);
  await expect(evidence.locator('#s3-business-evidence')).toContainText('HISTORICAL');
  await expect(evidence.locator('#s2-basic-evidence')).toContainText('Billing Issue');
  const privateEvidence = evidence.locator('#private-governed-evidence');
  await expect(privateEvidence).toContainText('PRIVATE SELECTED ALLOW + EXACT DENY VERIFIED');
  await expect(privateEvidence).toContainText('No new pending intent was created');
  await expect(privateEvidence).toContainText('NOT PROVED');
  await expect(privateEvidence).toContainText('4/4');
  await expect(privateEvidence).toContainText('created and read back');
  await expect(privateEvidence).toContainText('separate from the before-return read ACKs');
  await expect(evidence).toContainText('EXPIRED');
  for (const section of [authority, evidence]) {
    await section.scrollIntoViewIfNeeded();
    await expect(section).toBeVisible();
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
    await expect(page.locator('#evidence-boundaries')).toBeVisible();
    await expect(page.locator('#effect-authority ol > li')).toHaveCount(6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
  } finally {
    await context.close();
  }
});
