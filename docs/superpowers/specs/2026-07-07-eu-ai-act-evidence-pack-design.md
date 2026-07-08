# EU AI Act evidence pack (Part-2 capstone) — Design Spec

- **Status:** Draft (awaiting user review)
- **Date:** 2026-07-07
- **Author:** brainstormed with Copilot CLI
- **Related work:** PR #70 (MCP supply-chain gate `SUP-010..013` + `mcp_sbom.py` producer → Annex IV AI-BOM); PR #89 (agent-identity binding gate `IAM-006..009` + `agent_identity.py` producer → Art 12 identity / Art 26 deployer); the Part-2 runtime-governance roadmap — this is producer **C**, the terminal **aggregator** that consumes A + B + the `govern` verifier report + the production-readiness scorecard.

## 1. Problem & motivation

The EU AI Act's high-risk obligations phase in through **Aug 2 2026**; non-compliance
carries penalties up to **€35M / 7% of global turnover**. A provider/deployer of a
high-risk AI system must be able to show, on demand: an **Annex IV technical file**
(Art 11), a **risk-management system** (Art 9), **record-keeping / logging** (Art 12),
**human oversight** (Art 14), **accuracy · robustness · cybersecurity** (Art 15),
**deployer obligations** (Art 26), and — for many deployers — a **Fundamental Rights
Impact Assessment** (Art 27).

A team shipping an agent on Foundry already *produces most of this evidence* — a
13-pillar production-readiness scorecard, an MCP AI-BOM (`mcp-sbom.json`), an
agent-identity BOM (`agent-identity.json`), a `govern` verifier report, and
eval/red-team manifests. But that evidence is **scattered across artifacts and
mapped to no article**. Assembling it into a regulator-ready file is manual, done
late, and error-prone — and the temptation is to *claim* coverage that isn't
evidenced.

**C makes the last mile trivial:** one **offline, tenant-local, deterministic**
aggregation that maps the artifacts the customer already has onto the AI Act's
articles and emits a regulator-ready evidence pack — **honest about gaps, never
fabricating coverage**.

### Why this is "amplify the platform, make it trivial" (not replace)

Every input is a first-party or Threadlight-produced artifact (Foundry Continuous
Evaluation, AI Red Teaming Agent, Azure Monitor / App Insights, Purview, and our
`govern` / identity / supply-chain producers). C **orchestrates and attests**; it
performs **no conformity assessment**, gives **no legal advice**, and does not
replace a notified body. It scaffolds the file a human compliance owner completes
and signs.

### Non-goals (v1)

- **Not legal advice, not a conformity assessment, not a notified-body substitute.**
  Output is explicitly framed as "evidence assembled from your artifacts + a
  scaffold for your compliance owner to complete."
- **No live tenant pulls in v1** — offline aggregation of *committed* artifacts only
  (deterministic, reviewable, PR-diffable). Live Azure Monitor / Purview enrichment
  is fast-follow.
- **Art 12 _immutable_ log bundle** (actual log-byte export + notarization/signing)
  is fast-follow; v1 records the logging **posture + pointers**, not the log bytes.
- **No new pillar and no new must-fix findings** in the scorecard. C is a
  **generator**, not a gate — so it adds **no** `FINDING_CATALOG` entries and
  therefore **no** new remediation recipes. It re-projects existing findings.
- **Not a new skill.** Lives inside `threadlight-production-ready` next to A + B.

## 2. Boundary with existing skills

| Artifact (input) | Produced by | AI Act article it evidences |
|---|---|---|
| `production-readiness-manifest.json` | production-ready assessor | Art 9 (risk mgmt), Art 11 / Annex IV (tech file) |
| `mcp-sbom.json` | A (`mcp_sbom.py`) | Annex IV / Art 11 (AI-BOM), Art 15 (supply-chain security) |
| `agent-identity.json` | B (`agent_identity.py`) | Art 12 (identity traceability), Art 26 (deployer/owner) |
| `govern-manifest.json` + `agt-verifier-report.md` | `threadlight-govern` | Art 9 (risk/policy), Art 15 (RAI), OWASP-ASI evidence |
| `specs/evals-manifest.json` | `threadlight-evals` / Foundry CE | Art 15 (accuracy) |
| `specs/redteam-manifest.json` | `threadlight-redteam` / AI Red Teaming Agent | Art 15 (robustness) |
| Pillar 05 (observability) findings | production-ready assessor | Art 12 (logging posture) |
| Pillar 08 (HITL & audit) findings | production-ready assessor | Art 14 (human oversight) |

C sits **on top** of all of these. It is a **terminal aggregator** — consumed by
no other producer; its outputs are documents for a human/regulator.

## 3. Decisions (locked during brainstorming)

1. **Placement:** new `scripts/ai_act_evidence.py` inside `threadlight-production-ready`,
   with a **standalone CLI** (`--root`, `--out`, `--check`). It is **not** wired into
   the assessor's exit code — it is a **post-green compliance step**, run after the
   scorecard is green. `cicd` / `govern` may wire it as a pipeline step later.
2. **Offline, stdlib-only, tenant-local, deterministic.** No network. Same house
   style as `mcp_sbom.py` / `agent_identity.py`.
3. **Article scope v1:** Art 9, Art 11 + Annex IV, Art 12, Art 14, Art 15, Art 26,
   and an Art 27 **FRIA scaffold**.
4. **Coverage states:** `covered` · `partial` · `gap` · `not-applicable` · `scaffold`.
   A missing/malformed source artifact → `gap` (or `partial`) **with a remediation
   pointer** — never fabricated as covered.
5. **Outputs (default dir `docs/compliance/`):**
   `ai-act-evidence.json` (machine-readable manifest) · `annex-iv-technical-file.md`
   (Art 11 file, populated + GAP-marked) · `fria-scaffold.md` (Art 27 template).
6. **Versioning:** production-ready `0.7.0 → 0.8.0`; plugin manifests `1.8.0 → 1.9.0`.

## 4. Architecture & components

### 4.1 Aggregator — `ai_act_evidence.py` (new, pure-stdlib)

- `discover(root) -> Sources` — locate + load each optional artifact (scorecard
  manifest, `mcp-sbom.json`, `agent-identity.json`, `govern-manifest.json`,
  `evals-manifest.json`, `redteam-manifest.json`, spec). Missing/malformed → recorded
  as absent, never raises.
- `ARTICLE_MAP` — the data table (§4.2): article → obligation, source artifact(s),
  coverage rule, remediation skill(s).
- `assess(root) -> (evidence: dict, articles: list)` — for each mapped article,
  evaluate coverage from the discovered sources; pin each source's `sha256`.
- `build_evidence(articles, sources, *, now=None) -> dict` — the `ai-act-evidence.json`
  manifest (§5), schema `threadlight.ai-act-evidence/v1`, parity with the other BOMs.
  `now` injectable for deterministic tests.
- `render_annex_iv(evidence, sources) -> str` — the Annex IV technical-file markdown:
  one section per Annex IV point, populated from artifacts, explicit
  `> **GAP — not yet evidenced**` blocks where a source is absent.
- `render_fria(evidence) -> str` — the Art 27 FRIA scaffold (template with prompts,
  pre-filled where the system spec provides context).
- `main(argv)` — CLI. Default: emit the three outputs to `--out`, print a coverage
  summary to stderr, exit `0`. `--check`: exit `3` if any **must-have** article
  (Art 11, Art 12, Art 15) is a `gap` — for optional CI gating.

### 4.2 Article map (the heart)

| Article | Obligation | Source artifact(s) | `covered` when… | Remediation skill |
|---|---|---|---|---|
| **Art 9** | Risk-management system | `govern-manifest.json`; scorecard pillars 02/07 | govern report present & policy artefacts committed | `threadlight-govern` |
| **Art 11 + Annex IV** | Technical documentation + AI-BOM | scorecard manifest; `mcp-sbom.json` | scorecard manifest present **and** AI-BOM present | `threadlight-production-ready` |
| **Art 12** | Record-keeping / logging | pillar 05 (observability); `agent-identity.json` | pillar 05 pass **and** identity subjects traceable | `foundry-observability` |
| **Art 14** | Human oversight | pillar 08 (HITL & audit) | HITL gates declared + wired | `threadlight-hitl-patterns` |
| **Art 15** | Accuracy · robustness · cybersecurity | `evals-manifest.json`; `redteam-manifest.json`; `mcp-sbom.json` | evals **and** red-team present & fresh; supply-chain clean | `threadlight-evals`, `threadlight-redteam` |
| **Art 26** | Deployer obligations | `agent-identity.json` | every subject has a named human owner | `foundry-agt` |
| **Art 27** | FRIA | — | always `scaffold` (human completes) | fill `fria-scaffold.md` |

`partial` = some but not all sources present (e.g. Art 15 has evals but no red-team).

## 5. Data model — `ai-act-evidence.json`

```json
{
  "schema": "threadlight.ai-act-evidence/v1",
  "generator": "threadlight-production-ready/ai_act_evidence",
  "generator_version": "0.8.0",
  "generated_at": "2026-07-07T00:00:00Z",
  "tenant_local": true,
  "disclaimer": "Evidence assembled from committed artifacts. Not legal advice or a conformity assessment; a human compliance owner must review, complete, and sign.",
  "system": { "name": "<from spec>", "spec_sha256": "<hash|null>" },
  "articles": [
    {
      "id": "art-11-annex-iv",
      "title": "Technical documentation (Annex IV)",
      "obligation": "Maintain up-to-date technical documentation …",
      "coverage": "covered",
      "sources": [
        { "artifact": "tests/production-readiness-manifest.json", "sha256": "…", "present": true },
        { "artifact": "mcp-sbom.json", "sha256": "…", "present": true }
      ],
      "detail": "Scorecard manifest + MCP AI-BOM present.",
      "remediation": ["threadlight-production-ready"]
    }
  ],
  "summary": { "articles_total": 7, "covered": 4, "partial": 1, "gap": 1, "scaffold": 1, "not_applicable": 0 }
}
```

`generated_at` is injectable (`now`) so tests pin it; every source carries a
`sha256` + `present` flag so the pack is **provenance-traceable** and diffable.

## 6. Error handling

- Any missing/unreadable source → that article degrades to `gap`/`partial` with a
  remediation pointer; the aggregator **never raises** on absent evidence.
- Malformed JSON in a source → treated as absent + noted in `detail`.
- **No network, no tenant calls.** Determinism guaranteed for a fixed input tree
  (+ injected `now`).
- Exit `0` on successful emit; `--check` exit `3` only when a must-have article is a
  `gap`; exit `2` only on unusable `--root` / `--out`.

## 7. Testing

Pure-stdlib, house style (`spec_from_file_location`, bare `test_` + `assert`,
`_repo(**files)` helper). ~20–25 tests:

- **Article mapping:** each article resolves to `covered`/`partial`/`gap` for the
  right artifact-presence combinations (e.g. Art 15 = `partial` with evals but no
  red-team; `gap` with neither).
- **Honesty:** a repo with only the scorecard → most articles `gap`, **zero** false
  `covered`; disclaimer always present.
- **Determinism:** same input tree + fixed `now` → byte-identical manifest.
- **Tenant-local:** no socket / no network (pure functions; assert offline).
- **Provenance:** every `covered`/`partial` source carries a non-empty `sha256`.
- **Renderers:** `annex-iv-technical-file.md` emits every Annex IV section and a GAP
  block for each absent source; `fria-scaffold.md` emits the Art 27 template.
- **Integration:** full-artifact fixture → expected coverage summary; sparse fixture
  → still emits all three files, mostly gaps.
- **CLI:** `--check` exits `3` on a must-have gap, `0` otherwise.
- **Version:** asserts `0.8.0`.

## 8. Files touched & versioning

**New:**
- `skills/threadlight-production-ready/scripts/ai_act_evidence.py`
- `skills/threadlight-production-ready/tests/test_ai_act_evidence.py`
- `skills/threadlight-production-ready/references/eu-ai-act-mapping.md` (article→artifact map, human-readable)

**Edited:**
- `skills/threadlight-production-ready/SKILL.md` (v0.8.0 subsection + changelog table)
- `skills/threadlight-production-ready/tests/test_version.py` (0.8.0)
- `plugin.json` + `.github/plugin/marketplace.json` (1.9.0 + keywords: eu-ai-act, annex-iv, compliance-evidence)
- `CHANGELOG.md` (root, [Unreleased] Added)

**No** new pillar doc, **no** new recipes (C emits no scorecard findings).

## 9. Future work

- Live enrichment: Azure Monitor / App Insights log stats + Purview lineage pulls.
- Art 12 **immutable** log-bundle export + notarization / signing of the pack.
- `cicd` wiring: evidence-pack freshness as an optional merge gate.
- Art 6 / Annex III **risk-classification wizard** (is this system high-risk at all?).
- Portfolio rollup: one pack across many agents/subscriptions.
