# Cloud sandbox documentation — design

**Date:** 2026-07-02
**Status:** approved
**Scope:** docs only (README + GitHub Pages site). No code or skill behaviour changes.

## Problem

The org just enabled **GitHub Copilot cloud sandboxes** (`copilot --cloud`,
public preview) — ephemeral, GitHub-hosted Linux environments for agentic runs.
They are a great zero-install on-ramp for `threadlight-skills`, but they behave
differently from the Codespace we already document:

- A cloud sandbox **does not read `.devcontainer/`**, so the `post-create.sh`
  auto-wiring never runs there. Users must install the plugin from the
  marketplace manually.
- Network egress is governed by the org's **Copilot cloud agent policies**
  (firewall / allow-list), not by anything in this repo.
- It is **public preview** and **usage-billed**; no `az` / `azd` / Docker or
  Azure credentials are preloaded, so the deploy and production legs still need
  a full local or in-VNet box (same honest limit as the thin Codespace).

None of this is written down. Devs who reach for `copilot --cloud` won't know how
to wire the skills or what won't work.

## Goals

- Tell consumers how to run the skills in a cloud sandbox (launch + marketplace
  wiring) and set honest expectations (governance, deploy tooling, preview
  billing).
- Keep the experience site (`docs/`) consistent: the cloud sandbox should appear
  wherever Codespaces already does.
- Stay documentation-only and on-brand; touch no build/test/skill code.

## Non-goals

- Automating plugin install inside cloud sandboxes (out of our control — they
  ignore `.devcontainer/`).
- Changing the org's cloud-agent firewall policy (admin concern; we only list the
  hosts the skills call).
- Editing the cinematic landing page (`docs/index.html`) or its Playwright/axe
  a11y tests.

## Changes

1. **README.md** — new `### In a GitHub cloud sandbox` subsection inside the
   existing "Quickstart in GitHub Codespaces" area: `copilot --cloud`, the
   marketplace wiring (with the "sandboxes ignore `.devcontainer/`" note),
   inherited cloud-agent policy + the host allow-list the deploy/cost skills
   need, no-Azure-tooling limit, and the preview/usage-billed caveat.

2. **docs/customize.html** — extend the "Testing inside their boundary"
   comparison table (`Azure ML compute + VS Code` vs `GitHub Codespaces`) with a
   third **GitHub cloud sandbox** column across all four rows (Network, Private
   endpoints, Best for, Honest limit). Lead-in updated "Two patterns" → "Three
   patterns". The honest limit keeps parity with the vanilla Codespace row: no
   line of sight to private endpoints.

3. **docs/workbook.html** — a zero-install "try it first" callout at the top of
   the "Before you start" prereqs section, reusing the existing `.wb-note`
   component (inline lime accent, no new CSS): Codespace badge link + a
   `copilot --cloud` mention, framed as great for Stage ① Design while Deploy /
   Production still want a real box.

4. **CHANGELOG.md** — an `Added` entry summarising the above.

## Host allow-list (for the cloud-agent policy)

Derived from hosts the skills actually call: `management.azure.com`,
`*.services.ai.azure.com`, `ai.azure.com`, `login.microsoftonline.com`,
`sts.windows.net`, `prices.azure.com`, `github.com`, `ghcr.io`,
`mcr.microsoft.com`, `learn.microsoft.com`.

## Verification

- `copilot --cloud`, org policy inheritance, public-preview + usage billing
  confirmed against GitHub docs
  (`docs.github.com/en/copilot/how-tos/cloud-and-local-sandboxes`) and the
  2026-06-02 "Cloud and local sandboxes … now in public preview" changelog.
- HTML edits are additive (one table column, one callout) — no structural or CSS
  changes; `index.html` untouched so the Playwright/axe suite is unaffected.
- Pytest CI is unaffected (no Python or SKILL.md changes).
