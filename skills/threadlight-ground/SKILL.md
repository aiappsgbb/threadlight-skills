---
name: threadlight-ground
description: >-
  GROUND leg: assesses already-produced ACL/citation/refusal probe evidence
  into a threadlight.ground/v1 manifest (GRD-001 ACL, GRD-002 citation,
  GRD-003 refusal, GRD-004 freshness/coverage/baseline). The SPEC-derived
  knowledge_sources list is the authoritative inventory: every source's
  enabled control (ACL when permission_model=acl, citations when
  citation_required, refusal when refuse_when_unsupported) must be covered by
  evidence carrying that source_id, never inferred across sources. Missing
  source/control evidence, ambiguous expected_entitled, or missing runs ->
  not-verified, never guessed; a proven leak (an unentitled principal given
  any document outside its explicit allowed_document_ids, subsets included) ->
  must-fix. Explicit expected_entitled is required - no naive name heuristic.
  An executed must-fix/should-fix with complete coverage is still complete
  evidence; status is partial only when evidence is missing/not-verified.
  Malformed evidence shapes raise GroundEvidenceError before any output.
  Persists only source metadata, principal/document IDs, allowlisted finding
  detail, telemetry, and a retrieval-quality baseline reference - never
  content, prompts, completions, tokens/credentials, or customer payloads.
  Manual handoff - threadlight-auto never runs probes. USE FOR: grounding
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
| `GRD-001` | ACL enforcement | no `permission_model: acl` source declared → trivial `pass`; an ACL source has **no runs carrying its `source_id`** (uncovered); fewer than two distinct principals; no explicit `expected_entitled` on a run (ambiguous); no entitled *and* unentitled probe for a source; a source's declared `acl_probe_principals` are not all probed | an unentitled principal received **any** document outside its explicit `allowed_document_ids` — a subset is enough, and no allowlist means nothing is allowed |
| `GRD-002` | Citation grounding | a `citation_required` source has no citation runs carrying its `source_id` (uncovered) | any citation falls outside its run's retrieved set (`missing_from_retrieval`, sorted, de-duplicated) |
| `GRD-003` | Refusal behavior | a `refuse_when_unsupported` source has no refusal runs carrying its `source_id` (uncovered) | an executed probe for an unsupported query was **answered** instead of refused |
| `GRD-004` | Source freshness / coverage / baseline | no sources declared → trivial `pass`; a declared source has no covering run; a covered source's runs carry no valid RFC3339 `captured_at` (freshness unverifiable); no `retrieval_quality_baseline` was supplied | never `must-fix` — caps at `should-fix` (a covered source's oldest evidence is stale relative to its own `refresh_cadence`) |

`GRD-001..003` can reach `pass`, `must-fix`, or `not-verified`. `GRD-004` can
reach `pass`, `should-fix`, or `not-verified` — freshness/coverage gaps are
never escalated past `should-fix`, reserving `must-fix` for a *proven*
control failure. The manifest `status` is `partial` exactly when required
evidence is genuinely missing: a `not-verified` finding, or an uncovered /
freshness-unverifiable source hidden behind an aggregated must-fix. An
**executed** `must-fix`/`should-fix` with **complete coverage** is still
complete evidence and never downgrades `status` on its own.

> **Malformed shapes raise, they are never `not-verified`.** A missing/empty
> `source_id`, a `source_id` not in the declared inventory, a missing
> `document_ids`/`citations`/`retrieved_ids`/`query_id`, a non-string element,
> a non-boolean `expected_entitled`/`refused`, or a negative/non-finite
> telemetry value all raise `GroundEvidenceError` **before** any manifest is
> built, returned, or written. `not-verified` is reserved for genuinely
> *absent* evidence (an uncovered source, an ambiguous entitlement), never a
> *malformed* run.

## Source inventory is authoritative — coverage is never inferred

The `sources` argument **is** the SPEC-derived `knowledge_sources[]` input
contract (see `threadlight-design/references/speckit-template.md`), not
anything the CLI's `--project-root` supplies. Every declared source's
*enabled* control must be covered by evidence that carries **that source's
`source_id`**: ACL evidence when `permission_model == "acl"`, citation
evidence when `citation_required`, refusal evidence when
`refuse_when_unsupported`. Coverage for one source is never inferred from
another source's runs. A source that declares `acl_probe_principals` must
have every one of those principals probed.

## Explicit `expected_entitled` is required — no name heuristic

`assess_acl` classifies each ACL run only by an **explicit**
`expected_entitled` (or `entitled`) boolean on the run. There is no name
heuristic: a run without an explicit signal is *ambiguous* and yields
`not-verified`, never a guessed pass or fail. A source's ACL runs must
include at least one entitled (`true`) and one unentitled (`false`) probe.
An unentitled principal's `allowed_document_ids` is its allowlist of
legitimately-visible (e.g. public) documents; receiving anything outside it —
including a subset — is a `must-fix` leak, and **no allowlist means nothing
is allowed**.

## Evidence shapes

```jsonc
// acl_runs[] — one per principal probed against a source. source_id and an
// explicit expected_entitled are REQUIRED; allowed_document_ids is the
// unentitled principal's allowlist (public docs it may legitimately receive).
{"principal": "entitled-analyst", "document_ids": ["doc-1", "doc-2"], "source_id": "policy-library",
 "expected_entitled": true, "captured_at": "2026-08-17T09:00:00+00:00"}
{"principal": "unentitled-guest", "document_ids": ["public-1"], "source_id": "policy-library",
 "expected_entitled": false, "allowed_document_ids": ["public-1"], "captured_at": "2026-08-17T09:00:00+00:00"}

// citation_runs[] — one per answer's citations vs. what was actually retrieved
{"citations": ["doc-1"], "retrieved_ids": ["doc-1", "doc-2"], "source_id": "policy-library",
 "captured_at": "2026-08-17T09:00:00+00:00"}

// refusal_runs[] — one per unsupported-query probe
{"query_id": "q-out-of-scope-1", "refused": true, "source_id": "policy-library",
 "captured_at": "2026-08-17T09:00:00+00:00"}
```

`assess_grounding(*, sources, acl_runs, citation_runs, refusal_runs,
generated_at, retrieval_quality_baseline)` sanitizes `sources` (the SPEC
`knowledge_sources` declaration — see below), computes all four findings,
schema-validates the built manifest (so `--json` can never emit invalid or
oversharing data), and returns the full `threadlight.ground/v1` manifest.
`retrieval_quality_baseline` is a **reference** to the baseline artifact
(`threadlight-evals`) — a nonempty repo-relative path or id, never its
content, and never an absolute path, `..` traversal, or URL. A `None`
baseline is allowed but makes `GRD-004` `not-verified`.
`validate_citations(citations, retrieved_ids)`, `oldest_timestamp(runs)`, and
`aggregate_telemetry(runs)` are also exposed standalone for direct use.

## Persistence contract — never the raw evidence

The manifest persists **only**: sanitized source metadata (the six
`knowledge_sources` fields plus optional `acl_probe_principals`), principal
identifiers, document IDs, the four findings (whose `detail` is an
**allowlisted schema** of IDs, counts, status maps, and a controlled `reason`
enum — never a free-form note), aggregate telemetry (`retrieval_count`,
summed `subqueries`, summed `tokens`), and the retrieval-quality baseline
*reference*. It **never** persists retrieved document content, raw
query/prompt text, model completions, access tokens, credentials, or
customer payloads — `_summarize_*_runs` rebuilds curated evidence dicts from
scratch rather than passing raw runs through, and every write is scanned
recursively for credential/content/prompt-shaped **keys** (matched as whole
snake-case words, so a legitimate `tokens` **count** is never confused with a
credential-shaped `access_token`) **and** secret-shaped **values** (a
smuggled key/token in any ID field) before anything touches disk.
`aggregate_telemetry` sums only genuinely numeric, non-negative, finite
`subqueries`/`tokens` — a boolean, string, negative, or non-finite value
raises rather than being coerced.

## CLI

```bash
python3 skills/threadlight-ground/scripts/ground.py \
  --project-root . --evidence-file specs/ground-evidence.json --emit --gate
```

The `--evidence-file` is the SPEC-derived evidence bundle (`sources`,
`acl_runs`, `citation_runs`, `refusal_runs`, `retrieval_quality_baseline`,
optional `generated_at`) — the **`sources` inventory comes from that object,
not from `--project-root`**. `--project-root` is only the output/project
boundary: a `--manifest-path` that escapes it (an absolute path outside it or
a `..` traversal) is rejected and nothing is written. `GroundEvidenceError`,
`ManifestValidationError`, and read/write `OSError`/JSON errors are all caught
cleanly (exit 1) so a failed run never corrupts a prior valid manifest.

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
