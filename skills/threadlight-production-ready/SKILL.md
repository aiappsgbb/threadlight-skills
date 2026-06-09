---
name: threadlight-production-ready
description: >
  Use after threadlight-safe-check --phase post-deploy returns green to take a
  deployed pilot from "lab" to "ready for customer architecture review / CISO
  sign-off / paved path to production". Reads SPEC § 12 (target posture,
  must-have pillars, residency, RTO/RPO, SLA, incident owner) and probes Azure
  to produce an advisory production-readiness scorecard + uplift plan +
  customer-facing hand-off package across 13 pillars. Citadel-spoke recommended,
  AGT v4 in-process middleware second, standard AI Gateway / VNet third. Soft
  advisory — never fails a build; missing live-probe permissions degrade
  gracefully to `not-verified`.
  USE FOR: production readiness, prod-ready gate, customer architecture review,
  CISO sign-off prep, pilot-to-prod handover, paved path to production, citadel
  uplift, AGT uplift, AI gateway uplift, production checklist, production
  scorecard, go-live readiness, lab graveyard, hand-off package, residual risk,
  go-live recommendation, would-fail-hard-gate, evidence register, waiver
  register, model lifecycle, retirement notice, SRE handover, RACI.
  DO NOT USE FOR: deployment itself (threadlight-deploy), structural
  completeness gate (threadlight-safe-check), invocation testing (foundry-evals),
  in-process middleware authoring (foundry-agt), citadel hub provisioning
  (citadel-hub-deploy), citadel access contracts (citadel-spoke-onboarding),
  SRE Agent provisioning (azure-sre-agent), AppIn wiring (foundry-observability).
metadata:
  version: "1.0.0"
---

# Threadlight Production Ready — paving the path to production

The single skill in the chain that asks "**is this pilot ready for the
customer architecture review, or is it about to land in the lab graveyard?**"
and answers with a structured, evidence-backed artefact instead of tribal
knowledge.

> **Why this skill exists.** The `threadlight-*` chain ships a working
> agent in one session (design → local-test → deploy → safe-check).
> `safe-check` proves the pilot is **structurally complete and behaves**:
> every selector landed, every channel reaches, every cron ran, no
> placeholder image. But "green safe-check" ≠ "production-ready". The
> next conversation — CISO, SRE, FinOps, network architect, data
> protection — needs an artefact that says: **what posture is this in,
> what's missing, what would the uplift cost, who owns each gap, can we
> go live with waivers?** Without that artefact every pilot grows a
> tribal-knowledge answer that takes weeks to assemble, the customer
> defers the production phase, and the pilot quietly becomes a "lab
> graveyard" demo.
>
> This skill produces the artefact in one command.

## What this skill does NOT replace

| Concern | Use instead |
|---|---|
| Authoring SPEC / `deployment_manifest{}` | `threadlight-design` |
| Running `azd up` | `threadlight-deploy` |
| Structural / behavioural deploy gate | `threadlight-safe-check --phase post-deploy` |
| Invocation testing of the agent | `foundry-evals` |
| Wiring App Insights / OTel | `foundry-observability` |
| Provisioning Citadel hub | `citadel-hub-deploy` |
| Onboarding spoke to Citadel | `citadel-spoke-onboarding` |
| Provisioning Azure SRE Agent | `azure-sre-agent` |
| Authoring AGT in-process middleware | `foundry-agt` |
| Generating Bicep / Terraform | `azd-patterns`, `azureterraform`, `bicepschema` |
| Deploying to a VNet-injected Foundry | `foundry-vnet-deploy` |

**This skill recommends, never executes.** Every "must-fix" links to the
remediation skill above; the operator (or a follow-up Copilot session)
runs that skill.

## When to invoke

| You are at… | Run | Get |
|---|---|---|
| `safe-check --phase post-deploy` returned green and the customer wants to talk about production | `python tests/production_ready.py` | Markdown report + JSON manifest |
| Customer architecture review in 3 days | `python tests/production_ready.py --target citadel-spoke` (or other posture) | Same as above, scored against the declared target |
| Pilot has been parked for weeks; someone asks "could we ship this?" | `python tests/production_ready.py --static` (no live Azure auth needed) | Pure static scorecard from repo + safe-check manifests |
| You inherited a pilot whose SPEC has no § 12 | Skill still runs — posture falls back to `standard-ai-gateway`, an `RDY-002` finding surfaces "SPEC § 12 missing — add it from `references/spec-section-12-template.md`" | Author § 12, re-run for full scorecard |
| AGT v4 just shipped and you want to know if your pilot drifts | `python tests/production_ready.py --pillar agent-governance --agt-profile v4_preview` | AGT-only scorecard against the v4 profile |
| Customer accepted some `must-fix` findings as risk | Author `tests/production-readiness-waivers.json`, re-run | Report shows `score_with_waivers` and `would_fail_hard_gate` flags |

> **Rule of thumb.** This skill runs at most twice per pilot
> lifecycle: once when the pilot is heading into the customer
> architecture review (the artefact that lives in the deck), and once
> immediately before the go-live decision (the artefact that goes to
> CISO / the change advisory board). Running it every commit is noise.

## The 13 pillars

Each pillar gets its own reference doc under `references/pillars/`; the
skill ships with prose-heavy guidance per pillar so the LLM can reason
about findings, not just emit them.

| # | Pillar | What "good" looks like | Primary remediation skill |
|---|---|---|---|
| 1 | [`network-posture`](references/pillars/01-network-posture.md) | Resolved posture target met (Citadel spoke / AGT / VNet / standard); **data-residency sub-scored** (model region, APIM region, data-plane regions, backups, cross-border support) | `citadel-spoke-onboarding`, `foundry-vnet-deploy`, `foundry-network-runbook` |
| 2 | [`agent-governance`](references/pillars/02-agent-governance.md) | AGT in-process middleware wired (capability-based, version-agnostic); policy + verifier artefacts present; OWASP-ASI evidence current | `foundry-agt` |
| 3 | [`identity-access`](references/pillars/03-identity-access.md) | Workloads use managed identity; **no client secrets**; RBAC least-privilege; KV access via RBAC not access policies | `foundry-hosted-agents`, `azure-tenant-isolation`, `azd-patterns` |
| 4 | [`secrets`](references/pillars/04-secrets.md) | Key Vault with **soft-delete + purge protection**; no hardcoded secrets in repo; rotation policy declared; control-plane vs data-plane access scoped | `azd-patterns`, `foundry-hosted-agents` |
| 5 | [`observability`](references/pillars/05-observability.md) | App Insights connected at **account-level** (Foundry); OTel emit verified (recent traces); alert rules wired; workbook + retention declared | `foundry-observability` |
| 6 | [`continuous-evals`](references/pillars/06-continuous-evals.md) | SPEC § 9 scenarios scheduled (Plan A or Plan B); threshold alerts wired; last run within freshness window; eval datasets stored | `foundry-evals` |
| 7 | [`responsible-ai`](references/pillars/07-responsible-ai.md) | Content filters, jailbreak shields, grounded-language eval; AGT RAI policy; PII redaction declared; allow/deny tested | `foundry-agt`, `foundry-evals` |
| 8 | [`hitl-audit`](references/pillars/08-hitl-audit.md) | If SPEC § 8 declares gates: wired, persistent audit trail, escalation channel reachable, idempotent | `threadlight-hitl-patterns` |
| 9 | [`supply-chain`](references/pillars/09-supply-chain.md) | Container images pinned **by digest**; Bicep modules pinned; dependency scanning enabled; SBOM emitted | `azd-patterns` |
| 10 | [`cost`](references/pillars/10-cost.md) | Pricing plan declared (PAYG vs PTU); budget + anomaly alerts wired; forecast vs budget cap; idle-resource sweep done | `paygo-ptu-cost-analyzer` |
| 11 | [`reliability`](references/pillars/11-reliability.md) | Multi-region plan vs RTO/RPO from § 12; **backup/restore tested** (not just "configured"); runbook exists; chaos test done | `foundry-vnet-deploy`, `foundry-caphost-lifecycle` |
| 12 | [`sre-handover`](references/pillars/12-sre-handover.md) | **Evidence-based:** incident owner + escalation path; runbook links; alert destinations; SRE Agent resource/recipe if selected; handoff acceptance signed | `azure-sre-agent` (with `threadlight-pilot-handover` recipe) |
| 13 | [`model-lifecycle`](references/pillars/13-model-lifecycle.md) | Model deployment **names + versions pinned** (no `latest`); fallback model declared; retirement-notice owner; A/B or rollback strategy; region/capacity documented | `paygo-ptu-cost-analyzer`, `foundry-hosted-agents` |

**Per-finding status taxonomy:**

| Status | Meaning | Counts toward raw score? |
|---|---|---|
| `pass` | Check ran and the pillar requirement is met | ✅ |
| `should-fix` | Gap exists; not a hard blocker but should be addressed before go-live | ❌ |
| `must-fix` | Hard blocker for production go-live; would fail a v2 hard-gate | ❌ |
| `not-applicable` | Check correctly skipped (e.g., Citadel scoring against an AGT-target deployment) | ✅ (counts as pass for raw, with justification) |
| `not-verified` | Check could not run (no Azure auth, insufficient RBAC, static-only mode) | ⚪ (excluded from raw score; surfaced in `not_verified[]`) |
| `waived` | Customer explicitly accepted the gap with documented compensating control | ✅ in `score_with_waivers`, ❌ in `raw_score` |

## CLI

The CLI lives at `skills/threadlight-production-ready/scripts/production_ready.py`.
Pilot repos either:

1. **Install as a package wrapper** (matches `threadlight-safe-check`):
   create `threadlight/__init__.py` and drop `production_ready.py` as
   `scripts/production_ready.py` (copy into `tests/production_ready.py` in pilot repos, same pattern as `safe_check.py`). Invoke as `python tests/production_ready.py`.
2. **Copy as a tests script:** copy to `tests/production_ready.py`. Invoke
   as `python tests/production_ready.py`.

Both invocations are supported. The `-m` form is canonical (matches
`safe-check`); the file-path form is the no-packaging fallback.

```bash
# Default — all 13 pillars, live + static, both outputs
python tests/production_ready.py

# Subset of pillars
python tests/production_ready.py --pillar network-posture,observability

# Static only (no Azure auth required; live checks all → not-verified)
python tests/production_ready.py --static

# Quick smoke (subset of checks per pillar; for iteration)
python tests/production_ready.py --quick

# Explicit posture override (overrides SPEC § 12 resolution)
python tests/production_ready.py \
  --target citadel-spoke|agt|standard-ai-gateway|hybrid

# AGT profile (capability-based, version-agnostic)
python tests/production_ready.py --agt-profile auto|v3_7|v4_preview|none

# Explicit waiver file path
python tests/production_ready.py \
  --waivers tests/production-readiness-waivers.json

# Allow stale safe-check manifest (default rejects >24h or RG/sub/hash mismatch)
python tests/production_ready.py --accept-stale-safe-check

# Override output paths
python tests/production_ready.py \
  --out tests/production-readiness-manifest.json \
  --report docs/production-readiness-report.md

# Quiet output for CI / hooks
python tests/production_ready.py --quiet
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Checks ran and report was written. Per-finding statuses (including `must-fix` and `not-verified`) live inside the report. **The skill never returns non-zero for findings in v1 — it is soft-advisory.** |
| `2` | Missing prerequisite: no `specs/manifest.json`, no `tests/postdeploy-manifest.json`, safe-check manifest stale (use `--accept-stale-safe-check` to override) or scope-mismatched (different subscription/RG), or unknown `--pillar` id. **Missing SPEC § 12 does NOT exit 2** — the skill emits an `RDY-002` finding, falls back to `standard-ai-gateway` posture, and still produces the report. |
| `3` | I/O failure: cannot read inputs, cannot write outputs, `az` not on PATH at all. |

> Missing Azure auth or insufficient permissions for specific live
> probes ⇒ those checks are marked `not-verified` in the report;
> exit code stays `0`. The skill *never* turns into a deployment
> blocker by accident.

## Files in this skill

```
threadlight-production-ready/
├── SKILL.md                              (this file)
├── scripts/
│   └── production_ready.py               (single-file CLI; stdlib + az subprocess)
└── references/
    ├── spec-section-12-template.md       (how to author SPEC § 12)
    ├── report-template.md                (markdown report skeleton)
    ├── waivers-schema.json               (waiver file shape — JSON Schema)
    ├── handoff-checklist.md              (customer pre-go-live checklist)
    ├── live-probe-permissions.md         (per-check minimum Azure RBAC, tiered)
    ├── pillars/                          (one md per pillar — 13)
    │   ├── 01-network-posture.md
    │   ├── 02-agent-governance.md
    │   ├── 03-identity-access.md
    │   ├── 04-secrets.md
    │   ├── 05-observability.md
    │   ├── 06-continuous-evals.md
    │   ├── 07-responsible-ai.md
    │   ├── 08-hitl-audit.md
    │   ├── 09-supply-chain.md
    │   ├── 10-cost.md
    │   ├── 11-reliability.md
    │   ├── 12-sre-handover.md
    │   └── 13-model-lifecycle.md
    └── fixtures/
        └── sample-pilot/                 (mocked SPEC + manifest + az responses)
```

The CLI is **one file** (~600-800 LOC) intentionally — same posture as
`safe-check`. **Dependencies: stdlib + `az` CLI subprocess only.** No
`azure-mgmt-*` SDK packages, no `azure-identity` direct use. `az` carries
the `AzureCliCredential` for free.

## Inputs

| Source | Used for | Required? |
|---|---|---|
| `specs/SPEC.md` § 12 | Target posture, must-have pillars, residency, RTO/RPO, SLA, incident owner | **Yes** (skill exits 2 without it) |
| `specs/manifest.json` `deployment_manifest{}` | Selector-to-resource map; consumed by pillar 1 (network), 5 (observability) | Yes |
| `tests/postdeploy-manifest.json` | Latest `safe-check --phase post-deploy` output; **pre-flight checks freshness, RG/sub match, hash** | Yes |
| `infra/**/*.bicep`, `azure.yaml`, `src/**/Dockerfile` | Static analysis (pillars 4, 9, 10, 11, 13) | Yes |
| `tests/production-readiness-waivers.json` | Customer-accepted findings | Optional |
| `azd env get-values` | Current deployment binding (subscription, resource group, region) | Yes for live mode |
| Live Azure via `az` | Live probes (tiered per pillar — see [`references/live-probe-permissions.md`](references/live-probe-permissions.md)) | Optional (default on); missing perms → `not-verified` |

### Pre-flight: safe-check manifest validation

Before running any pillar, the skill validates `tests/postdeploy-manifest.json`:

1. **Exists** — file present at `--in-postdeploy` path (default `tests/postdeploy-manifest.json`).
2. **Phase** — top-level `phase == "post-deploy"`.
3. **Freshness** — `checked_at` timestamp within the freshness window
   (default 24h; override with `--accept-stale-safe-check`).
4. **Scope match** — `subscription_id` and `resource_group` match the
   current `azd env get-values` output (override with `--accept-stale-safe-check`).
5. **Hash match** — SHA256 of `specs/manifest.json` `deployment_manifest{}`
   matches the hash safe-check signed (catches "manifest changed after
   safe-check passed").

A stale or mismatched safe-check manifest is gameable — the operator
could pass safe-check, then edit the deployment, then run
production-ready. The hash and freshness checks prevent that. Override
explicitly with `--accept-stale-safe-check` if you know what you're doing.

## Outputs

### `tests/production-readiness-manifest.json` (machine-readable scorecard)

```jsonc
{
  "schema_version": "1.0",
  "generated_at": "2025-06-09T22:30:00Z",
  "tool_version": "1.0.0",
  "posture": {
    "declared": "citadel-spoke",     // from SPEC § 12 target_posture
    "detected": "citadel-spoke",     // from Azure evidence (APIM hub conn, etc.)
    "resolved": "citadel-spoke",     // CLI > SPEC § 12 > § 11b > evidence > standard-ai-gateway
    "resolution_path": [
      "CLI --target not provided",
      "SPEC § 12 target_posture = citadel-spoke",
      "Evidence: APIM Foundry connection present in spoke RG"
    ]
  },
  "score": {
    "raw": {                    // raw score — waivers do NOT improve this
      "pass": 28,
      "should_fix": 6,
      "must_fix": 3,
      "not_applicable": 4,
      "not_verified": 2,
      "total_assessable": 41,
      "percent_pass": 78
    },
    "with_waivers": {           // raw + accepted waivers folded in
      "pass": 30,
      "should_fix": 5,
      "must_fix": 1,
      "not_applicable": 4,
      "not_verified": 2,
      "waived": 3,
      "percent_pass_with_waivers": 86
    }
  },
  "go_live_recommendation": "ready_with_waivers",  // ready | ready_with_waivers | ready_with_unverified_risk | not_ready
  "would_fail_hard_gate": true,                    // bool — preview of v2 hard-mode behavior
  "verification_coverage": {                       // how much of the report is actually evidence vs gaps
    "verified": 22,
    "total_scoreable": 41,
    "percent": 53
  },
  "summary": {
    "top_findings": [
      { "pillar": "secrets", "id": "SEC-004", "status": "must-fix",
        "title": "Key Vault lacks purge protection",
        "remediation_skill": "azd-patterns" },
      // ... up to 5
    ]
  },
  "pillars": [
    {
      "id": "network-posture",
      "score": "pass-with-should-fix",
      "subsections": [
        { "id": "residency", "score": "pass", "findings": [/*...*/] }
      ],
      "findings": [
        { "id": "NET-001", "status": "pass",
          "title": "Resolved posture target met (citadel-spoke)",
          "evidence_ref": "EV-101" }
      ]
    }
    // ... 12 more
  ],
  "evidence_register": [
    {
      "id": "EV-101",
      "command": "az resource list -g rg-pilot --resource-type Microsoft.ApiManagement/service/apis",
      "scope": "subscription:abc.../resourceGroup:rg-pilot",
      "ran_at": "2025-06-09T22:29:55Z",
      "permission_tier": 5,
      "permission_role_required": "API Management Service Reader",
      "result": "1 resource matched"
    }
    // ... every probe recorded
  ],
  "not_verified": [
    {
      "id": "NV-001",
      "pillar": "cost",
      "check_id": "COST-002",
      "reason": "Cost Management Reader role missing on subscription",
      "permission_tier_required": 3
    }
  ],
  "waivers_applied": [
    {
      "id": "W-001",
      "finding_id": "REL-003",
      "owner": "alice@customer.com",
      "expiry": "2025-09-30",
      "justification": "Multi-region cutover scheduled for Q3",
      "compensating_control": "Backup tested weekly + restore drill 2025-07-15",
      "accepted_risk": "RPO 24h vs target 1h during cutover window"
    }
  ],
  "context": {
    "subscription_id": "abc...",
    "subscription_name": "Customer Sandbox",
    "tenant_id": "def...",
    "resource_group": "rg-pilot",
    "region": "westeurope",
    "azd_env_name": "pilot-fsi"
  }
}
```

### `docs/production-readiness-report.md` (customer-facing markdown)

10 sections, all required in v1 (no opt-out):

1. **Executive summary** — one page. Resolved posture, raw + waiver score, top 5 gaps, go-live recommendation, would-fail-hard-gate flag.
2. **Posture diagram** — current vs. target (Citadel spoke / AGT / standard / hybrid), as a mermaid block.
3. **Hard-gate preview** — what would fail if this were a gate, not advisory. The bridge to v2.
4. **Pillar scorecard** — 13-row table with score per pillar, plus the residency sub-section under pillar 1.
5. **Pillar deep-dives** — one block per pillar with all findings, evidence references, remediation links.
6. **Uplift plan** — ordered next steps. Each step links to the awesome-gbb skill that fixes it.
7. **Cost projection** — current usage → forecast under target SLA; PAYG vs PTU recommendation; idle-resource sweep.
8. **Eval summary** — last `foundry-evals` run vs SPEC § 9 thresholds; trend line if multiple runs available.
9. **Residual risk register + RACI + rollout/rollback/cutover** — the "what's left after waivers, who owns it, how do we land in production safely?" trio.
10. **Appendix** — glossary, reference architecture diagram, evidence register (table), waiver register (table), assumptions list.

The report is the customer-facing artefact. It's intentionally
dense — designed to land in a deck, an SRT, a CISO review, and a CAB
ticket without further editing.

## Posture target resolution

Citadel is the **recommended** enterprise posture, but the skill never
defaults *detection* to Citadel — that would spam non-Citadel customers
with irrelevant findings. The resolution order:

```
1. CLI --target flag                            (operator override)
2. SPEC § 12 target_posture                     (declared customer intent)
3. SPEC § 11b governance_hub.required: yes      (signals Citadel intent)
4. Deployed evidence:
   • APIM Foundry connection in current sub  →  citadel-spoke
   • Foundry account VNet-injected           →  hybrid / vnet
   • AGT middleware visible in src/agent     →  agt (if posture not else set)
   • Otherwise                               →  standard-ai-gateway
5. Default → standard-ai-gateway                (NOT Citadel)
```

When the resolved target ≠ Citadel:

- `network-posture` findings about Citadel are scored `not-applicable`
  (so they don't drag the raw score down).
- The executive summary surfaces a **non-scoring callout**: *"Recommended
  enterprise posture: Citadel-spoke. To assess against this posture,
  declare `target_posture: citadel-spoke` in SPEC § 12 or pass
  `--target citadel-spoke`."*
- The report includes a short side-comparison so the customer sees
  what Citadel would add (and what it would cost) **without being
  scored against it**.

## Live-probe permission tiers

Each check declares the minimum Azure RBAC tier it needs. Missing the
tier ⇒ the check is marked `not-verified`, the operator sees a
remediation hint, and the exit code stays `0`.

| Tier | Role | What it unlocks |
|---|---|---|
| 1 | `Reader` (sub or RG) | Resource inventory, types, names, tags, network config, role assignments, App Insights existence |
| 2 | `Monitoring Reader` / `Log Analytics Reader` | KQL queries for trace freshness, alert rule presence, workbook count |
| 3 | `Cost Management Reader` | Budget presence, anomaly alerts, PAYG/PTU split, idle resources |
| 4 | `Key Vault Reader` (control plane) | Vault config, soft-delete, purge protection, RBAC vs access-policies. **Never reads secret values, even with permission.** |
| 5 | Reader on the **Citadel hub RG** + `API Management Service Reader` | APIM Access Contract presence, Foundry connection status, hub-spoke wiring |

See [`references/live-probe-permissions.md`](references/live-probe-permissions.md)
for the per-check mapping. The skill prints a "what would I check with
more permissions?" hint at the end of the report so the operator can
go back and elevate before the customer review.

## Waiver discipline

Waivers turn `must-fix` into "accepted risk + compensating control"
without falsifying the raw score. The skill enforces a strict shape so
"waive everything" doesn't quietly produce a green report.

### Schema (see `references/waivers-schema.json`)

```jsonc
{
  "schema_version": "1.0",
  "waivers": [
    {
      "id": "W-001",                         // string, unique within file
      "finding_id": "SEC-004",               // must match a finding in the report
      "owner": "alice@customer.com",         // accountable person
      "expiry": "2025-09-30",                // ISO date; waiver invalidates after
      "justification": "string",             // why is this acceptable?
      "compensating_control": "string",      // what offsets the risk?
      "accepted_risk": "string"              // who is taking the residual risk?
    }
  ]
}
```

All five fields (`owner`, `expiry`, `justification`, `compensating_control`,
`accepted_risk`) are **required**. Missing any field ⇒ waiver is
ignored and a `WAIVER-INVALID` finding is added to the report.

### Score interaction

- `raw_score` never improves via waivers. It reflects the actual gap state.
- `score_with_waivers` is what you show the customer. Waived findings move
  from `must-fix` to `waived`.
- `would_fail_hard_gate: true` is set when the raw score has any
  `must-fix` finding even if waivers cover them — the report shows
  "this would NOT pass a hard gate; you are going live with N accepted
  risks" prominently.

This prevents the failure mode where a pilot ships with everything
waived and the next reviewer can't tell.

## TDD pressure scenarios (RED baseline)

These are the scenarios the skill must handle correctly. Use the
`writing-skills` TDD RED-GREEN-REFACTOR cycle to validate.

1. **No Azure auth, live default.** Must not exit non-zero. Must produce a report with `not-verified` covering every live check. Exec summary shows static-only mode.
2. **Reader-only Azure auth.** Tier-1 checks pass; tier 2/3/4/5 skipped as `not-verified` with remediation hints.
3. **Stale post-deploy manifest (>24h).** Must reject with exit 2 unless `--accept-stale-safe-check`.
4. **Subscription/RG mismatch between manifest and current `azd env`.** Same — exit 2 unless override.
5. **Hash mismatch (`deployment_manifest{}` changed after safe-check).** Same — exit 2 unless override.
6. **Standard AI Gateway target, no Citadel hub.** Must **not** spam Citadel findings — score `not-applicable` with a non-blocking enterprise-posture callout.
7. **Citadel target declared in § 12, no APIM Foundry connection.** Surface as `must-fix` in `network-posture`.
8. **AGT absent for a read-only retrieval agent (no actions).** Acceptable; `not-applicable` with justification linked to AGENTS.md tool list.
9. **AGT v4-looking artefact unknown to v3.7 checks.** With `--agt-profile auto`: `not-verified` + v4-migration callout. Never hard-fail.
10. **Key Vault exists but no purge protection / no rotation metadata.** `should-fix` in `secrets`.
11. **App Insights exists but no traces in last 30 min.** `should-fix` in `observability` — "claimed but not flowing".
12. **Eval dataset exists but no scheduled eval or alert.** `must-fix` in `continuous-evals`.
13. **Budget absent; PTU/PAYG unspecified.** `must-fix` in `cost`.
14. **Waiver file waives every `must-fix`.** `raw_score` unchanged; `score_with_waivers` reflects; `would_fail_hard_gate: true` prominent in the exec summary.
15. **SRE handoff has named owner but no alert route.** `should-fix` in `sre-handover` — partial credit.
16. **Multi-region/RTO declared but no backup/restore evidence.** `must-fix` in `reliability`.
17. **Model unpinned / using `latest` tag.** `must-fix` in `model-lifecycle`.

## Anti-rationalization counters

The skill explicitly counters the most common "this pilot is
production-ready, ship it" rationalizations. Each one maps to a pillar
finding so the LLM doesn't waive it implicitly.

| Rationalization | Counter |
|---|---|
| "Safe-check was green, so prod-ready" | Safe-check covers **structural** completeness only. Production needs all 13 pillars. Reference the 17 pressure scenarios above — most safe-check-green pilots fail at least 4. |
| "Customer didn't ask for Citadel" | Citadel scoring is opt-in via § 12 / `--target`. Even non-Citadel customers still need residency, governance, identity, secrets — those are pillar-independent. |
| "It's only a pilot" | The report IS the artefact that argues for the production investment. "Lab graveyard" happens to pilots that can't articulate what production would need. |
| "Key Vault exists, so secrets are solved" | KV existence ≠ purge protection ≠ soft-delete ≠ rotation policy ≠ RBAC scoping. Pillar 4 checks each independently. |
| "App Insights exists, so observability is solved" | AppIn resource ≠ ingestion. Pillar 5 checks **trace freshness** + alert rules + workbook + retention. |
| "Manual evals are enough" | Manual evals don't catch regression after a model swap or a prompt refactor. Pillar 6 requires **scheduled** evals + threshold alerts. |
| "Latest model/image tag is fine" | Models retire (Pillar 13). Images mutate (Pillar 9). Pin or page the on-call. |
| "SRE handoff happens after go-live" | No owner at go-live = pager goes to /dev/null. Pillar 12 requires owner + escalation route + signed acceptance **before** go-live. |
| "We don't have Azure permissions, so the report isn't useful" | Missing permissions are themselves verification debt. `not-verified` findings are reported as gaps, not skipped. The exec summary shows **verification coverage** (N of M checks verified) so the customer can decide whether to grant Reader and re-run. |
| "Everything is waived, so we're green" | Waivers never improve `score.raw` — only `score.with_waivers`. `would_fail_hard_gate` stays true while any unwaived must-fix remains, and the exec summary surfaces it prominently. |
| "Most checks were not-verified, so the percentage looks fine" | The exec summary reports verification coverage alongside score. Below the verification-coverage threshold the go-live recommendation cannot rise above `ready_with_unverified_risk` / `not_ready`. |
| "Platform/Citadel team owns this, not us" | Platform guardrails prove the hub exists, not that this workload is onboarded, routed, observed, evaluated, or owned. Pillar 1 checks the access contract; pillars 5/6/12 check the per-workload signals. |
| "It's internal-only / no PII" | Internal agents still need identity, audit, evals, owner, rollback, and a documented data-handling stance. "No PII" must be evidenced in § 12 + pillar 7, not assumed. |
| "Too many reds will scare the customer" | Hidden gaps scare CISOs more. The report separates raw gaps, accepted risks (waivers), and the remediation plan so the customer-facing artefact is a roadmap, not a verdict. |

The CLI surfaces the relevant counter alongside any `must-fix` finding
so the LLM-reading-this-report can't quietly waive it.

## Integration with the threadlight chain

```
threadlight-design          (writes SPEC § 12 with TODOs)
   ↓
threadlight-local-test
   ↓
threadlight-deploy
   ↓
threadlight-safe-check --phase post-deploy   (gaps: [] required)
   ↓
threadlight-production-ready                 ← this skill
   ↓
(customer architecture review / CISO sign-off / go-live decision)
   ↓
[uplift loop: re-run remediation skills → re-run this skill]
```

This skill is **chain step #9.** It does not modify the SPEC, the
manifest, the deployed resources, or any source file in the pilot repo
other than:

- `tests/production-readiness-manifest.json` (created/overwritten)
- `docs/production-readiness-report.md` (created/overwritten)
- `tests/production-readiness-waivers.json` (read-only; never written by the skill)

## Cross-skill index (remediation links)

When a pillar emits a `must-fix` or `should-fix`, the report links to the
remediation skill. These are the canonical mappings — keep this table in
sync with the awesome-gbb skill catalog as it evolves.

| Finding theme | Remediation skill | Family |
|---|---|---|
| Citadel spoke not onboarded / Access Contract missing | `citadel-spoke-onboarding` | `citadel-*` |
| Citadel hub absent (need to provision) | `citadel-hub-deploy` | `citadel-*` |
| VNet-injected Foundry needed | `foundry-vnet-deploy` | `foundry-*` |
| Network diagnostics needed | `foundry-network-runbook` | `foundry-*` |
| AGT in-process middleware needed | `foundry-agt` | `foundry-*` |
| Cap-host lifecycle / day-2 | `foundry-caphost-lifecycle` | `foundry-*` |
| Hosted-agent RBAC / managed identity | `foundry-hosted-agents` | `foundry-*` |
| OTel emit / AppIn wiring | `foundry-observability` | `foundry-*` |
| Eval scheduling / dataset shape | `foundry-evals` | `foundry-*` |
| Tenant-isolation hardening | `azure-tenant-isolation` | `azure-*` |
| ACR / Bicep / azd patterns | `azd-patterns` | `azd-*` |
| Cost analysis (PAYG vs PTU) | `paygo-ptu-cost-analyzer` | `paygo-*` |
| SRE Agent + handover recipe | `azure-sre-agent` (with `threadlight-pilot-handover` recipe) | `azure-sre-*` |
| HITL gate wiring | `threadlight-hitl-patterns` | `threadlight-*` |

## What this skill is NOT

- **Not a hard gate.** v1 is soft-advisory; the `would_fail_hard_gate`
  field is the bridge to v2. A v2 `--mode gate` flag would convert
  `must-fix` to exit code 1.
- **Not an executor.** The skill produces a report and an uplift plan.
  It never runs `azd up`, modifies infra, creates RBAC role assignments,
  rotates secrets, or onboards a Citadel spoke. Every fix is owned by
  the remediation skill linked in the finding.
- **Not a substitute for `foundry-evals`.** The eval summary pillar reads
  the latest eval-runs output; it does not run new evals.
- **Not a cost model.** The cost pillar checks for budget/anomaly
  presence and surfaces PAYG-vs-PTU recommendations from
  `paygo-ptu-cost-analyzer` outputs; it does not compute Azure pricing.
- **Not Citadel-specific.** Citadel is the recommended posture, not the
  default detection result. See "Posture target resolution".
- **Not cross-tenant.** v1 assumes single-tenant pilots.

## Out of scope for v1

- GitHub Actions CI workflow (deferred until false positives shake out)
- AGT v4 deep checks (capability-based; awaiting upstream v4 in awesome-gbb)
- Hard-gate mode (`--mode gate` is v2)
- `threadlight-experience.html` updates (separate cadence)
- Automatic invocation of remediation skills
- Cross-tenant residency analysis
- Reading Key Vault secret **values** (control-plane only — never reads
  data-plane secrets, even with sufficient permission)

## Versioning

Skill semver. v1.x is soft-advisory. v2.x will add `--mode gate`. v3.x
may add CI/CD scaffolding. Breaking changes to the JSON manifest schema
are gated behind a `schema_version` bump.
