# Pillar 13 — `model-lifecycle`

> **v0.3.0:** Adds `MDL-110` (TPM headroom — fails if planned model
> load would exceed `--quota-utilization`, default 80%), `MDL-111`
> (Foundry account capacity available in target region, via
> `Cognitive Services Usages Reader`), and `GOV-101` (Defender for
> AI Services plan enabled on the subscription — the most-skipped
> Defender plan in early pilots; required for jailbreak / prompt
> injection detection on Foundry endpoints).

> **What this pillar answers.** Are model deployment names and versions
> **pinned** (no `latest`)? Is there a fallback model? Is there a
> retirement-notice owner? Is there an A/B or rollback strategy? Are
> region/capacity constraints documented?

Models retire. Capacity caps trip. Content-safety policies drift.
Production must plan for all three.

## Checks

### Static (tier 0)

| ID | Check | Default status |
|---|---|---|
| `MDL-001` | Model deployments pinned to a specific version in `infra/` Bicep (not `latest`) — primary code-level pin gate | `must-fix` if `latest` found |
| `MDL-002` | Deprecation plan referenced in SPEC § 12 (link to Microsoft's deprecation calendar or an owned doc tracking model EOL dates) | `should-fix` if absent |
| `MDL-003` | Model upgrade canary process documented (`docs/model-canary.md` or referenced from § 12: dual-deployment + traffic split for version bumps) | `should-fix` if absent |
| `MDL-004` | Capacity / quota considered for prod scale (SPEC § 12 declares expected TPM and PTU-vs-PAYG decision) | `must-fix` if absent |
| `MDL-005` | Fallback model strategy documented (different model name OR different region) | `should-fix` if absent |
| `MDL-006` | Rate-limit handling in container/agent code (retry-with-backoff or queue) — grep for `429` / `RateLimitError` handling | `should-fix` if absent |
| `MDL-007` | Region / residency policy declared for models (SPEC § 12: where each deployment lives + why) | `should-fix` if absent |
| `MDL-008` | Knowledge index refresh cadence declared in SPEC § 12 (interval + owner — proves someone keeps the index fresh) | `should-fix` if absent |
| `MDL-009` | Project-level RBAC declared on Foundry account in SPEC § 12 (named principals + roles, not just account-level `Cognitive Services User`) | `should-fix` if absent |
| `MDL-010` | Knowledge index private-endpointed if `KI used` per SPEC § 12 (Bicep declares `privateEndpoint` on the AI Search resource backing the index) | `should-fix` if absent |
| `MDL-011` | Agent thread retention policy declared in SPEC § 12 (retention window + deletion owner — Foundry agents accumulate threads indefinitely by default) | `should-fix` if absent |

### Live (tier 1)

| ID | Check | Default status |
|---|---|---|
| `MDL-101` | Live deployments use pinned version (not `latest`) — confirms Bicep pin actually landed in the deployment | `must-fix` if found |
| `MDL-102` | Live deployments not in Microsoft's retiring / deprecated list (cross-checks against the deprecation calendar) | `should-fix` if any |
| `MDL-103` | Live capacity matches plan capacity (TPM provisioned ≈ TPM declared in § 12) | `should-fix` if drift |
| `MDL-104` | Live rate-limit breaches in the last 24h (KQL on `traces` for 429 counts) | `should-fix` if any — **experimental** (tier 2) |
| `MDL-110` | TPM headroom available for planned model load (planned-TPM / quota-TPM <= `--quota-utilization`, default 80%) | `must-fix` if exceeded |
| `MDL-111` | Foundry account capacity available in target region (`az cognitiveservices usage list`) | `should-fix` if region exhausted |
| `GOV-101` | Defender for AI Services plan enabled on the subscription (jailbreak / prompt-injection detection on Foundry endpoints) | `should-fix` if not Standard |

## Common gaps

- Bicep uses `properties.model.version: 'latest'` and Microsoft retires
  the version → pilot quietly switches to a newer version with
  different behaviour.
- Deployment name is `gpt-4o-{uniqueString(...)}` so every redeploy
  changes the name and the agent code needs an update or a env-var.
- No fallback declared → if capacity trips, pilot is down.
- No retirement-notice owner → Microsoft announces deprecation in the
  Foundry portal, nobody reads it for 6 months.
- No A/B strategy → swapping models in production = downtime.
- Agent code: `azure_openai.ChatCompletions.create(model="gpt-4o")` —
  hardcoded model name, can't swap without a code release.

## Remediation

| Finding | Skill |
|---|---|
| Pin model versions in Bicep | `azd-patterns`, manual Bicep edit |
| Add fallback deployment | `foundry-hosted-agents` |
| Choose PAYG vs PTU + plan capacity | `paygo-ptu-cost-analyzer` |
| Document rollback strategy | (manual; ties to pillar 11 runbook) |
| Refactor agent to call deployment-name, not model-name | `foundry-hosted-agents` |

## Why this pillar matters

The pilot launched with a model that just shipped. 18 months later
that model is in deprecation notice; the team forgot which versions
they pinned; the agent quietly upgrades to a new version mid-quarter
and starts answering customer prompts differently. The post-mortem is
"why didn't we know?". Pillar 13 is the answer.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.

## Live-leg gap evidence (Task 7)

These findings are **advisory, tier-0** evidence propagated from the executable
threadlight-upgrade leg(s). production-ready reads each same-named finding from the leg's
shared-envelope manifest under `specs/`. Absent a fresh, **complete** leg
manifest they stay `not-verified` (verification debt) — an incomplete, stale,
or `aborted` leg never inflates this pillar's score or readiness, and a
`must-fix` in the leg's evidence dominates regardless of envelope freshness.

| ID | Verified when the leg reports it (fresh + complete) | Severity |
|---|---|---|
| `UPG-001` | `threadlight-upgrade` confirmed the model/dependency compatibility matrix is current | `should-fix` |
| `UPG-002` | `threadlight-upgrade` addressed preview / runtime-policy expiry drift | `must-fix` |
