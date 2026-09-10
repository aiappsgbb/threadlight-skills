import { test, expect } from '@playwright/test';

async function expectReachableSummary(page, id) {
  const details = page.locator(`#${id}`);
  await expect(details).toHaveAttribute('open', '');
  const summary = details.locator(':scope > summary');
  await expect.poll(async () => {
    const box = await summary.boundingBox();
    const header = await page.locator('.masthead').boundingBox();
    const index = await page.locator('.floating-toc').boundingBox();
    return box.y - Math.max(header.y + header.height, index.y + index.height);
  }).toBeGreaterThanOrEqual(8);
  expect(await summary.evaluate(element => {
    const box = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
  })).toBe(true);
  await summary.click();
  await expect(details).not.toHaveAttribute('open', '');
}

for (const [slug, id] of [
  ['customize', 'handoff-steps'],
  ['self-improving', 'upgrade-files'],
  ['workbook', 'live-invocation'],
]) {
  test(`${slug}: clicked and direct disclosure links clear the sticky controls`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`/${slug}.html`);
    await page.locator(`main a[href="#${id}"]`).first().click();
    await expectReachableSummary(page, id);
    await page.goto('about:blank');
    await page.goto(`/${slug}.html#${id}`);
    await expectReachableSummary(page, id);
  });
}

test.describe('unenhanced chapter navigation', () => {
  test.use({ javaScriptEnabled: false });
  for (const [slug, hash] of [['governance', '#evidence'], ['basics', '#skills']]) {
    test(`${slug}: index links and section menu really receive pointer input`, async ({ page }) => {
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 844 });
        await page.goto(`/${slug}.html`);
        const index = page.locator('.floating-toc');
        await expect(index).toHaveCSS('pointer-events', 'auto');
        await expect(index).toHaveCSS('position', 'relative');
        await index.locator('summary').click();
        await expect(index.locator('details')).toHaveAttribute('open', '');
        const lastHash = await index.locator('details a').last().getAttribute('href');
        await index.locator('details a').last().click();
        await expect(page).toHaveURL(new RegExp(`${lastHash}$`));
        await page.goto('about:blank');
        await page.goto(`/${slug}.html`);
        await index.locator(`.cx-chapter-links a[href="${hash}"]`).click();
        await expect(page).toHaveURL(new RegExp(`${hash}$`));
        await expect(page.locator(`${hash} h2`).first()).toBeInViewport();
      }
    });
  }
});
