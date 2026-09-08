---
kind: sibling-skill
summary: Repair opted-in per-agent AgentOps configuration and regenerate bound operational evidence
sibling_skill: foundry-agentops
target_file: specs/agentops-manifest.json
edit_type: invoke-sibling
---

## Target file

Each opted-in agent's `agentops.yaml`, followed by the normalized
`specs/agentops-manifest.json`. No opt-in means not-applicable, not a new mandate.

## Edit type

`invoke-sibling` — explicit operator-approved remediation, never a side effect
of the advisory production-readiness assessment.

## Edit recipe

1. Use the merged, consumable-main
   [foundry-agentops skill](https://github.com/aiappsgbb/awesome-gbb/blob/2db28d1f52bf288f2d0fd40b7c8beb913ceeee09/skills/foundry-agentops/SKILL.md)
   at awesome-gbb commit `2db28d1f52bf288f2d0fd40b7c8beb913ceeee09`.
   This integration targets native AgentOps `0.14.0`; the merged skill is
   not certification and is not a claim of a tagged Threadlight release.
2. Repair the failing agent's configuration, exact version, current
   repository/commit/configuration/target/environment binding, immutable artifact
   references and freshness. Do not copy another agent's receipts.
3. Run paid evaluation or Doctor only with explicit owner approval and the
   existing application's authorized identity/telemetry scope. Missing telemetry
   is not healthy. Do not create secrets, widen RBAC, or modify Citadel.
4. Keep native outputs private and bounded; unset `GITHUB_STEP_SUMMARY`, do not
   publish native logs, prompts, responses or traces, and clean raw captures.
   Retaining raw artifacts requires separate explicit storage/retention scope.
5. Regenerate evidence using `threadlight-agentops`; consume it through the
   canonical `threadlight-evals`, `threadlight-redteam`, and binding-scoped
   `threadlight-govern` owners. Baseline changes always require separate approval.
   `threadlight-cicd` composes the existing workflow; do not generate a competing
   AgentOps workflow.

## Verification

Re-run `threadlight-agentops`, then the applicable canonical domain consumers,
then `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`.

`AOPS-001` uses the worst operational status across all opted-in agents. A
quality finding is excluded only when its exact source is represented by
current validated canonical checks; unproved mapping strings cannot hide
blockers. A pass does not certify deployment or whole-agent governance.

## Stale-plan check

Compare the current readiness manifest's canonical SHA256 to the apply-plan's
`manifest_sha256` before any edit; regenerate a stale plan instead of applying it.
