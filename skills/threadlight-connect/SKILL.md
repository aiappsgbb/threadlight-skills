---
name: threadlight-connect
description: >-
  CONNECT-leg: evidence-based swap of a scaffolded mock Foundry tool for a
  real endpoint. Extracts the contract a tool source actually reads,
  generates conformance tests, checks conformance vs a captured real
  sample, and gates mock -> real on conformance AND OBO user-scoped
  evidence AND required-role revalidation vs current identity. States:
  mock, real-unverified, real-verified, real-drift. Config writes (SPEC.md,
  mcp-config.json) require --apply plus a validated --real-endpoint, only
  once verified; dry run yields a nonempty apply plan, zero writes. Manual handoff — threadlight-auto does
  not run it. USE FOR: mock-to-real swap, contract extraction, conformance
  tests, integration_state, real-drift, OBO evidence gate, role
  revalidation, connect-manifest, publish/republish. DO NOT USE FOR:
  customer field mapping; OAuth on-behalf-of exchange itself (owned by
  `entra-agent-id`; scaffolds around its evidence); role assignment
  (`azure-rbac`); red-team scanning (threadlight-redteam); evals
  (threadlight-evals); AGT governance (threadlight-govern).
metadata:
  version: "0.1.0"
---

# Threadlight Connect — swap the mock, prove the real

The **CONNECT** leg: the step between "a pilot works against mocked tools"
and "a pilot is safe to point at a real endpoint." `threadlight-design` /
`threadlight-demo-data-factory` scaffold pilots against **mocked** Foundry
tools (a JSON sample file standing in for a real backend). This leg is the
one place in the pipeline that turns a mock into a real integration — and it
never does so on trust. It is a **manual handoff**: `threadlight-auto` does
not run this leg for you, because swapping to a real endpoint is an
operator decision that requires operator-supplied evidence.

> **Why "evidence-based."** An endpoint that *responds* is not the same as an
> endpoint that is *safe to depend on*. This leg only ever calls a swap
> `real-verified` after three independent, machine-checked facts hold at
> once: (1) the real response **conforms** to the contract the tool source
> code actually reads, (2) OBO (on-behalf-of) evidence shows the call was
> **user-scoped**, and (3) the **required roles** have been revalidated
> against the **current** agent identity — not a stale grant from a previous
> publish. No endpoint call is ever verified without evidence: this script
> never calls the real endpoint itself; the caller supplies the captured
> real response and evidence (e.g. from a manual test call made through
> `entra-agent-id`, the catalog skill that owns the actual OBO/OAuth token
> exchange).

## What this skill does and does not do

- **Does:** extract a data contract, generate conformance tests, check
  conformance, gate the mock → real state transition on conformance + OBO +
  role evidence, and — only with `--apply` and only once fully verified —
  transactionally update `specs/SPEC.md`, the effective MCP config
  (`infra/mcp-config.json` or the caller's actual equivalent) — pointing
  `servers/<tool>.url` at the validated `--real-endpoint` — and the
  `connect-manifest.json` together, rolling back a partially-applied write so
  the three never diverge. The real endpoint URL is persisted **only** in
  mcp-config.json; the manifest records just safe `endpoint_configured` /
  `endpoint_verified` booleans, never the URL.
- **Does NOT** map or rename customer-specific fields. Field **mapping**
  (e.g. `cust_id` → `customer_id`) is explicitly out of scope — this leg
  proves *conformance* between a mock contract and a real response; it does
  not transform payloads. Field mapping is a downstream, customer-specific
  concern left to the operator.
- **Does NOT** perform the OBO/OAuth token exchange itself. OBO handling here
  is scaffolding only: it validates the *shape* of OBO evidence the caller
  supplies and folds it into the state machine. The actual Entra Agent
  Identity / on-behalf-of implementation is owned by the upstream
  `entra-agent-id` catalog skill (see "See also" below) — this leg composes
  with it, it does not replace it.
- **Does NOT** call the real endpoint. No network call is made by
  `scripts/connect.py`. Every phase operates on evidence the caller already
  captured (tool source text, a mock sample, a captured real response, OBO
  evidence, role evidence).

## The state machine

Exactly four states — `mock`, `real-unverified`, `real-verified`,
`real-drift`:

| `target_state` | When |
|---|---|
| `real-drift` | Field-level conformance failed (missing required field, or a type mismatch) against a captured real sample that **had records to check**. |
| `real-unverified` | Conformance could not be verified — either the real response had **no items** (empty/missing `items`: insufficient evidence, never a vacuous pass), or conformance passed but OBO evidence is missing/not user-scoped, or required roles are not revalidated against the **current** agent identity. The latter includes the cases where **no `--current-agent-identity` was supplied at all** and where the revalidation names a **stale/mismatched** identity. |
| `real-verified` | Conformance passed against a **non-empty** real sample **and** OBO evidence is present and user-scoped **and** required roles are revalidated against a **supplied** current agent identity that the evidence names exactly. |

Required-role revalidation is **never opt-in**: a real apply requires
`--current-agent-identity`, and the role evidence must record exactly that
identity. Omitting the current identity, or supplying one the evidence does
not match, holds the swap at `real-unverified` and edits nothing.

`integration_state` is the **persisted** current state (read from a prior
`connect-manifest.json`; defaults to `mock`). It only ever advances on a
**successful** `--apply` — a failed or unverified transition never edits it,
and never edits `SPEC.md` / the MCP config. Publishing or **republishing**
always re-runs required-role revalidation against the current agent
identity before a swap can be called `real-verified` again — a role grant
recorded for a previous identity does not carry over.

## Phases

```
inspect          read the tool source + the mock sample
contract         extract_contract(): fields the source actually READS —
                 never sample keys that are merely present but unread
generate-tests   write an executable, dependency-free conformance test
                 module into the generated project (written every run)
verify           check_conformance(): field-level diff against a captured
                 real sample — {field, expected, actual, path} per diff;
                 an empty/missing real response is unevaluated, not a pass;
                 a non-object row is itself a difference at $.items[i]
plan             build_apply_plan(): file-by-file plan, always computed,
                 read-only, no writes — even in a dry run
apply            only with --apply AND target_state == real-verified AND a
                 validated --real-endpoint: builds and fully validates the
                 final manifest FIRST, points servers/<tool>.url at the real
                 endpoint, then transactionally updates specs/SPEC.md + the MCP
                 config + connect-manifest.json together (an in-process failure
                 after any write — or a failed post-apply endpoint re-read —
                 rolls every applied file back to its prior bytes/mode) and
                 records only the production changed paths
emit             on the non-apply / unverified path, write
                 specs/connect-manifest.json — shared envelope,
                 schema-validated, atomic, no credentials/tokens/customer
                 payloads (on a verified apply the manifest is committed
                 inside the transaction above instead)
```

## Usage

```bash
# Dry run: always safe. Computes target_state + a nonempty apply plan;
# never touches SPEC.md / mcp-config.json. --evidence-captured-at is optional:
# it records WHEN the real evidence was captured as freshness.source_oldest_at
# (omit it when unknown — it is then recorded as null, never faked).
python3 scripts/connect.py \
  --project-root ../my-pilot \
  --tool-name returns_get_case \
  --tool-source-file tool_source.py \
  --sample-file mock_sample.json \
  --real-response-file real_response.json \
  --obo-evidence-file obo_evidence.json \
  --role-evidence-file role_evidence.json \
  --evidence-captured-at 2026-08-10T09:00:00+00:00

# Publish: config changes require --apply, and only take effect once fully
# verified (conformance + OBO + role revalidation all pass). --real-endpoint is
# REQUIRED to apply — it is the real MCP URL that servers/<tool>.url is pointed
# at (persisted only in mcp-config.json, never in the connect manifest).
python3 scripts/connect.py \
  --project-root ../my-pilot \
  --tool-name returns_get_case \
  --tool-source-file tool_source.py \
  --sample-file mock_sample.json \
  --real-response-file real_response.json \
  --obo-evidence-file obo_evidence.json \
  --role-evidence-file role_evidence.json \
  --current-agent-identity agent-123 \
  --real-endpoint https://api.example.com/mcp \
  --apply
```

Evidence file shapes:

```jsonc
// obo_evidence.json
{"present": true, "user_scoped": true}

// role_evidence.json
{
  "revalidated": true,
  "required_roles": ["Case.Read"],
  "validated_roles": ["Case.Read", "Case.Write"],
  "agent_identity": "agent-123"
}
```

Evidence that is honestly absent or `false` is a normal `real-unverified`
finding. Evidence that is the **wrong shape** raises before anything is
written, so a malformed-evidence run never disturbs whatever valid
manifest/SPEC/mcp-config already existed. This is strict, with no silent
coercion: `required_roles` / `validated_roles` (alias `granted_roles`) must
each be a list of **non-empty, unique strings** (never `or []`-coerced from a
falsey scalar such as `""`, `0`, `{}`, or `false`); `agent_identity` must be a
non-empty string or null; and `present` / `user_scoped` / `revalidated` must be
actual booleans (`user_scoped: "true"` as a string is rejected). Note
`agent_identity` must equal the `--current-agent-identity` you pass, or the
swap stays `real-unverified`.

## The real endpoint — validated, bound, never leaked

Publishing a swap requires the real endpoint the runtime will actually call,
supplied via `--real-endpoint`. It is **optional for a dry run** (evidence
assessment only) but **mandatory to `--apply`** a verified swap: applying
without it is a controlled error with nothing written, so `INT-002` can never
pass on an integration that was never actually bound.

Before any write the endpoint is validated (a bad one raises with nothing
written): it must be a non-empty `https` URL — `http` is allowed **only** for
`localhost` / `127.0.0.1` — with a hostname, no embedded credentials
(`user:pass@host`), no URL fragment, no credential/SAS/token query parameter,
no whitespace/control characters, and it must **not** be the scaffolded mock
(the same conservative delimited-`mock` marker `threadlight-safe-check` uses —
so `mock` / `mocked` / `mockserver` are rejected while a real host that merely
contains the substring, like `mockingbird`, is fine).

On apply, `servers/<tool>.url` in the MCP config is (re)pointed at the
validated endpoint, preserving unrelated servers, unrelated top-level config,
and the tool entry's own safe unrelated fields (`type`, `headers`, …) while
dropping mock/stdio transport fields mutually exclusive with a real HTTPS
binding. A malformed existing `servers` / tool entry fails **closed** (raises,
nothing written) rather than being silently overwritten. The predicted binding
is checked before the transaction, and after the transaction the MCP config is
re-read and its effective endpoint asserted to still equal the validated real
endpoint — a mismatch rolls all three files back (or, if rollback also fails,
raises the inconsistent-state error) so success is never reported on a
divergent binding.

The endpoint URL is persisted **only** in `mcp-config.json`. The connect
manifest records just the safe booleans `evidence_summary.endpoint_configured`
(a validated endpoint was supplied) and `evidence_summary.endpoint_verified`
(the binding was persisted by a successful apply) — never the URL itself.

`apply` is transactional across `SPEC.md`, the MCP config, **and**
`connect-manifest.json`. The final manifest is built and fully schema-validated
*before* any file is mutated; then a temp file is staged for all three before
any is replaced, and if an in-process write fails after an earlier replace has
already landed, every applied file is rolled back to its captured prior
bytes/mode — the trio never diverges. The manifest is committed inside this
transaction (not written separately afterward), and only the two production
config paths are recorded in `changed_paths`. The single case this can't defend
against is a hard crash / power loss *between* two individually atomic replaces;
if the compensating rollback itself fails, the run raises an error naming the
unreconciled path(s) instead of reporting success. Forward writes and rollbacks
preserve permissions: an existing destination keeps its exact prior mode, and a
brand-new file gets a predictable non-executable mode that honors the process
umask (`0o644` under the usual `022`).

## Freshness — `source_oldest_at` reflects the evidence, not the run

`freshness.source_oldest_at` records when the **real evidence** was captured,
threaded in via `--evidence-captured-at` (an ISO-8601 timestamp). It is never
back-filled from the run's own `generated_at`: if the capture time is unknown,
the field is `null` rather than a misleadingly fresh value. A malformed
`--evidence-captured-at` is rejected up front, before any file is written.

## Conformance — lossless numeric widening only

A captured integer satisfies an expected `number`, and an integral float such
as `1.0` satisfies an expected `integer` (widening that loses no information).
A non-integral float such as `1.5` still drifts against an expected `integer`,
and booleans never count as numeric — so `true` never widens into
`integer`/`number`. A row that is not a JSON object (a scalar, array, or null)
is itself a conformance difference at `$.items[i]` (`expected: object`) and can
never vacuously pass, not even against an optional-only contract. The generated
conformance test module normalizes the `{"items": [...]}` wrapper and a bare
list, and flags non-object rows, exactly like the in-process check — so the
pytest scaffold and the runtime check always agree.

## Robust inputs — corrupt state and unparseable arguments

A prior `connect-manifest.json` or `mcp-config.json` that exists but is
malformed (or is valid JSON that isn't an object) is **never** silently reset
to a starting `mock`/`{}`; the run raises a clean error and leaves the bytes
on disk untouched for repair. A prior manifest that *is* a JSON object but is
**missing** `integration_state`, or carries a value outside the four valid
states, is likewise refused rather than quietly treated as `mock` — a corrupt
or tampered record must not fabricate a starting state. On an apply path, MCP
config validation happens before the generated conformance test is scaffolded,
so the CLI's `(nothing written)` report is literal. On the CLI, unparseable
input — malformed JSON in a `--*-file` argument or a tool source that isn't
valid Python — prints a single-line error (no traceback), returns a stable
nonzero exit code, and writes nothing. When a verified apply fails, the CLI
reports precisely that `SPEC.md`, `mcp-config.json`, and the connect manifest
were left unchanged — it never over-claims that *no* files changed, since the
regenerated conformance-test scaffold may have been rewritten.

## Contract extraction — exactly what is read, nothing that merely exists

`extract_contract()` AST-walks the tool source and only records fields
**actually read**: `row['id']` marks `id` as required; `row.get('status')`
marks `status` as optional. A field present in the sample but never read by
the source (e.g. an internal-only column) is structurally excluded — it can
never leak into the contract, the generated tests, or the manifest. Types
and cardinality are inferred **only where evidence exists** in the sample;
a field read but absent from the sample gets `type: null` rather than a
guess.

## Manifest findings — the stable `INT-001..004` gap-evidence contract

The emitted `specs/connect-manifest.json` always carries **exactly four
findings, one each, in this order** — the live-leg gap evidence that
`threadlight-production-ready` projects 1:1 onto its `INT-001..004` targets.
The IDs are never dynamic: field-level conformance detail stays in
`conformance.differences`, so a consumer always sees the same four IDs
regardless of how a real response diverged.

| Finding | Evidence | `pass` | `must-fix` | `not-verified` |
|---|---|---|---|---|
| `INT-001` | Contract conformance | real sample conforms (evaluated, no differences) | any field-level difference | unevaluated (no real records to check) |
| `INT-002` | Runtime mock→real binding (`target_state` + persisted `integration_state`) | `target_state: real-verified` **and** persisted `integration_state: real-verified` after successful `--apply` | `target_state: real-drift` | dry-run `target_state: real-verified` with persisted `integration_state: mock`; `target_state: mock` or `real-unverified` |
| `INT-003` | OBO user-scoped identity | OBO present **and** user-scoped | — (no explicit-failure signal in the evidence shape) | OBO absent or not user-scoped |
| `INT-004` | Required-role revalidation vs current identity | roles revalidated against the **current** identity | revalidation ran but a required role is missing | never revalidated, no current identity, or a stale/mismatched grant |

`status` follows evaluation, not the finding severities: the envelope stays
`partial` whenever conformance was unevaluated (nothing real to check) and is
`complete` once fully evaluated **even if a finding is `must-fix`** (e.g. a
`real-drift` result is a complete-but-failing manifest). Because the consumer
downgrades any `pass`/`should-fix` it reads from a `partial`, stale, or
`aborted` envelope to `not-verified`, an incomplete leg can never inflate a
pillar's readiness — negative (`must-fix`) evidence still dominates.

## Files

```
scripts/connect.py                            # stdlib implementation + CLI
references/data-contract.schema.json          # extract_contract() output shape
references/connect-manifest.schema.json       # connect-manifest.json shape (shared envelope)
tests/test_connect.py                         # pytest suite
```

## Tests

```bash
python3 -m pytest skills/threadlight-connect/tests/ -v
```

## See also — official Azure Skills

Threadlight exists to make Microsoft's own platform **trivial to adopt** —
never to replace it. For first-party depth behind the evidence this leg
consumes, reach for the official
**[Azure Skills](https://github.com/microsoft/azure-skills)** catalog.
*Further reading, not a dependency* — Threadlight's guidance stays the
source of truth for the pilot flow:

- **[`entra-agent-id`](https://github.com/microsoft/azure-skills/blob/main/skills/entra-agent-id/SKILL.md)** — **Entra Agent Identity Blueprints** + the actual OAuth token exchange (OBO / `fmi_path`) this leg's OBO evidence is scaffolded around.
- **[`azure-rbac`](https://github.com/microsoft/azure-skills/blob/main/skills/azure-rbac/SKILL.md)** — **least-privilege role** selection + assignment the required-role revalidation checks against.
