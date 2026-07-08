# specs/deployment-posture.md — canonical deploy-time decision record
# Resolved by threadlight-deploy Phase 1.5, Path 1 (SPEC § 11f pre-declares deployment_target).
deployment_target: customer-pilot
source: provided            # read verbatim from SPEC § 11f (deployment_posture)
chosen_at: 2026-07-07T14:06:00Z
operator: citadel-sample (non-interactive CI run)
overrides:
  networking: public          # status: supported-now
  replicas: single            # status: supported-now
  retention: 90d              # status: supported-now (Log Analytics retention param)
  model_pinning: ga-pinned    # status: supported-now (via __MODEL_VERSION__)
  defender: off               # status: supported-now (off + Ignore tag)
  cost_guardrails: none       # status: supported-now
  backup_dr: none             # status: supported-now
  continuous_eval: plan-a     # status: supported-now (foundry-evals Plan A defaults)
deferred_decisions:
  - waf-front-door
  - dr-runbook
  - regulated-7y-retention
