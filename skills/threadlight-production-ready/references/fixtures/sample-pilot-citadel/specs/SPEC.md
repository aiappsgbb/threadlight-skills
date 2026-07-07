# sample-pilot-citadel — Citadel-spoke happy-path

## 1. Mission
Citadel-spoke pilot fixture demonstrating all v0.3.0 critical static checks
passing on a real Bicep template. Used by `test_smoking_gun_regression.py`
as the positive control.

## 4. Architecture
Foundry account (publicNetworkAccess=Disabled) fronted by a Citadel APIM
hub Access Contract; pilot consumes the LLM via the hub's Unified AI API.
Inside the spoke: ACA workload with EasyAuth, KV with RBAC + soft-delete
+ purge, App Insights linked to a Log Analytics workspace, UAMI for all
data-plane access.

## 8. HITL
HITL gate: human-in-the-loop approval required for any tool call with
externalImpact=true. Audit trail stored in App Insights via OTel emit.

## 9. Eval scenarios
threshold pass rate 90%, grader llm-as-judge, dataset v1.0
scheduled nightly cron (Azure Functions cron trigger)

## 10. Cost and Pricing
budget $500/month, cost_owner finops@example.com, scaleToZero on ACA
between 22:00–06:00 UTC.

## 12. Production Readiness

- target_posture: citadel-spoke
- residency: EU
- rto: 4h
- rpo: 1h
- sla: 99.5
- incident_owner: oncall@example.com
- citadel_hub_rg: rg-citadel-hub-eu (set TL_CITADEL_HUB_RG=rg-citadel-hub-eu to enable NET-501 probe)
- runbook: docs/runbook.md
- backup configured + restore-drill artefact: docs/restore-drill-2026-05-15.md
- failure modes catalogued in this SPEC
- postmortem template referenced
- severity matrix documented
- AGT governance authored as a committed policy.yaml (schema-valid: version + name + rules), linted + verified in CI (.github/workflows/governance.yml)
- OWASP ASI 2026 reference present
- Defender for AI, Defender for Key Vault, Defender for Containers all set to Standard
