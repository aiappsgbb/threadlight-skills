# EU AI Act → Threadlight artifact mapping

`scripts/ai_act_evidence.py` is a **terminal aggregator**. It does not run any
new check — it maps the artifacts this skill and its siblings *already produce*
onto EU AI Act articles and emits a tenant-local evidence pack. It is offline,
deterministic, and read-only. It never calls Azure and never fabricates coverage.

> This is an engineering aid, **not legal advice**, and does not by itself
> constitute an EU AI Act conformity assessment. Article scope and risk
> classification must be confirmed by a qualified reviewer.

## How coverage is graded

Each article resolves to one state:

| State | Meaning |
| --- | --- |
| `covered` | Every required source is present and its signal is green. |
| `partial` | Some evidence is present but incomplete (a pillar is amber, or one of two sources is missing). |
| `gap` | The required evidence is absent. Run the mapped skill, then re-run. |
| `scaffold` | Template only — a human must complete the assessment (Art 27). |
| `not-applicable` | The obligation does not apply to this system. |

A **missing or malformed** source is treated as absent (never as evidence), so a
sparse repo produces honest gaps rather than a green wash. Every present source
is fingerprinted with a SHA-256 recorded in `ai-act-evidence.json`.

## The map

| Article | Obligation | Source artifact(s) | Produced by | Remediation skill |
| --- | --- | --- | --- | --- |
| **Art 9** — Risk management | Continuous, documented risk process | govern manifest + scorecard `agent-governance` pillar | `threadlight-govern`, `threadlight-production-ready` | `threadlight-govern` |
| **Art 11 + Annex IV** — Technical documentation | Technical file describing design & controls | scorecard manifest + `mcp-sbom.json` | `threadlight-production-ready` | `threadlight-production-ready` |
| **Art 12** — Record-keeping | Automatic lifetime event logging | scorecard `observability` pillar + `agent-identity.json` | `threadlight-production-ready`, `foundry-observability` | `foundry-observability` |
| **Art 14** — Human oversight | Effective oversight by natural persons | scorecard `hitl-audit` pillar | `threadlight-hitl-patterns` | `threadlight-hitl-patterns` |
| **Art 15** — Accuracy, robustness, cybersecurity | Demonstrated accuracy + resilience | `evals-manifest.json` + `redteam-manifest.json` + `mcp-sbom.json` | `threadlight-evals`, `threadlight-redteam` | `threadlight-evals` |
| **Art 26** — Deployer obligations | Named responsible owner, operate as instructed | `agent-identity.json` (owner coverage) | `threadlight-production-ready` (Epic B) | `foundry-agt` |
| **Art 27** — FRIA | Fundamental-rights impact assessment | *(scaffold — human-authored)* | — | `threadlight-govern` |

The `--check` flag exits `3` when a **load-bearing** article — Art 11, Art 12, or
Art 15 — is a `gap`; otherwise exit `0`. An unusable `--root` or unwritable
`--out` exits `2`.

## Where the sources come from

| Artifact | Emitted by | Default location |
| --- | --- | --- |
| `tests/production-readiness-manifest.json` | `production_ready.py` (the scorecard) | `tests/` or next to the report under `docs/` |
| `mcp-sbom.json` | `mcp_sbom.py` (Epic A) | repo root or `docs/` |
| `agent-identity.json` | `agent_identity.py` (Epic B) | repo root or `docs/` |
| `govern-manifest.json` | `threadlight-govern` | repo root or `specs/` |
| `specs/evals-manifest.json` | `threadlight-evals` | `specs/` |
| `specs/redteam-manifest.json` | `threadlight-redteam` | `specs/` |

## Outputs

`python3 scripts/ai_act_evidence.py --root . --out docs/compliance` writes three
files:

- **`ai-act-evidence.json`** — the machine-readable article map with per-source
  provenance and a coverage summary.
- **`annex-iv-technical-file.md`** — the human-readable Article 11 / Annex IV
  technical file, with each gap explicitly flagged.
- **`fria-scaffold.md`** — an Article 27 fundamental-rights impact-assessment
  template for a human to complete.

This amplifies the platform: it turns Foundry's own eval, red-team,
observability, and identity outputs into regulator-facing evidence. It does not
replace a conformity assessment, and it does not replace any platform primitive.
