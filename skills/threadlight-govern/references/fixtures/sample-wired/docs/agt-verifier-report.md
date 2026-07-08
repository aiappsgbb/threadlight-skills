# AGT governance attestation (FIXTURE — governed)

Emitted by `agt verify --badge` (OWASP ASI 2026 — Agentic Security Initiative).
Commit it on every model / prompt / policy change so the attestation tracks the
deployed agent.

[![OWASP ASI 2026](https://img.shields.io/badge/OWASP_ASI_2026-attested-brightgreen?style=flat-square)](https://github.com/microsoft/agent-governance-toolkit)

```json
{
  "schema": "governance-attestation/v1",
  "mode": "components",
  "passed": true,
  "coverage_pct": 100,
  "controls_passed": 10,
  "controls_total": 10,
  "toolkit_version": "<recorded at run time>",
  "attestation_hash": "<recomputed each run>"
}
```

Coverage reflects how much of the real runtime governance is wired
(`agent_compliance` / `agent_os.integrations.*`). To raise it, wire the
framework integration for your agent runtime — the attestation names any ASI
control still absent.
