---
name: threadlight-upgrade
description: >-
  UPGRADE leg: PLAN-ONLY compatibility/preview-drift scanner. Scans dependency
  pins, hosted-agent runtime policy, governance profile, and model families
  against a versioned compatibility-matrix.json, emitting threadlight.upgrade/v1
  with UPG-001 (matrix/dependency staleness), UPG-002 (preview/expiry drift),
  UPG-003 (source verification), plus one ordered, deduplicated migration plan.
  No network calls; source verification is fixture-driven. Unavailable source ->
  exact "Official source unavailable; no latest version was inferred.", never
  fabricates latest_version. Version compare is stdlib-only, numeric, never
  lexical/guessed. NEVER edits the project; no --apply exists. Read-only,
  confined to --project-root. Persists only IDs/versions/dates/safe source
  strings, never secrets. Handoff to an edit is manual. USE FOR: drift scanning,
  preview-to-GA planning, deprecation tripwires, upgrade-manifest. DO NOT USE
  FOR: applying edits, live lookups, ACL/citation grounding, cross-leg gating.
metadata:
  version: "0.1.0"
---

# Threadlight Upgrade — plan the migration, never perform it

The **UPGRADE** leg: the step that turns "our compatibility matrix says X is
drifting" into an ordered, file-by-file migration plan a human (or a
follow-up manual PR) can act on. `threadlight-upgrade` is a **coordinator**,
not a package manager, dependency resolver, or live version-checking
service — it never calls a package registry, a model catalog, or any other
network endpoint. It only compares a normalized description of a project
against a versioned, dated `references/compatibility-matrix.json` and turns
the comparison into three findings and one plan.

> **Why "plan-only."** Bumping a dependency, migrating a hosted-agent
> protocol mode, or retiring a deprecated governance profile is a
> human-reviewed change with its own tests and rollout — not something this
> skill should ever perform unattended. There is **no `--apply` flag at
> all**; passing one is an ordinary argparse "unrecognized arguments" error.
> Acting on the emitted `plan` is a **manual, human-driven step**.

## What this skill does and does not do

- **Does:** validate `references/compatibility-matrix.json`, compare a
  caller-supplied normalized `project` dict against it as of a given `today`,
  compute `UPG-001..003`, build one ordered/de-duplicated migration `plan`,
  schema-validate the result, and atomically write
  `specs/upgrade-manifest.json`.
- **Does NOT** call a package registry, a model catalog, or any other
  network endpoint. Official-source corroboration (`UPG-003`) is entirely
  **fixture-driven** via an injectable `source_results` mapping the caller
  supplies (simulating what a live adapter would have returned) — never a
  real network call.
- **Does NOT** edit the project, bump a dependency, rewrite a policy file, or
  implement `--apply`. It only reads (optionally, read-only) a
  `pyproject.toml`/`package.json`/runtime-policy fixture to help build the
  normalized `project` dict.

## The three findings

| ID | Checks | `not-verified` when | `should-fix` when |
|---|---|---|---|
| `UPG-001` | Matrix staleness + dependency drift | any pinned dependency's current version cannot be confidently parsed (a range, `latest`, anything ambiguous) | the matrix itself is older than its own `review_window_days` as of `today`, **or** a pinned dependency is behind the matrix's recorded `stable` release, **or** a dependency is pinned to a prerelease of that same release (e.g. `2.0.0b1` vs. stable `2.0.0`) |
| `UPG-002` | Preview/runtime-policy expiry drift | a project usage (a runtime-policy target, the governance profile, a model family) references a target that is not in the matrix, or is in the matrix under a different surface | the project targets a `deprecated` surface, a `preview` surface (more urgently when its `expiry_triggers` have already fired per the caller's `triggered_expiry_conditions`), or a target whose `expiry` date has already passed |
| `UPG-003` | Official source verification | `source_results` is absent (the common, honest case — no network call is ever made) or a specific check has no result to corroborate against; the exact detail message is *"Official source unavailable; no latest version was inferred."* and no `latest_version` is ever fabricated for it | a corroborated result's state differs from the matrix's recorded state (a genuine preview-to-GA or GA-to-deprecated transition) |

The manifest `status` is `partial` exactly when a finding is genuinely
`not-verified` (unparseable dependency version, a usage target absent from
the matrix, or unavailable/incomplete source corroboration); otherwise it is
`complete` — an executed `should-fix` with complete evidence stays
`complete` on its own. There is no `must-fix` ever emitted by this skill's
current checks (the schema allows it for parity with sibling skills), and no
`aborted` status either.

> **Malformed shapes raise, they are never `not-verified`.** A matrix missing
> a required key, an unknown key, a bad `state`/date/`review_window_days`, or
> a duplicate `target` all raise `UpgradeMatrixError` **before** any scan is
> attempted. A malformed `project` (wrong types for `dependencies`,
> `runtime_policy`, `governance_profile`, `model_families`,
> `triggered_expiry_conditions`, `artifact_paths`) raises
> `UpgradeProjectError`. `not-verified` is reserved for genuinely *ambiguous*
> evidence (an unparseable version, an unavailable source), never a
> *malformed* input.

## Version comparison is numeric, never lexical, never guessed

`parse_version` is a stdlib-only, regex-based parser recognizing an optional
leading `v`, a 1-4 segment release core, and a common semver/Python
prerelease tail (`a`/`alpha`, `b`/`beta`, `rc`/`c`/`pre`/`preview`, `dev`,
each with an optional trailing number) plus optional `+build` metadata. A
version it cannot confidently place — a range specifier (`>=1.0,<2.0`), a
bare `latest`, an empty string, a git SHA — parses to `None`. `compare_versions`
pads release tuples to equal length with zeros and compares a
`(release, stage_rank, stage_num)` tuple (`dev < alpha < beta < rc < final` of
the same release) — **never** a string/lexical comparison — and returns
`None` (ambiguous, `not-verified`) when either side is unparseable. This
skill never infers a "latest" version from a package name, a hard-coded
list, or prior model knowledge — only from the matrix's own `stable` field or
an injected `source_results` entry's own `latest_version`.

## Compatibility matrix (`references/compatibility-matrix.json`)

```jsonc
{
  "schema": "threadlight-upgrade-compatibility-matrix/v1",
  "version": "2026.08.1", "date": "2026-08-01",
  "source": "https://learn.microsoft.com/azure/ai-foundry/agents/whats-new",
  "entries": [
    {"surface": "agent-framework", "target": "agent-framework", "state": "stable",
     "source": "https://learn.microsoft.com/agent-framework/overview#versioning",
     "last_reviewed": "2026-01-01", "review_window_days": 90, "stable": "2.0.0"},
    {"surface": "hosted-agent-protocol", "target": "invocations", "state": "preview",
     "source": "https://learn.microsoft.com/azure/ai-foundry/agents/whats-new#invocations-api-preview",
     "last_reviewed": "2026-06-01", "review_window_days": 120,
     "stable": "responses", "replacement": "responses",
     "expiry_triggers": ["responses-end-to-end"]}
  ]
}
```

Every entry declares `surface` (one of the six canonical surfaces:
`hosted-agent-protocol`, `agent-framework`, `toolbox`, `skill-publication`,
`governance-profile`, `model-family`), a **globally-unique** `target`,
`state` (`stable`/`preview`/`deprecated`), a safe `source` reference string
(never a credential/token-bearing URL), `last_reviewed` and
`review_window_days`, and optionally `stable` (the known-stable
version/target to compare dependencies against), `replacement` (the
recommended migration target), `expiry` (the date a preview/deprecated state
formally ends), and `expiry_triggers` (official trigger names an
operator can confirm fired via `triggered_expiry_conditions`).
`agent-framework`/`toolbox`/`skill-publication` are **dependency surfaces**
(matched against `project["dependencies"]`, a name→version-string map); the
remaining three are **usage surfaces** (matched against
`project["runtime_policy"]`, `project["governance_profile"]`, and
`project["model_families"]`).

## `scan_project(project, matrix, today, source_results=None)`

Validates `matrix` (`validate_matrix`, raises before any output), coerces
`today` (a `date`, `datetime`, or `YYYY-MM-DD` string), computes `UPG-001`
(matrix staleness + per-dependency drift via `parse_version`/
`compare_versions`), `UPG-002` (usage drift against `runtime_policy` +
`governance_profile` + `model_families`), and `UPG-003` (fixture-driven
source corroboration), aggregates every generated `{path, reason, from, to}`
item into one `plan` — **de-duplicated by `(path, reason)`, sorted
deterministically, and 1-based ordered** — schema-validates the full
manifest (`validate_upgrade_manifest`, including a recursive
credential/content-key and secret-value scan mirrored from
`threadlight-ground`), and returns it. `source_results` is keyed
`"surface:target" -> {"state": ..., "latest_version": (optional),
"checked_at": (optional)}` and is **never** populated by a real network
call — it is either omitted (the honest "unavailable" case) or supplied as a
fixture by the caller/CLI.

## Persistence contract — never the raw evidence

The manifest persists **only**: surface/target identifiers, version
strings, ISO dates, safe `source` reference strings, and the three findings'
allowlisted `detail` (IDs, dates, counts, a controlled `reason` enum). It
**never** persists credentials, tokens, prompts, completions, or customer
payloads — every build/write is scanned recursively for
credential/content-shaped **keys** (whole-word match, so a legitimate
`content`-free field is never confused with an actual secret key) and
secret-shaped **values** (AWS/GitHub/Slack/OpenAI tokens, JWTs, URL-embedded
credentials, Bearer headers, Azure connection strings/SAS parameters, a
`key=value`-shaped secret) before anything is returned or touches disk.

## CLI

```bash
python3 skills/threadlight-upgrade/scripts/upgrade.py \
  --project-root . --pyproject-path pyproject.toml \
  --runtime-policy-path specs/runtime-policy.json \
  --source-results-path specs/upgrade-source-results.json \
  --today 2026-08-17 --emit --gate
```

`--project-root` bounds every project-describing path
(`--project-file`, `--pyproject-path`, `--package-json-path`,
`--runtime-policy-path`, `--manifest-path`); an escape (an absolute path
outside it, a `..` traversal including through a missing parent, or a
symlink resolving outside it) is rejected before any file is opened. There is
**no `--apply` flag** — it is not defined at all, so passing it is an
ordinary argparse usage error (exit 2). `--matrix-path` defaults to the
skill's own shipped `references/compatibility-matrix.json`.
`UpgradeMatrixError`, `UpgradeProjectError`, `ManifestValidationError`, and
read/write `OSError`/JSON errors are all caught cleanly (exit 1) so a failed
run never corrupts a prior valid manifest.

## Files

```
scripts/upgrade.py                             # stdlib implementation + CLI
references/compatibility-matrix.json           # versioned, dated compatibility matrix
references/upgrade-manifest.schema.json        # upgrade-manifest.json shape (shared envelope)
tests/test_upgrade.py                          # pytest suite
```

## Tests

```bash
python3 -m pytest skills/threadlight-upgrade/tests/ -v
```

## See also

- **`threadlight-ground`** — the ACL/citation/refusal leg this skill's
  shared-envelope and secret-scanning conventions are modeled on.
- **`threadlight-govern`**/**`threadlight-safe-check`** — own runtime policy
  enforcement and safety-control checks themselves; this leg only flags
  *version/surface drift* against the compatibility matrix, never enforces
  a control.
- **`threadlight-production-ready`** — a natural consumer of
  `UPG-001..003` from `specs/upgrade-manifest.json` alongside every other
  leg's findings for a single pilot go/no-go.
