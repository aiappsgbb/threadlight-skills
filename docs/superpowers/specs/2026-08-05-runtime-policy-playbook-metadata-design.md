# Runtime Policy and Playbook Metadata — Design Spec

- **Status:** Implemented
- **Date:** 2026-08-05
- **Author:** Brainstormed with Copilot CLI
- **Related upstream:** `aiappsgbb/agentic-loop` PRs
  [#14](https://github.com/aiappsgbb/agentic-loop/pull/14),
  [#15](https://github.com/aiappsgbb/agentic-loop/pull/15), and
  [#16](https://github.com/aiappsgbb/agentic-loop/pull/16)

## 1. Problem

Threadlight currently has two conflicting runtime defaults:

- `threadlight-design` records Microsoft Agent Framework (MAF) as the house
  default in `specs/foundation.md`.
- `threadlight-deploy` generates GitHub Copilot SDK (GHCP SDK) by default and
  falls back to MAF for capabilities the GHCP runtime does not currently cover.

That drift can produce a specification that disagrees with the generated
runtime. The decision is also duplicated in prose, so a future update can
silently reintroduce the mismatch.

Threadlight's Blueprint already derives a skill chain from each process-library
entry, but the resulting playbook package is not represented as a stable,
machine-readable contract. Prerequisites, expected artifacts, build skills, and
runtime-skill policy therefore remain implicit or duplicated across the site.

## 2. Goals

1. Establish one versioned runtime-policy contract consumed by design and
   deploy.
2. Preserve Threadlight's verified runtime behavior rather than copying
   upstream defaults blindly.
3. Make every process-library entry expose a generated playbook metadata block.
4. Reuse the existing process library and Blueprint; do not add another
   catalog or skill.
5. Add tests that fail when runtime defaults or generated playbook metadata
   drift.

## 3. Non-goals

- Adding a `threadlight-policy` skill.
- Replacing `threadlight-auto` with `/spec2cloud`.
- Switching GHCP hosted agents to the Responses protocol before the local
  runtime and Teams integration support it.
- Publishing runtime skills to Foundry; existing companion skills own that
  lifecycle.
- Hand-authoring metadata for every process entry.

## 4. Runtime policy

### 4.1 Source of truth

Add
`skills/threadlight-design/references/runtime-policy.json` with schema
`threadlight.runtime-policy/v1`. It defines:

- supported framework, runtime-shape, and protocol selectors;
- the default route;
- ordered exception routes and their trigger signals;
- a short rationale for every route.

The locked routes are:

| Priority | Trigger | Framework | Shape | Protocol |
|---|---|---|---|---|
| 1 | Explicit supported operator choice | Chosen value | Chosen value | Compatible value |
| 2 | `workflow_model: workflow` | `microsoft-agent-framework` | `workflow` | `responses` |
| 3 | Toolbox, custom Python tools, file generation, or latency-sensitive data-query tooling | `microsoft-agent-framework` | `agent` | `responses` |
| 4 | Otherwise | `github-copilot-sdk` | `agent` | `invocations` |

The default deliberately remains GHCP SDK plus Invocations because
`threadlight-deploy` documents and tests that runtime today. Upstream's GHCP
SDK plus Responses default is an adoption candidate only after the hosted
runtime supports it end to end.

### 4.2 Consumers

- `threadlight-design` reads the contract when filling
  `specs/foundation.md`; the template uses the canonical selectors and cites
  the policy file.
- `threadlight-deploy` reads the selected values from the foundation/SPEC,
  validates them against the contract before generating runtime files, and
  stops on unknown or incompatible combinations.
- `threadlight-auto` owns no separate runtime default. Its design and deploy
  stages inherit the same policy and surface policy-validation failures as hard
  stops.
- `THREADLIGHT.md` describes the policy at a high level and links to the
  machine-readable source.

### 4.3 Drift protection

Add a targeted test that parses `runtime-policy.json` and asserts:

- the default selector values are valid;
- every route references supported selectors;
- `foundation-template.md` names the policy default;
- `threadlight-design` and `threadlight-deploy` reference the policy file;
- the deploy documentation does not claim a different default.

## 5. Generated playbook metadata

### 5.1 Data shape

`scripts/build_process_library.py` adds a `playbook` object to every sanitized
entry:

```json
{
  "playbook": {
    "schema": "threadlight.playbook/v1",
    "level": "Advanced",
    "use_when": "A concise scenario-specific trigger",
    "build_skills": ["threadlight-design", "threadlight-local-test"],
    "run_skills": [],
    "run_skills_source": "generated-by-threadlight-design",
    "prerequisites": [
      "github-copilot",
      "threadlight-skills",
      "azure-subscription"
    ],
    "artifacts": ["specs/SPEC.md", "specs/manifest.json"]
  }
}
```

Rules:

- `level` maps `low`, `medium`, and `high` complexity to `Starter`,
  `Intermediate`, and `Advanced`.
- `use_when` uses an explicit sanitized source value when present and otherwise
  falls back to the process summary.
- `build_skills` uses the Blueprint's existing signal rules and canonical
  order.
- `run_skills` is empty in the static catalog because process-specific runtime
  skills are generated from the completed SPEC, not guessed by the catalog.
- `run_skills_source` makes that behavior explicit.
- `prerequisites` contains stable identifiers, not shell commands.
- `artifacts` is the ordered union of stable artifacts emitted by the selected
  build skills.

### 5.2 Blueprint consumption

`docs/assets/blueprint-logic.js` prefers
`process.playbook.build_skills`. It retains the existing derivation logic as a
compatibility fallback for older or custom process-library entries.

The generated prompt and automation explanation continue to use
`deriveSkills()`, so they automatically consume the metadata without a second
code path.

### 5.3 Generator behavior

The generator decorates only after applying the existing field whitelist and
leak scan. Metadata is derived from sanitized fields, so internal source fields
cannot influence the committed contract.

Invalid complexity values, malformed explicit metadata, or unknown skill names
cause generation to fail with a clear error rather than emitting partial
metadata.

## 6. Testing

Extend the existing Blueprint/process-library tests to verify:

- every committed entry has a valid `threadlight.playbook/v1` block;
- levels, skill names, prerequisites, and artifacts use allowed values;
- metadata skill order follows the canonical Threadlight order;
- `deriveSkills(entry)` equals `entry.playbook.build_skills`;
- a legacy entry without `playbook` still derives the same skill chain;
- the sanitizer still removes internal fields and blocks leak markers.

Run the existing targeted Node tests for Blueprint/process-library logic and
the existing Python tests that cover Threadlight skill contracts.

## 7. Documentation and compatibility

The process-library JSON change is additive. Existing readers that ignore
unknown fields continue to work. Blueprint keeps a fallback for custom or stale
entries, so rollout does not require an atomic site-data deployment.

Update the technical briefing to explain the runtime-policy authority order and
the generated playbook metadata contract. No plugin version bump is required
because no skill invocation interface changes.

## 8. Success criteria

- Design and deploy agree on GHCP SDK plus Invocations as the default.
- MAF exceptions are represented once and referenced everywhere else.
- Every committed process-library entry contains complete playbook metadata.
- Blueprint output is unchanged for existing scenarios.
- Targeted runtime-policy and Blueprint tests pass.
