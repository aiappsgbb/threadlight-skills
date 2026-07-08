# EU AI Act evidence pack — the threadlight way

> **What it is.** A regulator-facing evidence pack, built from the artifacts your
> pilot *already produced*. Threadlight maps them, article by article, onto the
> EU AI Act — so the deploy that just went green also carries its own compliance
> file. Tenant-local, offline, deterministic. It never calls Azure, and it never
> invents coverage.

The EU AI Act's high-risk obligations land in 2026. Most teams treat that as a
second project — a spreadsheet, a consultant, a scramble. It isn't. By the time
`threadlight-production-ready` returns green, you have already generated the
evidence: a production-readiness scorecard, an MCP SBOM, an agent-identity
AI-BOM, a governance manifest, and eval / red-team manifests. The evidence pack
is the last, small step that **maps what you have onto what the regulation
asks** — and tells you, honestly, where the gaps are.

## One prompt — Copilot does the rest

You don't run any command. GitHub Copilot, driving the Threadlight skills, does
everything: it reads the artifacts in your repo, maps them to the articles, and
writes the pack next to your code.

> `"Use threadlight-production-ready to generate the EU AI Act evidence pack for this pilot."`

Three files land under `docs/compliance/`:

| File | What it is |
| --- | --- |
| `ai-act-evidence.json` | The machine-readable article map + a coverage summary, with a SHA-256 for every source. |
| `annex-iv-technical-file.md` | The Article 11 / Annex IV technical documentation — every gap flagged in plain sight. |
| `fria-scaffold.md` | An Article 27 fundamental-rights-impact template for a human to complete. |

## The map — evidence you already have → the article it satisfies

| Article | Obligation | Evidence Threadlight already produced |
| --- | --- | --- |
| **Art 9** — Risk management | A continuous, documented risk process | Governance manifest + the agent-governance pillar |
| **Art 11 + Annex IV** — Technical documentation | A technical file describing design & controls | Production-readiness scorecard + MCP SBOM |
| **Art 12** — Record-keeping | Automatic, lifetime event logging | Observability pillar + agent-identity AI-BOM |
| **Art 14** — Human oversight | Effective oversight by real people | HITL & audit pillar |
| **Art 15** — Accuracy, robustness, cybersecurity | Demonstrated accuracy + resilience | Eval manifest + red-team manifest + MCP SBOM |
| **Art 26** — Deployer obligations | A named, responsible owner | Agent-identity AI-BOM (owner coverage) |
| **Art 27** — FRIA | A fundamental-rights impact assessment | Human-authored scaffold |

## Honest coverage — never a green wash

Every article is graded one of five states — `covered`, `partial`, `gap`,
`scaffold`, or `not-applicable` — and the pack **cannot fabricate a `covered`**:

- An empty repo produces zero `covered` articles — all honest gaps.
- A missing or malformed source degrades to `gap` / `partial` with a remediation
  pointer at the skill that produces it. It is never silently counted as evidence.
- Every source that *is* present carries a SHA-256 fingerprint, so the file is
  traceable to the exact artifact it came from.
- The same repo always produces byte-identical output — safe to commit, diff, and
  gate in CI.

Ask Copilot to *check* rather than generate, and it fails the run (non-zero exit)
when a load-bearing article — the technical file (Art 11), record-keeping
(Art 12), or accuracy & robustness (Art 15) — is a gap:

> `"Use threadlight-production-ready to check the EU AI Act evidence pack and fail if a load-bearing article is missing."`

## What this is — and isn't

This amplifies the platform. It turns Azure AI Foundry's own eval, red-team,
observability, and identity outputs into evidence a regulator can read — it does
not replace any of them, and it does not replace your conformity assessment. It
is an engineering aid, **not legal advice**. Have a qualified reviewer confirm
scope, risk classification, and completeness before you rely on it.

---

*The article → artifact → skill mapping is documented in full in
[`references/eu-ai-act-mapping.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/eu-ai-act-mapping.md).
Back to the [13-pillar production-readiness reference](production-readiness.md).*
