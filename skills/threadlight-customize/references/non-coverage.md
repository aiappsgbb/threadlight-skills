# Non-coverage boundary + decision log

> **Move 4 of `threadlight-customize`.** Close the engagement honestly. Copy
> this to `docs/threadlight-customize/non-coverage.md` and fill it in. Its job
> is to keep expectations calibrated for the customer's architecture / CISO
> review and to stop the next SE from assuming the onboarding is turnkey.

## Why a non-coverage statement is a deliverable

Threadlight deliberately **does not automate** a customer's production
onboarding. The seams below are where the customer's environment is unique and
where human judgment — not a generator — does the work. Naming them is the
honest, auditable thing to do, and it is faster than discovering them mid-review.

## What Threadlight deliberately does NOT automate

Confirm each line and add customer-specifics:

- **Landing-zone / network design.** Threadlight deploys *into* the customer's
  landing zone; it does not design VNets, subnets, firewall rules, or private
  DNS. Owner: customer platform team.
- **Identity & RBAC provisioning.** The deploy identity, federated credentials,
  and role assignments are created by the platform team (see `threadlight-cicd`
  env-setup runbooks). Threadlight consumes them.
- **Central platform / shared AI gateway.** Out of scope here — that is the
  `citadel-hub-deploy` / `citadel-spoke-onboarding` track.
- **Customer change-management & approvals.** Freeze windows, CAB, sign-off
  chains are the customer's process; Threadlight slots into them.
- **Customer-mandated IaC internals.** Where the customer requires their own
  modules (profile Block D), we build *on* them; we do not regenerate them.
- **Per-customer onboarding orchestration.** There is no auto-driver for this
  leg by design — it is human-led.

## Seams customized for THIS customer

A short record of what you changed, so the next SE can read the diff:

| Seam | Skill / file | What changed | Lives in overlay? | Rationale |
|---|---|---|---|---|
| _<e.g. deploy target RG>_ | _<skill / path>_ | _<…>_ | _<yes/no>_ | _<…>_ |
| _<network/private DNS wiring>_ | _<…>_ | _<…>_ | _<…>_ | _<…>_ |
| _<RBAC scope>_ | _<…>_ | _<…>_ | _<…>_ | _<…>_ |

## Decision log

| Date | Decision | Alternatives considered | Owner | Notes |
|---|---|---|---|---|
| _<date>_ | _<…>_ | _<…>_ | _<…>_ | _<…>_ |

## Handoff statement (paste into the architecture-review pack)

> Threadlight provided the pilot, the production-readiness assessment, and the
> deploy/CI-CD scaffolding. The production onboarding into _<customer>_'s
> environment was performed as a human-led customization (landing-zone
> targeting, private-network wiring, identity/RBAC consumption). The seams above
> are maintained in an overlay over a pinned upstream so future Threadlight
> updates remain mergeable. Items under "does NOT automate" remain owned by the
> _<customer>_ platform team.
