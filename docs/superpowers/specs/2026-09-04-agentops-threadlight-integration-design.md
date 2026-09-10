# AgentOps adoption and Threadlight integration design

**Date:** 2026-09-04  
**Status:** Approved design; implementation planning pending  
**Repositories:** `aiappsgbb/awesome-gbb`, then `aiappsgbb/threadlight-skills`

## Summary

Integrate [Azure AgentOps](https://github.com/Azure/agentops) in two ordered
layers:

1. Add `foundry-agentops` to `aiappsgbb/awesome-gbb` to adopt and operate
   AgentOps for an individual agent.
2. Add the opt-in `threadlight-agentops` lifecycle step to
   `aiappsgbb/threadlight-skills` to validate AgentOps evidence and project it
   into existing Threadlight contracts without replacing their domain owners.

AgentOps manages agent-level evaluation, regression, diagnostics, telemetry
inspection, release evidence, and operational workflows. It does not replace
Citadel. Citadel remains authoritative for APIM gateway controls, access
contracts, tenant isolation, routing, rate limiting, and platform policy
enforcement.

The integration is deliberately not one-to-one. Existing Threadlight legs
continue to own evaluation, red-team, governance, CI/CD, deployment validation,
and production-readiness semantics. AgentOps acts as an execution backend and
evidence source where its artifacts are compatible.

## Context

This design targets AgentOps `v0.14.0`, a pre-1.0 release. The relevant native
artifacts are:

```text
agentops.yaml
.agentops/agent.yaml
.agentops/results/<timestamp>/results.json
.agentops/results/<timestamp>/report.md
.agentops/results/latest/results.json
.agentops/release/latest/evidence.json
.agentops/release/latest/evidence.md
.agentops/agent/history.jsonl
.agentops/data/trace-regression.jsonl
.agentops/data/trace-regression-manifest.json
```

AgentOps `results.json` and `evidence.json` use versioned outer contracts, but
the evidence is unsigned, `latest/` aliases are mutable, some nested sections
are weakly typed, and Doctor sources can fail open. Threadlight must therefore
validate and bind the evidence independently before relying on it.

## Goals

- Make AgentOps adoption repeatable for each selected Foundry or HTTP agent.
- Preserve one canonical Threadlight owner for each production domain.
- Avoid duplicate eval runs, red-team campaigns, governance findings, and
  competing workflow files.
- Bind AgentOps evidence to a repository, commit, agent, target, and
  environment.
- Expose AgentOps operational conformance to `threadlight-production-ready`
  without double-counting domain findings.
- Keep the integration opt-in and non-penalizing for agents that do not adopt
  AgentOps.
- Keep Citadel ownership and boundaries unchanged.

## Non-goals

- Replacing Citadel, `threadlight-safe-check`, or any existing Threadlight
  domain leg.
- Treating AgentOps release evidence as certification or as the final
  production-readiness verdict.
- Making AgentOps mandatory for every Foundry or production workload.
- Reimplementing AgentOps configuration parsing in Threadlight.
- Copying prompt, response, trace, tool-call, or other sensitive payloads into
  Threadlight manifests.
- Automatically accepting a new regression baseline.
- Provisioning APIM, networking, identities, RBAC, branch protection, or
  environment approvals from the AgentOps adapter.

## Architecture

```text
aiappsgbb/awesome-gbb
  foundry-agentops
    - discover one agent and its deployment context
    - install and pin AgentOps
    - configure agentops.yaml and native workspace
    - configure eval, telemetry, Doctor, and release evidence
    - prepare standalone workflows when Threadlight is absent
    - diagnose and remediate AgentOps adoption
              |
              | native AgentOps artifacts
              v
aiappsgbb/threadlight-skills
  threadlight-agentops
    - discover opted-in agent roots
    - validate version, schema, integrity, freshness, and binding
    - emit specs/agentops-manifest.json
              |
              +--> threadlight-evals adapter
              +--> threadlight-redteam adapter
              +--> threadlight-govern supplemental evidence
              +--> threadlight-cicd workflow composition
              |
              v
  threadlight-production-ready
    - consumes normalized Threadlight manifests
    - consumes one AOPS-001 operational aggregate

Citadel remains a separate platform control plane throughout.
```

## Delivery order

### Phase 1: `foundry-agentops` in awesome-gbb

The upstream adoption skill must land first. It establishes the supported
AgentOps version, native configuration, operational procedures, and workflow
expectations that the Threadlight adapter will validate.

### Phase 2: `threadlight-agentops` in threadlight-skills

The lifecycle step lands after Phase 1. It consumes the conventions established
by `foundry-agentops`, then adds the Threadlight evidence contract and adapters.

No Threadlight finding may recommend `foundry-agentops` as an executable sibling
skill until that skill is available in awesome-gbb.

## `foundry-agentops` scope

### Responsibilities

For one selected agent at a time, the skill:

1. Resolves the agent root and deployment context from `azure.yaml`, azd
   environment values, `.foundry` metadata, and existing AgentOps files.
2. Installs `agentops-accelerator` at an exact supported version.
3. Initializes `agentops.yaml`, `.agentops/`, and safe ignore rules without
   overwriting existing configuration or datasets.
4. Binds one of the supported target forms:
   - Foundry prompt agent `name:version`;
   - Foundry hosted-agent URL;
   - generic HTTP endpoint;
   - direct model deployment.
5. Configures datasets, evaluators, explicit thresholds, regression baseline,
   telemetry sources, Doctor, and release evidence.
6. Provides day-2 procedures for eval execution, regression analysis, explicit
   baseline promotion, trace-to-regression promotion, Doctor, and evidence
   regeneration.
7. Supports optional AgentOps red-team and ASSERT/ACS features without making
   them prerequisites for initial adoption.

### Workflow modes

`foundry-agentops` has two workflow modes:

- **Standalone:** when the repository is not Threadlight-managed, it may use
  AgentOps native workflow generators.
- **Threadlight-aware:** when `specs/manifest.json` and Threadlight CI/CD
  conventions are detected, it does not create competing workflow files.
  It prepares only AgentOps configuration and prerequisites;
  `threadlight-cicd` owns the final pipelines.

### Explicit exclusions

The skill does not:

- evaluate Citadel posture;
- modify APIM, access contracts, networking, RBAC, or platform policy;
- declare a workload production-ready;
- overwrite an existing baseline without explicit approval;
- hide unavailable telemetry as healthy.

## `threadlight-agentops` scope

### Opt-in and discovery

Opt-in is per agent and is declared solely by the presence of
`agentops.yaml` in that agent's root.

Discovery order:

1. Enumerate `azure.yaml` services with `host: azure.ai.agent`.
2. Resolve their project folders as candidate agent roots.
3. Add `.foundry/agent-metadata*.yaml` roots not already represented by azd.
4. For a single-agent repository, allow the repository root as the agent root.
5. Select only candidate roots containing `agentops.yaml`.

The presence of `.agentops/` alone is not opt-in because it may be stale or
copied cache state. Threadlight does not add a parallel registry to
`specs/manifest.json`.

### Responsibilities

For every opted-in agent, the step:

- validates supported AgentOps version and native artifact versions;
- resolves agent, target, and environment identity from authoritative azd and
  `.foundry` sources;
- validates repository and commit binding;
- detects dirty source state;
- hashes every consumed artifact;
- anchors mutable `latest/` aliases to timestamped result directories;
- evaluates artifact and Doctor freshness;
- checks internal consistency between result, Doctor, and release evidence;
- detects whether Threadlight-owned workflows compose the required AgentOps
  jobs;
- records references to domain manifests without copying their findings;
- emits `specs/agentops-manifest.json`.

The step does not author AgentOps configuration and does not directly assign
production-readiness statuses.

### Configuration validation boundary

Threadlight does not parse `agentops.yaml` itself. It treats the file as opaque
configuration and:

1. hashes it for provenance;
2. invokes the supported AgentOps JSON analysis command with an argument array,
   timeout, and bounded output when the pinned executable is available;
3. trusts only the command's documented machine-readable validation result;
4. reports an AgentOps-declared parse or semantic error as `must-fix`;
5. reports configuration as `not-verified` when the executable is unavailable
   or its output contract is unsupported.

The exact AgentOps package pin is verified in the repository dependency lock or
CI install step, not inferred from `agentops.yaml`.

## Ownership and overlap matrix

| AgentOps evidence or behavior | Canonical Threadlight owner | Integration rule |
|---|---|---|
| Eval results, thresholds, baseline, regression | `threadlight-evals` | AgentOps is an alternate execution backend. Translate into `evals-manifest.json`; do not rerun the same batch. |
| AgentOps red-team output | `threadlight-redteam` | Normalize into the existing scan-result contract; do not create duplicate SAFE findings or rerun an equivalent campaign. |
| ASSERT/ACS and governance signals | `threadlight-govern` | Supplemental evidence only. AGT policy, tests, CI gate, and attestation remain authoritative. |
| PR, deploy, and scheduled AgentOps jobs | `threadlight-cicd` | Compose into Threadlight workflows. Do not emit concurrent AgentOps workflow files in Threadlight mode. |
| Deployment shape, images, channels, jobs, and reachability | `threadlight-safe-check` | No AgentOps substitution. Safe Check remains the mandatory structural and behavioral deployment gate. |
| Production score and go-live recommendation | `threadlight-production-ready` | Consume only normalized Threadlight manifests and the AOPS aggregate, never raw AgentOps JSON. |
| Gateway, access contracts, isolation, routing, rate limiting | Citadel | No AgentOps ownership or mutation. |

## Domain adapter behavior

### Evals

When compatible, fresh, and bound AgentOps `results.json` exists,
`threadlight-evals` uses it for offline results, thresholds, pass rate, and
regression evidence. It retains ownership of:

- capability mapping to EVAL finding IDs;
- status taxonomy;
- continuous-evaluation schedule and alert checks;
- freshness policy;
- champion-challenger requirements;
- `specs/evals-manifest.json`.

The adapter must not rerun an equivalent AgentOps batch merely to produce the
Threadlight manifest.

### Red team

When AgentOps red-team output contains compatible category rates, counts,
strategies, target identity, and timestamps, `threadlight-redteam` normalizes
it into its existing scan-result model and emits the existing
`specs/redteam-manifest.json`.

Unsupported or incomplete AgentOps output becomes `not-verified`; it never
passes because a command completed.

### Governance

AgentOps ASSERT/ACS signals may strengthen provenance or indicate that a check
ran. They cannot satisfy the AGT policy, runtime enforcement, test fixture, CI
gate, or attestation capabilities owned by `threadlight-govern`.

### CI/CD

For opted-in agents, `threadlight-cicd` composes:

```text
pull request
  -> AgentOps eval and regression comparison

post-deploy
  -> AgentOps Doctor
  -> AgentOps release evidence
  -> threadlight-agentops normalization
  -> existing Threadlight domain adapters/gates

schedule
  -> AgentOps Doctor
  -> evidence publication and notification
```

GitHub Actions and Azure DevOps continue to use Threadlight's existing OIDC/WIF,
environment approvals, runner, least-privilege RBAC, and Citadel boundary
contracts.

## Conflict and precedence rules

1. Raw AgentOps artifacts never directly set a production-ready finding.
2. The canonical Threadlight domain owner translates evidence and applies
   Threadlight policy.
3. If two valid sources assess the same control and disagree, preserve both
   provenance records and use the worse status.
4. A better result never hides a worse valid result.
5. A conflict remains visible until an operator resolves the cause or explicitly
   invalidates one source.
6. AgentOps blockers already mapped to eval, red-team, or governance findings
   are referenced but not counted again in the AgentOps operational aggregate.
7. Unmapped operational blockers remain visible through `AOPS-001`.

## AgentOps manifest contract

Output:

```text
specs/agentops-manifest.json
```

Illustrative shape:

```json
{
  "schema": "threadlight-agentops-manifest/v1",
  "tool_version": "0.1.0",
  "captured_at": "2026-09-04T14:00:00Z",
  "repository": {
    "remote": "aiappsgbb/example-agent",
    "commit": "0123456789abcdef",
    "dirty": false
  },
  "verdict": "operational",
  "agents": [
    {
      "agent_key": "support-agent",
      "root": "src/agent",
      "service": "agent",
      "environment": "prod",
      "target_fingerprint": "sha256:...",
      "agentops": {
        "required_version": "0.14.0",
        "detected_version": "0.14.0"
      },
      "native_release_status": "ready_with_warnings",
      "capabilities": {
        "configuration_valid": {"status": "pass"},
        "version_pinned": {"status": "pass"},
        "binding_valid": {"status": "pass"},
        "artifact_integrity_valid": {"status": "pass"},
        "doctor_recent": {"status": "should-fix"},
        "release_evidence_consistent": {"status": "pass"},
        "workflow_composed": {"status": "pass"}
      },
      "artifacts": [
        {
          "kind": "results",
          "path": "src/agent/.agentops/results/20260904T135500Z/results.json",
          "sha256": "...",
          "captured_at": "2026-09-04T13:55:00Z",
          "schema_version": 1
        }
      ],
      "domain_manifest_refs": {
        "evals": "specs/evals-manifest.json",
        "redteam": "specs/redteam-manifest.json",
        "govern": "specs/govern-manifest.json"
      },
      "unmapped_blockers": []
    }
  ],
  "summary": {
    "opted_in": 1,
    "pass": 0,
    "should_fix": 1,
    "must_fix": 0,
    "not_verified": 0
  }
}
```

All paths are repository-relative. The manifest contains metrics and references,
not prompts, responses, trace payloads, tool arguments, or full Doctor payloads.

## Verdict and failure model

The per-capability status vocabulary is:

```text
pass / should-fix / must-fix / not-verified / not-applicable
```

Classification:

| Condition | Status |
|---|---|
| No `agentops.yaml` for an agent | `not-applicable`; the agent is excluded and the repository is not penalized |
| Supported, pinned version; valid binding; fresh, consistent evidence; composed workflows | `pass` |
| Doctor stale, AgentOps `ready_with_warnings`, or scheduled Doctor workflow absent | `should-fix` |
| Opt-in configuration invalid; version unpinned or mismatched; target binding wrong; artifact hash/integrity failure; unmapped operational release blocker | `must-fix` |
| AgentOps executable unavailable; permissions missing; unknown future schema/version; provenance cannot be established | `not-verified` |

The repository-level AgentOps verdict is the worst per-agent status. It never
silently drops an opted-in agent because another agent passed.

## Production-ready integration

`threadlight-production-ready` adds one aggregate finding:

| ID | Pillar | Meaning |
|---|---|---|
| `AOPS-001` | `sre-handover` | Every opted-in agent has current, bound, internally consistent AgentOps operational evidence and composed lifecycle workflows |

Behavior:

- No opted-in agents: `AOPS-001` is `not-applicable` and does not affect score.
- One or more opted-in agents: the finding takes the worst operational status.
- Detail lists each agent and its open operational capabilities.
- Eval, red-team, and governance blockers already represented by their canonical
  findings are not scored again in `AOPS-001`.
- The detailed AgentOps manifest remains the single source of truth; the
  production-ready report shows only the aggregate and references.

## Security, privacy, and provenance

- Pin the supported `agentops-accelerator` version exactly.
- Treat every AgentOps artifact as untrusted repository input.
- Validate known outer schema versions; unknown future versions are
  `not-verified`, never pass.
- Hash every consumed artifact with SHA-256.
- For `results/latest`, require content equivalence with a timestamped result
  directory.
- For mutable release evidence that has no timestamped native counterpart,
  require same-run provenance: the AgentOps evidence command and
  `threadlight-agentops` normalization must execute in the same CI or local
  process chain, and the adapter records the command receipt, start/end
  timestamps, exit code, artifact hash, and current commit. Previously generated
  `release/latest` evidence without that proof is `not-verified`, not pass.
- Record repository remote, commit, dirty state, agent key, target fingerprint,
  and environment.
- Reject absolute and traversal paths from emitted evidence.
- Do not copy free-text prompts, responses, contexts, tool calls, trace bodies,
  credentials, or receiver addresses.
- Treat Markdown and nested strings as untrusted display content.
- Use argument arrays, `shell=False`, timeouts, and bounded output if the
  adapter invokes AgentOps.
- Do not call write-capable AgentOps MCP tools from assessment mode.
- Keep all evidence tenant-local; no external upload is part of this design.

## Testing

### Threadlight unit and fixture coverage

The Threadlight side remains Python stdlib-only and covers:

1. repository with no AgentOps opt-in;
2. one opted-in agent;
3. multiple agents with mixed opt-in;
4. invalid `agentops.yaml`;
5. unpinned and mismatched AgentOps versions;
6. unknown future artifact version;
7. missing executable or Azure permissions;
8. stale Doctor and release evidence;
9. `latest/` not matching a timestamped result;
10. artifact hash failure;
11. malformed timestamps and clock skew;
12. dirty repository and commit mismatch;
13. wrong target or environment binding;
14. no prompt, response, trace payload, absolute path, or secret leakage;
15. eval adapter reuses AgentOps results without duplicate execution;
16. red-team adapter reuses compatible AgentOps output without duplicate
    campaign execution;
17. governance evidence remains supplemental;
18. conflicting valid sources preserve provenance and select the worse status;
19. mapped blockers are not duplicated in `AOPS-001`;
20. Threadlight workflows contain AgentOps jobs without competing generated
    AgentOps workflow files;
21. Citadel hub, spoke, access-contract, RBAC-scope, and central-platform
    boundaries are unchanged.

### Live coverage

Live AgentOps and Azure tests are separate and opt-in. They verify a supported
AgentOps release against:

- one Foundry prompt agent;
- one hosted or HTTP agent;
- a multi-agent repository;
- GitHub Actions OIDC;
- Azure DevOps WIF where available;
- App Insights/Log Analytics Doctor sources;
- insufficient-permission degradation.

Live tests do not replace deterministic fixture tests.

## Rollout

1. Implement and release `foundry-agentops` with a pinned supported AgentOps
   version and standalone/Threadlight-aware workflow behavior.
2. Add AgentOps fixtures and the `threadlight-agentops` manifest producer.
3. Add the eval adapter.
4. Add the red-team adapter.
5. Add supplemental governance ingestion.
6. Extend `threadlight-cicd` workflow composition.
7. Add `AOPS-001` consumption to `threadlight-production-ready`.
8. Run an opt-in field test on a single agent, then a mixed multi-agent
   repository.

Each rollout step is additive. Existing repositories without `agentops.yaml`
retain their current behavior.

## Locked decisions

- `foundry-agentops` lands before the Threadlight step.
- AgentOps is opt-in in every posture.
- Opt-in is per agent and declared by `agentops.yaml`.
- AgentOps does not replace Citadel.
- Existing Threadlight domain manifests remain canonical.
- `threadlight-agentops` is an adapter and evidence validator, not a second
  domain engine.
- `threadlight-cicd` owns workflows inside Threadlight projects.
- Conflicting valid evidence uses worst-status precedence.
- `production-ready` receives one non-duplicating `AOPS-001` aggregate under
  `sre-handover`.
- Missing opt-in is `not-applicable`, not a gap.
