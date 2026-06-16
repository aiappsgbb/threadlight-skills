import { chromium } from '@playwright/test';

const pages = [
  {
    url:     'http://127.0.0.1:4173/index.html',
    prefix:  'landing',
    targets: [
      { name: 'hero',       anchor: '#scene-hero' },
      { name: 'show',       anchor: '#scene-show' },
      { name: 'funnel',     anchor: '#scene-funnel' },
      { name: 'posters',    anchor: '#scene-posters' },
      { name: 'industries', anchor: '#scene-industries' },
      { name: 'channels',   anchor: '#scene-channels' }
    ]
  },
  {
    url:     'http://127.0.0.1:4173/funnel.html',
    prefix:  'funnel',
    targets: [
      { name: 'hero',         anchor: '.chapter-hero' },
      { name: 'ladder',       anchor: '#stage-ladder' },
      { name: 'show',         anchor: '#stage-show' },
      { name: 'glance',       anchor: '#stage-glance' },
      { name: 'recap',        anchor: '#stage-recap' }
    ]
  },
  {
    url:     'http://127.0.0.1:4173/industries.html',
    prefix:  'industries',
    targets: [
      { name: 'hero',       anchor: '.chapter-hero' },
      { name: 'grid',       anchor: '#sector-grid' },
      { name: 'spec',       anchor: '#ind-spec' },
      { name: 'recap',      anchor: '#industry-recap' }
    ]
  },
  {
    url:     'http://127.0.0.1:4173/production.html',
    prefix:  'production',
    targets: [
      { name: 'hero',          anchor: '.chapter-hero' },
      { name: 'triggers',      anchor: '#governance-triggers' },
      { name: 'postures',      anchor: '#posture-overview' },
      { name: 'scorecard',     anchor: '#prod-scorecard' },
      { name: 'recap',         anchor: '#chapter-recap' }
    ]
  }
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
  for (const pg of pages) {
    await page.goto(pg.url, { waitUntil: 'networkidle' });
    await page.waitForTimeout(800);
    for (const t of pg.targets) {
      const el = page.locator(t.anchor).first();
      await el.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      await el.screenshot({ path: `screenshots/${vp.name}-${pg.prefix}-${t.name}.png` });
    }
  }
  await ctx.close();
}
await browser.close();
console.log('done');
