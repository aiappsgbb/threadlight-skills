# Locally observed-run receipt convention

This is a **Threadlight convention, not a native AgentOps schema**. It makes
healthy evidence attainable without pretending native files have repository,
commit or environment fields. It does not create a new authority, replace
deployment attestations, or certify the observer's Azure access.

## Independent committed policy — no mandatory PKI

Before execution, the existing owner reviews and commits each selected root's
`.threadlight/agentops-binding.json`. Ordinary same-process observation needs no
keys or signing infrastructure. Existing-owner signature verification is an
optional stronger policy, described below.

Policy shape:

```json
{
  "schema": "threadlight-agentops-binding/v1",
  "target_sha256": "<SHA-256 of independently resolved native TargetInfo-shaped identity>",
  "environment_sha256": "<SHA-256 of independently approved environment identity>",
  "required_doctor_sources": ["foundry"],
  "thresholds": [{"metric": "relevance", "criteria": ">=", "expected": 5}]
}
```

Target hashing is UTF-8 JSON, sorted keys, compact separators `(",", ":")`,
finite values, with the full native TargetInfo shape (`kind`, `raw`, `protocol`,
`name`, `version`, `url`, `deployment`, including nulls as independently resolved).
Do **not** derive the approved target/environment from the eval or release file.
The owner's authoritative deployment/readback and approval define those values.
Target forms are `foundry_prompt`, `foundry_hosted`, `http_json`,
`model_deployment`, `model_direct`. Credentials are not target identity.

Required source names are the native history names `foundry` and/or `monitor`.
Select them from actual operational requirements, not to hide missing sources.
For monitor, all four diagnostic statuses must be `ok`. Source enablement alone
does not prove collection; project-level telemetry does not prove agent-specific
ingestion. This conformance signal never claims that broader ingestion proof.

Optional independent baseline approval:
`baseline_sha256`, `baseline_dataset_sha256`, `baseline_target_sha256`.
Optional scan approval:
`redteam_fingerprint` (the exact tagged native algorithm over the independently
approved target, sorted/lowercased categories and strategies, objective budget and
six-decimal threshold) and `redteam_fail_threshold`.
No adapter parses config or mints these approvals from `latest`.

## Same-process observation and observed-run record

The packaged runtime calls these shared APIs only after checking existing
execution/identity/export/retention approval:

1. `begin_observation(repo, root, *, operation, run_id_sha256, approval_sha256,
   artifact_paths)` captures committed scope, clean source state, configuration,
   before-hashes and actual start time. It returns an opaque one-use in-process
   token; it is not an execution authorization.
   Eval requires `dataset`, `analysis` and `latest` paths; an explicit `result`
   path is optional. Without it, completion identifies the unique newly written
   native result matching latest. Begin hashes preexisting result candidates
   first; replaying their unchanged bytes, even through a refreshed latest alias,
   is rejected. Snapshot/discovery reads are bounded to 4,096 entries and 32 MiB.
   Native run directories are mutable; hashes do not make them immutable.
   Doctor reuses missing eval paths from the
   already validated prior receipt, never from unbound native files.
2. Execute the approved command through the real bounded runtime runner. Do not
   print raw stdout/stderr; unset `GITHUB_STEP_SUMMARY`.
3. `finish_observation(token, *, exit_code)` accepts completed exit 0 or preserved
   exit 2, checks unchanged inputs, new output hashes and native timestamps inside
   the actual invocation, and writes a receipt. Other failures cancel observation.
   `cancel_observation(token)` releases a failed/abandoned capture.
   Exit 2 additionally requires internally valid negative artifacts: failed eval
   outcome, or actual Doctor findings that can trigger the approved severity
   floor. Success-shaped artifacts cannot be used as an exit-2 fallback.

The actual native package/identity checks belong to the runtime before capture,
not a constant field in a receipt. Offline tests use a clearly synthetic command,
not a claim that the native package or Azure executed.

The record retains the following compatibility fields:

```json
{
  "schema": "threadlight-agentops-run-receipt/v1",
  "producer": "observed-run",
  "operation": "eval",
  "repository_commit": "<full current clean commit>",
  "root": ".",
  "config_sha256": "<hash of agentops.yaml bytes actually used>",
  "target_sha256": "<independent policy hash>",
  "environment_sha256": "<independent policy hash>",
  "package_version": "0.14.0",
  "upstream_sha": "fb5c93eee489c71ef4084fa209adae24f762e3d7",
  "started_at": "<RFC3339 before eval execution>",
  "finished_at": "<RFC3339 after the captured run and Doctor>",
  "run_id_sha256": "<hash of existing run identity>",
  "approval_sha256": "<hash of existing scope/identity/export/retention approval>",
  "artifacts": {
    "result": {"path": ".agentops/results/2026-09-08T10-00-00Z/results.json", "sha256": "<hash>"},
    "latest": {"path": ".agentops/results/latest/results.json", "sha256": "<same hash>"},
    "evidence": {"path": ".agentops/release/latest/evidence.json", "sha256": "<hash>"},
    "history": {"path": ".agentops/agent/history.jsonl", "sha256": "<hash>"},
    "analysis": {"path": ".agentops/operations/eval-analysis.json", "sha256": "<hash>"},
    "dataset": {"path": "data.jsonl", "sha256": "<hash>"},
    "workflow": {"path": ".github/workflows/threadlight-ci.yml", "sha256": "<hash>"}
  },
  "observation": {"schema": "threadlight-agentops-observation/v1"}
}
```

The illustrative `observation` above is abbreviated: only the runtime produces
it. Actual fields include producer-code hash, invocation times/exit, before-hashes,
scope hash and record digest. Doctor observations retain the previously validated
eval binding, never retroactively bind unobserved eval files. Do not hand-author
the observation object or regenerate timestamps on old native outputs.

Every artifact reference is agent-root-relative except `workflow`, which is
**repository-root-relative**, including for nested agents. The six roles through
`dataset` shown above are conventional; `result`, `latest`, `analysis`, `dataset`
are required. `evidence` and `history` are an optional pair for eval-only capture,
mandatory for explicit Doctor refresh. Unchanged old Doctor artifacts are not
attached to a new eval observation. `workflow`, `baseline`, `redteam` are optional. Missing
workflow execution association remains partial. The native dataset path must
match the observed local snapshot path (either that relative path or its exact
in-root absolute resolution, checked privately); remote/non-local dataset evidence remains
unverified until an approved local snapshot binding is available. Native absolute
dataset paths outside the selected root are not rewritten to manufacture a match.
If `.agentops/agent.yaml` exists, an additional `doctor_config` artifact reference
to that exact path is mandatory so changing Doctor configuration cannot evade
the run binding.

## Optional existing-owner signatures

To require authenticated imported provenance, explicitly set
`require_signature: true` and `trust_key` to an existing committed public-key
path in the binding policy. This opts out of the ordinary unsigned capture route.
Private signing keys remain in existing protected runner/release controls, never
the repository. Optional-signature tests generate disposable synthetic keys only.

The existing signing service signs the **exact record bytes** using its existing
RSA/SHA-256 controls. The verifier uses OpenSSL
`dgst -sha256 -verify <committed-public-key> -signature <detached-signature> <record>`.
No new key is generated or enrolled by the bundler. The record, signature and
original payloads stay in existing restricted storage and retention controls;
the normalized manifest only contains their digests.

Import using [the skill's bundling command](../SKILL.md). It verifies before
creating the conventional receipt files with restrictive permissions and
exclusive creation, verifies again after installation, and removes only files
it created if installation fails. Existing files require prior owner-managed
archival; they are never overwritten. Each assessment and downstream validation rechecks local observation consistency
or the selected signature policy, plus original source hashes.

## Limitations are explicit

- Approval hashes identify an already approved run; they are not permissions to
  execute another run. The adapter never turns receipt presence into execution
  authorization.
- Local observations are **not Azure authentication, cryptographic attestations
  or proof against a malicious actor controlling the local host/files**. Their
  purpose is correct observed-run provenance and fail-closed import semantics
  under the existing trusted runner. Native files without a captured observation
  remain unverified; the packaged capture route rejects unchanged old outputs.
- Optional signatures also require actual observations, not subjective latest
  assertions. A compromised approved signing authority cannot be detected by
  local signature verification.
- Repository commit trust is inherited from the existing reviewed checkout and
  release controls. This module does not authenticate arbitrary Git history.
- Exact artifact hashing does not make mutable artifacts immutable. Archive under
  existing controls before subsequent runs mutate them.
- Dirty source state has no implicit exemption. Exact generated lifecycle outputs
  are excluded so same-pass report emission does not masquerade as a source
  change: `specs/{agentops,evals,redteam,governance}-manifest.json`,
  `docs/{evals-report,redteam-report,agt-governance-report}.md`, and
  `.threadlight/{auto-state,auto-next}.json`. No directory-wide exemption applies;
  source, deployment metadata, binding policy, keys and configuration remain
  inputs. These report/state paths are reserved outputs, not supported run inputs;
  their own canonical validators still apply. Exported `GENERATED_OUTPUTS` is the
  exact implementation allowlist.
- Native 4/5 quality plus a valid blocked Doctor cycle is a negative-path result,
  not readiness. Existing historical live results are not rebound or relabeled.
