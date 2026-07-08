---
name: threadlight-govern
description: >-
  PROTECT-leg for threadlight pilots: makes the Microsoft Agent Governance
  Toolkit (AGT) actually govern a deployed agent — not just document it.
  Scaffolds a real, schema-valid, versioned `policy.yaml`, commits `agt test`
  fixtures, gates CI on `agt lint-policy` + `agt verify` (OWASP ASI 2026
  attestation), and emits `specs/govern-manifest.json` for
  threadlight-production-ready pillars 2 and 7. USE FOR: agent action
  governance, AGT policy, agt lint-policy, agt test, agt verify, tool
  allow/deny, excessive-agency guardrail, default-deny policy, OWASP ASI 2026,
  governance CI gate, policy attestation, responsible-ai policy, PROTECT stage,
  govern leg, govern-manifest. DO NOT USE FOR: content filtering at the model
  edge (Azure AI Content Safety) — AGT governs actions; adversarial scanning
  (threadlight-redteam); quality evals (threadlight-evals); completeness gate
  (threadlight-safe-check); deep AGT authoring upstream (foundry-agt).
metadata:
  version: "0.2.0"
---

# Threadlight Govern — make AGT *govern*, then prove it

The **PROTECT** leg of `path2production`. `threadlight-production-ready`
*scores* agent-runtime governance (pillar 2) and responsible-AI controls
(pillar 7), but until now the scorecard could only say *"go wire AGT"* and
delegate the actual work to the external `foundry-agt` skill. Nothing in the
pipeline ever produced governance evidence. This skill is that missing
executable leg.

```
DESIGN → BUILD/DEPLOY → DISCOVER → [ PROTECT ] → GOVERN → IMPROVE
                                   threadlight-govern
```

> **Why this skill exists.** A pilot can pass every structural gate —
> resources deployed, evals green, telemetry flowing — and still ship an
> agent with **no action-level governance**: a tool-calling agent that can
> `shell_exec`, email externally, or act with excessive agency because
> nothing constrains its actions. Microsoft's Responsible-AI guidance for
> Foundry treats agent-runtime controls as a **PROTECT** checkpoint, not an
> afterthought. The real Agent Governance Toolkit
> (`pip install agent-governance-toolkit`, CLI `agt`) governs those actions
> through a committed, schema-valid **policy** that CI **lints, replays, and
> verifies**, plus a committed OWASP ASI 2026 **attestation**. This leg
> scaffolds that policy, wires the CI gate, and scores the committed evidence
> so the scorecard can *verify the leg ran* instead of *asking the operator to
> go run it*.

## What this skill governs (and does not)

- **Governs:** agent **actions** — tool calls, tool allow/deny, approval
  gates, excessive-agency limits — expressed as a real AGT `policy.yaml` and
  proven by `agt lint-policy` / `agt test` / `agt verify` in CI.
- **Does NOT govern tokens:** prompt/response token-level filtering is the
  *model* guardrail (Azure AI Content Safety / prompt shields configured on
  the model). AGT references the RAI posture; it does not replace the model
  filter.
- **Does NOT red-team:** running adversarial probes against the agent is the
  DISCOVER leg → `threadlight-redteam`. Govern enforces the policy that
  red-team findings harden.
- **Runtime enforcement depth is `foundry-agt`'s.** Raising `agt verify`
  coverage means wiring the real framework governance integration
  (`agent_compliance` / `agent_os.integrations.*`) into the agent runtime —
  the deep upstream work `foundry-agt` owns. This leg governs the *evidence*.

## The contract — `specs/govern-manifest.json`

`scripts/govern_check.py` walks the pilot repo and emits a manifest whose
capability keys **mirror the pillar-02 capability-detection table exactly**,
so `threadlight-production-ready` and this skill never disagree:

| Capability key | Meaning | Severity when missing |
|---|---|---|
| `policy_artefact_present` | a committed `agt-policy.yaml` / `policy.yaml` exists | must-fix |
| `policy_schema_valid` | policy carries the real required fields `version` + `name` + `rules[]` (what `agt lint-policy` requires) | must-fix |
| `policy_versioned` | ruleset pins a real semver (not `latest`) | should-fix |
| `policy_default_deny` | policy declares a default-deny posture (`deny_by_default` / `default_action: deny`) | should-fix |
| `sensitive_action_rules_present` | ≥1 rule `deny`/`block`/`escalate`s a sensitive or state-changing action (pillar 7) | should-fix¹ |
| `policy_tests_present` | committed `agt test` fixtures (`{input, expected_verdict}`) | should-fix |
| `ci_gate_present` | a CI workflow runs `agt verify` / `agt lint-policy` / `agt test` | should-fix |
| `attestation_present` | committed `agt verify` attestation (`governance-attestation/v1`) exists | should-fix |
| `attestation_fresh` | attestation within the freshness window | should-fix |
| `asi_reference_present` | OWASP ASI 2026 anchor present | should-fix |

¹ becomes `must-fix` when no policy artefact exists at all.

Verdict roll-up: any `must-fix` → `ungoverned`; only `should-fix`/`not-verified`
→ `partial`; all clear → `governed`.

## Usage

```bash
# 1. Assess an existing pilot (read-only) — prints the governance report
python3 scripts/govern_check.py --target ../my-pilot

# 2. Emit the manifest + human report the scorecard consumes
python3 scripts/govern_check.py --target ../my-pilot --emit
#   → writes specs/govern-manifest.json + docs/agt-governance-report.md

# 3. CI gate — exit 2 on any must-fix capability
python3 scripts/govern_check.py --target ../my-pilot --gate

# JSON for piping
python3 scripts/govern_check.py --target ../my-pilot --json
```

Flags: `--profile {auto,v3_7,v4_preview,none}` (matches pillar-02
`--agt-profile`; `none` → governance not applicable), `--freshness-days N`
(attestation staleness window, default 90).

### The leg, end-to-end (what the agent does)

This is a **producing** leg — it explicitly creates artefacts (it does not
silently mutate the user's repo for *findings*; per the SACRED rule, finding
remediation is dispatched). The agent runs the leg in three moves:

1. **Author policy** — if `policy_artefact_present` / `policy_schema_valid` is
   must-fix, copy the right template from `references/policy-templates/`
   (`default`, `hitl`, or `pii-deny`), pin a ruleset `version`, fill the tool
   families from the pilot's `agent.yaml` tool set, and validate with
   `agt lint-policy`. Commit `agt test` fixtures alongside it.
2. **Gate CI** — if `ci_gate_present` is missing, add a workflow that runs
   `agt lint-policy` + `agt verify` as required gates, plus `agt test` as an
   **advisory** step (`continue-on-error: true` — 4.1.0's replay schema rejects
   valid `escalate`/`conditions[]` policies; see `references/wiring-snippet.md`;
   parity with recipe `AGT-001`).
3. **Attest + score** — commit the `agt verify` attestation to
   `docs/agt-verifier-report.md`, then re-run `govern_check.py --emit` so the
   manifest flips to `governed`.

## How `production-ready` consumes this

`threadlight-production-ready` pillar 2 reads `specs/govern-manifest.json`. If
the manifest is present and `verdict == governed` (and `attestation_fresh`),
pillar 2 and the RAI half of pillar 7 report **verified** with the manifest as
evidence, instead of emitting the `AGT-001` remediation. A stale or `ungoverned`
manifest re-opens the finding. No manifest → unchanged legacy behaviour
(score + delegate).

## Relationship to `foundry-agt`

`foundry-agt` (awesome-gbb) is the deep upstream skill that *authors* AGT
capabilities and knows the full AGT / AgentOS runtime surface.
`threadlight-govern` is the **thin pipeline leg** that decides *whether AGT
governs this pilot*, drives the three moves above, and produces the manifest the
threadlight scorecard understands. Use `foundry-agt` for advanced policy
authoring and runtime integration; use this skill to make governance a *step
that runs* in `path2production`.

## Files

```
scripts/govern_check.py                 # stdlib validator → govern-manifest.json
references/policy-templates/
  default.policy.yaml                   # baseline default-deny + block/deny rules
  hitl.policy.yaml                      # adds human-approval (escalate) gates
  pii-deny.policy.yaml                  # strict PII-egress denial
references/wiring-snippet.md            # author → lint → test → CI-gate → attest
references/govern-manifest.schema.json  # the manifest contract (v2)
references/fixtures/sample-wired/        # governed pilot (verdict: governed)
  policy.yaml · fixtures/ · .github/workflows/ · docs/agt-verifier-report.md
references/fixtures/sample-bare/          # ungoverned pilot (verdict: ungoverned)
tests/test_govern_check.py              # stdlib unittest (no pytest)
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## See also — official Azure Skills

Threadlight exists to make Microsoft's own platform **trivial to adopt** — never
to replace it. For first-party depth behind this governance leg, reach for the
official **[Azure Skills](https://github.com/microsoft/azure-skills)** catalog.
*Further reading, not a dependency* — Threadlight's guidance stays the source of
truth for the pilot flow:

- **[`entra-agent-id`](https://github.com/microsoft/azure-skills/blob/main/skills/entra-agent-id/SKILL.md)** — **Entra Agent Identity Blueprints** + OAuth token exchange (OBO / `fmi_path`); the first-party agent-identity this governance leg binds policy to.
- **[`azure-rbac`](https://github.com/microsoft/azure-skills/blob/main/skills/azure-rbac/SKILL.md)** — **least-privilege role** selection + assignment for the agent's managed identity.
