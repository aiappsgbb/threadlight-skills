# Native-to-Threadlight evidence mapping

Supported upstream: Azure/agentops `v0.14.0`, commit
`fb5c93eee489c71ef4084fa209adae24f762e3d7`. The actual contracts are
[results.py](https://github.com/Azure/agentops/blob/v0.14.0/src/agentops/core/results.py),
[thresholds.py](https://github.com/Azure/agentops/blob/v0.14.0/src/agentops/pipeline/thresholds.py),
[comparison.py](https://github.com/Azure/agentops/blob/v0.14.0/src/agentops/pipeline/comparison.py),
[evidence_pack.py](https://github.com/Azure/agentops/blob/v0.14.0/src/agentops/services/evidence_pack.py),
[history.py](https://github.com/Azure/agentops/blob/v0.14.0/src/agentops/agent/history.py),
[eval_analysis.py](https://github.com/Azure/agentops/blob/v0.14.0/src/agentops/services/eval_analysis.py),
[redteam_runner.py](https://github.com/Azure/agentops/blob/v0.14.0/src/agentops/services/redteam_runner.py)
and [governance.py](https://github.com/Azure/agentops/blob/v0.14.0/src/agentops/core/governance.py).

## Shared API

Consumers add `skills/_shared` to their usual module path and import `agentops`,
**not** the producer script:

- `discover_opted_in_agents(repo: Path) -> list[dict]`: `{agent_key, root}`,
  with `root` repository-relative. `service` is included only for an unambiguous
  root `azure.yaml` agent-service mapping.
- `validate_manifest(data, *, repo: Path, now=None) -> dict`
- `load_manifest(repo: Path, *, now=None) -> dict`: bounded read of
  `specs/agentops-manifest.json`.
- `AgentOpsValidationError(ValueError)`: missing, malformed, unsupported,
  expired, source-mismatched or edited manifest. Error messages never echo raw
  values.

Discovery first uses root azd agent-service projects and adjacent
`.foundry/agent-metadata*.yaml` marker roots, plus explicit root opt-in. Without
azd/Foundry declarations only, it falls back to a bounded marker walk excluding
dot-directories, tool/cache/build folders, tests, fixtures, documentation, examples,
samples and catalog/skills folders. See the exact list in
[`SKILL.md`](../SKILL.md#opt-in-and-independent-binding) or exported
`DISCOVERY_EXCLUDED_DIRS`. Explicit azd projects can override fallback folder
exclusions, not path/symlink protections. Only bounded root `azure.yaml` is parsed
with the repository's existing PyYAML; aliases, duplicate keys, unsupported shapes
and over-complex documents fail closed. Native configuration is never parsed.

Validation re-derives the exact normalized manifest from current local evidence:
all opted-in roots, current repository state, committed policy, local observed-run
consistency (or optional existing-owner signatures),
source hashes, freshness and domain facts. A schema-only green, missing root,
removed blocker, changed config, forged receipt or extra payload field is not
accepted. No producer imports, native CLI or network occur.

Explicit runtime capture uses the shared `begin_observation`, `finish_observation`
and `cancel_observation` APIs. Only the packaged runtime surrounds its approved
bounded command with these calls; consumers never call them. Local observations
are not Azure attestation or authentication against a malicious local host.
Old unsigned native files without an observation remain unverified. Existing
signature verification can be explicitly required; no new PKI is mandatory.

## Public envelope

`schema: threadlight-agentops-manifest/v1`, `tool_version: 0.1.0`;
`generated_at`, `freshness: {valid_for_hours, source_oldest_at}`,
`status: complete|partial|aborted`, `findings`, plus:

- `repository: {remote, commit, dirty}`: sanitized host/owner/repo URL without
  credentials/query/fragment, commit or null, source dirty indicator excluding
  only the exact generated lifecycle outputs listed in the receipt contract.
- `verdict: operational|partial|blocked|not-applicable`.
- `summary: {agents_total, operational, partial, blocked}`.
- `agents`: identity, verdict, provenance, capabilities, domains and findings.

Provenance contains current commit; configuration, target, environment and
receipt SHA-256; oldest verified source time; repository-relative artifact
path/hash map. No raw target identifiers or local absolute paths are emitted.
Capabilities `config`, `pin`, `binding`, `integrity`, `doctor_freshness`,
`release_consistency`, `workflow` each carry an explicit `status`.
Domain keys are exactly `evals`, `redteam`, `govern`; each has `status`,
`verdict`, `summary`, and `blockers`.

Statuses: `verified|not-verified|stale|invalid|not-applicable`.
Domain verdicts: `pass|fail|unknown|supplementary`.
Findings/blockers: `{code, owner, severity}`; envelope findings also carry
`agent_key`. Owner is `agentops|evals|redteam|govern`; severity is
`must-fix|should-fix`. Codes are adapter constants, never native free text.

## Native acceptance and domain ownership

| Native evidence | Validated facts and normalized allowlist | Canonical owner |
|---|---|---|
| Version-1 `results.json` | Required raw fields, list-valued `rows.metrics` (`name/value/error/reason`), unique row indices, execution-error counts, rates, explicit thresholds and finite approved metrics. Native threshold `expected`/`actual` strings (`>=5`, `4`) become numeric reviewed values. Aggregate quality failure is distinct from execution success. | Evals |
| Native `comparison` | Separately approved baseline hash/target/dataset, fresh baseline, current/baseline/delta/direction and row consistency. Native “higher is improved” is descriptive, not a policy gate. Unbound/missing baseline never verifies comparison. | Evals |
| Unversioned red-team dataclass | Positive counts/objective budget, count/rate consistency with four-decimal tolerance, actual per-category **and per-strategy** counts, exact native fingerprint algorithm, independently approved scan fingerprint/threshold. Four core categories: hate/unfairness, violence, sexual, self-harm. No spreading a global ASR across absent buckets. Gate is strictly greater than threshold. | Redteam |
| Version-1 release + Doctor history | Fresh timestamps, real count/severity/history findings, explicit required enabled source statuses, current original eval target/timing/count/threshold consistency. Native `latest_eval.passed`, not an invented `overall_passed` field. Monitoring secondary statuses live inside `diagnostics`. | AgentOps operations |
| Release governance context | Native release status plus `runtime_verified=false`, `policy_verified=false`, `attestation_verified=false`. No ACS/runtime/signed-v1 coverage inferred from native ready, ASSERT file or policy presence. | Govern, supplementary only |
| Signed receipt workflow reference | Exact committed repository-root workflow bytes associated with the observed run: `.github/workflows/*.yml|yaml`, `.azuredevops/pipelines/*.yml|yaml`, or root `azure-pipelines.yml|yaml`. No workflow creation or deployment authorization; nested `.github` directories do not count. | CI/CD |

Domain summaries use fixed allowlists. Evals expose native seven summary counts/
rates, the source `finished_at` timestamp, approved `aggregate_metrics`, normalized `thresholds`, and
`comparison: {status, regressions}`. Redteam exposes counts, rate, objective
budget, approved categories/strategies, actual `per_category`,
`core_category_coverage`, threshold, and source `generated_at`. Both source
timestamps are validated RFC3339 values so canonical consumers can enforce a
stricter freshness window than the producer; the envelope generation timestamp
must never refresh old domain evidence. Unknown metric/category/strategy contracts
remain unverified until reviewed; adapters do not export arbitrary names.

**Deduplication is conditional:** a consumer must see an actual domain `status`,
accept it through the shared validator, and represent each specific blocker code
in its canonical output before suppressing that blocker elsewhere. A source
path, canonical manifest filename, empty domain object or green domain headline
is not representation. Unmapped native blockers remain `AOPS-DOCTOR-BLOCKED`
owned by AgentOps, including when other native checks map to domains. Never drop
unknown blockers to avoid double-counting. Domain quality alone is not the
operational aggregate, but native release blockers still require a recorded owner.

Recognized native **blocked** checks can carry an exact domain owner:
`Latest eval gate` with the tagged threshold-failure summary maps to
`AOPS-EVAL-QUALITY` only when the fresh verified eval domain actually fails.
`Red team readiness` maps to `AOPS-REDTEAM-QUALITY` only for a fresh verified
threshold breach with matching native state, rate, threshold and verified target.
Each mapping consumes exactly one matching native blocker occurrence. The code
remains in both the domain's blockers and typed agent/envelope findings, so the
aggregate stays blocked until a canonical consumer represents that precise code.
Check-only blockers, duplicate/unmatched occurrences, unknown meanings and real
critical Doctor findings remain `AOPS-DOCTOR-BLOCKED`; they cannot be hidden by a
recognized neighboring domain check.

## Boundaries

Per-file maximum is 8 MiB (manifest 1 MiB; receipt/policy/config/key 64 KiB).
Discovery is limited to 20,000 entries; rows/history to 10,000. Subprocess output
is drained with a shared budget and timeout before allocation grows unbounded.
Traversal, symlinks, special files and non-relative unsafe paths fail closed.
Default assessment is read-only; emission is explicit.

Never copy inputs, responses, expected answers, context, tool calls, raw config,
errors/reasons, Doctor findings, links, traces, raw SDK red-team output, credentials,
or absolute paths. Native warnings and unknown blockers are represented by fixed
codes, not text. This is local payload handling, not a network-export opt-out.
