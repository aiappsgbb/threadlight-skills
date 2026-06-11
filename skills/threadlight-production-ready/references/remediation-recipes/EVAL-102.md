---
kind: manual
summary: Confirm whether a regression evaluation baseline exists
target_file: manual
edit_type: manual
---

## Target file
Manual operator/team conversation. Evidence may later be documented in `SPEC.md` section 9, `evals/`, or release notes, but this recipe does not prescribe a single file edit.

## Edit type
`manual` — DO NOT auto-apply. The finding asks whether the customer has a regression eval baseline; the assessor cannot determine baseline quality, threshold approval, or release-blocking intent from repository structure alone.

## Edit recipe
Present this to the operator before proceeding:

> **EVAL-102 — Regression eval baseline discovery**
>
> 1. Identify the latest accepted evaluation baseline for the production workload: dataset version, prompt/agent version, model deployment, thresholds, and pass/fail decision.
> 2. Confirm the baseline is reviewed by the owning team and is used to compare future releases.
> 3. If no accepted baseline exists, schedule a field-team conversation to define one before go-live.
> 4. Record the baseline location, owner, approval date, and release-blocking policy in the project handover notes or SPEC section 9.
>
> If go-live proceeds without a regression baseline, file an explicit waiver with signed justification.

## Verification
Re-run Threadlight production readiness after the operator documents the baseline decision:

```bash
python3 scripts/production_ready.py --target-rg ${TARGET_RG} --target-sub ${TARGET_SUB}
```

EVAL-102 remains `not-verified` until a future automated probe can retrieve baseline and threshold evidence. For v0.5.0, successful remediation is the documented manual decision or waiver.

## Stale-plan check (the agent MUST do this before applying)
Recompute `sha256(canonical_json(<current production-readiness-manifest.json>))` and compare against `apply_plan["manifest_sha256"]`. If they differ, the plan is stale — refuse to apply and ask the operator to re-run `--onboard` to get a fresh apply-plan. See SKILL.md "Stale-plan detection" for the full contract.
