# SPEC § 12 — Production Readiness template

> **What this is.** The recommended skeleton for the `§ 12 Production
> Readiness` block in `specs/SPEC.md`. Ships by default in the
> `threadlight-design` SPEC template; this file is the canonical
> reference for what each field means and what good values look like.

The block is consumed by `threadlight-production-ready` as the
**declared customer intent** for the pilot. Without § 12 the skill
exits 2 — production-readiness can't be assessed against an unstated
target.

## Recommended skeleton

```markdown
## § 12 — Production Readiness

### Target posture

target_posture: <citadel-spoke | agt | standard-ai-gateway | hybrid | unset>

# Leave `unset` if the customer hasn't decided yet. The skill will fall
# back to `standard-ai-gateway` for scoring (NOT citadel) so non-Citadel
# customers aren't spammed with Citadel findings.

### Must-have pillars

# Skill scores all 13 pillars by default. List any that are MUST-HAVE
# vs MAY-HAVE here. Missing must-have pillar findings = must-fix.
must_have_pillars:
  - network-posture
  - identity-access
  - secrets
  - observability
  - sre-handover
  - cost
  # Add others per customer requirement; otherwise default per-customer industry.

### Residency

residency:
  model_region: <e.g., westeurope>
  gateway_region: <e.g., westeurope>
  data_plane_region: <e.g., westeurope>
  telemetry_region: <e.g., westeurope>
  backup_region: <e.g., westeurope>
  allow_paired_region: <yes | no>            # backup region = paired region of primary?
  cross_border_support: <yes | no>           # may Microsoft Support access logs from outside region?

### RTO / RPO / SLA

rto: <e.g., 1h>
rpo: <e.g., 15m>
sla_target: <e.g., 99.9%>

### Incident ownership

incident_owner: <email | distribution list | Teams channel>
escalation_path:
  - L1: <on-call rota or team>
  - L2: <SRE / platform team>
  - L3: <product owner>

### Operations model

operations_model: <product-team-owned | sre-shared | sre-fully-owned>
sre_agent_adoption: <none | pilot | recipe-applied>   # azure-sre-agent recipe?

### Pricing / capacity

pricing_plan: <payg | ptu | mixed>
ptu_capacity:                       # only if ptu or mixed
  - region: <e.g., westeurope>
    model: <e.g., gpt-4o>
    ptus: <int>
fallback_to_payg: <yes | no>        # only if ptu

### Model lifecycle

models:
  - name: <deployment name, e.g., gpt-4o-prod>
    model: <e.g., gpt-4o>
    version: <pinned, e.g., 2024-11-20>
    region: <e.g., westeurope>
    role: <primary | fallback>
retirement_notice_owner: <email>
rollback_strategy: <traffic-switch | blue-green | rolling | none>

### Waivers policy

waivers:
  policy_doc: <link to customer's waiver acceptance process>
  default_expiry_days: 90
  approvers:
    - <name / role>
```

## Field guide

### `target_posture`

| Value | Meaning |
|---|---|
| `citadel-spoke` | Pilot is a spoke onto a Citadel Governance Hub. Pillar 1 will check APIM Access Contract presence. |
| `agt` | Pilot relies on AGT in-process middleware. Pillar 2 checks AGT capabilities. |
| `standard-ai-gateway` | Pilot routes through a customer-owned APIM AI Gateway that isn't the Citadel reference impl. |
| `hybrid` | Mix. Some workloads citadel, others agt. |
| `unset` | Posture not yet decided. Skill defaults to `standard-ai-gateway` for scoring (not citadel). |

### `must_have_pillars`

The 13 pillars (see SKILL.md) — list any that are MUST-HAVE for this
customer. By default the skill scores all 13; this field elevates
findings in named pillars to `must-fix` regardless of severity, and
demotes findings in unlisted pillars to `should-fix`. Use to capture
regulator-driven priorities (e.g., FSI must-have `secrets`, `sre-handover`).

### `residency`

The most under-declared field in pilots. Set every sub-field. The
skill scores each one independently against deployed reality. Default
to the customer's regulatory residency region (e.g., `eu`, `uksouth`,
`australia`); don't default to `westus`.

`allow_paired_region: yes` lets the backup live in the paired region
(e.g., `northeurope` ↔ `westeurope`). Set to `no` if the customer's
risk team requires the exact region.

`cross_border_support: no` means Microsoft Support access from outside
the residency region is not permitted — affects diagnostic settings
shipping logs to the wrong region.

### `rto` / `rpo` / `sla_target`

Express RTO/RPO in human-readable units (`15m`, `1h`, `4h`, `24h`).
The skill parses these for pillar 11 reliability scoring.

### `incident_owner` / `escalation_path`

Required for pillar 12. Email or distribution list, not a person's name
in a job role that rotates.

### `operations_model`

| Value | Meaning |
|---|---|
| `product-team-owned` | Product team paged for incidents; SRE consulted |
| `sre-shared` | Joint pager rota; SRE primary on infra incidents, product on logic incidents |
| `sre-fully-owned` | SRE pager; product team consulted |

### `pricing_plan`

| Value | Meaning |
|---|---|
| `payg` | Pay-as-you-go; no capacity reservation; rate-limited by Foundry defaults |
| `ptu` | Provisioned Throughput Units; capacity reservation per region per model |
| `mixed` | PTU baseline + PAYG overflow |

If `ptu` or `mixed`, list `ptu_capacity` per region+model. Set
`fallback_to_payg: yes` to absorb peak overflow.

### `models`

Every model deployment with name + model + version + region + role.
Pillar 13 cross-checks against the deployed reality.

### `retirement_notice_owner`

The person who watches Microsoft's Foundry deprecation notices for
each model and triggers a migration before the deprecation date. Pillar
13 surfaces a `should-fix` if this field is empty.

### `rollback_strategy`

| Value | Meaning |
|---|---|
| `traffic-switch` | Dual deployment, switch traffic by routing config |
| `blue-green` | Two environments, swap on cutover |
| `rolling` | Rolling restart of instances with new version |
| `none` | No rollback — risky for production |

## Example: FSI KYC pilot, Citadel-spoke

```markdown
## § 12 — Production Readiness

target_posture: citadel-spoke

must_have_pillars:
  - network-posture
  - identity-access
  - secrets
  - observability
  - responsible-ai
  - hitl-audit
  - cost
  - sre-handover

residency:
  model_region: westeurope
  gateway_region: westeurope
  data_plane_region: westeurope
  telemetry_region: westeurope
  backup_region: northeurope
  allow_paired_region: yes
  cross_border_support: no

rto: 1h
rpo: 15m
sla_target: 99.9%

incident_owner: ai-ops@bank.example
escalation_path:
  - L1: foundry-pilot-rota
  - L2: ai-platform-team
  - L3: head-of-ai

operations_model: sre-shared
sre_agent_adoption: recipe-applied

pricing_plan: ptu
ptu_capacity:
  - region: westeurope
    model: gpt-4o
    ptus: 100
fallback_to_payg: yes

models:
  - name: kyc-gpt4o
    model: gpt-4o
    version: "2024-11-20"
    region: westeurope
    role: primary
  - name: kyc-gpt4o-fallback
    model: gpt-4o-mini
    version: "2024-07-18"
    region: westeurope
    role: fallback
retirement_notice_owner: ai-ops@bank.example
rollback_strategy: traffic-switch

waivers:
  policy_doc: https://intranet.bank.example/risk/waivers
  default_expiry_days: 90
  approvers:
    - head-of-ai
    - ciso-delegate
```

## Example: SMB chatbot pilot, standard-ai-gateway (no Citadel)

```markdown
## § 12 — Production Readiness

target_posture: standard-ai-gateway

must_have_pillars:
  - identity-access
  - secrets
  - observability
  - cost
  - sre-handover

residency:
  model_region: eastus
  gateway_region: eastus
  data_plane_region: eastus
  telemetry_region: eastus
  backup_region: eastus2
  allow_paired_region: yes
  cross_border_support: yes

rto: 24h
rpo: 24h
sla_target: 99%

incident_owner: pilot-owner@smb.example
escalation_path:
  - L1: product-eng
  - L2: head-of-eng

operations_model: product-team-owned
sre_agent_adoption: none

pricing_plan: payg

models:
  - name: chat-gpt4o-mini
    model: gpt-4o-mini
    version: "2024-07-18"
    region: eastus
    role: primary
retirement_notice_owner: pilot-owner@smb.example
rollback_strategy: rolling

waivers:
  policy_doc: (none)
  default_expiry_days: 60
  approvers:
    - head-of-eng
```

## Notes

- The block is **markdown** (the skill reads it from `specs/SPEC.md`),
  but the structure mirrors a YAML manifest. Future versions of the
  skill may consume a machine-readable `specs/manifest.json` § 12
  block — for now, markdown parsing is sufficient.
- The block should ship **by default** in the `threadlight-design`
  SPEC template, with TODO markers on every field. This forces every
  pilot to confront the production posture decision at design time,
  not at go-live time.
- The field set is intentionally a superset of what most pilots need.
  Don't be tempted to trim it — the missing field is the one that
  ends up as the production gap.
