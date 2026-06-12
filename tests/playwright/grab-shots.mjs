import { chromium } from '@playwright/test';
const url = 'http://127.0.0.1:4173/index.html';
const targets = [
  { name: 'evo',        anchor: '.evolution-band' },
  { name: 'funnel',     anchor: '#scene-funnel' },
  { name: 'industries', anchor: '#scene-industries' },
  { name: 'channels',   anchor: '#scene-channels' }
];
const viewports = [
  { name: 'desktop', w: 1440, h: 900, scheme: 'dark' },
  { name: 'light',   w: 1440, h: 900, scheme: 'light' },
  { name: 'mobile',  w: 390,  h: 844, scheme: 'dark' }
];
const browser = await chromium.launch();
for (const vp of viewports) {
  const ctx = await browser.newContext({
    viewport: { width: vp.w, height: vp.h },
    deviceScaleFactor: 2,
    colorScheme: vp.scheme,
    reducedMotion: 'reduce'
  });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  for (const t of targets) {
    const el = page.locator(t.anchor).first();
    await el.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await el.screenshot({ path: `screenshots/${vp.name}-${t.name}.png` });
  }
  await ctx.close();
}
await browser.close();
console.log('done');
