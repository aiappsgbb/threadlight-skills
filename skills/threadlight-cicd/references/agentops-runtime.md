# AgentOps observation in the existing CI workflow

The generated `.threadlight/skills/threadlight-cicd/scripts/agentops_runtime.py`
is a thin entry point to **one** packaged implementation:
`threadlight-agentops/scripts/native_observer.py`. The skill's Doctor refresh,
the CI entry point and an explicit `--agentops-bin` selector all use this same
implementation and approval contract. No external observer, signing service or
new PKI is required.

A **same-process** receipt is local provenance, **not remote attestation**,
authentication proof or production certification. Imported unsigned native files
alone remain unverified. Do not manufacture a new observation from old `latest`
files.

## Prerequisites

Review and commit the generated tooling/workflow and the per-agent
`.threadlight/agentops-binding.json`. It independently binds target, environment,
metric policy and root-relative dataset/baseline paths; workflow paths are
repository-relative. See the bundled AgentOps receipt contract. Existing
signature-required bindings are an optional separate import route, not silently
converted to unsigned observations.

Use Python 3.12+, `agentops-accelerator==0.14.0`, approved `azure-identity` 1.25.x
(at least 1.25.3), and PyYAML for bounded azd metadata parsing. Native
`agentops.yaml` is opaque to Threadlight. The executable must be the installed
`agentops` in the current Python environment; an explicit selector changes the
path, not the implementation or authorization rules.

Preserve existing OIDC/WIF identity and environment approvals. Require distinct,
existing absolute `AZURE_CONFIG_DIR` and `AZD_CONFIG_DIR`, with no symlinks.
AZD remains credential-empty; non-CLI selectors also require an empty CLI
directory. `AZURE_TOKEN_CREDENTIALS` is explicit. No login, account switch,
resource creation, RBAC change or Citadel operation occurs here.

## Existing owner approval

Supply `.agentops/threadlight/approvals/eval.json` or `doctor.json` privately in
each opted-in root. This record represents an existing explicit authorization;
the tool never creates one. Approve inference cost, actual telemetry/export
destinations, payload capture, access and retention **before native analysis**,
which can itself inspect remote datasets.

```json
{
  "schema": "threadlight-agentops-runtime-approval/v1",
  "approved": true,
  "telemetry_scope_approved": true,
  "operation": "eval",
  "root": ".",
  "repository_commit": "<current clean commit>",
  "config_sha256": "<agentops.yaml SHA-256>",
  "target_sha256": "<independently approved binding-policy target hash>",
  "environment_sha256": "<independently approved binding-policy environment hash>",
  "run_id_sha256": "<native_observer.run_identity(operation)>",
  "context_sha256": "<native_observer.execution_context_hash(repo, agent_root, environment)>",
  "not_before": "<RFC3339 UTC or offset>",
  "expires_at": "<RFC3339, at most 24 hours after not_before>",
  "raw_artifacts": {"scope": ".agentops", "retention_hours": 24}
}
```

For nested agents, `root` and `raw_artifacts.scope` name that root and its
`.agentops` directory relative to the repository. For Doctor, use operation
`doctor`; optional `doctor_severity` is `critical` (default), `warning` or `info`.
The explicit `.agentops/agent.yaml` controls the approved source/lookback policy.
The approval and its complete bytes are rechecked around native calls.

`run_identity(operation)` hashes existing GitHub repository/run/attempt or ADO
project/build/attempt plus operation. Local approved execution instead needs
`THREADLIGHT_AGENTOPS_RUN_ID`. The single-use marker is per agent root and
operation/run, including failed attempts. A retry requires another explicit
authorization; there is no automatic native replay.

`execution_context_hash(repo, agent_root, environment)` fingerprints relevant
runtime variables, repository and agent dotenv/config files, dataset and baseline
bytes privately. Computing a hash does not verify or approve a destination.
The committed binding's `artifact_paths` supplies the dataset, workflow and
optional baseline; missing or mismatched binding fails before execution.

An existing baseline cannot be silently omitted or promoted. Its content,
approved target and dataset hashes must match. Eval requires fresh comparison
evidence; Doctor may retain older baseline evidence without refreshing its age
or executing another eval.

## Commands and outcomes

The observer invokes preset native commands, not commands from native output:

```text
agentops eval analyze --dir . --format json
agentops eval run --config agentops.yaml [--baseline ./<approved-relative-path>]
agentops doctor --workspace . --config .agentops/agent.yaml --evidence-pack --severity-fail <approved-floor>
```

Eval and Doctor are separate approved operations. Eval-only never implicitly
runs Doctor; Doctor refresh requires the previously observed eval binding.
The receiver verifies new outputs, source timestamps, scope and hashes before
publishing `specs/agentops-manifest.json`. Neither exit zero nor a partial
manifest substitutes for verified execution.

The CI entry point preserves observed exit **2**, including an explicitly
approved warning/info Doctor floor; execution or prerequisite failure is **1**.
Quality failure is also preserved in the canonical eval manifest, not rewritten
as a pass. Native readiness and quality remain separate from a technically
complete skill smoke.

## Private capture and canonical consumption

Output is bounded and retained privately under the approved root. Native payloads,
approval files, secrets and raw reports never enter CI logs, step summaries or
public artifacts. `GITHUB_STEP_SUMMARY` and interpreter/shell injection settings
are removed from native subprocess environments. Only allowlisted normalized
metadata may be published.

Keep originals until the source-validating consumers finish, then apply the
approved retention policy. Deleting files or an agent does not prove Responses
purge or remote attestation.

After normalization the existing canonical assessor consumes the batch:

```text
python3 .threadlight/skills/threadlight-evals/scripts/evals_check.py --target . --emit
```

No duplicate batch or automatic baseline promotion is introduced. Threadlight
owns generated workflows, eval/red-team policy and final production assessment;
AgentOps does not replace their gates.
