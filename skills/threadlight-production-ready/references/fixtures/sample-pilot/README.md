# sample-pilot fixture

A minimal threadlight pilot used by `threadlight-production-ready` for
documentation and local repro. **Not a real Azure deployment.** Subscription IDs
and resource group names below are placeholders.

## Use

```bash
# from the threadlight-skills repo root
python skills/threadlight-production-ready/scripts/production_ready.py \
    --static \
    --root skills/threadlight-production-ready/references/fixtures/sample-pilot
```

This produces:

- `tests/production-readiness-manifest.json` — machine-readable scorecard
- `docs/production-readiness-report.md` — customer-facing markdown report

Outputs are gitignored within the fixture directory (see `.gitignore`).

## What this fixture is missing on purpose

The fixture is intentionally a **failing pilot** so the report shows ~ 25-50%
score with multiple `must-fix` findings. It demonstrates:

- ✅ a network module in Bicep, public access disabled (NET-001, NET-003 pass)
- ❌ no AGT middleware, no Key Vault, no App Insights, no eval scenarios
- ❌ no budget alerts, no runbook, no RAI policy
- 🟡 a populated SPEC § 12 with `target_posture: standard-ai-gateway`

Run the skill to see exactly what gaps the production-readiness check surfaces.

## To make this fixture green

Walk the `uplift_plan` in the generated markdown report. Each finding lists the
awesome-gbb skill that fixes it.
