// End-to-end checks for docs/index.html (landing) — public release scope.
// Numbers in the cost widget come from the v0.1.0 golden manifest.
// The skills catalog page (docs/skills.html) is deliberately out of scope
// for the initial release — it lives untracked under .gitignore.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const LANDING = '/index.html';

test.describe('landing page (index.html)', () => {
  test('renders the hero + has working title', async ({ page }) => {
    await page.goto(LANDING);
    await expect(page).toHaveTitle(/Threadlight/);
    await expect(page.getByRole('heading', { name: /Business process/i })).toBeVisible();
    await expect(page.locator('.hero-cta-row .btn-primary').first()).toContainText(/Open the technical briefing/i);
  });

  test('nav has no duplicate Setup link', async ({ page }) => {
    await page.goto(LANDING);
    const setupLinks = page.locator('header.masthead nav.nav a', { hasText: /^Setup$/ });
    // Zero (removed) or one (kept) is fine; two or more is a regression.
    await expect(await setupLinks.count()).toBeLessThan(2);
  });

  test('footer is public-safe (no "internal use" leak)', async ({ page }) => {
    await page.goto(LANDING);
    const footer = page.locator('footer').first();
    await expect(footer).toBeVisible();
    await expect(footer).not.toContainText(/internal use/i);
    await expect(footer).toContainText(/open guidance/i);
  });

  test('cost forecast section lives on production.html and animates to the golden numbers', async ({ page }) => {
    await page.goto('/production.html');
    const cost = page.locator('#prod-cost');
    // Scroll the COUNTER block (not the whole section) into view so the
    // IntersectionObserver fires on mobile viewports too.
    const counters = cost.locator('.cost-counters');
    await counters.scrollIntoViewIfNeeded();
    // Counter animation is ~1.1s + buffer; mobile needs a tad more.
    await page.waitForTimeout(2000);
    const current     = cost.locator('.ctr-value.is-current');
    const recommended = cost.locator('.ctr-value.is-recommended');
    await expect(current).toContainText('$397.57 /mo');
    await expect(recommended).toContainText('$341.19 /mo');
    await expect(cost.locator('.cost-savings-pill')).toContainText(/56\.38/);
    await expect(cost.locator('.cost-recs .cost-rec')).toHaveCount(3);
    await expect(cost.locator('.cost-table tbody tr')).toHaveCount(7);
    await expect(cost.locator('a[href*="/skills/threadlight-consumption-iq"]').first()).toBeVisible();
    // Framing: forecast, not "save money"
    const head = (await cost.locator('.section-head').textContent()) || '';
    expect(head).toMatch(/forecast/i);
    expect(head).not.toMatch(/cheapest|cut/i);
  });

  test('all internal anchors resolve', async ({ page }) => {
    await page.goto(LANDING);
    const hrefs = await page.locator('header.masthead nav.nav a[href^="#"]').evaluateAll(
      (els) => els.map((e) => e.getAttribute('href'))
    );
    for (const h of hrefs) {
      const id = h.slice(1);
      await expect(page.locator('#' + id), `nav target ${h} should exist`).toHaveCount(1);
    }
  });

  test('has favicon + OpenGraph tags', async ({ page }) => {
    await page.goto(LANDING);
    const icon = await page.locator('link[rel="icon"]').getAttribute('href');
    expect(icon).toMatch(/^data:image\/svg\+xml/);
    await expect(page.locator('meta[property="og:title"]')).toHaveCount(1);
    await expect(page.locator('meta[property="og:description"]')).toHaveCount(1);
    await expect(page.locator('meta[name="twitter:card"]')).toHaveCount(1);
  });

  test('axe-core: no serious or critical a11y violations on landing', async ({ page }) => {
    await page.goto(LANDING);
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa'])
      .disableRules(['color-contrast'])
      .analyze();
    const offenders = results.violations.filter(v => ['serious', 'critical'].includes(v.impact));
    expect(offenders, JSON.stringify(offenders, null, 2)).toEqual([]);
  });
});

test.describe('catalog page (skills.html) — DEFERRED out of initial release', () => {
  test.skip('the docs/skills.html catalog is intentionally not shipped yet; its prior tests live in git history', () => {
    // The catalog page lives untracked under .gitignore and may come back in
    // a follow-up release. Reinstate the prior describe block when it does.
  });
});

test.describe('deck-spine additions (evolution / funnel / industries / channels)', () => {
  test('evolution band renders all five waves with Skills marked active', async ({ page }) => {
    await page.goto(LANDING);
    const band = page.locator('.evolution-band');
    await expect(band).toBeVisible();
    const chips = band.locator('.evo-chip');
    await expect(chips).toHaveCount(5);
    await expect(chips.nth(0)).toContainText('LLMs');
    await expect(chips.nth(1)).toContainText('RAG');
    await expect(chips.nth(2)).toContainText('Multi-agent');
    await expect(chips.nth(3)).toContainText('MCP');
    await expect(chips.nth(4)).toContainText('Skills');
    await expect(chips.nth(4)).toHaveClass(/is-now/);
  });

  test('funnel teaser on home shows exactly 5 stages with skill chips', async ({ page }) => {
    await page.goto(LANDING);
    const funnel = page.locator('#scene-funnel');
    await funnel.scrollIntoViewIfNeeded();
    const steps = funnel.locator('.funnel-step');
    await expect(steps).toHaveCount(5);
    const expected = ['01 · Conversation', '02 · Co-design', '03 · Deploy', '04 · Safe-check', '05 · Production'];
    for (let i = 0; i < expected.length; i++) {
      await expect(steps.nth(i)).toContainText(expected[i]);
    }
    // every step links to the funnel deep page
    const hrefs = await steps.evaluateAll(els => els.map(e => e.getAttribute('href')));
    for (const h of hrefs) {
      expect(h, 'funnel step should link to the funnel chapter').toMatch(/^\.\/funnel\.html(#|$)/);
    }
    // skill chips per stage make the teaser more than a label
    const allText = (await steps.allTextContents()).join(' | ');
    expect(allText).toMatch(/threadlight-design/);
    expect(allText).toMatch(/threadlight-deploy/);
    expect(allText).toMatch(/threadlight-production-ready/);
  });

  test('funnel chapter has stage-show vertical chain-rail with 3 primary skill cards + 6 supporting chips', async ({ page }) => {
    await page.goto('/funnel.html');
    const show = page.locator('#stage-show');
    await show.scrollIntoViewIfNeeded();
    const rail = show.locator('.chain-rail');
    await expect(rail).toHaveCount(1);
    // 3 primary skill cards (design / deploy / production-ready)
    const cards = rail.locator('.skill-card');
    await expect(cards).toHaveCount(3);
    const names = (await cards.locator('.skill-name').allTextContents()).join(' | ').toLowerCase();
    expect(names).toMatch(/threadlight-design/);
    expect(names).toMatch(/threadlight-deploy/);
    expect(names).toMatch(/threadlight-production-ready/);
    // 6 supporting chips between primary cards
    const chips = rail.locator('.aux-chip');
    await expect(chips).toHaveCount(6);
    const chipNames = (await chips.locator('.aux-name').allTextContents()).join(' | ').toLowerCase();
    for (const s of ['demo-data-factory', 'local-test', 'safe-check', 'hitl-patterns', 'workspace-ui', 'event-triggers']) {
      expect(chipNames, `funnel chain should name ${s}`).toContain(s);
    }
  });

  test('funnel chapter keeps the slim stage-glance grid (gl-step)', async ({ page }) => {
    await page.goto('/funnel.html');
    const funnel = page.locator('#stage-glance');
    await funnel.scrollIntoViewIfNeeded();
    await expect(funnel.locator('.stage-glance .gl-step')).toHaveCount(5);
  });

  test('industries strip renders all 6 sectors with non-empty pilots', async ({ page }) => {
    await page.goto(LANDING);
    const ind = page.locator('#scene-industries');
    await ind.scrollIntoViewIfNeeded();
    const tiles = ind.locator('.industry-tile');
    await expect(tiles).toHaveCount(6);
    const sectors = await ind.locator('.industry-tile .it-sector').allTextContents();
    for (const s of ['Financial services', 'Healthcare', 'Manufacturing', 'Retail', 'Public sector', 'Telco']) {
      expect(sectors.join(' | ').toLowerCase()).toContain(s.toLowerCase());
    }
    // each tile has a one-line pilot description, not empty
    for (let i = 0; i < 6; i++) {
      const line = await tiles.nth(i).locator('.it-line').textContent();
      expect((line || '').trim().length, `industry ${i} should have copy`).toBeGreaterThan(20);
    }
  });

  test('kratos showcase links out to live demo + GitHub repo', async ({ page }) => {
    await page.goto(LANDING);
    const ch = page.locator('#scene-kratos');
    await ch.scrollIntoViewIfNeeded();
    await expect(ch.getByRole('heading', { name: /Kratos Agent/i })).toBeVisible();
    const live = ch.locator('a[href="https://aka.ms/kratos"]');
    await expect(live).toBeVisible();
    await expect(live).toHaveAttribute('target', '_blank');
    await expect(live).toHaveAttribute('rel', /noopener/);
    const repo = ch.locator('a[href="https://github.com/kmavrodis/kratos-agent"]');
    await expect(repo).toBeVisible();
    // Screenshot loads from the kratos repo, not embedded in this repo
    const shot = ch.locator('img.kc-shot');
    await expect(shot).toHaveAttribute('src', /raw\.githubusercontent\.com\/kmavrodis\/kratos-agent/);
    await expect(shot).toHaveAttribute('alt', /kratos/i);
  });

  test('kratos sits in the no-code-alt slot (near the end, NOT slot 2) and is framed as the simpler-path option', async ({ page }) => {
    await page.goto(LANDING);
    // Order: hero → show → funnel → prod-ready → industries → kratos → cta
    // Kratos must NOT be slot 2 (that pushes a competitor before the chain message)
    const ids = await page.locator('main section.scene').evaluateAll(
      els => els.map(e => e.id)
    );
    const heroIdx   = ids.indexOf('scene-hero');
    const kratosIdx = ids.indexOf('scene-kratos');
    const showIdx   = ids.indexOf('scene-show');
    const ctaIdx    = ids.indexOf('scene-cta');
    expect(heroIdx,   'scene-hero present').toBeGreaterThanOrEqual(0);
    expect(kratosIdx, 'scene-kratos present').toBeGreaterThanOrEqual(0);
    expect(showIdx,   'scene-show present').toBeGreaterThanOrEqual(0);
    expect(ctaIdx,    'scene-cta present').toBeGreaterThanOrEqual(0);
    // Chain SVG must come BEFORE Kratos (we tell the threadlight story first)
    expect(showIdx,   'chain SVG before kratos').toBeLessThan(kratosIdx);
    // Kratos must sit late in the page, immediately before the CTA
    expect(kratosIdx, 'kratos immediately precedes CTA').toBe(ctaIdx - 1);

    // The hero MUST surface a small no-code banner linking to kratos (so the
    // reader sees the alternative without scrolling past the whole chain)
    const banner = page.locator('#scene-hero .hero-banner');
    await expect(banner).toBeVisible();
    await expect(banner.locator('a[href="#scene-kratos"]')).toHaveCount(1);
    const bannerText = (await banner.textContent() || '').toLowerCase();
    expect(bannerText).toMatch(/no[- ]code|no customization/);
    expect(bannerText).toMatch(/kratos/);

    // Kratos block framing
    const ch  = page.locator('#scene-kratos');
    const txt = ((await ch.locator('.channels-copy').textContent()) || '').toLowerCase();
    expect(txt).toMatch(/customization/);
    expect(txt).toMatch(/threadlight/);
  });

  test('home chain SVG hero shows three panels: paragraph, SPEC, deployed agent', async ({ page }) => {
    await page.goto(LANDING);
    // The chain SVG hero lives in #scene-show — three panels: paragraph, SPEC, deployed agent
    const hero = page.locator('#scene-show .svg-hero svg.funnel-chain-svg');
    await expect(hero).toHaveCount(1);
    const stageLabels = hero.locator('text.panel-num');
    expect(await stageLabels.count(), 'chain SVG should have 3 stage labels').toBe(3);
    const titles = (await hero.locator('text.panel-title').allTextContents()).join(' | ');
    expect(titles).toMatch(/paragraph/i);
    expect(titles).toMatch(/SPEC/i);
    expect(titles).toMatch(/deployed agent/i);
    // Sub-copy must be public-readable, not promise a CLI we don't ship
    const sub = (await page.locator('#scene-show .sub').first().textContent()) || '';
    expect(sub).not.toMatch(/^\s*no cli\b/i);  // bare "No CLI" claim is false (Copilot CLI exists)
    expect(sub).not.toMatch(/the seller/i);
  });

  test('skill-eyebrow disclaimer (.md, not commands) ships on every page', async ({ page }) => {
    // Skills are .md files a coding agent reads — not bash commands. This eyebrow
    // is the site-wide correction to the original framing and must appear above
    // the signature artefact on every chapter page.
    const pages = [LANDING, '/funnel.html', '/production.html', '/industries.html'];
    for (const url of pages) {
      await page.goto(url);
      const eyebrow = page.locator('.skill-eyebrow');
      await expect(eyebrow, `${url} should ship a .skill-eyebrow`).toHaveCount(1);
      const text = (await eyebrow.textContent()) || '';
      expect(text, `${url} eyebrow positions skills as .md`).toMatch(/\.md/);
      expect(text, `${url} eyebrow positions skills against being commands`).toMatch(/not commands/i);
      expect(text, `${url} eyebrow names coding-agent hosts`).toMatch(/copilot/i);
    }
  });

  test('top nav is chapter pages only — no in-page anchors', async ({ page }) => {
    for (const url of [LANDING, '/funnel.html', '/industries.html', '/production.html']) {
      await page.goto(url);
      const hrefs = await page.locator('header.masthead nav.nav a').evaluateAll(
        els => els.map(e => e.getAttribute('href') || '')
      );
      expect(hrefs.length, `${url} nav should have at least 3 links`).toBeGreaterThanOrEqual(3);
      expect(hrefs.length, `${url} nav should stay under 6 links`).toBeLessThanOrEqual(6);
      for (const h of hrefs) {
        expect(h, `${url}: nav link "${h}" should be a chapter page, not an in-page anchor`)
          .toMatch(/^(\.\/)?(index|funnel|industries|production)\.html$/);
      }
      // Page link labels must include all 3 chapters
      const labels = (await page.locator('header.masthead nav.nav a').allTextContents()).join(' | ');
      expect(labels).toMatch(/Funnel/);
      expect(labels).toMatch(/Production-ready/);
      expect(labels).toMatch(/Industries/);
    }
  });
});

test.describe('public-safety audit (no leaks of internal-only phrasing)', () => {
  test('no obvious leak terms on landing, funnel, industries, or production', async ({ page }) => {
    for (const path of [LANDING, '/funnel.html', '/industries.html', '/production.html']) {
      await page.goto(path);
      const body = (await page.locator('body').textContent()) || '';
      const forbidden = [
        /internal use/i,
        /confidential/i,
        /do not share/i,
        /microsoft only/i,
        /tier-1 european telco/i
      ];
      for (const re of forbidden) {
        expect(body, `${path} should not contain ${re}`).not.toMatch(re);
      }
    }
  });
});

test.describe('deep pages (funnel.html + industries.html)', () => {
  test('funnel.html renders the chapter hero + ladder SVG centerpiece', async ({ page }) => {
    await page.goto('/funnel.html');
    await expect(page).toHaveTitle(/funnel/i);
    await expect(page.locator('h1')).toContainText(/five named stages/i);
    // After the SVG pass the #stage-show terminal section is gone — the ladder
    // SVG IS the centerpiece, sitting right after the chapter hero.
    const sections = ['#chapter-top', '#stage-ladder', '#stage-glance', '#stage-recap'];
    for (const id of sections) {
      await expect(page.locator(id), `funnel section ${id} should exist`).toHaveCount(1);
    }
    // The ladder SVG hero is locked above the fold
    await expect(page.locator('#stage-ladder.ladder-svg-hero')).toHaveCount(1);
    await expect(page.locator('#stage-ladder svg')).toHaveCount(1);
    // Stage-glance grid is back to the slim gl-step layout (the rich
    // funnel-step cards live on the homepage teaser instead).
    await expect(page.locator('#stage-glance .stage-glance .gl-step')).toHaveCount(5);
    await expect(page.locator('#stage-glance .read-deeper a[href*="THREADLIGHT.md"]')).toHaveCount(1);
  });

  test('industries.html renders the chapter hero + posterized sections', async ({ page }) => {
    await page.goto('/industries.html');
    await expect(page).toHaveTitle(/industries/i);
    await expect(page.locator('.chapter-hero h1')).toContainText(/one pattern/i);
    const sections = ['#chapter-top', '#sector-grid', '#ind-spec', '#industry-recap'];
    for (const id of sections) {
      await expect(page.locator(id), `industry section ${id} should exist`).toHaveCount(1);
    }
    // posterized: faux SPEC.md preview + read-deeper to operator MD
    await expect(page.locator('#ind-spec .spec-preview')).toHaveCount(1);
    await expect(page.locator('#ind-spec .read-deeper a[href*="THREADLIGHT.md"]')).toHaveCount(1);
  });

  test('production.html renders the chapter hero + amber→green journey + scorecard', async ({ page }) => {
    await page.goto('/production.html');
    await expect(page).toHaveTitle(/production/i);
    await expect(page.locator('h1')).toContainText(/(green safe-check|production-ready|go-live)/i);
    const sections = [
      '#chapter-top',
      '#amber-green',
      '#governance-triggers',
      '#posture-overview',
      '#prod-scorecard',
      '#chapter-recap',
    ];
    for (const id of sections) {
      await expect(page.locator(id), `production section ${id} should exist`).toHaveCount(1);
    }
    // The amber→green journey is the new centerpiece — six amber pillars, six green
    const amber = page.locator('#amber-green .agt-column.is-amber .agt-pillar');
    const green = page.locator('#amber-green .agt-column.is-green .agt-pillar');
    expect(await amber.count(), 'amber column should have 6 pillars').toBeGreaterThanOrEqual(6);
    expect(await green.count(), 'green column should have 6 pillars').toBeGreaterThanOrEqual(6);
    // The track has both day labels
    const labels = (await page.locator('#amber-green .agt-stage-labels .agt-day').allTextContents()).join(' | ');
    expect(labels).toMatch(/Day\s+0/i);
    expect(labels).toMatch(/Day\s+6/i);
    // The scorecard is still the destination — 13-pillar preview + read-deeper
    await expect(page.locator('#prod-scorecard .scorecard-preview')).toHaveCount(1);
    const pillars = page.locator('#prod-scorecard .sc-pillar');
    const pillarCount = await pillars.count();
    expect(pillarCount, 'scorecard should have at least 13 pillars').toBeGreaterThanOrEqual(13);
    await expect(page.locator('#prod-scorecard .read-deeper a[href*="production-readiness.md"]')).toHaveCount(1);
  });

  test('floating ToC auto-builds on all chapter pages with the right link count', async ({ page }) => {
    const expectations = [
      { url: LANDING,             min: 6 },
      { url: '/funnel.html',      min: 4 },
      { url: '/industries.html',  min: 3 },
      { url: '/production.html',  min: 4 },
    ];
    for (const { url, min } of expectations) {
      await page.goto(url);
      const toc = page.locator('.floating-toc');
      await expect(toc, `${url} should have a floating-toc element`).toHaveCount(1);
      await page.evaluate(() => window.scrollTo(0, 1));
      await page.waitForFunction(() => document.querySelector('.floating-toc.is-ready') !== null, null, { timeout: 4000 });
      const links = toc.locator('a[data-toc-link]');
      const count = await links.count();
      expect(count, `${url} ToC should have at least ${min} links`).toBeGreaterThanOrEqual(min);
    }
  });

  test('production teaser on home introduces the 4 themes + 13 pillars', async ({ page }) => {
    await page.goto(LANDING);
    const prod = page.locator('#scene-prod-ready');
    await prod.scrollIntoViewIfNeeded();
    // 4 theme cards
    const themes = prod.locator('.theme-quad .theme-card');
    await expect(themes).toHaveCount(4);
    // Theme names cover the 13 pillars
    const allText = (await themes.allTextContents()).join(' | ').toLowerCase();
    expect(allText).toMatch(/network/);
    expect(allText).toMatch(/observability|governance/);
    expect(allText).toMatch(/supply chain|cost/);
    expect(allText).toMatch(/lifecycle|hand-off/);
    // Pillar number chips render
    const chips = await themes.locator('.th-pillars span').count();
    expect(chips, 'all 13 pillar chips should render across the 4 themes').toBe(13);
    // Teaser still links out to the deep chapter
    await expect(prod.locator('a[href="./production.html"]')).toHaveCount(1);
  });

  test('skills chain block lives on home with all 3 primary skill cards + supporting chips', async ({ page }) => {
    await page.goto(LANDING);
    const chain = page.locator('#scene-chain');
    await chain.scrollIntoViewIfNeeded();
    // 3 primary skill cards: design, deploy, production-ready
    const cards = chain.locator('.skill-card');
    await expect(cards).toHaveCount(3);
    const skillNames = (await cards.locator('.skill-name').allTextContents()).join(' | ').toLowerCase();
    expect(skillNames).toMatch(/threadlight-design/);
    expect(skillNames).toMatch(/threadlight-deploy/);
    expect(skillNames).toMatch(/threadlight-production-ready/);
    // Supporting chips: 3 always-wired + 3 conditional
    const chips = chain.locator('.aux-chip');
    await expect(chips).toHaveCount(6);
    // Each chip names a real threadlight-* skill
    const chipNames = (await chips.locator('.aux-name').allTextContents()).join(' | ').toLowerCase();
    for (const s of ['demo-data-factory', 'local-test', 'safe-check', 'hitl-patterns', 'workspace-ui', 'event-triggers']) {
      expect(chipNames, `skills chain should name ${s}`).toContain(s);
    }
    // Hero CTA "See the chain" should land on this block
    const ctaHref = await page.locator('#scene-hero .hero-cta-row a[href="#scene-chain"]').getAttribute('href');
    expect(ctaHref).toBe('#scene-chain');
  });

  test('landing teasers point at the deep pages', async ({ page }) => {
    await page.goto(LANDING);
    await expect(page.locator('#scene-funnel a[href^="./funnel.html"]').first()).toHaveCount(1);
    await expect(page.locator('#scene-industries a[href="./industries.html"]')).toHaveCount(1);
    await expect(page.locator('#scene-prod-ready a[href="./production.html"]')).toHaveCount(1);
  });

  test('deep pages link back to home + each other', async ({ page }) => {
    for (const url of ['/funnel.html', '/industries.html', '/production.html']) {
      await page.goto(url);
      await expect(page.locator('header.masthead .brand a[href="./index.html"]')).toHaveCount(1);
      const navText = (await page.locator('header.masthead nav.nav').textContent()) || '';
      expect(navText).toMatch(/Home/);
      expect(navText).toMatch(/Funnel/);
      expect(navText).toMatch(/Production-ready/);
      expect(navText).toMatch(/Industries/);
    }
  });

  test('axe-core: no serious or critical a11y violations on deep pages', async ({ page }) => {
    for (const url of ['/funnel.html', '/industries.html', '/production.html']) {
      await page.goto(url);
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa'])
        .disableRules(['color-contrast'])
        .analyze();
      const offenders = results.violations.filter(v => ['serious', 'critical'].includes(v.impact));
      expect(offenders, `${url}\n${JSON.stringify(offenders, null, 2)}`).toEqual([]);
    }
  });

  test('chapter visual toolkit: stat-strip, inline ToC, and signature artefact render on each chapter', async ({ page }) => {
    // After the Posterize-v2 SVG pass, funnel & production lift hand-authored
    // SVG centerpieces (.ladder-svg-hero, .amber-green-track) into chapter heroes.
    // Industries keeps its faux SPEC.md preview as its signature artefact.
    const pages = [
      { url: '/funnel.html', bodyClass: 'chapter-funnel', minTocLinks: 2, artefact: '.ladder-svg-hero,.amber-green-track,.terminal-card,.scorecard-preview,.spec-preview' },
      { url: '/production.html', bodyClass: 'chapter-production', minTocLinks: 4, artefact: '.ladder-svg-hero,.amber-green-track,.terminal-card,.scorecard-preview,.spec-preview' },
      { url: '/industries.html', bodyClass: 'chapter-industries', minTocLinks: 3, artefact: '.ladder-svg-hero,.amber-green-track,.terminal-card,.scorecard-preview,.spec-preview' },
    ];
    for (const p of pages) {
      await page.goto(p.url);
      const bodyClass = await page.locator('body').getAttribute('class');
      expect(bodyClass, `${p.url} body class`).toContain(p.bodyClass);
      const statStrip = page.locator('.chapter-hero .stat-strip');
      await expect(statStrip, `${p.url} has stat-strip in hero`).toHaveCount(1);
      const stats = page.locator('.chapter-hero .stat-strip .stat');
      const statCount = await stats.count();
      expect(statCount, `${p.url} stat count`).toBeGreaterThanOrEqual(3);
      const tocLinks = page.locator('.chapter-hero .chapter-toc-inline a');
      const tocCount = await tocLinks.count();
      expect(tocCount, `${p.url} inline ToC link count`).toBeGreaterThanOrEqual(p.minTocLinks);
      const artefactCount = await page.locator(p.artefact).count();
      expect(artefactCount, `${p.url} signature artefact count`).toBeGreaterThanOrEqual(1);
    }
  });

  test('design-system tokens are loaded on every page', async ({ page }) => {
    for (const url of ['/', '/funnel.html', '/production.html', '/industries.html']) {
      await page.goto(url);
      const tokens = await page.evaluate(() => {
        const s = getComputedStyle(document.documentElement);
        return {
          s4: s.getPropertyValue('--s-4').trim(),
          tDisplay: s.getPropertyValue('--t-display').trim(),
          easeOut: s.getPropertyValue('--ease-out').trim(),
        };
      });
      expect(tokens.s4, `${url} --s-4 token`).toBe('20px');
      expect(tokens.tDisplay, `${url} --t-display token`).toMatch(/^clamp\(/);
      expect(tokens.easeOut, `${url} --ease-out token`).toMatch(/cubic-bezier/);
    }
  });

  test('funnel ladder centerpiece is present with five rails', async ({ page }) => {
    await page.goto('/funnel.html');
    const ladder = page.locator('#stage-ladder');
    await expect(ladder).toHaveCount(1);
    await expect(ladder).toHaveClass(/ladder-svg-hero/);
    const diagram = ladder.locator('svg').first();
    await expect(diagram).toHaveCount(1);
    // Five named stages are inside the SVG ladder
    const text = (await diagram.textContent()) || '';
    expect(text).toMatch(/Conversation/i);
    expect(text).toMatch(/Co-design/i);
    expect(text).toMatch(/Deploy/i);
    expect(text).toMatch(/Safe-check/i);
    expect(text).toMatch(/Production/i);
  });

  test('production posture triptych replaces the old 4-up grid', async ({ page }) => {
    await page.goto('/production.html');
    const triptych = page.locator('#posture-overview .poster-triptych');
    await expect(triptych).toHaveCount(1);
    const posters = triptych.locator('.poster-card');
    await expect(posters).toHaveCount(3);
    // each poster must have a signature svg shape, an h3, and a poster-foot
    for (let i = 0; i < 3; i++) {
      const card = posters.nth(i);
      await expect(card.locator('svg')).toHaveCount(1);
      await expect(card.locator('h3')).toHaveCount(1);
      await expect(card.locator('.poster-foot')).toHaveCount(1);
    }
  });

  test('funnel artefact-relay SVG shows the 5 named files flowing left-to-right', async ({ page }) => {
    await page.goto('/funnel.html');
    const relay = page.locator('#stage-relay.relay-svg-hero');
    await expect(relay).toHaveCount(1);
    const svg = relay.locator('svg.artefact-relay-svg');
    await expect(svg).toHaveCount(1);
    // Five stage tags STAGE 01..STAGE 05 must all appear inside the SVG
    const tags = (await svg.locator('text.ar-stage').allTextContents()).join(' | ');
    for (const want of ['STAGE 01', 'STAGE 02', 'STAGE 03', 'STAGE 04', 'STAGE 05']) {
      expect(tags, `relay SVG must include ${want}`).toContain(want);
    }
    // Five artefact names per stage exit: SPEC.md / Agent+dataset / Live agent / Scorecard / Hand-off
    const names = (await svg.locator('text.ar-name').allTextContents()).join(' | ');
    for (const want of ['SPEC.md', 'Agent + dataset', 'Live agent', 'Scorecard', 'Hand-off']) {
      expect(names, `relay SVG must include artefact ${want}`).toContain(want);
    }
  });

  test('production CISO pentagon SVG shows 5 trigger questions around a core', async ({ page }) => {
    await page.goto('/production.html');
    const svg = page.locator('#governance-triggers svg.ciso-pentagon-svg');
    await expect(svg).toHaveCount(1);
    // Five numbered question tags
    const tags = (await svg.locator('text.ciso-num').allTextContents()).join(' | ');
    for (const want of ['01 · IDENTITY', '02 · AUDIT', '03 · RBAC', '04 · TELEMETRY', '05 · EVALS']) {
      expect(tags, `CISO SVG must include ${want}`).toContain(want);
    }
    // Five spoke lines from the core
    await expect(svg.locator('g.ciso-spokes > line')).toHaveCount(5);
  });

  test('production posture-trio hero SVG renders 3 distinct architectural shapes', async ({ page }) => {
    await page.goto('/production.html');
    const svg = page.locator('#posture-overview svg.posture-trio-svg');
    await expect(svg).toHaveCount(1);
    const tags = (await svg.locator('text.pt-tag').allTextContents()).join(' | ');
    expect(tags).toContain('01 · GATEWAY-FRONTED');
    expect(tags).toContain('02 · FOUNDRY-NATIVE');
    expect(tags).toContain('03 · CUSTOMER-OWNED EDGE');
    // sits BEFORE the existing poster-triptych, not replacing it
    const fig = svg.locator('xpath=ancestor::figure[1]');
    await expect(fig).toHaveCount(1);
  });

  test('reveal animation does NOT strand content for real visitors (no JS required for visibility)', async ({ page }) => {
    // This test is the regression guard for the .reveal opacity:0 bug.
    // If .reveal stays hidden on initial paint without scrolling, 11+ elements
    // on funnel and production pages become invisible to real users.
    for (const url of ['/funnel.html', '/production.html', '/index.html', '/industries.html']) {
      await page.goto(url);
      // wait for safety-net sweep (1.2s) to fire
      await page.waitForTimeout(1500);
      const result = await page.evaluate(() => {
        const reveals = [...document.querySelectorAll('.reveal')];
        // Bug regression: any .reveal without .in stays at opacity:0 forever
        // (the html.js .reveal:not(.in) rule). The safety net must add .in
        // to every element so the transition (1.1s + delay) can complete.
        const notIn = reveals.filter(el => !el.classList.contains('in')).length;
        return { total: reveals.length, notIn };
      });
      expect(result.notIn, `${url} should have .in on every .reveal after safety-net sweep (had ${result.notIn}/${result.total} still missing .in)`).toBe(0);
    }
  });

  test('industries sector grid shows six poster cards above the SPEC mock', async ({ page }) => {
    await page.goto('/industries.html');
    const grid = page.locator('#sector-grid .poster-triptych');
    await expect(grid).toHaveCount(1);
    const cards = grid.locator('.poster-card');
    await expect(cards).toHaveCount(6);
    // posterized: each card now points at the shared SPEC.md mock
    const hrefs = await cards.evaluateAll(els => els.map(a => a.getAttribute('href')));
    for (const h of hrefs) {
      expect(h, 'industries poster card should link to the shared SPEC mock').toBe('#ind-spec');
    }
    for (let i = 0; i < 6; i++) {
      await expect(cards.nth(i).locator('svg.icon use')).toHaveCount(1);
    }
  });

  test('chapter recap block carries scorecard, three metrics, and CTA bar', async ({ page }) => {
    const cases = [
      { url: '/funnel.html',     recap: '#stage-recap' },
      { url: '/production.html', recap: '#chapter-recap' },
      { url: '/industries.html', recap: '#industry-recap' },
    ];
    for (const c of cases) {
      await page.goto(c.url);
      const block = page.locator(`${c.recap} .chapter-recap-block`);
      await expect(block, `${c.url} has chapter-recap-block`).toHaveCount(1);
      await expect(block.locator('.recap-scorecard'), `${c.url} scorecard`).toHaveCount(1);
      await expect(block.locator('.recap-metrics .metric-card'), `${c.url} metrics`).toHaveCount(3);
      await expect(block.locator('.recap-cta .btn'), `${c.url} cta count`).toHaveCount(3);
    }
  });

  test('every page advertises an OG image PNG', async ({ page }) => {
    const cases = [
      { url: '/',                 want: 'og-home.png' },
      { url: '/funnel.html',      want: 'og-funnel.png' },
      { url: '/production.html',  want: 'og-production.png' },
      { url: '/industries.html',  want: 'og-industries.png' },
    ];
    for (const c of cases) {
      await page.goto(c.url);
      const og = await page.locator('meta[property="og:image"]').getAttribute('content');
      const tw = await page.locator('meta[name="twitter:image"]').getAttribute('content');
      expect(og, `${c.url} og:image`).toContain(c.want);
      expect(tw, `${c.url} twitter:image`).toContain(c.want);
      // og:image:width/height must exist
      await expect(page.locator('meta[property="og:image:width"]')).toHaveCount(1);
      await expect(page.locator('meta[property="og:image:height"]')).toHaveCount(1);
    }
  });
});
