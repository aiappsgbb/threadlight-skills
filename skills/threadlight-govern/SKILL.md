---
name: threadlight-govern
description: >-
  PROTECT-leg for threadlight pilots: wires the Microsoft Agent Governance
  Toolkit (AGT) into a deployed agent and verifies it is actually enforcing —
  not just documented. Scaffolds a versioned policy artefact, wires in-process
  governance middleware at the container boundary, runs `agt verify` for
  committed OWASP ASI 2026 evidence, and emits `specs/govern-manifest.json`
  for threadlight-production-ready pillars 2 and 7. USE FOR: agent runtime
  governance, AGT wiring, action-level policy, tool allow/deny,
  excessive-agency guardrail, governance middleware, agt verify, OWASP ASI
  2026, policy artefact, responsible-ai policy, PROTECT stage, govern leg,
  govern-manifest. DO NOT USE FOR: content filtering at the model edge (Azure
  AI Content Safety) — AGT governs actions; adversarial scanning
  (threadlight-redteam); quality evals (threadlight-evals); completeness gate
  (threadlight-safe-check); deep AGT authoring upstream (foundry-agt).
metadata:
  version: "0.1.0"
---

# Threadlight Govern — make AGT *enforce*, then prove it

The **PROTECT** leg of `path2production`. `threadlight-production-ready`
*scores* agent-runtime governance (pillar 2) and responsible-AI controls
(pillar 7), but until now the scorecard could only say *"go wire AGT"* and
delegate the actual work to the external `foundry-agt` skill. Nothing in the
pipeline ever **ran** governance. This skill is that missing executable leg.

```
DESIGN → BUILD/DEPLOY → DISCOVER → [ PROTECT ] → GOVERN → IMPROVE
                                   threadlight-govern
```

> **Why this skill exists.** A pilot can pass every structural gate —
> resources deployed, evals green, telemetry flowing — and still ship an
> agent with **no action-level governance**: a tool-calling agent that can
> `shell_exec`, email externally, or act with excessive agency because
> nothing constrains its actions at runtime. Microsoft's Responsible-AI
> guidance for Foundry treats agent-runtime controls as a **PROTECT**
> checkpoint, not an afterthought. AGT is the in-process middleware that
> enforces a committed policy on every tool call (~8–12µs/eval). This leg
> scaffolds that policy, wires the middleware at the boundary, and commits
> the `agt verify` evidence so the scorecard can *verify the leg ran*
> instead of *asking the operator to go run it*.

## What this skill governs (and does not)

- **Governs:** agent **actions** — tool calls, tool allow/deny, approval
  gates, excessive-agency limits — enforced in-process by AGT middleware.
- **Does NOT govern tokens:** prompt/response token-level filtering is the
  *model* guardrail (Azure AI Content Safety / prompt shields configured on
  the model). AGT references the RAI posture; it does not replace the model
  filter.
- **Does NOT red-team:** running adversarial probes against the agent is the
  DISCOVER leg → `threadlight-redteam`. Govern enforces the policy that
  red-team findings harden.

## The contract — `specs/govern-manifest.json`

`scripts/govern_check.py` walks the pilot repo and emits a manifest whose
capability keys **mirror the pillar-02 capability-detection table exactly**,
so `threadlight-production-ready` and this skill never disagree:

| Capability key | Meaning | Severity when missing |
|---|---|---|
| `middleware_wired_at_boundary` | AGT middleware imported + applied at the agent entry-point | must-fix |
| `policy_artefact_present` | a committed `agt-policy.yaml` / `policy.yaml` exists | must-fix |
| `policy_versioned` | policy pins a real version (not `latest`) | should-fix |
| `rai_policy_present` | policy declares content-filter / prompt-shield / PII block (pillar 7) | should-fix¹ |
| `verifier_artefact_present` | committed `agt verify` evidence exists | should-fix |
| `verifier_fresh` | verifier artefact within the freshness window | should-fix |
| `asi_reference_present` | OWASP ASI 2026 anchor present | should-fix |
| `sidecar_pattern` | informational — sidecar (Path B) vs in-process (Path A) | n/a |

¹ becomes `must-fix` when no policy artefact exists at all.

Verdict roll-up: any `must-fix` → `not-wired`; only `should-fix`/`not-verified`
→ `partial`; all clear → `wired`.

## Usage

```bash
# 1. Assess an existing pilot (read-only) — prints the wiring report
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
(verifier staleness window, default 90).

### The leg, end-to-end (what the agent does)

This is a **producing** leg — it explicitly creates artefacts (it does not
silently mutate the user's repo for *findings*; per the SACRED rule, finding
remediation is dispatched). The agent runs the leg in three moves:

1. **Scaffold policy** — if `policy_artefact_present` is must-fix, copy the
   right template from `references/policy-templates/` (`default`, `hitl`, or
   `pii-deny`), pin a version, and fill the tool allow/deny lists from the
   pilot's `agent.yaml` tool set.
2. **Wire middleware** — if `middleware_wired_at_boundary` is must-fix, apply
   the snippet in `references/wiring-snippet.md` at the agent entry-point
   (keyless, `DefaultAzureCredential`). See `references/remediation-recipes`
   parity with `AGT-001`.
3. **Verify + commit** — run `agt verify --strict`, commit the report to
   `docs/agt-verifier-report.md`, then re-run `govern_check.py --emit` so the
   manifest flips to `wired`.

## How `production-ready` consumes this

`threadlight-production-ready` pillar 2 reads `specs/govern-manifest.json`. If
the manifest is present and `verdict == wired` (and `verifier_fresh`), pillar 2
and the RAI half of pillar 7 report **verified** with the manifest as evidence,
instead of emitting the `AGT-001` remediation. A stale or `not-wired` manifest
re-opens the finding. No manifest → unchanged legacy behaviour (score + delegate).

## Relationship to `foundry-agt`

`foundry-agt` (awesome-gbb) is the deep upstream skill that *authors* AGT
capabilities and knows the full AGT/AgentOS surface. `threadlight-govern` is
the **thin pipeline leg** that decides *whether AGT is wired in this pilot*,
drives the three moves above, and produces the manifest the threadlight
scorecard understands. Use `foundry-agt` for advanced policy authoring; use
this skill to make governance a *step that runs* in `path2production`.

## Files

```
scripts/govern_check.py                 # stdlib validator → govern-manifest.json
references/policy-templates/
  default.policy.yaml                   # baseline allow/deny + RAI block
  hitl.policy.yaml                      # adds human-approval gates
  pii-deny.policy.yaml                  # strict PII egress denial
references/wiring-snippet.md            # middleware wiring (Python + TS)
references/govern-manifest.schema.json  # the manifest contract
references/fixtures/sample-wired/       # passing pilot (verdict: wired)
references/fixtures/sample-bare/        # ungoverned pilot (verdict: not-wired)
tests/test_govern_check.py              # stdlib unittest (no pytest)
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```
