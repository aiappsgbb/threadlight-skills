// End-to-end checks for the Threadlight GitHub Pages site (docs/).
//
// This suite tracks the CURRENT site contract, after a large restructure:
//   - docs/index.html          — the scrubbable DEMO landing page.
//   - docs/basics.html         — skills, construction agent and process agent.
//   - docs/funnel.html         — Build: the six-phase lifecycle narrative.
//   - docs/production.html     — the 13-pillar production-ready chapter.
//   - docs/industries.html     — the process library (89 processes / 15 industries).
//   - docs/self-improving.html — the learning-CI chapter.
//   - blueprint / case-study / customize / workbook round out the chapters.
//
// Deliberate boundaries (no duplication):
//   - The five NEW-skill surfaces (threadlight-qualify + Cowork zip, the
//     connect / ground / loadtest evidence progression, and the plan-only
//     threadlight-upgrade lifecycle scan) are owned by gap-closure.spec.mjs.
//   - The "how it works" primer internals are owned by how-it-works.spec.mjs.
// Both are covered there and are intentionally not re-asserted in this file.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const LANDING = '/index.html';

// Every page that carries the shared masthead + chapter nav.
const CHAPTER_PAGES = [
  '/index.html',
  '/basics.html',
  '/funnel.html',
  '/production.html',
  '/industries.html',
  '/self-improving.html',
  '/blueprint.html',
  '/case-study.html',
  '/customize.html',
  '/workbook.html',
  '/governance.html',
];

// The top nav is chapter pages only — these five, in this shape.
const NAV_TARGET_RE = /^(\.\/)?(index|basics|funnel|case-study|production)\.html$/;
const NAV_LABELS = ['Home', 'Basics', 'Build', 'Case study', 'Production'];

// Turn an absolute production OG url into a path the local static server can
// serve (docs/ is the web root; production serves under a /threadlight-skills base).
function toLocalAssetPath(absUrl) {
  return new URL(absUrl).pathname.replace(/^\/threadlight-skills/, '');
}

test.describe('landing page — the scrubbable demo (index.html)', () => {
  test('renders the demo hero, the Threadlight brand, and the 24-skill public count', async ({ page }) => {
    await page.goto(LANDING);
    await expect(page).toHaveTitle(/working pilot/i);
    await expect(page.locator('header.masthead .brand-name')).toContainText(/Threadlight/);
    const hero = page.locator('#demo-h');
    await expect(hero).toBeVisible();
    await expect(hero).toContainText(/working pilot/i);
    await expect(hero).not.toContainText(/governed agent/i);
    // The public library is exactly 24 skills — stated in the primer.
    await expect(page.locator('#how-it-works')).toContainText(/24\s+skills/i);
  });

  test('footer is public-safe (open guidance, no "internal use" leak)', async ({ page }) => {
    await page.goto(LANDING);
    // The page footer is the direct child of <body>; a hidden .sc-footer also
    // lives inside a demo-reel beat, so scope to the real one.
    const footer = page.locator('body > footer');
    await expect(footer).toBeVisible();
    await expect(footer).toContainText(/open guidance/i);
    await expect(footer).not.toContainText(/internal use/i);
  });

  test('the reel enhances and its cover fires on click (basic visual interaction)', async ({ page }) => {
    await page.goto(LANDING);
    const reel = page.locator('#reel');
    // demo-reel.js upgrades the static storyboard into the interactive reel.
    await expect(reel).toHaveClass(/is-enhanced/);
    // Seven scrubbable beats on the rail.
    await expect(reel.locator('.beat-rail .beat-chip')).toHaveCount(7);
    const cover = reel.locator('[data-reel="cover"]');
    await expect(cover).toBeVisible();
    // Firing the poster starts the experience and dismisses the cover.
    await reel.locator('[data-reel="start"]').click();
    await expect(reel).toHaveClass(/is-started/);
    await expect(cover).toBeHidden();
  });

  test('favicon is an inline SVG and the social meta tags are present', async ({ page }) => {
    await page.goto(LANDING);
    const icon = await page.locator('link[rel="icon"]').getAttribute('href');
    expect(icon).toMatch(/^data:image\/svg\+xml/);
    await expect(page.locator('meta[name="description"]')).toHaveAttribute(
      'content',
      'An evidence-backed reel of the Threadlight pipeline: one paragraph types in, the agent is specced, validated on your PC, and shown through captured deployment proof — play, pause, scrub, replay, then read the real case study.',
    );
    await expect(page.locator('meta[property="og:title"]')).toHaveCount(1);
    await expect(page.locator('meta[property="og:description"]')).toHaveAttribute(
      'content',
      'An evidence-backed reel: paragraph in → specced, validated on your PC, and shown through captured deployment proof. Play, pause, scrub, replay — then open the real case study for the live run.',
    );
    await expect(page.locator('meta[name="twitter:card"]')).toHaveCount(1);
  });

  test('puts value contract and evidence wording boundaries on the primary pages journey', async ({ page }) => {
    await page.goto(LANDING);
    await expect(page.locator('.home-context')).not.toHaveAttribute('open', '');
    await page.locator('.home-context > summary').click();
    await expect(page.locator('#how-it-works')).toContainText(/working pilot with selected runtime governance/i);
    await expect(page.locator('.demo-sub')).toContainText(/curated demo path|evidence-backed recreation/i);
    await expect(page.locator('main')).not.toContainText(/one continuous run/i);

    const valueEvidence = page.locator('#value-evidence');
    await expect(valueEvidence).toContainText(/SPEC\s*(§|section)\s*14/i);
    await expect(valueEvidence).toContainText(/settled Azure actuals/i);
    await expect(valueEvidence).toContainText(/cost per successful interaction/i);
    await expect(valueEvidence.locator('a[href="./self-improving.html#how"]')).toBeVisible();

    const reel = page.locator('section[aria-label="Threadlight pipeline demo reel"]');
    const recap = page.locator('section.recap');
    await expect(reel).toContainText(/evidence-backed recreation/i);
    await expect(reel).toContainText(/captured live-run proof/i);
    await expect(reel).toContainText(/captured proof/i);
    await expect(reel).toContainText(/auditable artifact|auditable artefact/i);
    await expect(reel).toContainText(/recreated journey/i);
    await expect(reel).not.toContainText(/six-skill pipeline/i);
    await expect(reel).not.toContainText(/live in your tenant/i);
    await expect(reel).not.toContainText(/live in your own tenant/i);
    await expect(reel).not.toContainText(/on your Azure/i);
    await expect(reel).not.toContainText(/nothing mocked/i);
    await expect(recap).toContainText(/curated Threadlight demo path/i);
    await expect(recap).not.toContainText(/six-skill pipeline/i);
    await expect(recap).not.toContainText(/live in your tenant/i);
    await expect(recap).not.toContainText(/live in your own tenant/i);
    await expect(recap.locator('.recap-flow')).toHaveAttribute('aria-label', 'Curated Threadlight demo path');
    await expect(reel.locator('a[href="./case-study.html#proof"]')).toBeVisible();
  });

  test('Beat 4 uses captured-deployment wording instead of live-tenant claims', async ({ page }) => {
    await page.goto(LANDING);
    const beat4 = page.locator('article.beat[data-beat="4"]');
    await expect(beat4.locator('.art')).toHaveAttribute(
      'aria-label',
      'Captured deployment snapshot of the credit-memo agent',
    );
    await expect(beat4.locator('.beat-title')).toHaveText('Captured deployment proof.');
    await expect(beat4.locator('.art-head b')).toHaveText('credit-memo · captured proof');
    await expect(beat4.locator('img[alt]')).toHaveAttribute(
      'alt',
      /captured proof|captured deployment snapshot/i,
    );
    await expect(beat4).toContainText(/captured deployment proof/i);
    await expect(beat4).not.toContainText(/deployed to your Azure/i);
    await expect(beat4).not.toContainText(/live credit-memo agent in your Azure tenant/i);
    await expect(beat4).not.toContainText(/credit-memo · live/i);
    await expect(beat4).not.toContainText(/running live/i);
  });
});

test.describe('primary navigation — shared across every chapter page', () => {
  for (const url of CHAPTER_PAGES) {
    test(`${url} carries the masthead + the five chapter links`, async ({ page }) => {
      await page.goto(url);
      const nav = page.locator('header.masthead nav.nav');
      await expect(nav).toHaveCount(1);
      // The brand always links home.
      await expect(page.locator('header.masthead .brand a[href="./index.html"]')).toHaveCount(1);

      const links = nav.locator('a');
      const hrefs = await links.evaluateAll((els) => els.map((e) => e.getAttribute('href') || ''));
      expect(hrefs.length, `${url} nav link count (lower bound)`).toBeGreaterThanOrEqual(3);
      expect(hrefs.length, `${url} nav link count (upper bound)`).toBeLessThanOrEqual(6);
      for (const h of hrefs) {
        expect(h, `${url}: nav link "${h}" should be a chapter page`).toMatch(NAV_TARGET_RE);
      }
      // No duplicate nav targets (regression guard for double-linked chapters).
      expect(new Set(hrefs).size, `${url} nav has no duplicate targets`).toBe(hrefs.length);

      const labels = (await links.allTextContents()).map((t) => t.trim()).join(' | ');
      for (const want of NAV_LABELS) {
        expect(labels, `${url} nav labels include ${want}`).toContain(want);
      }
      await expect(nav.locator('a[href="./funnel.html"]')).toHaveText('Build');
    });
  }

  test('mobile chapter navigation toggles open and closed from the shared masthead control', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/production.html');

    const masthead = page.locator('header.masthead');
    const toggle = page.locator('[data-mobile-nav-toggle]');
    const nav = page.locator('header.masthead nav.nav');

    await expect(masthead).toHaveClass(/mobile-nav-enhanced/);
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(nav).toBeHidden();

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(nav).toBeVisible();

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(nav).toBeHidden();
  });

  test('no-JS mobile chapter navigation stays visible and usable', async ({ browser }) => {
    const context = await browser.newContext({
      javaScriptEnabled: false,
      viewport: { width: 390, height: 844 },
    });
    const page = await context.newPage();

    try {
      await page.goto('/production.html');

      const masthead = page.locator('header.masthead');
      const nav = masthead.locator('nav.nav');
      const links = nav.locator('a');

      await expect(masthead).not.toHaveClass(/mobile-nav-enhanced/);
      await expect(page.locator('[data-mobile-nav-toggle]')).toHaveCount(0);
      await expect(nav).toBeVisible();
      await expect(links).toHaveCount(5);
      await expect(links.first()).toBeVisible();

      await links.filter({ hasText: 'Build' }).click();
      await expect(page).toHaveURL(/funnel\.html$/);
    } finally {
      await context.close();
    }
  });
});

test.describe('public-safety audit — no internal-only phrasing leaks', () => {
  for (const url of CHAPTER_PAGES) {
    test(`${url} contains no leak terms`, async ({ page }) => {
      await page.goto(url);
      const body = (await page.locator('body').textContent()) || '';
      for (const re of [/internal use/i, /do not share/i, /microsoft only/i, /confidential/i]) {
        expect(body, `${url} should not contain ${re}`).not.toMatch(re);
      }
    });
  }
});

test.describe('Build chapter (funnel.html)', () => {
  test('hero leads with a bounded pilot example and aligned metadata', async ({ page }) => {
    await page.goto('/funnel.html');
    await expect(page).toHaveTitle(/^Build —/);
    for (const selector of ['meta[name="description"]', 'meta[property="og:description"]', 'meta[name="twitter:description"]']) {
      await expect(page.locator(selector)).toHaveAttribute('content', /working pilot.*evidence.*handoff/);
    }
    await expect(page.locator('[data-visual-anchor]')).toContainText('not a run receipt');
    await expect(page.locator('[data-execution-boundary]')).toContainText('Auto plans; the coding agent executes.');
  });

  test('existing chapter anchors remain valid', async ({ page }) => {
    await page.goto('/funnel.html');
    for (const id of [
      'scene-hero',
      'scene-show',
      'scene-funnel',
      'scene-chain',
      'scene-prod-ready',
      'scene-customize',
      'scene-industries',
      'scene-kratos',
      'scene-cta',
    ]) await expect(page.locator(`#${id}`)).toHaveCount(1);
  });

  test('six named phases lead to their own content', async ({ page }) => {
    await page.goto('/funnel.html');
    await expect(page.locator('#scene-funnel [data-phase-name]')).toHaveText(['Enter', 'Build', 'Integrate', 'Assure', 'Ship', 'Improve']);
    for (const href of await page.locator('#scene-funnel a').evaluateAll(els => els.map(e => e.getAttribute('href')))) {
      await expect(page.locator(href)).toHaveCount(1);
    }
  });

  test('adjacent paths retain their real destinations', async ({ page }) => {
    await page.goto('/funnel.html');
    const ch = page.locator('#scene-kratos');
    const live = ch.locator('a[href="https://aka.ms/kratos"]');
    await expect(live).toBeVisible();
    await expect(live).toHaveAttribute('target', '_blank');
    await expect(live).toHaveAttribute('rel', /noopener/);
    await expect(ch.locator('a[href="https://github.com/kmavrodis/kratos-agent"]')).toBeVisible();
    await expect(page.locator('#scene-industries a[href*="industries.html"]')).toBeVisible();
  });
});

test.describe('production chapter (production.html)', () => {
  test('hero: production-ready leads with scoped evidence and unresolved gaps', async ({ page }) => {
    await page.goto('/production.html');
    await expect(page).toHaveTitle(/Production-ready/i);
    await expect(page.locator('#chapter-top h1')).toContainText(/prove it can ship/i);
    await expect(page.locator('#chapter-top')).toContainText(/uplift\/handoff plan/i);
    await expect(page.locator('#chapter-top')).not.toContainText(/so the pilot ships/i);
    await expect(page.locator('#chapter-top')).not.toContainText(/Amber turns green/i);
    await expect(page.locator('#chapter-top .pr-hero-facts')).toContainText('13');
    await expect(page.locator('[data-visual-anchor]')).toContainText('BEFORE THE EFFECT');
    await expect(page.locator('[data-visual-anchor]')).toContainText('not a live assessment');
    await expect(page.locator('.pr-evidence-console')).toContainText('NOT VERIFIED');
  });

  test('the chapter sections are all present and name the thirteen pillars', async ({ page }) => {
    await page.goto('/production.html');
    for (const id of ['chapter-top', 'why', 'checks', 'legs', 'proof', 'target', 'ship', 'start', 'chapter-recap']) {
      await expect(page.locator('#' + id), `production section #${id}`).toHaveCount(1);
    }
    await expect(page.locator('#checks')).toContainText(/13 pillars|thirteen pillars/i);
  });

  test('the gap grid names nine gaps, each with a live repository skill link', async ({ page }) => {
    await page.goto('/production.html');
    const cards = page.locator('#legs .gap-card');
    await expect(cards).toHaveCount(9);
    const links = page.locator('#legs .gap-card a.gap-link');
    await expect(links).toHaveCount(9);
    const hrefs = await links.evaluateAll((els) => els.map((e) => e.getAttribute('href')));
    for (const h of hrefs) {
      expect(h, `gap link ${h} points at a real aiappsgbb skill`).toMatch(
        /github\.com\/aiappsgbb\/(threadlight-skills|awesome-gbb)\//,
      );
    }
    // The grid names the platform-capability skills. (connect / ground / loadtest
    // are covered by gap-closure.spec.mjs and are intentionally not re-asserted here.)
    const gridText = ((await page.locator('#legs').textContent()) || '').toLowerCase();
    for (const s of ['threadlight-govern', 'threadlight-evals', 'threadlight-redteam', 'threadlight-consumption-iq', 'threadlight-cicd']) {
      expect(gridText, `gap grid should name ${s}`).toContain(s);
    }
  });

  test('evidence wording: the proof section separates forecast, settled Azure actuals, and reconciliation', async ({ page }) => {
    await page.goto('/production.html');
    const proof = page.locator('#proof');
    await expect(proof).toContainText(/forecast/i);
    await expect(proof).toContainText(/Azure actuals/i);
    await expect(proof).toContainText(/reconcil/i);
    await expect(proof).toContainText(/target subscription/i);
    await expect(proof).toContainText(/resource group/i);
    await expect(proof).toContainText(/not-verified/i);
  });
});

test.describe('industries chapter (industries.html)', () => {
  test('hero, sections and the 89/15 catalogue remain discoverable', async ({ page }) => {
    await page.goto('/industries.html');
    await expect(page).toHaveTitle(/industries/i);
    await expect(page.locator('#chapter-top h1')).toContainText(/Loans.*Claims.*Operations/i);
    for (const id of ['chapter-top', 'library', 'ind-spec', 'industry-recap']) {
      await expect(page.locator('#' + id), `industries section #${id}`).toHaveCount(1);
    }
    const body = (await page.locator('main').textContent()) || '';
    expect(body, 'names the 89-process library').toMatch(/89\s+(shaped\s+)?(business\s+)?process/i);
    expect(body, 'names the 15-industry spread').toMatch(/15\s+industr/i);
    await expect(page.locator('[data-visual-anchor] #ind-search')).toBeVisible();
  });
});

test.describe('self-improving chapter (self-improving.html)', () => {
  test('title, headline, and the four numbered sections', async ({ page }) => {
    await page.goto('/self-improving.html');
    await expect(page).toHaveTitle(/Self-improving/i);
    await expect(page.locator('h1')).toContainText(
      /One finished run.*A sharper next step/i,
    );
    for (const id of ['how', 'caught', 'found', 'maintain']) {
      await expect(page.locator('#' + id), `self-improving section #${id}`).toHaveCount(1);
    }
  });

  test('evidence wording: the mechanism stays diagnostics-to-backlog and does not claim automatic remediation', async ({ page }) => {
    await page.goto('/self-improving.html');
    const how = page.locator('#how');
    const lede = (await page.locator('.lede').textContent()) || '';
    await expect(how).toContainText(/diagnostics-to-backlog/i);
    expect(lede).toContain('green or red');
    expect(lede).toMatch(/one run, not a baseline/);
    expect(lede).toContain('ranked fixes');
    expect(lede).toMatch(/maintainer reviews/);
    expect(lede).toContain('Nothing auto-applies');
    await expect(how).toContainText(
      /maintainers\s+may review candidate signatures and explicitly add rules\/tests/i,
    );
    await expect(how).toContainText(
      /only reviewed\s+rule\/test updates improve later classification/i,
    );
    await expect(how).not.toContainText(/learnings,\s+re-reading raw logs by hand is slow and noisy/i);
    await expect(how).not.toContainText(/taught itself/i);
    await expect(how).not.toContainText(/self-teaching/i);
    await expect(how).not.toContainText(/^it gets better$/i);
    await expect(how).not.toContainText(/automatic remediation/i);
  });
});

test.describe('case study chapter (case-study.html)', () => {
  test('evidence wording: the cost section stays a reviewed monthly projection, not a literal actuals claim', async ({ page }) => {
    await page.goto('/case-study.html');
    const cost = page.locator('#cost');
    await expect(cost).toContainText(/Reviewed monthly projection/i);
    await expect(cost).not.toContainText(/What it actually costs/i);
  });

  test('framing stays a governed working pilot with a conditional production gate', async ({ page }) => {
    await page.goto('/case-study.html');
    await expect(page).toHaveTitle(/governed working pilot/i);
    await expect(page.locator('#chapter-top')).toContainText(/One onboarding\/safe-check gate stayed conditional/i);
    await expect(page.locator('#chapter-top')).toContainText(/not current business-live proof/i);
    await expect(page.locator('#glance')).toContainText(/onboarding\/safe-check gate remained conditional/i);
    await expect(page.locator('#shipped')).not.toContainText(/This is the finish line/i);
    await expect(page.locator('#verdict')).toContainText(/governed working pilot/i);
  });
});

test.describe('chapter chrome — floating ToC, stat strips, design tokens', () => {
  const TOC_PAGES = [
    { url: '/governance.html', min: 5 },
    { url: '/funnel.html', min: 6 },
    { url: '/production.html', min: 6 },
    { url: '/industries.html', min: 4 },
    { url: '/self-improving.html', min: 4 },
    { url: '/customize.html', min: 3 },
    { url: '/workbook.html', min: 6 },
    { url: '/case-study.html', min: 6 },
  ];
  for (const { url, min } of TOC_PAGES) {
    test(`${url} auto-builds a floating ToC whose links resolve`, async ({ page }) => {
      await page.goto(url);
      await expect(page.locator('.floating-toc.is-ready')).toHaveCount(1);
      const links = page.locator('.floating-toc a[data-toc-link]');
      const count = await links.count();
      expect(count, `${url} ToC link count`).toBeGreaterThanOrEqual(min);
      // Every ToC link points at a section that exists on the page.
      const ids = await links.evaluateAll((els) => els.map((e) => e.getAttribute('data-toc-link')));
      for (const id of ids) {
        await expect(page.locator('#' + id), `${url} ToC target #${id}`).toHaveCount(1);
      }
    });
  }

  test('process and readiness chapters lead with a useful visual', async ({ page }) => {
    for (const url of ['/production.html', '/industries.html', '/blueprint.html']) {
      await page.goto(url);
      await expect(page.locator('[data-visual-anchor]').first()).toBeVisible();
    }
  });

  test('design-system tokens are loaded on every page', async ({ page }) => {
    for (const url of CHAPTER_PAGES) {
      await page.goto(url);
      const tokens = await page.evaluate(() => {
        const s = getComputedStyle(document.documentElement);
        return {
          s4: s.getPropertyValue('--s-4').trim(),
          tDisplay: s.getPropertyValue('--t-display').trim(),
          easeOut: s.getPropertyValue('--ease-out').trim(),
        };
      });
      expect(tokens.s4, `${url} --s-4`).toBe('20px');
      expect(tokens.tDisplay, `${url} --t-display`).toMatch(/^clamp\(/);
      expect(tokens.easeOut, `${url} --ease-out`).toMatch(/cubic-bezier/);
    }
  });
});

test.describe('reveal animation — never strands content for real visitors', () => {
  // Regression guard for the .reveal opacity:0 bug: the safety-net sweep must
  // add .in to every reveal so no section is left invisible without scrolling.
  for (const url of ['/index.html', '/funnel.html', '/production.html', '/industries.html']) {
    test(`${url} reveals all resolve to .in`, async ({ page }) => {
      await page.goto(url);
      await page.waitForTimeout(1500); // safety-net sweep fires at 1.2s
      const result = await page.evaluate(() => {
        const reveals = [...document.querySelectorAll('.reveal')];
        return { total: reveals.length, notIn: reveals.filter((el) => !el.classList.contains('in')).length };
      });
      await expect(page.locator('main h1')).toBeVisible();
      expect(result.notIn, `${url} left ${result.notIn}/${result.total} reveals hidden`).toBe(0);
    });
  }
});

test.describe('Open Graph & social assets', () => {
  // Each page's og:image must resolve to an asset that actually ships in the
  // repo, and og:image must equal twitter:image.
  const OG_PAGES = [
    { url: '/governance.html', asset: '/assets/og/og-production.png' },
    { url: '/index.html', asset: '/assets/og/og-home.png' },
    { url: '/funnel.html', asset: '/assets/og/og-home.png' },
    { url: '/production.html', asset: '/assets/og/og-production.png' },
    { url: '/industries.html', asset: '/assets/og/og-industries.png' },
    { url: '/self-improving.html', asset: '/assets/og/og-self-improving.svg' },
    { url: '/case-study.html', asset: '/assets/og/og-production.png' },
    { url: '/customize.html', asset: '/assets/og/og-customize.svg' },
    { url: '/workbook.html', asset: '/assets/og/og-home.png' },
  ];
  for (const { url, asset } of OG_PAGES) {
    test(`${url} advertises an OG image that actually exists`, async ({ page }) => {
      await page.goto(url);
      const og = await page.locator('meta[property="og:image"]').getAttribute('content');
      const tw = await page.locator('meta[name="twitter:image"]').getAttribute('content');
      expect(og, `${url} og:image is set`).toBeTruthy();
      expect(tw, `${url} twitter:image mirrors og:image`).toBe(og);
      const local = toLocalAssetPath(og);
      expect(local, `${url} og:image maps to the expected asset`).toBe(asset);
      const res = await page.request.get(local);
      expect(res.ok(), `${url} og:image asset resolves (${res.status()})`).toBeTruthy();
    });
  }

  test('blueprint.html keeps social metadata but intentionally ships no OG image', async ({ page }) => {
    await page.goto('/blueprint.html');
    // Meaningful absence: no image is advertised, so there is no broken-file promise…
    await expect(page.locator('meta[property="og:image"]')).toHaveCount(0);
    await expect(page.locator('meta[name="twitter:image"]')).toHaveCount(0);
    // …but the core social metadata is still present (this is a policy, not a gap).
    await expect(page.locator('meta[property="og:title"]')).toHaveCount(1);
    await expect(page.locator('meta[name="twitter:card"]')).toHaveCount(1);
  });
});

test.describe('internal link integrity — page links reachable from the primary pages', () => {
  test('every internal .html link resolves (excluding anchors/downloads/mailto/external)', async ({ page }) => {
    const broken = [];
    const checked = new Set();
    for (const url of CHAPTER_PAGES) {
      await page.goto(url);
      const hrefs = await page.locator('a[href]').evaluateAll((els) => els.map((e) => e.getAttribute('href') || ''));
      for (const raw of hrefs) {
        if (!raw) continue;
        if (/^(https?:|mailto:|tel:)/i.test(raw)) continue; // external / mailto / tel
        if (raw.startsWith('#')) continue; // intentional in-page anchor
        const path = raw.split('#')[0];
        if (!path) continue; // pure anchor
        if (!/\.html$/i.test(path)) continue; // page links only (skips downloads/assets)
        let norm = path.replace(/^\.\//, '/');
        if (!norm.startsWith('/')) norm = '/' + norm;
        if (checked.has(norm)) continue;
        checked.add(norm);
        const res = await page.request.get(norm);
        if (!res.ok()) broken.push(`${url} -> ${raw} (${res.status()})`);
      }
    }
    expect(broken, `broken internal page links:\n${broken.join('\n')}`).toEqual([]);
  });
});

test.describe('accessibility — no serious/critical axe violations', () => {
  for (const url of ['/index.html', '/funnel.html', '/production.html', '/industries.html', '/self-improving.html']) {
    test(`${url} passes axe (wcag2a/aa, contrast excluded)`, async ({ page }) => {
      await page.goto(url);
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa'])
        .disableRules(['color-contrast'])
        .analyze();
      const offenders = results.violations.filter((v) => ['serious', 'critical'].includes(v.impact));
      expect(offenders, `${url}\n${JSON.stringify(offenders, null, 2)}`).toEqual([]);
    });
  }
});
