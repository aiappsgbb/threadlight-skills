# Pillar 2 — `agent-governance`

> **What this pillar answers.** Is the agent's behaviour governed by an
> in-process policy layer (AGT — Agent Governance Toolkit) that:
> (a) is wired at the **container boundary**, not just registered as
> a skill; (b) carries a versioned policy artefact; and (c) emits
> verifier output the SRE / CISO can read?

This pillar is **capability-based, not version-pinned.** AGT is
evolving rapidly (v3.7 → v4) and the upstream awesome-gbb `foundry-agt`
skill is mid-update. Pin to behaviour, not to a version string.

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `AGT-001` | If `target_posture` ∈ `{citadel-spoke, agt, hybrid}` and any agent has tool calls that mutate state, AGT middleware must be wired at the agent container | `must-fix` if missing |
| `AGT-002` | AGT policy artefact present (`agt-policy.yaml`, `policy.yaml`, or equivalent referenced from `agent.yaml` / `container.py`) | `must-fix` if AGT-001 fails |
| `AGT-003` | Policy artefact has a version field (not `latest`, not absent) | `should-fix` if missing |
| `AGT-004` | Verifier output artefact present (latest run committed, e.g., `docs/agt-verifier-report.md` or `tests/agt-verifier.json`) | `should-fix` if missing |
| `AGT-005` | OWASP / Agentic-Specific Index 2026 ("ASI") evidence: file references a known ASI version | `should-fix` if absent |
| `AGT-006` | `--agt-profile auto` (default): infer profile from policy artefact shape and skill version detected; **never hard-fail on unknown profile, mark `not-verified` instead** | n/a (control flag) |

### Live (tier 1)

| ID | Check | Default status |
|---|---|---|
| `AGT-101` | If AGT runs in a sidecar container, sidecar present in the deployed ACA | `must-fix` if expected |
| `AGT-102` | If AGT logs telemetry to AppIn, recent traces (last 24h) carry an AGT span/operation | `should-fix` if absent (with hint: may be deployed but not exercised) |

## Capability detection

Rather than asserting `version == "3.7.x"`, infer capabilities from
artefacts present:

| Capability | Detection signal |
|---|---|
| `policy_artefact_present` | File matching `**/agt-policy.{yaml,json}` or referenced from `agent.yaml` |
| `verifier_artefact_present` | File matching `**/agt-verifier*.{md,json}` or `docs/agt-verifier-report.md` |
| `middleware_wired_at_boundary` | Import of `agt` / `AgentGovernance` / `apply_governance` in `container.py` or `src/agent/main.py` |
| `sidecar_pattern` | `agt-sidecar` container in `infra/` or `agt-sidecar` service in `azure.yaml` |
| `rai_policy_present` | Content-filter / shield / PII-redaction block in policy artefact |

The `--agt-profile` flag toggles which capabilities are **required**:

| Profile | Required capabilities |
|---|---|
| `none` | none (pillar marked `not-applicable`) |
| `v3_7` | `policy_artefact_present`, `middleware_wired_at_boundary` |
| `v4_preview` | `policy_artefact_present`, `verifier_artefact_present`, `middleware_wired_at_boundary`, `rai_policy_present` |
| `auto` (default) | Best-effort: detect profile by capability set; if unknown → `not-verified` with v4-migration callout |

This survives the v3.7 → v4 transition without code changes in this
skill.

## Common gaps

- AGT is "documented" in § 11b but never wraps the agent at the
  container boundary — it's loaded as a skill instead, which means
  Foundry-Agent will route around it for direct LLM calls.
- Policy file exists but pinned to `latest` so every redeploy can change
  the rule surface silently.
- Verifier output is committed once at scaffold time, never refreshed.
  Surface in the report as "verifier artefact is 90 days old".
- ASI version reference is missing entirely; the customer's risk team
  has no anchor for "what threats does this cover?".

## Remediation

| Finding | Skill |
|---|---|
| Wire AGT middleware / scaffold policy | `foundry-agt` |
| Choose AGT vs Citadel-spoke posture | This skill's `network-posture` pillar callout + `foundry-agt` decision matrix |

## Why this pillar matters

The customer's CISO will ask: "what stops the agent from sending
customer PII into the model when answering an off-topic question?"
"What audits the answer?" "What version is the rule set?" "Who signed
off on this version?" If the agent has no AGT layer (or AGT exists
nominally but isn't wired), the answer is "the prompt", which is the
wrong answer.

## Notes on the AGT v3.7 → v4 transition

Awesome-gbb is mid-update for AGT v4. v1 of this skill (`--agt-profile
auto`) does best-effort detection and falls back to `not-verified` with
a "v4 migration may be required" callout for any artefact that doesn't
match the v3.7 shape. **Never hard-fail on profile mismatch.** When
the awesome-gbb v4 reference lands, the `v4_preview` profile is
upgraded to `v4` and a new `v5_preview` may appear. This skill version
will bump minor.
