---
kind: sibling-skill
summary: Reduce prompt-injection attack-success-rate by hardening tool authorization and re-running the red-team scan
sibling_skill: threadlight-redteam
---

## Target file
N/A — this fix runs the threadlight `threadlight-redteam` and `threadlight-govern` legs. The red-team scan measured a prompt-injection (direct and/or indirect/XPIA) attack-success-rate (ASR) above the configured threshold.

## Edit type
`sibling-skill`

## Edit recipe
1. Review the succeeded injection attacks in `docs/redteam-report.md` (and `specs/redteam-manifest.json` → `asr.prompt_injection` / `asr.indirect_attack`). Note whether the wins are direct (user-supplied) or indirect (injected via retrieved content / tool output).
2. Harden via `threadlight-govern`:
   - Enable `prompt_shields.indirect_attack` in `policy.yaml` to defend against XPIA from retrieved/tool content.
   - Move consequential tools behind a human-in-the-loop approval gate (`human_in_the_loop.require_approval_for`) so an injected instruction cannot auto-execute.
   - Restrict `tools.allow` to the minimum set and add abused sinks to `tools.deny`.
3. Re-run the adversarial scan and refresh evidence:

   ```bash
   python3 skills/threadlight-redteam/scripts/redteam_check.py --target . --emit
   ```

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. SAFE-102 should flip from `must-fix` to `pass` once `specs/redteam-manifest.json` reports `prompt_injection_asr_ok = pass` and the scan is fresh.
