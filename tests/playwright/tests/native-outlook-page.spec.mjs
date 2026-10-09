import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

for (const theme of ['light', 'dark']) {
  test(`native Outlook journey is readable and accessible in ${theme} theme`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await page.goto('/production.html#workflow-in-action');
    await expect(page.locator('html')).not.toHaveAttribute('data-theme');
    const journey = page.locator('#effect-authority');
    await expect(journey).toContainText('Human reviewer');
    await expect(journey).toContainText('Outlook');
    await expect(journey).toContainText('one-use');
    await journey.locator('[data-action-implementation] > summary').click();
    await expect(journey.getByRole('list', { name: 'Primary business-action path' }).locator(':scope > li')).toHaveCount(3);
    await expect(journey.getByRole('list', { name: 'Supporting authority services' }).locator(':scope > li')).toHaveCount(2);
    await journey.getByRole('tab', { name: 'Human review', exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
    const violations = (await new AxeBuilder({ page }).include('#effect-authority')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
    expect(violations).toEqual([]);
    const target = page.locator('#evidence-boundaries');
    await page.goto('/production.html#evidence-boundaries');
    await expect.poll(() => target.evaluate(el => {
      const top = el.getBoundingClientRect().top;
      return top >= document.querySelector('.floating-toc').getBoundingClientRect().bottom - 1 && top < innerHeight;
    })).toBe(true);
    const guide = target.getByRole('link', { name: 'Follow the existing-pilot guide', exact: true });
    await guide.focus();
    await expect(guide).toBeFocused();
  });
}
