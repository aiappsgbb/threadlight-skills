# sample-pilot-v4 fixture

A minimal threadlight pilot configured for AGT v4-preview that demonstrates a
**passing** v4 deep-check scorecard. Sibling to `sample-pilot/` (which is
intentionally a failing pilot baseline). **Not a real Azure deployment** —
subscription IDs and resource-group names below are placeholders.

## Use

```bash
# from the threadlight-skills repo root
python skills/threadlight-production-ready/scripts/production_ready.py \
    --static \
    --agt-profile v4_preview \
    --root skills/threadlight-production-ready/references/fixtures/sample-pilot-v4
```

This produces (in the fixture root):

- `tests/production-readiness-manifest.json` — machine-readable scorecard
- `docs/production-readiness-report.md` — customer-facing markdown report

Outputs are gitignored within the fixture directory.

## What this fixture is by design

A pilot that **passes the AGT v4-preview deep checks** (`AGT-V4-001/002/003/006/007`):

- ✅ `requirements.txt` declares `agent-governance-toolkit-core==4.1.0` → AGT-V4-001 pass
- ✅ `policies/governance.yaml` carries `agent_control_specification_version:` + `intervention_points:` block with 5 canonical intervention keys → AGT-V4-002 pass
- ✅ `policies/dynamic-budget.yaml` uses `cost_per_window:` and `time_window:` → AGT-V4-003 detected
- ✅ `.github/workflows/agt-verify.yml` uses `microsoft/agent-governance-toolkit/action@v4` with `toolkit-version: "4.1.0"` → AGT-V4-006 pass
- ✅ `tests/verifier-report.json` carries 4 of 5 v4 audit fields → AGT-V4-007 pass
- ⚪ `AGT-V4-101` always emits as `not-verified` (tier 2 KQL probe deferred to v2)

Other pillars (network, secrets, observability, etc.) still fail because this
fixture is AGT-focused — only the agent-governance pillar is meant to pass.

## How this complements sample-pilot

| Fixture | AGT artefacts | Best for |
|---|---|---|
| `sample-pilot/` | None (intentional) | Demonstrates failing baseline across all 13 pillars |
| `sample-pilot-v4/` | Full v4 surface | Demonstrates passing AGT-V4-* deep checks; smoke validates the detection regex |
