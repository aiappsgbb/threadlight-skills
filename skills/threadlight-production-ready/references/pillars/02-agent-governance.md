# Pillar 2 — `agent-governance`

> **What this pillar answers.** Is the agent's behaviour governed by an
> in-process policy layer (AGT — Agent Governance Toolkit) that:
> (a) is wired at the **container boundary**, not just registered as
> a skill; (b) carries a versioned policy artefact; and (c) emits
> verifier output the SRE / CISO can read?

This pillar is **capability-based, not version-pinned.** AGT is
evolving rapidly (v3.7 → v4 shipped 2026-06-01) and the upstream
awesome-gbb `foundry-agt` wrapper skill is currently lagging at AGT
3.7.0 per their PR #242 (5-distribution reorg landed upstream but
their wrapper has not absorbed it yet). This skill detects AGT v4
directly because customers may install AGT distributions independently
of any wrapper skill.

When `--agt-profile v4_preview` is selected, this pillar runs an
additional layer of **AGT v4 deep checks** (`AGT-V4-001/002/003/006/007`
static + `AGT-V4-101` live) that anchor on the v4-specific surface
(5-distribution package names, ACS `intervention_points:` schema,
dynamic policy conditions, composite GitHub Action with mandatory
`toolkit-version:`, and the expanded audit-field set). The
version-agnostic `AGT-001..006` / `AGT-101..102` checks above still
run unchanged. See
[`docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md`](../../../../docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md)
for the recon evidence and design rationale.

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

## AGT v4 deep checks (`--agt-profile v4_preview` only)

Five static + one live check fire **only** when the resolved profile is
`v4_preview`. Each one carries explicit tri-state gating so it never
false-fails on non-v4 pilots and never leaks into `v3_7` / `none`
output. The full evidence trail and the exact regex anchors are in the
[design note](../../../../docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md).

| ID | Detection signal (where to look) | Default status |
|---|---|---|
| `AGT-V4-001` | `requirements*.txt`, `pyproject.toml`, `package.json` declare one of `agent-governance-toolkit-{core,runtime,sre,cli}` or `agent-governance-toolkit[full]` | `pass` if v4 names found; `not-applicable` if no AGT deps at all; `must-fix` only if v3.7-shape names are declared without v4 names |
| `AGT-V4-002` | A policy YAML carries `agent_control_specification_version:` and an `intervention_points:` block with at least one canonical key (`agent_startup`, `input`, `pre_model_call`, `post_model_call`, `pre_tool_call`, `post_tool_call`, `output`) | `pass` if both markers present; `not-applicable` if no policy YAML exists; `should-fix` if policy exists but lacks the ACS markers |
| `AGT-V4-003` | A policy YAML (or `agent_os.policies.dynamic_context` import) references `time_window`, `day_of_week`, `cost_per_window`, or `token_count_per_window` | `pass` (informational — never `must-fix`); `not-applicable` if nothing dynamic detected |
| `AGT-V4-006` | A workflow under `.github/workflows/` uses `microsoft/agent-governance-toolkit/action@vX` | `pass` if the action step also sets `toolkit-version:`; `must-fix` if the action is used without `toolkit-version:`; `not-applicable` if the action is never used |
| `AGT-V4-007` | A committed verifier JSON (under `tests/**/verifier*.json`, `tests/**/agt-verifier*.json`, or `docs/**/agt-verifier*.json`) | `pass` if ≥3 of the 5 v4 audit fields (`arguments_hash`, `approver_did`, `policy_version`, `issued_at`, `completed_at`) are present; `should-fix` if JSON exists but is missing them; `not-verified` if no JSON exists |
| `AGT-V4-101` (live, tier 2) | KQL probe in App Insights for denial events carrying a v4-shaped `policy_version` | Always `not-verified` in v1 (KQL probe deferred — same pattern as `AGT-102`) |

**Deferred to a follow-up PR:**

- `AGT-V4-004` — modernized PII regex check (too narrow as `should-fix`; first-party detectors may replace regexes entirely)
- `AGT-V4-005` — first-party CLI integration package detection (informational-only and niche)

**Detection scoping (critical):** v4 signals are only matched against
artefact files (deps lists, policy YAMLs, workflows, source `.py`/`.ts`).
Docs prose (`docs/**`, `README.md`, `*.md`) is **never scanned** for v4
markers, so a mention of "AGT v4" in a comment can't flip `auto` mode
into `v4_preview`.

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

**Upstream status (2026-06-10):** AGT v4.1.0 shipped on 2026-06-01 in
`microsoft/agent-governance-toolkit` (git tag `v4.1.0`, all Python
distributions pinned at `4.1.0`). The `awesome-gbb` `foundry-agt`
wrapper skill is **currently lagging at AGT 3.7.0** per their PR #242
coordinator decision (option B — defer the wrapper bump to a follow-up).

This skill detects AGT v4 directly via artefact signals — customers may
install AGT distributions independently of any wrapper skill, and the
pillar is capability-based per the policy at the top of this doc. The
`--agt-profile v4_preview` flag enables the AGT-V4-* deep checks listed
in the table above; `--agt-profile auto` resolves automatically by
scanning artefact files for any of the v4 signals (deps, policy schema,
or dynamic conditions).

**Never hard-fail on profile mismatch.** When the `foundry-agt` wrapper
absorbs v4 and the v4 GA designation lands, the `v4_preview` profile
will be renamed `v4` (and a new `v5_preview` may appear); this skill
version will bump minor at that point.
