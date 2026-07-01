# Devcontainer + GitHub Codespaces quickstart — design

**Date:** 2026-07-01
**Status:** Approved (brainstorming → implementation)
**Scope:** Small, additive PR. No changes to skills, tests, or CI.

## Goal

Let a developer open this repo in **GitHub Codespaces** and, within about a
minute, have **Copilot CLI installed with all 16 threadlight skills active** —
without installing anything locally. This lowers the barrier to *trying* the
Threadlight pipeline (fork it, open a Codespace, start prompting).

## Non-goals

- **Not** turning the repo into a GitHub "template repository." That is a
  one-click repo *setting* (Settings → Template repository), not code, so it
  needs no PR. A devcontainer + an "Open in Codespaces" badge already delivers
  the quickstart, and works for fork / clone / template-clone alike. The two are
  complementary; flipping the template switch later is an independent follow-up.
- **Not** a contributor/test environment. The image deliberately omits the
  Python (pytest/pyyaml) and Node/Playwright test toolchains.
- **Not** a deploy environment. `azd`, `az`, `bicep`, and Docker are **not**
  baked in, so the heavy deploy skills (`threadlight-deploy`, etc.) will not run
  in this box. It is for authoring and exploring the skills.

## Audience

Consumers, not contributors — someone who wants to *use* the skills, per the
decision captured during brainstorming.

## Design

### Files added

1. `.devcontainer/devcontainer.json`
   - Base image: `mcr.microsoft.com/devcontainers/base:ubuntu` (thin; no
     Python/Node toolchain).
   - Feature: `ghcr.io/devcontainers/features/github-cli` — several skills shell
     out to `gh`.
   - `postCreateCommand`: runs `.devcontainer/post-create.sh`.
   - VS Code customization: recommend the GitHub Copilot extension (harmless,
     optional).

2. `.devcontainer/post-create.sh`
   - Installs Copilot CLI via the official script to `/usr/local/bin`
     (`curl -fsSL https://gh.io/copilot-install | sudo bash`) — no Node
     dependency, lands on `PATH`.
   - Wires the **local** repo checkout as the skill source:
     - `copilot plugin marketplace add "$REPO_ROOT"` (reads
       `.github/plugin/marketplace.json`).
     - `copilot plugin install threadlight-skills@threadlight-skills`.
   - Prints a next-steps banner: run `copilot`, then `/login` on first launch.
   - Idempotent and non-fatal: re-running (e.g. on rebuild) must not error out.

3. `README.md` — new "Quickstart in GitHub Codespaces" section:
   - "Open in Codespaces" badge.
   - What boots ready (Copilot CLI + 16 skills from the local checkout).
   - The **published-marketplace** command as the alternative wiring
     (`copilot plugin marketplace add aiappsgbb/threadlight-skills` + install),
     so the doc shows both local and released paths.
   - An honest **Limitations** note (below).

### Why local + marketplace both

Brainstorming decision: wire the local checkout (so a dev gets exactly the
version in the repo they opened — ideal for a fork/template workflow) **and**
document the published marketplace command in the README (so readers can pull
the released plugin independently of local edits).

### Wiring mechanism (verified)

`copilot plugin marketplace add <path>` accepts a local path and registers the
marketplace defined at `<path>/.github/plugin/marketplace.json`. Installing
`threadlight-skills@threadlight-skills` from it reports "Installed 16 skills."
Both steps succeed **without authentication** — only invoking the model needs
`/login`. Verified locally in a sandboxed `COPILOT_HOME`.

## Limitations (documented in README, honest per repo tone)

- **Auth:** first `copilot` launch requires `/login` (OAuth device flow).
  Codespaces injects a repo-scoped `GITHUB_TOKEN` that lacks the "Copilot
  Requests" permission and may be picked up from the environment; if it
  interferes with sign-in, `unset GITHUB_TOKEN` for the `copilot` session and
  run `/login`.
- **workiq and other MCP/agent tools** may not function in a Codespace.
- **Azure deploy tooling** (`azd`, `az`, `bicep`, Docker) is intentionally
  absent; use a full local/VNet environment for the deploy and production legs.

## Verification

- `bash -n .devcontainer/post-create.sh` (syntax lint).
- Re-confirm the marketplace-add + install flow prints "Installed 16 skills" in
  a sandboxed `COPILOT_HOME`.
- `python -c "import json; json.load(open('.devcontainer/devcontainer.json'))"`
  or `jq` to confirm the devcontainer JSON parses (allowing for JSONC comments —
  validate the comment-stripped form if comments are used).

## Rollback

Purely additive: delete `.devcontainer/` and revert the README section. No
runtime, CI, or skill behavior depends on these files.
