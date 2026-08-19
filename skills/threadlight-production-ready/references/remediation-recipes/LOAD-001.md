---
kind: sibling-skill
summary: Keep load tests inside the production-safety guard
sibling_skill: threadlight-loadtest
---

## Target file
N/A — this fix runs the threadlight `threadlight-loadtest` leg. LOAD-001 is must-fix when a load run targeted a `production` endpoint without an explicit `allow_production` acknowledgement — the guard aborted the run. Remediation runs the leg (or its upstream fix) and refreshes `specs/load-manifest.json`, whose shared envelope production-ready reads as gap evidence for `LOAD-001`.

## Edit type
`sibling-skill`

## Edit recipe
1. Point the load test at a non-production endpoint (`--endpoint-class non-production`) — the default, safe path. Only if a production run is genuinely intended and reviewed, pass `--endpoint-class production --allow-production` with an explicit written justification.
2. Re-run `threadlight-loadtest` to produce a clean load manifest. `--budget-ceiling-usd` is your explicit, reviewed cost ceiling in USD (example below: 25); the endpoint/adapter/credential wiring lives in the JSON load profile.

   ```bash
   python3 skills/threadlight-loadtest/scripts/loadtest.py \
     --profile "${LOAD_PROFILE_FILE}" \
     --budget-ceiling-usd 25 \
     --endpoint-class non-production \
     --out specs/load-manifest.json
   ```

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. LOAD-001 flips from `must-fix` to `pass` once `specs/load-manifest.json` records `LOAD-001: pass` in a `complete`, fresh envelope. A `partial`, stale, or `aborted` `specs/load-manifest.json` keeps `LOAD-001` at `not-verified` (or `must-fix` on negative evidence) — an incomplete leg never inflates readiness.
