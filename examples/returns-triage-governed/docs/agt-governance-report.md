# Agent governance (AGT) — wiring report

> Verdict: **PARTIAL** · profile `auto` · captured 2026-07-07T13:02:05+00:00

| Capability | Status | Evidence / hint |
|---|---|---|
| `verifier_artefact_present` | 🟠 should-fix | no committed `agt verify` evidence (docs/agt-verifier-report.md / tests/agt-verifier.json) |
| `middleware_wired_at_boundary` | ⚪ not-verified | no recognised agent entry-point found to inspect |
| `verifier_fresh` | ⚪ not-verified | no verifier artefact to age |
| `policy_artefact_present` | ✅ pass | policy.yaml |
| `policy_versioned` | ✅ pass | version: 1.0.0 |
| `rai_policy_present` | ✅ pass | content-filter / prompt-shield / PII block detected in policy |
| `asi_reference_present` | ✅ pass | OWASP ASI 2026 reference found |
| `sidecar_pattern` | ➖ not-applicable | in-process (Path A) — no sidecar |

Consumed by `threadlight-production-ready` pillars 2 (agent-governance) + 7 (responsible-ai).
