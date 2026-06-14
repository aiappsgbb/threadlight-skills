#!/usr/bin/env node
// Convert the four OG SVG covers into 1200x630 PNGs via Playwright.
// Usage:
//   1) start the local site: python3 -m http.server 4173 --bind 127.0.0.1 --directory docs
//   2) node tests/playwright/og-build.mjs
//
// Outputs: docs/assets/og/og-{home,funnel,production,industries}.png

import { chromium } from '@playwright/test';
import { resolve } from 'node:path';

const BASE = process.env.OG_BASE || 'http://127.0.0.1:4173';
const PAGES = [
  { slug: 'home',        svg: '/assets/og/og-home.svg' },
  { slug: 'funnel',      svg: '/assets/og/og-funnel.svg' },
  { slug: 'production',  svg: '/assets/og/og-production.svg' },
  { slug: 'industries',  svg: '/assets/og/og-industries.svg' },
];

const browser = await chromium.launch();

for (const { slug, svg } of PAGES) {
  // Fresh context per shot to bypass image caching.
  const ctx = await browser.newContext({
    viewport: { width: 1200, height: 630 },
    deviceScaleFactor: 1,
  });
  const page = await ctx.newPage();
  // Cache-bust each request to ensure the right SVG is fetched.
  const cacheBust = Date.now();
  await page.goto(`${BASE}${svg}?v=${cacheBust}`, { waitUntil: 'networkidle' });
  // Force inline SVG to render at exactly 1200x630.
  await page.evaluate(() => {
    const svg = document.documentElement;
    svg.setAttribute('width', '1200');
    svg.setAttribute('height', '630');
    document.body && (document.body.style.margin = '0');
  });
  await page.waitForTimeout(200);
  const out = resolve('docs/assets/og', `og-${slug}.png`);
  await page.screenshot({
    path: out,
    type: 'png',
    clip: { x: 0, y: 0, width: 1200, height: 630 },
  });
  console.log(`wrote ${out}`);
  await ctx.close();
}

await browser.close();


