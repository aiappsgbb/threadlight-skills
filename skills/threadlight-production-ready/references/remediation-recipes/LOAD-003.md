---
kind: sibling-skill
summary: Hold SLO thresholds (p95 latency / error rate) under load
sibling_skill: threadlight-loadtest
---

## Target file
N/A — this fix runs the threadlight `threadlight-loadtest` leg. LOAD-003 is must-fix when observed samples violated the declared `slo` thresholds (p95 latency / error rate), or not-verified when no samples or no SLO were collected. Remediation runs the leg (or its upstream fix) and refreshes `specs/load-manifest.json`, whose shared envelope production-ready reads as gap evidence for `LOAD-003`.

## Edit type
`sibling-skill`

## Edit recipe
1. Declare `slo` thresholds in the SPEC and collect real samples; if thresholds are violated, tune scaling / caching / model routing until they hold.
2. Re-run `threadlight-loadtest` to refresh the manifest.

   ```bash
   python3 skills/threadlight-loadtest/scripts/loadtest.py --spec specs/SPEC.md --out specs/load-manifest.json
   ```

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. LOAD-003 flips to `pass`/`should-fix` once `specs/load-manifest.json` records it in a `complete`, fresh envelope. A `partial`, stale, or `aborted` `specs/load-manifest.json` keeps `LOAD-003` at `not-verified` (or `must-fix` on negative evidence) — an incomplete leg never inflates readiness.
