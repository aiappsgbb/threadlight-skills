# Verified release contract

The default pipeline prepares a **preproduction candidate**, executes evaluation,
red-team and MCP producers, and only then admits a separate production job.
Production approval belongs to the CI environment owner. A declared environment
in YAML does **not** configure its approval or branch checks.

## Before enabling the workflow

1. Configure distinct validation/production environments, deployment identities,
   RG-scoped permissions and private runners where required. Do not grant the
   validation identity production or central-hub access.
2. Protect the main branch, generated tooling, release policy and producer code.
   Require independent production-environment approval and restrict its federated
   credential to that environment. Do not create a main-branch credential that
   bypasses the environment.
3. Copy `specs/release-policy.example.json` to `specs/release-policy.json`, replace
   every placeholder, and commit the reviewed deployment adapters, datasets,
   thresholds and toolchain lock files. The example is deliberately not an
   executable policy. Missing configuration fails before deployment.

Both CI systems use one validation job and one dependent, protected production
job. CI artifacts contain metadata and hashes only; model responses, attack
payloads, credentials and raw diagnostics must not be published.

## Application-owned adapters

The runner invokes explicit argv arrays with `shell=False`. It is not a new
Azure deployment engine, model evaluator or attack scanner. The adapters are
reviewed application code using the actual selected SDK/CLI and registered
services; substituting an assessor or old report for execution is not supported.

| Adapter | Required behavior |
|---|---|
| `validation.prepare` | Build/deploy the current source into the declared validation target using its approved toolchain. Do not touch production. |
| `observe` | Independently read the registered deployment's current source, immutable image digest and version. Never echo the requested target as if observed. |
| `evals.producer` | Execute the actual evaluator, retain its raw current run, then invoke the canonical `evals_check.py` assessor. Existing AgentOps evaluation remains the native owner when selected. |
| `redteam.producer` | Execute the approved scanner against the candidate, retain its raw current scan, then invoke `redteam_check.py`. A report formatter is not the scan. |
| `production.promote` | Reuse the validated immutable image, not rebuild mutable source tags. Consume the supplied stable `operation_id`; enforce backend idempotency and reconcile unknown outcomes. |

MCP discovery/checking uses the supplied `mcp_sbom.py` directly and does not
refresh the lock automatically. Install the approved application/producer
dependencies on the runner; the CI generator does not invent customer adapters,
run credentials, remote datasets or paid-probe authorization.

The private `THREADLIGHT_RELEASE_REQUEST` file contains this CI context,
the approved target and the observed candidate. Treat it as host-owned input,
not a model-editable document. An observer returns:

```json
{
  "schema": "threadlight-deployment-observation/v1",
  "target_id": "the independently observed deployment resource ID",
  "environment": "validation",
  "source_sha": "40-character source commit",
  "image_digest": "sha256:64-character-image-digest",
  "version": "the observed registered version",
  "observed_at": "2026-09-16T10:00:00Z"
}
```

For opted-in AgentOps, the generated example selects the supplied
`release_agentops.py` bridge, not another evaluator. The independent observer
must additionally return `evaluation_targets`, mapping every discovered
`agent_key` to the SHA-256 of its actual native target object. The bridge checks
the existing committed native binding against that observation before invoking
`agentops_runtime.py`, then verifies current native receipts and calls the
canonical evaluator assessor. It records metadata linking the native receipt
to this release, without inventing quality metrics or changing upstream wire
models.

The existing pinned native runtime, fresh scoped owner approval, isolated
credential contexts and private retention remain prerequisites. In particular,
native AgentOps requires the approved AZD credential directory to be empty;
do not delete an application's populated azd context to satisfy this. Prepare
the separate approved native context when that deployment adapter uses azd.
Generated native outputs must be Git-ignored while policy, datasets and tooling
remain committed.

The raw evaluation/scan output must identify its real `finished_at` and carry
`release_binding: {"ci": request.ci, "candidate": request.candidate}` alongside
the provider's data. The canonical assessor must reference that exact output
through `metrics.latest_run` or `scan_result`. Listed transient outputs are
removed before the producer runs; input datasets, code and retained historical
archives are not cleanup targets. Remote datasets require immutable version/
content verification in the adapter, not a mutable URI relabeled as evidence.

## What blocks promotion

Required evidence must be complete, within the current execution window and
bound to the observed candidate, source, inputs, policy, CI run and attempt.
The canonical reader rejects missing counts, contradictory findings, unknown
required capabilities, unmet quality thresholds, stale scans, insufficient
attack volume and missing attack categories.

All capabilities are required by default. An explicit reviewed evaluation
`required_capabilities` list can leave optional adoption controls, such as A/B
comparison, outside this release decision. Dataset, execution, freshness and
quality checks cannot be omitted. This is not blanket acceptance of `partial`.
The manifest's broader verdict is not rewritten.

Only a successful validation writes the candidate receipt. Its exact SHA-256
travels separately through the validation job's output; the promotion job checks
it before consuming the artifact. A receipt from another source/run/attempt,
an expired approval wait, changed input or a failed required domain blocks
promotion. Rerun the complete validation rather than relabel old evidence.

After promotion, the production observer must confirm the same image digest.
A failure is **not rollback**. The runner never retries promotion automatically;
its local attempt record is not a distributed idempotency service. The actual
promotion adapter must fence the stable operation ID in its durable backend,
including across runner loss. Keep business routing closed until that adapter's
required post-deployment checks complete.

## Separate acceptance boundaries

This is a trusted-CI execution contract, not a signed external attestation or
whole-application production certification. Protected branch/environment
configuration, runner isolation and honest independent observers are explicit
trust assumptions.

Selected action governance still requires its own current signed bootstrap,
policy, identities, registered service evidence and application acceptance.
Use the supported `resume-signed-bootstrap/v1` route where applicable; do not
revive the rejected legacy azd register/bind/start sequence. Candidate evaluation
does not substitute for fresh production governance evidence, business-write
proof, data authorization, capacity, incident response or rollback readiness.
