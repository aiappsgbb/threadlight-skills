# EU AI Act evidence pack — the threadlight way

> **What it is.** An engineering evidence inventory for human review, built from
> artifacts your pilot already produced. It maps selected artifacts to EU AI Act
> topics. Tenant-local and offline; it evaluates evidence at the current time
> and never calls Azure.
> `covered` is an inventory result, **not certification** or legal compliance.

The pack brings a production-readiness scorecard, MCP SBOM, agent-identity
AI-BOM, governance manifest and eval / red-team manifests into one article map.
It does not generate those upstream artifacts or establish that a green deploy
satisfies an obligation. A qualified reviewer must determine applicable
obligations, dates, risk classification and the adequacy of the underlying evidence.

## One prompt — Copilot does the rest

You don't run any command. GitHub Copilot, driving the Threadlight skills, does
everything: it reads the artifacts in your repo, maps them to the articles, and
writes the pack next to your code.

> `"Use threadlight-production-ready to generate the EU AI Act evidence pack for this pilot."`

Three files land under `docs/compliance/`:

| File | What it is |
| --- | --- |
| `ai-act-evidence.json` | The machine-readable article map + a coverage summary, with a SHA-256 of each present source's normalized text. |
| `annex-iv-technical-file.md` | The Article 11 / Annex IV technical documentation — every gap flagged in plain sight. |
| `fria-scaffold.md` | An Article 27 fundamental-rights-impact template for a human to complete. |

## The map — existing evidence → article for review

| Article | Obligation | Evidence Threadlight already produced |
| --- | --- | --- |
| **Art 9** — Risk management | A continuous, documented risk process | Governance manifest + the agent-governance pillar |
| **Art 11 + Annex IV** — Technical documentation | A technical file describing design & controls | Production-readiness scorecard + MCP SBOM |
| **Art 12** — Record-keeping | Automatic, lifetime event logging | Observability pillar + agent-identity AI-BOM |
| **Art 14** — Human oversight | Effective oversight by real people | HITL & audit pillar |
| **Art 15** — Accuracy, robustness, cybersecurity | Demonstrated accuracy + resilience | Eval manifest + red-team manifest + MCP SBOM |
| **Art 26** — Deployer obligations | A named, responsible owner | Agent-identity AI-BOM (owner coverage) |
| **Art 27** — FRIA | A fundamental-rights impact assessment | Human-authored scaffold |

## Coverage semantics and limits

The states are `covered`, `partial`, `gap`, `scaffold`, and `not-applicable`.
They reflect article-specific predicates, not a universal all-signals-green gate:

- **Art 11:** a scorecard and MCP SBOM that are non-empty JSON objects or lists
  are enough for `covered`.
- **Art 15:** non-empty JSON objects or lists for evals and red-team are enough;
  their quality/safety verdicts are not checked by this predicate. The linked SBOM
  is included in the map but is not required by this coverage predicate.
- **Art 9:** the shared binding evaluator must return `pass` with `live: true`.
  This remains binding-scoped evidence, not satisfaction of the entire article.
- **Art 12 / 14:** observability plus non-empty identity content / HITL pillar
  status are checked; pillar lookup prefers the with-waivers status. **Art 26**
  checks positive subject count and equal owned count. **Art 27** is a scaffold.

Missing, unreadable or syntactically invalid JSON is not a present source.
Empty `{}` / `[]` cannot satisfy the non-empty predicates. However, parseable,
non-empty **schema-invalid** content can satisfy Art 11 or Art 15: the aggregator
does not generally validate source schemas, freshness or successful eval outcomes.
Run the owning validators and inspect their results separately.

Present sources carry **normalized-text SHA-256 fingerprints**, not exact
source-byte digests. The producer reads UTF-8 text with newline normalization
(including CRLF → LF), then re-encodes it as UTF-8 before hashing. A CRLF file's
ordinary on-disk SHA-256 therefore differs from the emitted fingerprint. These
hashes establish neither authenticity nor quality.

Output is deterministic only for the same artifacts, implementation/dependencies
and evaluation time. The CLI records current wall-clock time in `generated_at`,
so reruns over unchanged files need not be byte-identical. Article 9 also
reevaluates policy expiry and evidence freshness: unchanged artifacts can lose
coverage as time passes. Do not freeze time to preserve an old coverage result.
Review content and privacy before committing a pack. An empty repo produces no
`covered` articles.

Ask Copilot to *check* rather than generate, and it fails the run (exit `3`)
when a load-bearing article — the technical file (Art 11), record-keeping
(Art 12), or accuracy & robustness (Art 15) — is a gap:

> `"Use threadlight-production-ready to check the EU AI Act evidence pack and fail if a load-bearing article is missing."`

`--check` permits `partial`; exit `0` is not a compliance pass. An unusable root
or unwritable output exits `2`.

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
