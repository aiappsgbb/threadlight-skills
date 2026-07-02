# MSA agent-quality learnings → threadlight-design — design

**Date:** 2026-07-02
**Status:** Design — awaiting user review before implementation
**Scope:** Small, additive change to `threadlight-design` generation contract only. No new phases, no Fast-PoC change, no `orchestrator.py` / `threadlight-auto` / pipeline edits. ~4 bullet insertions across 2 files.

## Goal

A review of `hatasaki/Multi-Specialized-Agents` (MSA) — a network of five automotive **specialist** prompt agents, each grounded in its own knowledge base, composed by a routing + synthesis orchestrator — raised one question:

> Does MSA's *agentic design philosophy* suggest ways to improve the **quality of the agents & skills that `threadlight-design` (and `threadlight-deploy`) generate for customers** — setting aside MSA's tech stack (Durable Functions / Foundry hosting / MAF) and setting aside `threadlight-auto` (a convenience shortcut, not a mandatory orchestrator)?

**Answer:** Yes, modestly. threadlight already shares MSA's cleanest principle — *skills are capabilities; the agent orchestrates; skills never coordinate other skills* (`threadlight-design/SKILL.md` 679–681). MSA sharpens the **generated units** in two high-value, low-risk ways worth doing now (S3, S4); deeper ideas are recorded and deferred.

## Findings — MSA philosophy vs. what threadlight already generates

| # | MSA design principle | Already in threadlight? | Verdict |
|---|----------------------|--------------------------|---------|
| S1 | Decompose by **domain expertise**, not process-step / actor | We decompose by actor/step — Skill Derivation Recipe (`SKILL.md` Step 5, 674–677) | **Defer** — conditional 2nd lens, only for `agent`-model advisory agents |
| S2 | **Per-specialist bounded grounding** (each unit owns its corpus) | Default is **one** Foundry IQ index per process (`SKILL.md` 690–698) | **Defer** — changes knowledge-source topology; heavier |
| **S3** | **Explicit synthesis / reconciliation** when units compose an answer | Synthesis is a *detected trait* only (`process-traits.md` 43); no reconciliation contract is generated | **DO NOW** |
| **S4** | **Honesty about knowledge boundaries**: ground → if absent, say so + label general knowledge → cite | *Citation* exists (`speckit-template.md` §7 line 203 + `policy_citation_rate` KPI 335). **Labeled-degradation / "say so if not found" is absent** anywhere in the generated-agent contract. | **DO NOW** |
| S5 | **Provenance-transparent** composed output (which unit produced what) | Partly present — speckit audit trail + `answer.citations[]` (`speckit-template.md` 339, 394) | **Defer** — surfacing per-skill contribution is output-format work |
| — | **Independently-evaluable specialists** (per-domain eval sets) | `threadlight-deploy` backfills one `evals/` set | **Defer** — touches eval generation |

**Rejected outright:** MSA's multi-*agent* topology and its tech stack. "One agent, many skills" is simpler, cheaper, and already captures the good part. We borrow the *treat-each-skill-as-a-grounded, honest, self-describing specialist* mindset **inside one agent** — not the deployment model.

## Non-goals

- **No** multi-agent topology; **no** MSA tech stack (Durable Functions / Foundry hosting / MAF).
- **No** edits to `orchestrator.py`, `threadlight-auto`, or the pipeline sequence.
- **No** new interview questions and **no** new required SPEC field (citation already exists; degradation is a behavioral *output*, not user input).
- We do **not** edit the *code* inside generated customer projects — we change what the generator *instructs* the produced agent to do.

## Design

### Insertion point (why here)

`references/skill-template.md` and `references/agents-template.md` **do not exist in this repo** — they are marked "📎 upstream reference set" (`SKILL.md` 1966–67). The **authoritative in-repo generation contract** is therefore the inline **"MUST have" / "Must include" lists in `threadlight-design/SKILL.md` Step 6 (746–767)**. That is the single lightest place to add these requirements; every generated agent already flows through it.

### Change A — S4: grounding-honesty behavioral default (universal, ~3 lines)

**A1.** `threadlight-design/SKILL.md` Step 6 §2 (AGENTS.md "Must include", at **"Behavioral guidelines"**, line 766) — require the generated Behavioral guidelines to contain a grounding-honesty clause. Proposed generated-instruction text:

> **Grounding & honesty.** Ground substantive answers in the agent's knowledge source (SPEC §7). If the knowledge source does not contain the answer, say so explicitly; answer from general knowledge only when clearly labeled as such. Cite sources per SPEC §7 Citation requirement.

**A2.** `threadlight-design/SKILL.md` Step 6 §1 (each skill "MUST have", 748–753) — add one bullet:

> - **Grounding & honesty contract** (knowledge-backed skills): name the grounding source and the labeled-degradation fallback — say so when the source lacks the answer; label any general-knowledge answer.

*Citation is already enforced by `speckit-template.md` §7 + the `policy_citation_rate` KPI — this change adds only the "say-so + label general knowledge" behavior that is currently missing.*

### Change B — S3: cross-skill synthesis / reconciliation clause (conditional, ~3 lines)

**B1.** `threadlight-design/SKILL.md` Step 6 §2 (Behavioral guidelines) — **conditional** requirement, emitted only when the generated agent **composes answers from more than one skill** — heuristically, ≥2 knowledge/analysis/synthesis skills, **or** the Synthesis trait was detected in discovery. Proposed generated-instruction text:

> **Cross-skill synthesis.** When more than one skill contributes to a single answer, reconcile rather than concatenate: preserve technical detail, resolve disagreements explicitly, and surface cross-domain trade-offs.

**B2.** `references/process-traits.md` Synthesis row (line 43) — add a one-line pointer to the reconciliation contract in the "Discovery Questions" cell, so trait detection carries the guidance to Step 6.

### Footprint & versioning

~4 additive bullet insertions across **2 files** (`SKILL.md`, `process-traits.md`). Implementation also bumps `threadlight-design` `metadata.version` 1.8.0 → 1.8.1 (courtesy; no schema change).

## Efficiency safeguards (hard constraint)

- **Additive only** — no restructuring of Step 6, no new Phase/Step.
- **S3 is conditional** → single-skill PoCs are untouched (no bloat).
- **Fast-PoC path unaffected** — these are generation-*output* requirements, not new interview questions.
- **Zero** edits to `orchestrator.py`, `threadlight-auto`, or pipeline order.
- Net added generated-instruction text per agent: ~2–4 lines (negligible tokens).

## Deferred — next-wave backlog (tracked)

Explicitly carried to a **next wave**; not implemented in this change:

- **S1 — expertise-decomposition lens.** Offer a domain-expertise decomposition option alongside the actor/step Skill Derivation Recipe (Step 5, `SKILL.md` 674–677), conditional for `agent`-model advisory agents.
- **S2 — per-skill bounded grounding.** Let a skill own a scoped corpus instead of the default single Foundry IQ index per process (`SKILL.md` 690–698); changes knowledge-source topology.
- **S5 — provenance-transparent composed output.** Surface which skill produced which part of a composed answer (builds on the speckit audit trail + `answer.citations[]`).
- **deploy-eval — per-domain eval sets.** Have `threadlight-deploy` backfill independently-evaluable eval sets per skill/domain rather than one shared `evals/`.

Each is a coherent follow-up but exceeds the "highest-value, efficiency-safe" bar for this change and would touch heavier surfaces (Step 5 recipe, knowledge-source topology, output format, eval generation). Revisit as a batch after S3/S4 land.

## Verification

When implemented (separate step, after this design is approved):
- `grep -n "Grounding & honesty" skills/threadlight-design/SKILL.md` → present in Step 6 §1 and §2.
- `grep -n "Cross-skill synthesis" skills/threadlight-design/SKILL.md` → present, conditional wording.
- `process-traits.md` Synthesis row references the reconciliation contract.
- `metadata.version` bumped to 1.8.1.
- Sanity: generate (or dry-read) an AGENTS.md for a single-skill PoC and for a multi-skill answer-composing agent — the synthesis clause appears only for the latter; the honesty clause appears for both.

## Rollback

Purely additive text in two skill-authoring files. Revert the two edits and the version bump; no runtime, CI, or generated-project behavior depends on prior state.
