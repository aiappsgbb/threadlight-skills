---
kind: sibling-skill
summary: Reduce data/prompt exfiltration attack-success-rate by denying egress sinks and re-running the red-team scan
sibling_skill: threadlight-redteam
---

## Target file
N/A — this fix runs the threadlight `threadlight-redteam` and `threadlight-govern` legs. The red-team scan measured a data/prompt exfiltration attack-success-rate (ASR) above the configured threshold — the agent could be coaxed into leaking its system prompt, secrets, or PII.

## Edit type
`sibling-skill`

## Edit recipe
1. Review the successful exfiltration attacks in `docs/redteam-report.md` (and `specs/redteam-manifest.json` → `asr.exfiltration`). Identify the egress channel used (external HTTP, email, file write, verbatim system-prompt disclosure).
2. Harden via `threadlight-govern` — apply the strict `pii-deny` policy template and:
   - Add the abused channels to `tools.deny` (`http_post`, `send_external_email`, `file_write_external`).
   - Set `data_egress.block_external_pii: true` and enable `pii_deny` / response redaction.
   - Ensure the system prompt is not returnable (instruction-hierarchy guard).
3. Re-run the adversarial scan and refresh evidence:

   ```bash
   python3 skills/threadlight-redteam/scripts/redteam_check.py --target . --emit
   ```

## Verification
Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`. SAFE-103 should flip from `must-fix` to `pass` once `specs/redteam-manifest.json` reports `exfiltration_asr_ok = pass` and the scan is fresh.
