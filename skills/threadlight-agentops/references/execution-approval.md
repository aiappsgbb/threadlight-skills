# Existing-owner approval for the packaged native observer

`scripts/native_observer.py` is the single native execution implementation.
The CI runtime is a thin caller; it creates no external observer, signer, login
or infrastructure. Default assessment remains read-only.

An approval file represents a decision already made through the owner's existing
process. The helper never creates approvals. Local booleans/hashes do not
authenticate an Azure identity or a malicious local host. Native execution and
Doctor source diagnostics must independently succeed; local observation is not
attestation or certification.

## Before execution

Commit the independent `.threadlight/agentops-binding.json`. For runtime use,
include `artifact_paths: {"dataset": "data.jsonl"}`; optional `workflow` is
repository-relative and `baseline` is root-relative. A selected baseline requires
the existing baseline hash/target/dataset approval fields; a bound baseline
cannot be silently omitted or promoted.

Use already installed `agentops-accelerator==0.14.0` and `azure-identity~=1.25.3`.
The selected native executable must be in the runtime interpreter's environment.
The observer installs nothing. Preserve the approved `AZURE_TOKEN_CREDENTIALS`,
isolated absolute `AZURE_CONFIG_DIR`, and credential-empty `AZD_CONFIG_DIR`.
Non-CLI credential selectors also require an empty CLI directory. No global
cache fallback or new login is enabled to repair a failure.

The owner must approve actual telemetry destination, payload capture and
retention, including native auto-discovery, **before** execution. Private logs
are not a network-export opt-out. Native `.agentops/` outputs must already be
excluded from source control and protected by existing restricted retention.
Prepare Doctor configuration before the eval capture if Doctor will be used;
changing captured configuration invalidates the corresponding old binding.

## Current-run approval

Each agent root supplies `.agentops/threadlight/approvals/eval.json` or
`doctor.json`, bounded to 64 KiB:

| Field | Required meaning |
|---|---|
| `schema` | `threadlight-agentops-runtime-approval/v1` |
| `approved`, `telemetry_scope_approved` | Explicit booleans `true` from the existing owner process |
| `operation` | Requested `eval` or `doctor` |
| `root`, `repository_commit` | Selected root and current clean source commit |
| `config_sha256` | Exact approved opaque `agentops.yaml` bytes |
| `target_sha256`, `environment_sha256` | Independent committed policy values, not native-derived identity |
| `run_id_sha256` | `native_observer.run_identity(operation)` for current CI run/attempt |
| `context_sha256` | `native_observer.execution_context_hash(repo, root, environment)` after owner-approved setup |
| `not_before`, `expires_at` | Current timezoned RFC3339 interval, at most 24 hours |
| `raw_artifacts` | `{scope: "<root>/.agentops", retention_hours: <1..720>}`; root `.` uses `.agentops` |

Context hashing includes selected environment values, bounded dotenv files,
configurations and declared dataset/baseline/workflow bytes without publishing
their contents. `GITHUB_STEP_SUMMARY`, `BASH_ENV`, `PYTHONPATH` and `PYTHONHOME`
are removed from native children; user site packages are disabled.
`AGENTOPS_AGENT` overrides are refused.

GitHub run/attempt or Azure DevOps build/attempt identity is used when present.
For a locally approved technical smoke, the owner supplies a fresh non-secret
`THREADLIGHT_AGENTOPS_RUN_ID`. Approval is one-use per root/operation. Failures
and timeouts consume it too; retries need a newly approved run/attempt.

## Actual commands and capture

Eval first runs native `eval analyze --dir . --format json` and requires genuine
version-1 ready config/dataset status, then `eval run --config agentops.yaml`
with the explicit baseline if selected. It never implicitly runs Doctor or
red-team. Native default result paths are discovered by a bounded, unique
byte-for-byte match to the new latest mirror, not invented from a timestamp.

Doctor requires an existing validated eval receipt and explicit
`.agentops/agent.yaml`, then runs
`doctor --workspace . --config .agentops/agent.yaml --evidence-pack`.
It cannot bless previously unbound eval files.

The same process brackets actual bounded commands with shared observation
begin/finish calls. Input stability, current scope, new output hashes and native
timestamps within the invocation are required. Native exit 2 remains a negative
gate, not success. Raw stdout/stderr are bounded and retained privately.

Keep originals until all canonical consumers and readiness have completed
their hash checks. Cleanup afterward follows the owner's existing retention
decision; do not upload raw payloads as public workflow artifacts.
