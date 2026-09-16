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
   thresholds and toolchain lock files. Set each target's distinct deployment
   `client_id`, tenant and subscription. The example is deliberately not an
   executable policy. Missing configuration fails before deployment.

Both CI systems use one validation job and one dependent, protected production
job. CI artifacts contain metadata and hashes only; model responses, attack
payloads, credentials and raw diagnostics must not be published.

## Operator sequence

This contract is copied into the application repository. It describes commands
that the generated workflow invokes, not permission to run a deployment locally.

1. Review the generated pipeline, both environment-setup directories and
   `specs/release-policy.example.json`. Supply actual adapters and locked
   dependencies, then commit the reviewed `specs/release-policy.json`.
2. Have the platform owner configure federation, isolated validation scope,
   production environment checks and approved runner preparation. The generated
   example is not an operational default; do not run setup scripts without approval.
3. Start the authorized workflow from protected main. `preflight` checks
   configuration, entrypoints and committed source in the real CI context;
   it does not authenticate or execute the adapters. Do not forge CI variables
   to simulate authority on a laptop.
4. Let `validate` prepare and observe the candidate, execute all required
   producers and emit `.threadlight-release/candidate.json` only on acceptance.
   The workflow carries its SHA-256 separately as a validation job output.
5. After production-environment approval, `promote` receives that output through
   `--receipt-sha256`, rechecks the receipt and current authorization, invokes
   the application promotion adapter and observes production. Business routing
   and broader go-live acceptance remain application-owned.

The policy's `max_age_seconds` covers the original validation window, including
subsequent approval waits. It is not reset when the receipt is downloaded.
Review this bound against expected execution/approval time before running;
never extend it or rewrite timestamps to rescue an already expired attempt.

## Application-owned adapters

The runner invokes explicit argv arrays with `shell=False`. It is not a new
Azure deployment engine, model evaluator or attack scanner. The adapters are
reviewed application code using the actual selected SDK/CLI and registered
services; substituting an assessor or old report for execution is not supported.
Preflight checks executable availability and explicit entrypoint files.
Python, Bash, sh and Node commands must name a committed project script; inline
code and Python `-m` adapters are not supported. Wrap such application commands
in a reviewed entrypoint. Use canonical relative paths without `./` aliases.
Python entrypoints are syntax-checked without executing them. Dependencies still
belong to the reviewed application toolchain; preflight is not an SDK smoke test.

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
The GitHub workflow installs its supplied azd tool before executable preflight;
other application dependencies and ADO runner tools must already be prepared.
Main-branch prompt/document changes are not blanket-skipped: Markdown can contain
executable agent instructions, not just documentation.

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
Supply it as an absolute private regular file through
`THREADLIGHT_AGENTOPS_CONTEXT`; native execution replaces inherited deployment
credential/export variables with that context's explicit values and restores
them afterward. The generated environment-specific variables are
`THREADLIGHT_AGENTOPS_VALIDATION_CONTEXT` and
`THREADLIGHT_AGENTOPS_PRODUCTION_CONTEXT`. Neither the file nor its path creates
approval. Follow the [native runtime contract](agentops-runtime.md) for preparation,
package requirements, one-use scope and production Doctor prerequisites.
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
Any canonical `must_fix` still blocks, including continuous-evaluation wiring
findings; explicit scope excludes optional incomplete adoption, not must-fix
findings or required core checks.

Only a successful validation writes the candidate receipt. Its exact SHA-256
travels separately through the validation job's output; the promotion job checks
it before consuming the artifact. A receipt from another source/run/attempt,
an expired approval wait, changed input or a failed required domain blocks
promotion. Rerun the complete validation rather than relabel old evidence.
The authenticated service principal must match the selected target's `client_id`.
Source, policy, receipt/input hashes and freshness are checked again after
identity waits and immediately before promotion dispatch. Validation likewise
rechecks current policy, inputs and CI context after credential waits.

After promotion, the production observer must confirm the same image digest.
A failure is **not rollback**. The runner never retries promotion automatically;
its local attempt record is not a distributed idempotency service. The actual
promotion adapter must fence the stable operation ID in its durable backend,
including across runner loss. Keep business routing closed until that adapter's
required post-deployment checks complete.

## Stop and recover

| Failure point | Meaning | Operator action |
|---|---|---|
| Preflight or identity check | The reviewed configuration, source, executable or principal is not acceptable; this is not a completed release | Correct the actual prerequisite through review and start a new authorized run |
| Candidate preparation or required evidence | A validation target may exist, but no new accepted candidate receipt is available | Inspect private diagnostics, fix the cause and rerun complete validation; do not substitute a saved report |
| Approval wait, checksum, inputs or attempt mismatch | Previous validation no longer authorizes this promotion | Run complete validation again under the current source and policy; do not retry only promotion with stale authority |
| Promotion timeout or runner loss: outcome unknown | The backend may have committed an effect even if CI is red | Keep traffic closed; reconcile the durable adapter operation record and actual target before another attempt |
| Production observation mismatch | Promotion ran, but the observed image/source/target is not the accepted result | Stop and reconcile through the application runbook; automatic rollback is not provided |

The local attempt file is conservative retry protection, not proof of a durable
remote result. Do not delete it to force a retry. Backend reconciliation,
rollback decisions and any resource cleanup need their own authorization;
they are not implied by a failed CI job.

## Separate acceptance boundaries

This is a trusted-CI execution contract, not a signed external attestation or
whole-application production certification. Protected branch/environment
configuration, runner isolation and honest independent observers are explicit
trust assumptions.
The exact pinned native CLI is locally exercised with an HTTP loopback target
and deterministic F1 evaluator through the bridge and canonical acceptance.
Scheduled wiring in that fixture is inventory, not an executed schedule.
Local adapter fixtures and this native result are not Azure deployment,
production Doctor, governance runtime or live business acceptance.

Selected action governance still requires its own current signed bootstrap,
policy, identities, registered service evidence and application acceptance.
Use the supported `resume-signed-bootstrap/v1` route where applicable; do not
revive the rejected legacy azd register/bind/start sequence. Candidate evaluation
does not substitute for fresh production governance evidence, business-write
proof, data authorization, capacity, incident response or rollback readiness.
