# Examples

Real, **sanitized** outputs captured from Threadlight runs — published so you can inspect
what the skills actually generate and govern, rather than take our word for it. This is the
[case study](../docs/case-study.html)'s *"don't trust the agent, read the receipts"* ethos,
made literal.

| Sample | What it shows | Captured |
|---|---|---|
| [`returns-triage-governed/`](./returns-triage-governed) | A returns-triage agent built end-to-end on a private Citadel hub: spec → network-isolated IaC → generated skills → a **committed governance policy** (linted + CI-gated) → readiness scorecard. | 2026-07-07 |

## Sanitization

Every sample has live credentials and identifiers removed before publication:

- `.env*` and `.azure/` (API keys, subscription / tenant / resource IDs) are **excluded**.
- Private hub endpoints and resource names are replaced with **placeholders**
  (e.g. `apim-citadel-hub.azure-api.net`, `rg-returns-triage-sample`).

They are **illustrative snapshots, not runnable as-is** — point-in-time captures that show
the shape and quality of the generated artifacts.
