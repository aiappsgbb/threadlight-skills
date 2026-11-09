# Phase 1.5: Deployment-posture gate

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when recording or re-running the deployment posture.

## Phase 1.5: Deployment-Posture Gate (interactive when SPEC § 11f is silent)

**Trigger (two paths):** mirror of Phase 7 (Citadel) — explicit when SPEC
pre-declares, interactive when SPEC is silent. The goal is the same: never
silently equate "deploy" with pilot-posture defaults, especially when the
operator said "production-grade".

### Why this is its own phase (not folded into Phase 1)

- Phase 1 reads the spec; Phase 1.5 records an **effective posture decision**
  that downstream phases (Phase 6 module composer, Phase 5 azd hooks) consume
  mechanically.
- The posture record outlives a single run — when an operator re-runs
  `threadlight-deploy` after a tweak, Phase 1.5 reads
  `specs/deployment-posture.md` first and only re-prompts when the SPEC and
  the posture file disagree, or when the file is missing.
- "Production-grade" means different things in different deployments
  (public ingress at a startup, private endpoints + DR at a regulated
  bank). Asking once and writing the answer down avoids 20-30 min provision
  cycles spent rediscovering the same trade-offs.

### Inputs / Outputs

- **Reads**: `specs/SPEC.md` § 11f (optional) and `specs/deployment-posture.md`
  (when present from a prior run).
- **Writes**: `specs/deployment-posture.md` — the canonical deploy-time
  decision record. Phase 5 / 6 hooks materialize `azd env` vars from this
  file later; **Phase 1.5 does NOT write `azd env` vars itself** (the azd
  scaffold doesn't exist yet at this point).

**Authority order** (drift mitigation): SPEC § 11f → `specs/deployment-posture.md`
→ `azd env` vars (materialized only, never authoritative).

### Path 1: SPEC § 11f pre-declares `deployment_target` **OR** a posture file already exists

When **either** of the following is true, Phase 1.5 takes the matching
posture defaults **silently** (no operator prompt) and proceeds to Phase 2:

1. **SPEC § 11f sets `deployment_target`** to `demo-sandbox`,
   `customer-pilot`, or `production-bound`. Phase 1.5 writes
   `specs/deployment-posture.md` with `source: provided` on every row.
2. **`specs/deployment-posture.md` already exists** with a valid
   `deployment_target` value (and no conflicting SPEC § 11f — see Re-run
   contract below). This is the canonical **CI / non-interactive override
   pattern**: a wrapper (workflow, script, prior run) seeds the posture file
   ahead of time, and Phase 1.5 honours it. Do NOT overwrite an existing
   posture file with a fresh interactive prompt.

Default posture per target (each row tagged as **supported-now** today vs
**deferred** = recorded in posture but NOT auto-implemented in this PR):

| Posture row        | demo-sandbox          | customer-pilot                  | production-bound                          | supported-now? |
|--------------------|-----------------------|---------------------------------|-------------------------------------------|----------------|
| Networking         | public                | public                          | private-required                          | public ✓ / private-required = **deferred** (needs `threadlight-deploy/references/azd-modules` VNet work) |
| Replicas           | single                | single                          | ha-min-replicas                           | ✓ (verify per-module) |
| Model pinning      | preview-ok            | ga-pinned                       | ga-pinned                                 | ✓ (via `__MODEL_VERSION__`) |
| Retention          | 90d                   | 90d                             | regulated-7y or customer-defined          | ✓ (Log Analytics retention param) |
| Defender for Cloud | off + Ignore tag      | off + Ignore tag                | on + alerts                               | off ✓ / on = **deferred** (separate plan enrollment) |
| Cost guardrails    | none                  | none                            | budget + alert rules                      | none ✓ / budgets = **deferred** |
| Backup / DR        | none                  | none                            | Cosmos PITR + paired-region failover      | PITR ✓ (when Cosmos selected) / failover = **deferred** |
| Continuous eval    | Plan A defaults       | Plan A defaults                 | scheduled cadence + on-call routing       | ✓ (via `microsoft-foundry`) |

### Path 2: SPEC § 11f absent — ask the operator once

When the SPEC does not pre-declare § 11f, Phase 1.5 prompts the operator
with **audience-neutral wording** (no "the customer's tenant" — say "the
target subscription" so internal IT / SI partner operators see themselves
in the question):

> **Deployment posture.** What kind of deployment is the target
> subscription expecting? This determines defaults for networking,
> replicas, model pinning, retention, Defender, cost guardrails, and DR.
> - `demo-sandbox` — throwaway RG, public ingress, single replica,
>   preview model OK, no Defender, no DR. (default — safe to pick when
>   unsure)
> - `customer-pilot` — short-lived in the target subscription, GA-pinned
>   model, 90d retention, no Defender, no DR.
> - `production-bound` — meant for sustained operation. Triggers the
>   production-grade checklist below.
> Choose [demo-sandbox / customer-pilot / production-bound]:

### T-0 operator-blocking checks (foreground these for `customer-pilot` and `production-bound`)

Before letting Phase 2 generate runtime files, surface the items the
operator MUST confirm now — discovering them at `azd up` time is the
failure mode this gate exists to prevent:

- **Azure RBAC for selected posture** — required role assignments
  confirmed (production-bound typically needs Contributor + Network
  Contributor for PE + Defender Plan Admin)
- **Tenant / subscription / region** — `az account show` matches
  expectation; per-tenant `AZURE_CONFIG_DIR` set per the
  `threadlight-tenant-isolation` skill
- **Quota / capacity / model availability** — preflight
  `az cognitiveservices usage list` for the selected model in the chosen
  region
- **Public ingress vs private networking** — choose now, not at provision
  time. Private requires VNet + Private Endpoints (deferred today)
- **Secrets / API-key exception** — if any, requires Key Vault and
  breaks the keyless mandate; record explicitly in posture file
- **Owner / on-call contact** — recorded for production-bound handoff

### Production-grade checklist (surfaced for customer-pilot and production-bound)

Walk the operator through each row of the table above. For each row,
record their choice and tag with `supported-now` (composer/module already
exposes the param) or `deferred` (recorded in posture, NOT auto-implemented
this PR — surfaces as `<!-- TODO(posture): ... -->` in `main.bicep`).

### Persist to `specs/deployment-posture.md`

```yaml
# specs/deployment-posture.md — canonical deploy-time decision record
deployment_target: production-bound
source: provided  # or: inferred | defaulted-after-skip | open-question
chosen_at: 2026-05-23T14:02:00Z
operator: alex@partner.example
overrides:
  networking: private-required   # status: deferred
  replicas: ha-min-replicas       # status: supported-now
  retention: regulated-7y         # status: supported-now
  model_pinning: ga-pinned        # status: supported-now
  defender: on                    # status: deferred
  cost_guardrails: budget+alerts  # status: deferred
  backup_dr: pitr+failover        # status: PITR supported-now / failover deferred
  continuous_eval: scheduled      # status: supported-now
deferred_decisions:
  - waf-front-door
  - dr-runbook
```

> Phase 5 / 6 hooks read this file later and materialize `azd env` vars
> from `supported-now` rows. `deferred` rows surface as TODO comments in
> `main.bicep` (see Phase 6 Step 1.7).

### Re-run contract

When Phase 1.5 runs and `specs/deployment-posture.md` and/or SPEC § 11f
already exist:

1. **Read** `specs/deployment-posture.md` if present.
2. **If both SPEC § 11f and the posture file exist**: diff them. On conflict,
   surface the conflict and ask which wins (do NOT silently trust either).
   When they agree (or only one is present), proceed silently.
3. **If only the posture file exists and SPEC § 11f is silent**: accept the
   posture file as canonical and proceed **non-interactively** to Phase 2.
   Do NOT re-render the checklist and do NOT prompt — this is the contract
   the CI / non-interactive override pattern relies on (see Path 1 #2).
4. **Operator-initiated tweak only**: if the operator explicitly asks to
   change a row (interactive session, not CI), re-render the checklist with
   current values pre-filled and only re-prompt rows being changed.

### Cross-references

- Production-bound posture biases **Phase 7 (Citadel handoff)** toward
  "yes, ask" — a governance hub is the norm for prod. See Phase 7 § Path 2.
- Phase 6 (Module Composer) reads `specs/deployment-posture.md` and passes
  posture-sensitive params to Bicep modules ONLY for `supported-now` rows.
  `deferred` rows become TODO comments. See Phase 6 § Step 1.7.
