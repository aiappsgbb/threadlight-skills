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
      configured production environment only, without a main-branch credential
      that bypasses approval; ADO the separately protected service connection.
- [ ] Validation and production have distinct environments, identities and
      targets; reviewed policy includes each principal's client ID.

## RBAC
- [ ] Role assignments are **least-privilege** and scoped to the
      **target/spoke RG only** (runbook `02`) — not subscription, never hub.
- [ ] For **keyless** (managed-identity) Foundry targets, the deploy identity
      also has **Role Based Access Control Administrator** at the **same RG
      scope** — `azd provision` performs `roleAssignments/write` to grant the app
      identity its data-plane roles, which `Contributor` alone cannot do.
- [ ] Every assignment is documented and revocable.
- [ ] For `citadel-spoke`: the pilot identity has **no** role on the hub,
      shared APIM, shared networking, or platform Key Vault.

## Gates & stages
- [ ] Production promotion runs behind an **environment approval** (required
      reviewers / checks), not on every push.
- [ ] Reviewed application adapters prepare the separate preproduction target
      and execute actual eval/red-team producers; MCP runs the supplied checker.
- [ ] Strict current acceptance precedes the dependent production job. Missing,
      stale or contradictory evidence and required-domain failures block it.
- [ ] The candidate receipt SHA-256 travels through a separate CI output and is
      checked with source, policy, input hashes, run/attempt and freshness after
      identity waits and immediately before dispatch.
- [ ] Promotion uses the validated immutable image and a durable backend
      idempotency key. Post-promotion observation matches that digest; the
      adapter keeps business routing closed until required checks complete.
- [ ] Failure is not rollback. Required live governance, failover and telemetry
      checks remain separately authorized and evidenced.
- [ ] Optional native jobs use a private explicit context, pinned dependencies
      and current scoped owner approval, not inherited deployment credentials.

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
- [ ] The [release contract](release-contract.md), executable tooling, datasets
      and configured policy are reviewed; private outputs are Git-ignored.
- [ ] The `threadlight-production-ready` handoff checklist **section G** is
      satisfied (the production deploy path exists).
