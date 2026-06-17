# Pipeline design checklist

Use this before handing a generated pipeline to the customer. It is the
operator-facing companion to the generated `env-setup/README.md` (which tells
you what to hand the dev team vs the platform team).

## Identity & secrets
- [ ] Authentication is **OIDC / Workload Identity Federation** — no
      `AZURE_CREDENTIALS`, client secret, or PAT stored in the CI system.
- [ ] A dedicated **UAMI** (or app registration) exists per pilot/environment
      (runbook `01`); its client-id replaces `REPLACE_WITH_UAMI_CLIENT_ID`.
- [ ] The federated credential **subject** is scoped tightly — GitHub
      `environment:production` (preferred) or a specific branch; ADO the
      service-connection subject.

## RBAC
- [ ] Role assignments are **least-privilege** and scoped to the
      **target/spoke RG only** (runbook `02`) — not subscription, never hub.
- [ ] Every assignment is documented and revocable.
- [ ] For `citadel-spoke`: the pilot identity has **no** role on the hub,
      shared APIM, shared networking, or platform Key Vault.

## Gates & stages
- [ ] Production deploy runs behind an **environment approval** (required
      reviewers / checks), not on every push.
- [ ] `provision` and `deploy` are **separate stages** so a reviewer can
      inspect changes before resources mutate.
- [ ] Post-deploy step re-runs the readiness re-assessment where applicable.

## Networking / runners
- [ ] If the landing zone uses **private endpoints**, the pipeline targets a
      **private runner** (GitHub `runs-on` self-hosted label / ADO `pool`
      ref) wired to the spoke VNet (runbook `03`).
- [ ] Managed DevOps Pools chosen first; self-hosted only when required.
- [ ] The runner identity is still the federated UAMI (private networking
      changes *where* the job runs, not *how* it authenticates).

## Boundary (must-tell)
- [ ] This is a **separate repo/pipeline** from `citadel-hub-deploy`.
- [ ] The pipeline deploys **only** use-case resources into the spoke/target
      RG; it never deploys/modifies the hub or shared platform resources.
- [ ] `onboarding-path.json` + `central-platform-boundary.md` are present and
      reflect the chosen path.

## Hand-off
- [ ] Platform team has the `env-setup/` runbooks + `.sh` scripts and has
      provisioned UAMI/federation, RBAC, and (if needed) the private runner.
- [ ] Dev team has the pipeline file committed to the pilot repo.
- [ ] The `threadlight-production-ready` handoff checklist **section G** is
      satisfied (the production deploy path exists).
