---
name: threadlight-ground
description: >-
  GROUND leg: assesses already-produced ACL/citation/refusal probe evidence
  into a threadlight.ground/v1 manifest (GRD-001 ACL, GRD-002 citation,
  GRD-003 refusal, GRD-004 freshness/coverage). Missing
  principals/permissions/runs -> not-verified, never guessed; a proven leak
  (incompatible principals given the identical protected doc set) ->
  must-fix, with explicit expected_entitled avoiding naive false positives.
  An executed must-fix/should-fix is still complete evidence; status is
  partial only when evidence is missing/not-verified. Persists only source
  metadata, principal/document IDs, findings, telemetry - never content,
  prompts, completions, tokens/credentials, or customer payloads. Manual
  handoff - threadlight-auto never runs probes. USE FOR: grounding
  evidence, ACL leak detection, citation validation, refusal checks,
  ground-manifest, knowledge_sources gate. DO NOT USE FOR: Foundry IQ
  retrieval, quality scoring (threadlight-evals), live probes, cross-leg
  readiness gating (threadlight-production-ready).
metadata:
  version: "0.1.0"
---

# Threadlight Ground — prove the retrieval is safe, not just plausible

The **GROUND** leg: the step that turns "the agent cited something" into "the
citation is provably grounded, access-controlled, and honest about what it
doesn't know." `threadlight-ground` is a **coordinator**, not a retrieval or
evaluation engine — it never calls Foundry IQ, never issues a retrieval
query, and never runs an evaluator. It only ingests probe *results* an
operator already captured (an ACL probe run, a citation validation run, a
refusal probe run) and turns them into four findings.

> **Why "coordinator."** Provisioning and query planning stay with
> `foundry-iq` (Azure AI Search Knowledge Agent); quality/relevance scoring
> stays with `threadlight-evals`. This leg's job is narrower and stricter:
> given evidence someone already gathered, does it *prove* the knowledge
> source is safe to ground answers on? This is a **manual, live handoff** —
> `threadlight-auto` does not run live ACL/citation/refusal probes against a
> real agent for you. Running those probes against production or pilot data
> is an operator decision; this script only assesses evidence the operator
> already captured and supplies.

## What this skill does and does not do

- **Does:** sanitize a SPEC `knowledge_sources` declaration (whitelist-only —
  see `references/ground-manifest.schema.json`), assess ACL enforcement,
  citation grounding, and refusal behavior from caller-supplied probe runs,
  compute source freshness/coverage, and emit a schema-validated,
  atomically-written `specs/ground-manifest.json`.
- **Does NOT** call Foundry IQ, run a retrieval query, or invoke an
  evaluator. Every finding here is computed from evidence the caller already
  captured — a live probe is the operator's responsibility, not this
  script's.
- **Does NOT** score answer quality/relevance (`threadlight-evals`) or
  aggregate findings across every leg into a single go/no-go
  (`threadlight-production-ready` consumes GRD-001..004 for that).

## The four findings

| ID | Checks | `not-verified` when | `must-fix` when |
|---|---|---|---|
| `GRD-001` | ACL enforcement | no ACL-protected source declared → trivial `pass`; ACL-protected but no ACL runs supplied; a run is missing its `principal`/`document_ids`; fewer than two distinct principals; entitlement cannot be determined for a run (ambiguous name, no `expected_entitled`) | an entitled and an unentitled principal probe returned the **identical** protected document set |
| `GRD-002` | Citation grounding | no citation validation runs supplied; a run is missing `citations`/`retrieved_ids` | any citation falls outside its run's retrieved set (`missing_from_retrieval`, sorted, de-duplicated) |
| `GRD-003` | Refusal behavior | no refusal probe runs supplied; a run is missing a boolean `refused` result | an executed probe for an unsupported query was **answered** instead of refused |
| `GRD-004` | Source freshness/coverage | no sources declared → trivial `pass`; sources declared but zero evidence at all | never `must-fix` — caps at `should-fix` (a declared source is uncovered, or its oldest evidence is stale relative to its `refresh_cadence`) |

`GRD-001..003` can reach `pass`, `must-fix`, or `not-verified`. `GRD-004` can
reach `pass`, `should-fix`, or `not-verified` — freshness/coverage gaps are
never escalated past `should-fix`, reserving `must-fix` for a *proven*
control failure. The manifest `status` is `partial` exactly when any finding
is `not-verified` — an **executed** `must-fix`/`should-fix` is still complete
evidence and never downgrades `status` on its own.

## Avoiding naive false positives — `expected_entitled`

`assess_acl` classifies each ACL run's principal as entitled/unentitled in
this order: (1) an explicit `expected_entitled` (or `entitled`) boolean on
the run — always authoritative when present; (2) a best-effort name
heuristic (`entitled`/`unentitled`, `admin`/`guest`, etc.) that exists only
so obviously-named fixtures work without ceremony. An ambiguous name that
matches neither heuristic returns *unknown*, which yields `not-verified` —
never a guessed pass or fail. **Supply `expected_entitled` explicitly for
real evidence**; the name heuristic is a demo convenience, not a substitute
for a real permission check.

## Evidence shapes

```jsonc
// acl_runs[] — one per principal probed against a source
{"principal": "entitled-analyst", "document_ids": ["doc-1", "doc-2"], "source_id": "policy-library",
 "expected_entitled": true, "captured_at": "2026-08-17T09:00:00+00:00"}

// citation_runs[] — one per answer's citations vs. what was actually retrieved
{"citations": ["doc-1"], "retrieved_ids": ["doc-1", "doc-2"], "source_id": "policy-library"}

// refusal_runs[] — one per unsupported-query probe
{"query_id": "q-out-of-scope-1", "refused": true, "source_id": "policy-library"}
```

`assess_grounding(*, sources, acl_runs, citation_runs, refusal_runs,
generated_at)` sanitizes `sources` (the SPEC `knowledge_sources` declaration
— see below), computes all four findings, and returns the full
`threadlight.ground/v1` manifest. `validate_citations(citations,
retrieved_ids)` and `oldest_timestamp(runs)` /
`aggregate_telemetry(runs)` are also exposed standalone for direct use.

## Persistence contract — never the raw evidence

The manifest persists **only**: sanitized source metadata (the six
`knowledge_sources` fields), principal identifiers, document IDs, the four
findings, and aggregate telemetry (`retrieval_count`, summed `subqueries`,
summed `tokens`). It **never** persists retrieved document content, raw
query/prompt text, model completions, access tokens, credentials, or
customer payloads — `_summarize_*_runs` rebuilds curated evidence dicts from
scratch rather than passing raw runs through, and every write is scanned
recursively for credential/content/prompt-shaped keys (matched as whole
snake-case words, so a legitimate `tokens` **count** is never confused with
a credential-shaped `access_token`) before anything touches disk.
`aggregate_telemetry` sums only genuinely numeric `subqueries`/`tokens` —
a boolean, string, or non-finite value raises rather than being coerced.

## Files

```
scripts/ground.py                            # stdlib implementation + CLI
references/ground-manifest.schema.json       # ground-manifest.json shape (shared envelope)
tests/test_ground.py                         # pytest suite
```

## Tests

```bash
python3 -m pytest skills/threadlight-ground/tests/ -v
```

## See also

- **`foundry-iq`** — owns Foundry IQ (Azure AI Search Knowledge Agent)
  provisioning and the actual retrieval/query planning this leg's evidence
  comes from.
- **`threadlight-evals`** — owns answer-quality/relevance scoring; a
  separate concern from the ACL/citation/refusal proofs here.
- **`threadlight-production-ready`** — consumes `GRD-001..004` from
  `specs/ground-manifest.json` alongside every other leg's findings for a
  single pilot go/no-go.
