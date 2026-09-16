# EU AI Act → Threadlight artifact mapping

`scripts/ai_act_evidence.py` is a **terminal aggregator**. It does not run any
new check — it maps the artifacts this skill and its siblings *already produce*
onto EU AI Act articles and emits a tenant-local evidence pack. It is offline,
deterministic, and read-only with respect to source artifacts (it writes the pack).
It never calls Azure. Its labels describe inventory, **not certification**.

> This is an engineering aid, **not legal advice**, and does not by itself
> constitute an EU AI Act conformity assessment. Article scope and risk
> classification must be confirmed by a qualified reviewer.

## How coverage is graded

Each article resolves to one state:

| State | Meaning |
| --- | --- |
| `covered` | The article-specific inventory predicate below is met; not a universal green verdict. |
| `partial` | Some evidence is present but incomplete (a pillar is amber, or one of two sources is missing). |
| `gap` | The required evidence is absent. Run the mapped skill, then re-run. |
| `scaffold` | Template only — a human must complete the assessment (Art 27). |
| `not-applicable` | The obligation does not apply to this system. |

Missing, unreadable or syntactically invalid JSON is treated as absent. Parseable
JSON is present for provenance; this does **not** imply schema validation.
Every present source is fingerprinted with a SHA-256 in `ai-act-evidence.json`.

Actual predicates in [`ai_act_evidence.py`](../scripts/ai_act_evidence.py):

- **Art 11:** scorecard and MCP SBOM must be non-empty JSON objects or lists.
- **Art 15:** eval and red-team sources must be non-empty JSON objects or lists.
  Their verdicts and the mapped SBOM do not gate this predicate.
- **Art 9:** the shared governance evaluator must return `pass` and `live: true`;
  its binding scope must still be respected.
- **Art 12:** observability pillar `green` plus non-empty identity content.
  **Art 14:** HITL pillar `green` (`amber` gives `partial`). Pillar lookup prefers
  `status_with_waivers` over `status_raw`.
- **Art 26:** positive integer `summary.subject_count`, with integer
  `summary.owned` equal to it. **Art 27:** human-authored scaffold.

Empty objects/lists cannot satisfy `_has_content`, but non-empty **schema-invalid**
sources can satisfy Art 11 / Art 15. Those predicates do not check freshness,
eval success or safety thresholds. Validate upstream artifacts with their owners;
do not interpret this pack as a quality gate or conformity assessment.

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
| `specs/governance-manifest.json` | `threadlight-govern` / deployed collector | Current v1 evidence; legacy `govern-manifest.json` discovery is not current proof |
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
