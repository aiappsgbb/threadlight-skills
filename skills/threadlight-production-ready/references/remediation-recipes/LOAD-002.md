---
kind: sibling-skill
summary: Keep projected load inside the budget ceiling
sibling_skill: threadlight-loadtest
---

## Target file
N/A — this fix runs the threadlight `threadlight-loadtest` leg. LOAD-002 is must-fix when a known load projection exceeded the configured budget ceiling (the run aborted), or not-verified when no trustworthy projection / adapter / endpoint was available. Remediation runs the leg (or its upstream fix) and refreshes `specs/load-manifest.json`, whose shared envelope production-ready reads as gap evidence for `LOAD-002`.

## Edit type
`sibling-skill`

## Edit recipe
1. Inspect the projection in `specs/load-manifest.json`; either lower the planned load, raise the reviewed budget ceiling, or right-size capacity so the projection fits.
2. Ensure an adapter + endpoint + credential reference are configured so a real projection can be produced.
3. Re-run `threadlight-loadtest`. Raise `--budget-ceiling-usd` to the newly reviewed ceiling (USD) if that is the chosen fix; keep `--endpoint-class non-production`.

   ```bash
   python3 skills/threadlight-loadtest/scripts/loadtest.py \
     --profile "${LOAD_PROFILE_FILE}" \
     --budget-ceiling-usd 25 \
     --endpoint-class non-production \
     --out specs/load-manifest.json
   ```

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. LOAD-002 flips to `pass`/`should-fix` once `specs/load-manifest.json` records it in a `complete`, fresh envelope. A `partial`, stale, or `aborted` `specs/load-manifest.json` keeps `LOAD-002` at `not-verified` (or `must-fix` on negative evidence) — an incomplete leg never inflates readiness.
