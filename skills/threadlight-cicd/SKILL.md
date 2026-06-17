---
name: threadlight-cicd
description: >
  Use when a Threadlight pilot has to reach production through a CI/CD pipeline
  instead of the coding agent running `azd up` directly — i.e. the real customer
  case where prod deploys run under a federated identity with scoped RBAC, often
  from private-VNet runners, and the agent has no standing deploy rights. Opens
  with an onboarding-path decision gate (is a central platform environment
  required? already deployed?) and then generates a production deploy pipeline
  (GitHub Actions OR Azure DevOps) plus environment-setup runbooks the customer's
  platform team executes: user-assigned managed identity + OIDC / Workload
  Identity Federation credentials (no secrets), least-privilege RBAC scoped to
  the target resource group, and managed-or-self-hosted private-VNet runners.
  Emits an auditable onboarding-path.json and a central-platform-boundary.md that
  keeps the pilot pipeline strictly separate from central-platform deployment
  (citadel-hub-deploy). This is the authoritative, expanded home for the basic
  scaffold that threadlight-production-ready Phase 3 (`--scaffold-cicd`) still
  ships for backward-compat.
  USE FOR: ci/cd pipeline, prod deploy pipeline, github actions pipeline,
  azure devops pipeline, azure-pipelines.yml, azd-deploy-prod.yml, OIDC,
  federated credentials, workload identity federation, WIF, UAMI, user-assigned
  managed identity, no secrets pipeline, scoped RBAC, least privilege role
  assignment, self-hosted runners, managed devops pool, private vnet runners,
  larger runners private networking, restricted environment deploy, no deploy
  rights, platform team handoff, env setup runbook, onboarding path gate,
  central platform boundary, parallel track, spoke pipeline, citadel-spoke prod
  pipeline, production handoff pipeline, pipeline for restricted customer env.
  DO NOT USE FOR: deploying the central Citadel hub / shared APIM AI gateway /
  shared networking / platform Key Vault (that is the separate central-platform
  track — use citadel-hub-deploy in awesome-gbb); wiring a pilot to consume an
  existing hub via an Access Contract (use citadel-spoke-onboarding); the actual
  first-run azd deploy of the pilot in a permissive sandbox (use
  threadlight-deploy); the production-readiness scorecard / pillar assessment
  (use threadlight-production-ready); full runner IaC / Bicep VMSS authoring
  (runbook + pipeline wiring only here); GitLab CI (out of scope v1).
metadata:
  version: "0.1.0"
---

# Threadlight CI/CD — prod-deploy pipeline + env-setup runbooks

> The skill that answers "**how does this pilot actually deploy to production
> when the agent can't run `azd up` and has no standing rights?**" — by
> generating a federated-identity CI/CD pipeline plus the runbooks the
> customer's platform team runs to stand up the identity, permissions, and
> runners. Secret-free by construction; parallel-track-safe by design.

## When to use

- The pilot's prod environment is **locked down**: direct writes are restricted,
  deploys must go through a pipeline, and the agent has no deploy rights.
- You need a **GitHub Actions or Azure DevOps** prod-deploy pipeline that logs in
  with **OIDC / Workload Identity Federation** (no `AZURE_CREDENTIALS`, no client
  secret, no PAT).
- The platform team needs **ready-to-run `az` runbooks** for the UAMI, federated
  credentials, least-privilege RBAC, and (for private VNets) the runners.
- You must make the **central-platform boundary** explicit so the pilot pipeline
  never touches the Citadel hub.

**When NOT to use:** deploying the hub itself (`citadel-hub-deploy`), onboarding a
spoke onto an existing hub (`citadel-spoke-onboarding`), the permissive first-run
deploy (`threadlight-deploy`), or the readiness scorecard
(`threadlight-production-ready`). See the description's DO NOT USE FOR list.

## Onboarding-path decision gate (runs FIRST)

Before generating anything, resolve which track the pilot is on. `resolve_onboarding_path()`
branches on two questions and is the source of truth for posture + RBAC scope:

```
Is a central platform env required?  (Citadel hub / shared AI gateway /
                                      shared networking / platform Key Vault)
│
├─ no  ──────────────────► standalone        (validate target sub/RG, shared-resource
│                                             usage, network exposure FIRST)
│                                             posture: standard-ai-gateway | agt | direct
│                                             RBAC scope: target-rg
│
└─ yes ─► already deployed?
          │
          ├─ yes ─────────► spoke-onboard    (consume hub via Access Contract →
          │                                   citadel-spoke-onboarding)
          │                                   posture: citadel-spoke · RBAC scope: spoke-rg
          │
          └─ no  ─────────► hub-deploy-then-spoke
                                              (stand up hub on the SEPARATE central
                                               track → citadel-hub-deploy, THEN
                                               citadel-spoke-onboarding)
                                              posture: citadel-spoke · RBAC scope: spoke-rg
```

**Invariant:** a spoke pilot's deploy identity is scoped to the **spoke resource
group only** — never the hub, regardless of whether the hub already exists. The
resolved decision is written to `docs/threadlight-cicd/onboarding-path.json` so
the choice is auditable.

## Parallel-track boundary (the must-tell)

The pilot pipeline is a **separate repo and pipeline** from the central platform.

| Concern | Owner | Track / skill |
|---|---|---|
| Citadel hub, shared APIM AI gateway, shared networking, platform Key Vault | Central platform team | **`citadel-hub-deploy`** (awesome-gbb, separate repo) |
| Wire the pilot to consume the hub via an Access Contract | Platform / SE | **`citadel-spoke-onboarding`** |
| Deploy the pilot's **use-case** resources into the spoke/target RG | This pipeline | **`threadlight-cicd`** |

The generated `central-platform-boundary.md` states the pilot pipeline **must not**
deploy or modify the hub, and that its UAMI RBAC is **spoke-RG-scoped only**.

```mermaid
flowchart LR
  subgraph central["Central platform repo (separate)"]
    HUB["citadel-hub-deploy<br/>hub · shared APIM · networking · KV"]
  end
  subgraph pilot["Pilot repo (this pipeline)"]
    PIPE["threadlight-cicd<br/>azd-deploy-prod → spoke/target RG only"]
  end
  HUB -. "Access Contract<br/>(citadel-spoke-onboarding)" .-> PIPE
  PIPE -. "never writes" .-x HUB
```

## Quick reference

| Goal | Command |
|---|---|
| Interactive onboarding-path gate + generate | `python scripts/generate_pipeline.py --onboard` |
| GitHub Actions, standalone (public) | `python scripts/generate_pipeline.py --platform github-actions --central-env-required no --repo-full-name owner/repo --target-sub <sub> --target-rg <rg> --tenant-id <tid>` |
| Azure DevOps, spoke onto existing hub | `python scripts/generate_pipeline.py --platform azure-devops --central-env-required yes --central-env-exists yes --ado-org <org> --ado-project <proj> --ado-service-connection <sc> --target-sub <sub> --target-rg <rg> --tenant-id <tid>` |
| Private-VNet target (self-hosted / managed pool) | add `--private-network` |
| From a saved framing file | `--framing-file framing.json` |
| Run the test suite | `python -m pytest tests/ -v` |

## What it emits

Rendered deterministically (offline, no Azure calls, no secrets) into the pilot repo:

- **Pipeline** — `.github/workflows/azd-deploy-prod.yml` (GitHub, OIDC, `environment:`
  approval gate, `azd provision`/`deploy`) **or** `azure-pipelines.yml` (Azure DevOps,
  WIF service connection, `AzureCLI@2` + azd, environment approvals, pool ref).
- **Env-setup runbooks** — `docs/threadlight-cicd/env-setup/`:
  - `01-uami-federated-credentials.md` + `.sh` (UAMI + GH OIDC or ADO WIF — no secrets)
  - `02-rbac-role-assignments.md` + `.sh` (least-privilege, scoped to the target RG)
  - `03-runners-private-vnet.md` + `.sh` (managed **and** self-hosted options)
  - `README.md` (what to hand the dev team vs the platform team)
- **Boundary + decision record** — `central-platform-boundary.md`, `onboarding-path.json`.

Public targets default to hosted runners (`ubuntu-latest` / ADO `vmImage`); private
targets switch to `self-hosted` labels / a named ADO pool.

## Relationship to threadlight-production-ready

`threadlight-production-ready` Phase 3 (`--scaffold-cicd`) still ships a **basic**
GitHub-Actions-only scaffold for backward-compat. **This skill is the authoritative,
expanded home**: both platforms, the onboarding-path gate, the env-setup runbooks,
and the central-platform boundary. After the readiness scorecard is green, hand off
here for the production pipeline.

## Generator API (for tests / automation)

`scripts/generate_pipeline.py` exposes:

- `resolve_onboarding_path(framing) -> dict` — the decision gate (path, posture,
  rbac_scope, needs_validation, next_actions).
- `build_context(framing, resolved) -> dict` — template token context.
- `generate(framing, out_root) -> list[Path]` — render + write artifacts.
- `VERSION` — semver, matched against this file's `metadata.version` by `test_version.py`.

Templates live under `references/` as `{{TOKEN}}` files rendered by `_render` (pure
stdlib). Tests under `tests/` pin the artifact paths, the OIDC/WIF-only invariant
(no long-lived secrets), and the boundary content.

## Common mistakes

- **Widening RBAC scope.** Never scope the deploy role to the subscription or a
  central-platform RG. Target RG only.
- **Reaching for a secret.** If you find yourself adding `AZURE_CREDENTIALS` or a
  client secret, stop — use OIDC/WIF. The test suite fails the build if a secret
  or PAT lands in any emitted file.
- **Letting the pilot deploy the hub.** A missing central env is stood up on the
  `citadel-hub-deploy` track, not by this pipeline.
- **Skipping the gate.** Generating before resolving the onboarding path produces
  the wrong posture and RBAC scope. Run `--onboard` (or pass the flags) first.

## References

- [`references/onboarding-path-decision.md`](references/onboarding-path-decision.md) —
  the decision tree (standalone vs spoke-onboard vs hub-deploy-then-spoke) and when to
  engage `citadel-hub-deploy` vs `citadel-spoke-onboarding`.
- [`references/best-practices.md`](references/best-practices.md) — OIDC/WIF federation,
  least-privilege RBAC, environment gates, and private-VNet runners, with Microsoft
  Learn citations.
- [`references/pipeline-design-checklist.md`](references/pipeline-design-checklist.md) —
  operator hand-off checklist before giving a pipeline to the customer.
- `references/github-actions/`, `references/azure-devops/`, `references/env-setup/` —
  the `{{TOKEN}}` templates the generator renders.
