# `tests/playwright/` — local site smoke + a11y

Public-safe end-to-end checks for `docs/index.html` (landing). Runs
locally with Chromium against a short-lived `python3 -m http.server`
rooted at `docs/`.

> The skills catalog page (`docs/skills.html`) is **deliberately out of
> scope for the initial release**. The file lives untracked under
> `.gitignore` for local preview only; its prior tests live in git
> history and can be reinstated when the catalog ships.

## What it covers

- Landing renders, no duplicate Setup link in the nav, footer carries
  the public-safe wording ("open guidance", not "internal use"), and
  every nav anchor target resolves.
- The `#scene-cost` "Cost intelligence" widget animates from `$0`
  to the deterministic v0.1.0 golden numbers — `$397.57 /mo` current,
  `$341.19 /mo` recommended, `−$56.38 / −14.2%` savings — with 7 table
  rows and 3 recommendation cards. The "Browse the skill" CTA points to
  the `threadlight-consumption-iq` folder on GitHub.
- Deck-spine scenes: evolution band (5 waves with Skills active), funnel
  (5 stages with valid internal anchor targets), industries strip (6
  sectors with non-empty pilot copy + GitHub deep-links), Kratos
  showcase (live + GitHub CTAs, target=_blank, rel=noopener), other
  channels strip (Copilot Studio · SRE Agent · MCP host), nav
  (Funnel + Channels present, total ≤ 8 links).
- `axe-core` reports no serious / critical WCAG 2.1 AA violations on
  the landing page (color-contrast is checked manually because the dark
  theme's gradient text doesn't lend itself to automated contrast).
- A public-safety audit confirms no leak terms ("internal use",
  "confidential", "do not share", "microsoft only", or the legacy
  "tier-1 european telco" phrasing) appear anywhere on the landing
  page.

Tests run in four projects: chromium-desktop @ 1440×900, chromium-mobile
(Pixel 7), chromium-dark, chromium-light.

## Run it

```bash
cd tests/playwright
npm install
npx playwright install chromium
npx playwright test
```

The Playwright config auto-starts `python3 -m http.server 4173` rooted
at `../../docs` and tears it down when the suite ends.

## Visual review

```bash
cd tests/playwright
node grab-shots.mjs
```

Writes screenshots for the new deck-spine scenes × {desktop dark / light
/ mobile} to `tests/playwright/screenshots/` (gitignored).

## What's NOT checked

- The cost widget numbers are hand-mirrored from
  `skills/threadlight-consumption-iq/references/fixtures/sample-pilot-consumption/expected/cost-manifest.json`.
  If that golden ever changes, the widget must be hand-updated AND
  these tests will fail to surface the drift — that's intentional.
- Visual regression. Screenshots are not asserted byte-for-byte; the
  designer pass uses them manually.

