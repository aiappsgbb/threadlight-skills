---
kind: sibling-skill
summary: Reduce jailbreak attack-success-rate by hardening the governance policy and re-running the red-team scan
sibling_skill: threadlight-redteam
---

## Target file
N/A — this fix runs the threadlight `threadlight-redteam` and `threadlight-govern` legs. The red-team scan measured a jailbreak attack-success-rate (ASR) above the configured threshold; remediation hardens the agent's governance policy and re-scans to confirm the ASR drops.

## Edit type
`sibling-skill`

## Edit recipe
1. Inspect the failing rows in `docs/redteam-report.md` (and `specs/redteam-manifest.json` → `asr.jailbreak`) to see which jailbreak strategies succeeded.
2. Harden the agent against those strategies using `threadlight-govern`:
   - Strengthen the `responsible_ai.prompt_shields` block in `policy.yaml` (`jailbreak: enabled`, `indirect_attack: enabled`).
   - Tighten the model content filter (`raiPolicyName` / Foundry content filter) to `high` for the abused harm categories.
   - Constrain `tools.deny` and `excessive_agency` limits so a successful jailbreak cannot reach a consequential action.
3. Re-run the adversarial scan via `threadlight-redteam` and refresh evidence:

   ```bash
   python3 skills/threadlight-redteam/scripts/redteam_check.py --target . --emit
   ```

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. SAFE-101 should flip from `must-fix` to `pass` once `specs/redteam-manifest.json` reports `jailbreak_asr_ok = pass` (jailbreak ASR ≤ threshold) and the scan is fresh.
