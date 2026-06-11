---
kind: manual
summary: Confirm whether the customer has any evaluation harness
target_file: manual
edit_type: manual
---

## Target file
Manual operator/team conversation. Evidence may later be documented in `SPEC.md` section 9 and the `evals/` directory, but this recipe does not prescribe a single file edit.

## Edit type
`manual` — DO NOT auto-apply. The finding asks whether the customer has any evaluation harness at all; the answer requires operator and field-team context that the assessor cannot infer from repository files alone.

## Edit recipe
Present this to the operator before proceeding:

> **EVAL-101 — Evaluation harness discovery**
>
> 1. Confirm whether the customer currently runs any evaluation harness for the production workload, including Foundry evals, custom regression scripts, prompt/agent test suites, or manually operated scorecards.
> 2. If a harness exists, capture where results are stored, who owns it, how often it runs, and how failures are triaged.
> 3. If no harness exists, schedule a field-team conversation to scope the minimum viable eval harness before go-live.
> 4. Record the decision, owner, and next action in the project handover notes or SPEC section 9.
>
> If go-live proceeds without an eval harness, file an explicit waiver with signed justification.

## Verification
Re-run Threadlight production readiness after the operator documents the decision:

```bash
python3 scripts/production_ready.py --target-rg ${TARGET_RG} --target-sub ${TARGET_SUB}
```

EVAL-101 remains `not-verified` until a future automated probe can retrieve eval-run evidence. For v0.5.0, successful remediation is the documented manual decision or waiver.

## Stale-plan check (the agent MUST do this before applying)
Recompute `sha256(canonical_json(<current production-readiness-manifest.json>))` and compare against `apply_plan["manifest_sha256"]`. If they differ, the plan is stale — refuse to apply and ask the operator to re-run `--onboard` to get a fresh apply-plan. See SKILL.md "Stale-plan detection" for the full contract.
