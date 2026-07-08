# Onboarding-path decision tree

The skill runs this gate **before generating any artifact**. It decides
whether the pilot needs a central platform environment and, if so, whether
that environment already exists — then routes to the right sibling skill. The
resolved choice is written to `docs/threadlight-cicd/onboarding-path.json`.

```
Does the pilot require a CENTRAL PLATFORM environment?
(Citadel hub / shared AI gateway / shared networking / platform Key Vault)
│
├─ NO ──────────────────────────────────────────────► path = standalone
│       Double-check first (ask): target sub/RG, posture
│       (standard-ai-gateway | agt | direct), any shared/existing
│       resources consumed, network exposure (public vs private).
│       RBAC scope = target-rg. needs_validation = true.
│
└─ YES
   │
   Is that central environment ALREADY deployed?
   │
   ├─ YES ─────────────────────────────────────────► path = spoke-onboard
   │       Onboard the pilot as a SPOKE: consume the hub via an
   │       Access Contract → citadel-spoke-onboarding.
   │       Ask for hub coordinates (hub sub, APIM resource id,
   │       access-contract product) to validate the contract.
   │       posture = citadel-spoke. RBAC scope = spoke-rg.
   │
   └─ NO ──────────────────────────────────────────► path = hub-deploy-then-spoke
           Stand the hub up on the SEPARATE central track →
           citadel-hub-deploy (awesome-gbb, different repo/pipeline),
           THEN citadel-spoke-onboarding to wire the pilot in.
           The pilot pipeline still never deploys the hub.
           posture = citadel-spoke. RBAC scope = spoke-rg.
```

## When to engage which sibling skill

| Path | Engage | Repo / pipeline | RBAC scope |
|---|---|---|---|
| `standalone` | (none) | pilot repo only | target RG |
| `spoke-onboard` | `citadel-spoke-onboarding` | pilot repo (consumes hub) | spoke RG |
| `hub-deploy-then-spoke` | `citadel-hub-deploy` **then** `citadel-spoke-onboarding` | **central** repo for the hub, pilot repo for the spoke | spoke RG (pilot); hub scope stays with the central team |

## Invariants

- Spoke paths (`spoke-onboard`, `hub-deploy-then-spoke`) **always** resolve
  `rbac_scope = spoke-rg`. The pilot identity is never granted hub scope.
- `citadel-hub-deploy` is mentioned **only** on the `hub-deploy-then-spoke`
  path (the hub doesn't yet exist). When the hub already exists, the gate
  points at `citadel-spoke-onboarding` only.
- The generator emits the pipeline + runbooks; it **never** deploys a hub or
  invokes a sibling skill itself. Hub/spoke onboarding is an operator action
  taken on the central track with consent.

See `central-platform-boundary.md` (generated per-run) for the boundary the
chosen path must respect, and `best-practices.md` for the federation/RBAC
rationale.
