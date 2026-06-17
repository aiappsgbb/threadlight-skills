# Production-readiness, the threadlight way

> **What's new in v0.3.0** (Nov 2025). The 0.3.0 release closed an
> adversarial-review smoking gun: 16 critical static checks were
> regex-searching the concatenated raw text of every `.bicep` file in
> the repo, so a *comment* like `// virtualNetworks should be used`
> made `NET-001` pass. 0.3.0 replaces that with `BicepGraph` — a real
> ARM-graph parser that shells `az bicep build --stdout` and walks the
> compiled JSON resources (including nested `Microsoft.Resources/
> deployments` from module references). The 14 most-critical static
> checks (`NET-001/002/003/004`, `IAM-002/005`, `SEC-001/005/006`,
> `OBS-001/002`, `REL-006`, `MDL-001`) now answer the question "is the
> resource *declared*?" instead of "does the word appear *somewhere*?".
>
> Other 0.3.0 changes:
>
> - **`bicep` CLI is now a hard prerequisite.** Missing CLI exits 2
>   with `az bicep install` instructions, no silent regex fallback.
> - **`not-verified` scores 0**, not 2-of-4. A run with all gaps marked
>   "couldn't check" no longer gets a 50% honour score.
> - **`verification_debt` is a first-class manifest field** — total +
>   per-pillar count of `not-verified` findings, surfaced in the exec
>   summary so the gap "we couldn't check this" no longer hides inside
>   the percent.
> - **21 unimplemented stubs retired** to `experimental: true`,
>   excluded from scoring unless `--include-experimental` is set.
> - **5 long-stubbed live probes wired:** OBS-106 (per-account diag
>   settings), OBS-102 (App Insights KQL via `az monitor log-analytics
>   query`), SEC-106 (KV diag coverage), SRE-104 (activity-log alerts),
>   NET-501 (Citadel APIM Access Contract via `TL_CITADEL_HUB_RG`).
> - **14 new finding IDs:** Defender plans (`GOV-101/102/103`),
>   Secure Score floor (`GOV-104`), Defender recs surfaced
>   (`GOV-105`), Policy (`GOV-201/202/203`), Foundry RBAC + knowledge
>   index PE + thread policy (`MDL-009/010/011`), quota pre-flight
>   (`MDL-110/111`), restore-drill freshness (`REL-007/008`).
> - **Industrialization:** `--diff`, `--gate-preview` (exit 2 on
>   would-fail-hard-gate), `--remediate <id>` (prints bash recipe),
>   `--include-experimental`, trend CSV append per run, OIDC CI
>   recipe replaces `AZURE_CREDENTIALS`, `azd hook` install script.
>
> See `CHANGELOG.md` for the full delta. Bicep-only — Terraform is
> explicitly out of scope in this skill, forever.

> *Green `safe-check` proves a pilot is structurally complete and behaves. It does not prove the customer's CISO, SRE, FinOps and network architect can sign off on it. That conversation needs an evidence-backed artefact — produced in one command, not weeks of tribal-knowledge assembly.*

This page is the long-form companion to the [`threadlight-production-ready`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/SKILL.md) skill. It explains **what production-readiness means in the threadlight chain**, the **three postures** a pilot can target, the **thirteen cross-cutting pillars** the skill scores, the **status taxonomy** that surfaces what's actually blocking go-live, and the **two moments per pilot lifecycle** when you run it.

---

## 1. Why this skill exists

The `threadlight-*` chain ships a working agent in one session: **design → local-test → deploy → safe-check**. `safe-check --phase post-deploy` proves the pilot is *structurally complete and behaves* — every selector landed, every channel reaches, every cron ran, no placeholder image.

But **"green safe-check" ≠ "production-ready"**. The next conversation — CISO, SRE, FinOps, network architect, data protection — needs an artefact that says:

- **What posture is this in?** Citadel spoke? AGT-only? Standard AI gateway?
- **What's missing?** Per-pillar gaps with severity.
- **What would the uplift cost?** Effort estimate + named remediation skill.
- **Who owns each gap?** Pilot team / customer team / SRE / SecOps.
- **Can we go live with waivers?** Score with and without customer-accepted compensating controls.

Without that artefact, every pilot grows a tribal-knowledge answer that takes weeks to assemble. The customer defers the production phase. The pilot quietly becomes a **lab graveyard** demo.

`threadlight-production-ready` produces the artefact in one command. **Soft-advisory** — never fails a build. **Gracefully degrading** — missing Azure permissions become `not-verified` findings, not crashes.

---

## 2. What "production-ready" means here

This skill is **the artefact, not the gate.** It does not stop a deploy. It does not enforce a hard policy. It produces two outputs you can put in front of decision-makers:

| Output | Audience | Purpose |
|---|---|---|
| `docs/production-readiness-report.md` | Customer architecture review · CISO sign-off pack · pilot-to-prod handover deck | Human-readable scorecard, per-pillar findings with severity, evidence register (with `captured_at` timestamps), waiver register, residual-risk list, go-live recommendation |
| `tests/production-readiness-manifest.json` | CI · change advisory board · automated dashboards | Machine-readable manifest with `raw_score`, `score_with_waivers`, `would_fail_hard_gate`, `evidence_freshness`, full per-finding detail |

The report is **the conversation starter** with the customer's production team. It replaces the four-week scramble to assemble "is this pilot ready?" with a one-command answer that can be reviewed, waived, and iterated.

---

## 3. The three postures

A pilot's production posture is **resolved from SPEC § 12** (the production-readiness section of the threadlight SPEC). The skill ships a [`spec-section-12-template.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/spec-section-12-template.md) for authoring it. If § 12 is missing, posture falls back to `standard-ai-gateway` and an `RDY-002` finding surfaces "author § 12 before the architecture review."

### 🛡️ Citadel spoke *(default · recommended)*

The customer's **AI Citadel hub** fronts every model call via APIM — JWT enforcement, per-tenant rate-limit, content filter, semantic cache, ledger. The pilot's Foundry project is onboarded as a **spoke** with access contracts, and AGT in-process middleware enforces fine-grained policy inside the agent itself.

**Two layers, one audit chain.** APIM perimeter governs *who and what reaches the model*. AGT in-process governs *what the agent does with the response*.

**Right when** the customer tenant already has — or is provisioning — Citadel. This is the GBB AI Apps recommended posture for any new pilot.

Remediation skills: [`citadel-spoke-onboarding`](https://github.com/aiappsgbb/awesome-gbb), [`citadel-hub-deploy`](https://github.com/aiappsgbb/awesome-gbb), [`foundry-agt`](https://github.com/aiappsgbb/awesome-gbb).

### 🧬 AGT-only, in-process middleware

No central AI gateway available. The [**Agent Governance Toolkit**](https://github.com/microsoft/agent-governance-toolkit) (v4.1 preview, detected automatically) sits inside the agent process — 8–12 μs per evaluation, hash-chained audit, OWASP-ASI 2026 evidence. Policy + verifier artefacts are committed to the repo.

**Right when** the customer is in a greenfield or experimental tenant where introducing APIM mid-pilot would be premature. Still produces auditable evidence; just operates one defence layer instead of two.

Remediation skills: [`foundry-agt`](https://github.com/aiappsgbb/awesome-gbb), [`foundry-observability`](https://github.com/aiappsgbb/awesome-gbb).

### 🌐 Standard AI gateway / VNet

Brownfield or regulated estate with an **existing APIM**, NetSec-controlled **VNet injection**, or Microsoft Defender for Cloud baseline posture. The pilot **conforms to the established gateway pattern** rather than introducing Citadel mid-flight.

**Right when** the customer's NetSec team owns the perimeter and the pilot has to slot in behind it. The skill scores against that perimeter's contract (private endpoints, allowlists, JWT validation) rather than Citadel's.

Remediation skills: [`foundry-vnet-deploy`](https://github.com/aiappsgbb/awesome-gbb), [`foundry-hosted-agents`](https://github.com/aiappsgbb/awesome-gbb), [`azure-tenant-isolation`](https://github.com/aiappsgbb/awesome-gbb).

> **Hybrid is supported.** `--target hybrid` runs Citadel checks where applicable and AGT checks where Citadel artefacts are missing. Useful for pilots mid-uplift.

---

## 4. The thirteen pillars

Every pillar has its own [reference doc under `references/pillars/`](https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-production-ready/references/pillars) — prose-heavy guidance so the LLM can reason about findings, not just emit them.

| # | Pillar | What "good" looks like | Primary remediation skill |
|---|---|---|---|
| 1 | [`network-posture`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/01-network-posture.md) | Resolved posture target met (Citadel spoke / AGT / VNet / standard); **data-residency sub-scored** (model region, APIM region, data-plane regions, backups, cross-border support) | `citadel-spoke-onboarding`, `foundry-vnet-deploy` |
| 2 | [`agent-governance`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/02-agent-governance.md) | AGT in-process middleware wired (capability-based, version-agnostic); policy + verifier artefacts present; OWASP-ASI evidence current; v4-preview deep checks when v4 detected | `foundry-agt` |
| 3 | [`identity-access`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/03-identity-access.md) | Workloads use **managed identity**; **no client secrets**; RBAC least-privilege; Key Vault access via RBAC not access policies | `foundry-hosted-agents`, `azure-tenant-isolation`, `azd-patterns` |
| 4 | [`secrets`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/04-secrets.md) | Key Vault with **soft-delete + purge protection**; no hardcoded secrets in repo; rotation policy declared; control-plane vs data-plane access scoped | `azd-patterns`, `foundry-hosted-agents` |
| 5 | [`observability`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/05-observability.md) | App Insights connected at **account-level** (Foundry); OTel emit verified (recent traces); alert rules wired; workbook + retention declared | `foundry-observability` |
| 6 | [`continuous-evals`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/06-continuous-evals.md) | SPEC § 9 scenarios scheduled (Plan A or Plan B); threshold alerts wired; last run within freshness window; eval datasets stored | `foundry-evals` |
| 7 | [`responsible-ai`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/07-responsible-ai.md) | Content filters, jailbreak shields, grounded-language eval; AGT RAI policy; PII redaction declared; allow/deny tested | `foundry-agt`, `foundry-evals` |
| 8 | [`hitl-audit`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/08-hitl-audit.md) | If SPEC § 8 declares gates: wired, persistent audit trail, escalation channel reachable, idempotent | `threadlight-hitl-patterns` |
| 9 | [`supply-chain`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/09-supply-chain.md) | Container images **pinned by digest**; Bicep modules pinned; dependency scanning enabled; SBOM emitted | `azd-patterns` |
| 10 | [`cost`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/10-cost.md) | Pricing plan declared (PAYG vs PTU); budget + anomaly alerts wired; forecast vs budget cap; idle-resource sweep done | `paygo-ptu-cost-analyzer` |
| 11 | [`reliability`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/11-reliability.md) | Multi-region plan vs RTO/RPO from § 12; **backup/restore tested** (not just "configured"); runbook exists; chaos test done | `foundry-vnet-deploy` |
| 12 | [`sre-handover`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/12-sre-handover.md) | **Evidence-based:** incident owner + escalation path; runbook links; alert destinations; SRE Agent resource/recipe if selected; handoff acceptance signed | `azure-sre-agent` |
| 13 | [`model-lifecycle`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/13-model-lifecycle.md) | Model deployment **names + versions pinned** (no `latest`); fallback model declared; retirement-notice owner; A/B or rollback strategy; region/capacity documented | `paygo-ptu-cost-analyzer`, `foundry-hosted-agents` |

> **Pillars are version-agnostic where the underlying surface evolves.** Pillar 2 detects AGT v3.7 *or* v4-preview by **capability**, not version pin. AGT v4-preview deep checks (5 static + 1 live) gate on `--agt-profile v4_preview` (or `auto` resolving to v4 when v4 artefacts are present in the repo).

---

## 5. Status taxonomy

Findings aren't just pass/fail — they encode **what the operator can actually do** about them.

| Status | Meaning | Counts toward raw score? |
|---|---|---|
| `pass` | Check ran and the pillar requirement is met | ✅ |
| `should-fix` | Gap exists; not a hard blocker but should be addressed before go-live | ❌ |
| `must-fix` | Hard blocker for production go-live; would fail a v2 hard-gate | ❌ |
| `not-applicable` | Check correctly skipped (e.g., Citadel scoring against an AGT-target deployment) | ✅ (counts as pass for raw, with justification) |
| `not-verified` | Check could not run (no Azure auth, insufficient RBAC, static-only mode) | ⚪ (excluded from raw score; surfaced in `not_verified[]` with `verification_coverage`) |
| `waived` | Customer explicitly accepted the gap with a documented compensating control | ✅ in `score_with_waivers`, ❌ in `raw_score` |

The manifest reports **both** `raw_score` and `score_with_waivers`, plus a `would_fail_hard_gate` boolean that flips true if any `must-fix` finding lacks a waiver. The report's executive summary calls this out so reviewers see the unfiltered posture **and** the customer-accepted posture side-by-side.

### Evidence freshness

Every live probe stamps a `captured_at` timestamp (ISO 8601 UTC, second precision). The manifest's top-level `evidence_freshness` block surfaces the oldest evidence and flips a `stale` boolean when the oldest probe exceeds the `--freshness-hours` window (default 24h). The report's evidence register shows a `Collected` column; when stale, the executive summary surfaces an "Oldest evidence" bullet so reviewers know the report is reading from older probes.

---

## 6. When to invoke

> **Rule of thumb.** This skill runs **at most twice per pilot lifecycle**:
>
> 1. **Heading into the customer architecture review** — the artefact that lives in the deck.
> 2. **Immediately before the go-live decision** — the artefact that goes to CISO or the change advisory board.
>
> Running it every commit is noise. Running it once after the pilot has been parked for weeks is fine — `--static` mode works with no Azure auth at all.

| You are at… | Run | Get |
|---|---|---|
| `safe-check --phase post-deploy` returned green and the customer wants to talk about production | `python tests/production_ready.py` | Markdown report + JSON manifest, all 13 pillars, live + static |
| Customer architecture review in 3 days, posture is known | `python tests/production_ready.py --target citadel-spoke` | Same, scored against the declared target |
| Pilot has been parked for weeks; someone asks "could we ship this?" | `python tests/production_ready.py --static` | Pure static scorecard from repo + safe-check manifests (no Azure auth needed) |
| Inherited a pilot whose SPEC has no § 12 | Skill still runs — falls back to `standard-ai-gateway`; `RDY-002` surfaces "author § 12" | Author § 12 from the [template](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/spec-section-12-template.md), re-run for full scorecard |
| AGT v4 shipped and you want deep checks | `python tests/production_ready.py --pillar agent-governance --agt-profile v4_preview` | AGT-only scorecard against the v4 surface (5-distribution reorg, ACS `intervention_points:` schema, dynamic policy conditions, composite-action pinning, v4 audit-field set) |
| Customer accepted some `must-fix` findings as risk | Author `tests/production-readiness-waivers.json`, re-run | Report shows `score_with_waivers` + `would_fail_hard_gate` flags |

---

## 7. What this skill does NOT replace

This skill is **the cross-cutting scorecard.** It does not replace any of the focused skills that produce the evidence it reads.

| Concern | Use instead |
|---|---|
| Authoring SPEC / `deployment_manifest{}` | `threadlight-design` |
| Running `azd up` | `threadlight-deploy` |
| Generating the prod CI/CD pipeline + env (UAMI / federated creds, RBAC, private-VNet runners) | `threadlight-cicd` |
| Structural / behavioural deploy gate | `threadlight-safe-check --phase post-deploy` |
| Invocation testing of the agent | `foundry-evals` |
| Wiring App Insights / OTel | `foundry-observability` |
| Provisioning Citadel hub | `citadel-hub-deploy` |
| Onboarding spoke to Citadel | `citadel-spoke-onboarding` |
| Provisioning Azure SRE Agent | `azure-sre-agent` |
| Authoring AGT in-process middleware | `foundry-agt` |
| Generating Bicep / Terraform | `azd-patterns`, `azureterraform`, `bicepschema` |
| Deploying to a VNet-injected Foundry | `foundry-vnet-deploy` |

**This skill recommends, never executes.** Every `must-fix` and `should-fix` links to the remediation skill above. The operator (or a follow-up Copilot session) runs that skill.

---

## 8. Outputs

```
docs/production-readiness-report.md         # human-facing scorecard (markdown)
tests/production-readiness-manifest.json    # machine-readable manifest (CI / dashboards)
```

The report has a stable structure: executive summary (posture, raw score, waivered score, would-fail-hard-gate, oldest evidence) → per-pillar findings (status, evidence, remediation skill, effort estimate) → evidence register (every live probe with `captured_at`) → waiver register → residual-risk list → go-live recommendation.

The manifest is `schema_version: "1.0"` (additive evolution; no breaking changes since GA). Tool version follows semver under `VERSION` (currently `0.2.0` after the per-evidence freshness ship).

---

## 9. CLI cheatsheet

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

# Override the freshness window for the evidence-staleness banner
python tests/production_ready.py --freshness-hours 48

# Override output paths
python tests/production_ready.py \
  --out tests/production-readiness-manifest.json \
  --report docs/production-readiness-report.md

# Quiet output for CI / hooks
python tests/production_ready.py --quiet
```

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Checks ran and report was written. Per-finding statuses (including `must-fix` and `not-verified`) live inside the report. **The skill never returns non-zero for findings in v1 — it is soft-advisory.** |
| `2` | Missing prerequisite: no `specs/manifest.json`, no `tests/postdeploy-manifest.json`, safe-check manifest stale (use `--accept-stale-safe-check` to override) or scope-mismatched (different subscription/RG), or unknown `--pillar` id. **Missing SPEC § 12 does NOT exit 2** — the skill emits `RDY-002` and falls back. |
| `3` | I/O failure: cannot read inputs, cannot write outputs, `az` not on PATH at all. |

Missing Azure auth or insufficient permissions for specific live probes ⇒ those checks are marked `not-verified` in the report; exit code stays `0`. **The skill never turns into a deployment blocker by accident.**

---

## 10. Where this fits in the chain

```
threadlight-design        →  threadlight-local-test  →  threadlight-deploy   →  threadlight-safe-check
(SPEC + manifest)            (mock data + smoke)        (azd up)                (--phase post-deploy)
                                                                                       │
                                                                                       ▼
                                                                       threadlight-production-ready
                                                                       (paved path to production)
                                                                                       │
                                                                                       ▼
                                                              docs/production-readiness-report.md
                                                              tests/production-readiness-manifest.json
                                                                                       │
                                                                                       ▼
                                                          customer architecture review · CISO sign-off
                                                          change advisory board · pilot-to-prod handover
```

**Soft-advisory by design.** The skill before it (`safe-check`) is the structural gate. The skills after it are conversations with humans. This skill is the **bridge** — the artefact that turns "we built something that works" into "here is the evidence the customer's production team needs."

**Then the pilot ships through a pipeline, not a laptop.** Once the scorecard is green, [`threadlight-cicd`](https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-cicd) generates the production deploy pipeline (GitHub Actions / Azure DevOps) and the env-setup runbooks the platform team runs — OIDC/WIF identity, least-privilege RBAC scoped to the spoke RG, and private-VNet runners — because in a real customer tenant the agent rarely has rights to run `azd up` itself. It is a deliberate **manual handoff** (not part of the auto chain), and it stays a **separate repo/pipeline** from the central platform: it never touches the Citadel hub, shared APIM, or platform networking — those remain `citadel-hub-deploy`.

---

## Read next

- **Full skill metadata + invocation patterns:** [`skills/threadlight-production-ready/SKILL.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/SKILL.md)
- **Author SPEC § 12 from scratch:** [`references/spec-section-12-template.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/spec-section-12-template.md)
- **Pre-go-live handoff checklist:** [`references/handoff-checklist.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/handoff-checklist.md)
- **Generate the prod CI/CD pipeline + env (UAMI/federated creds, RBAC, private-VNet runners):** [`threadlight-cicd`](https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-cicd) · [onboarding-path decision tree](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-cicd/references/onboarding-path-decision.md)
- **Per-pillar Azure RBAC for live probes:** [`references/live-probe-permissions.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/live-probe-permissions.md)
- **Sample CI workflow (PR comments + artefacts):** [`references/ci-github-actions.yml`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/ci-github-actions.yml)
- **End-to-end workshop (1 hour, includes a production-readiness pass):** [WORKSHOP-1H-QUICKSTART.md](WORKSHOP-1H-QUICKSTART.md)
- **The whole threadlight chain (technical briefing):** [THREADLIGHT.md](https://github.com/aiappsgbb/threadlight-skills/blob/main/THREADLIGHT.md)
- **Foundational skills (Citadel, AGT, Foundry, SRE Agent):** [awesome-gbb](https://aiappsgbb.github.io/awesome-gbb/)

---

*Maintained as part of [aiappsgbb/threadlight-skills](https://github.com/aiappsgbb/threadlight-skills). Issues and PRs welcome.*
