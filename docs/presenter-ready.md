# Presenter-ready PoC delivery contract

`presenter-ready` is an **explicit opt-in delivery profile**, not production
certification, a governance mode, a runtime choice, or permission to deploy.
Offline verification is not hosted acceptance.
A process owner owns the whole first usable journey. A catalog publisher may
own packaging, but consumes the process handoff rather than inventing another
description, script, availability window or sizing story.

Select it in the existing `specs/manifest.json`:

```json
{"delivery_profile": "presenter-ready"}
```

Preserve the rest of that manifest, including `deployment_manifest` and selected
governance. Absence or `"default"` keeps the existing pipeline. An invalid explicit
value is blocked, not silently off. The one process-owned handoff is
`specs/presenter-contract.json` (`threadlight-presenter-contract/v1`).
SPEC, workspace copy, executable rehearsal and publishing consume this contract;
they are views, not alternative authorities. Do not copy another process's rules,
data or receipts when reusing its method.

## Design the first journey, not a list of backends

Keep the business description to a page. For example: **Northstar Retail**, a
fictional retailer; a returns reviewer inspects an order and edits a draft
disposition. Deterministic eligibility rules bound the options; the agent explains
the evidence and proposes wording. The result is a saved synthetic draft, **not
settlement or a refund**. A human remains responsible for approval outside the PoC.

Build this first vertical before adding secondary backends:
entry -> useful interaction -> explicit persistence where applicable ->
independent readback -> reopen. A read-only, deliberately nonpersistent process
must explain the exception; do not invent a business write to satisfy the profile.
Include downloads **only if promised**. These exceptions do not weaken selected
governance, auth or real business-action controls.

The JSON handoff has these required fields (unknown business detail must be
resolved in design, not replaced by a green receipt):

| Field | Content |
|---|---|
| `schema`, `process_id`, `owner` | `threadlight-presenter-contract/v1`, stable process identity, accountable process team |
| `description` | Text: `role`, `problem`, `fictional_company`, `agent_contribution`, `outcome`, `human_responsibility`; nonempty string lists: `inputs`, `deterministic_rules`, `inclusions`, `exclusions` |
| `journey` | Text: `entry`, `interaction`, `recovery`; booleans: `persistence`, `download`. Persistence requires `store`, `readback`, `reopen`; otherwise `nonpersistent_reason`. Download requires `download_artifact`. |
| `availability` | Text: `presenter_access`, `historical_read`, `session`, `source_revision`, `preparation`, `concurrency`, `idempotency`, `uncertain_effect`; timezone-aware `effective_at`, `expires_at` |
| `deployment` | Pinned `guidance` object below; explicit `consumer`, `manifest`, `runtime_root`, `entrypoint`, `lockfile`, `adapter`, `protocol`, `protocol_version`, `model_env`; unified azd also `service`; native SDK also `create_entrypoint` |
| `inputs` | Nonempty path lists for `runtime`, `interface`, `source`, `script`, `sizing`. Include **whole source directories**, not a hand-picked subset; files outside those directories must be listed explicitly. Paths are workspace-relative, no symlinks or escapes. |
| `evidence_max_age_hours` | Positive finite hours (at most 8760), agreed with the process owner for hosted rehearsal/human evidence; not source TTL, session TTL or permission duration |
| `sizing` | `status`: `proposed` or `measured`; explanations for `model_tokens`, `model_rounds`, `logical_tool_calls`, `resource_units`, `comparison`; explicit `unpriced` list (empty only if everything is priced) |
| `publication` | `files`: every promised published file; `bundles`: `{path, source_root}` for each ZIP, whose complete source subtree must match its contents |

The executable contract fixtures in
[`test_presenter.py`](../skills/_shared/tests/test_presenter.py) show two independent
PoCs and both deployment consumers. They are test data, **not customer acceptance**.
Populate the handoff from the actual SPEC and chosen runtime; do not promote
these fixture receipts.

## One versioned deployment authority

Design, Deploy and Safe Check read
[`presenter-deployment-pin.json`](../skills/_shared/presenter-deployment-pin.json).
Copy that exact object to `deployment.guidance`. It selects
[awesome-gbb's hosted-agent guide at an immutable commit](https://github.com/aiappsgbb/awesome-gbb/blob/2a29e084882a52e74b94def3ee9bb32b73592e60/skills/foundry-hosted-agents/SKILL.md).
Its relative templates, deployment-preflight and private/brownfield instructions
must be read at **that same commit**, not a mutable installed companion.

For standalone authoring bundles, this checked copy of the guidance object is
kept equal to the shared pin by the catalog's technical-guidance test:

<!-- deployment-guidance -->
```json
{
  "repository": "aiappsgbb/awesome-gbb",
  "commit": "2a29e084882a52e74b94def3ee9bb32b73592e60",
  "path": "skills/foundry-hosted-agents/SKILL.md"
}
```

The Cowork Design ZIP includes this guide, not the executable shared validator or
other skills. Repository-relative code/test links below are engineering handoff
references: use a complete pinned catalog for execution.

This is deployment **layout guidance**, not a new SDK/runtime pin. Existing
Foundation/runtime-policy and selected governance cohort remain authoritative.
Do not adopt upstream model recommendations, upgrade SDKs, overwrite SDK files,
change a governance pin, or switch protocols to make a check pass.

| Consumer | Single definition and validation |
|---|---|
| `unified-azd` | Root `azure.yaml`; selected `services.<service>` has native top-level `host: azure.ai.agent`, `project`, `kind`, `protocols`, and **list-shaped** `environmentVariables`. No `config` wrapper. Project points to the declared runtime root. Use the pinned upstream template. |
| `native-sdk` | One frozen native create-definition JSON at the declared `manifest` path, with native `kind`, `protocol_versions`, `environment_variables`; the declared `create_entrypoint` is packaged and fingerprinted. No competing root `azure.yaml`. Retain native creation/provisioning provenance and use the upstream approved raw/brownfield path. This is not automatic certification of arbitrary SDK definitions. |

Native-SDK projects can explicitly select the bounded ACA consumer below, without
regenerating the incumbent agent or migrating it to azd. Unchecked ancillary
services and scheduled jobs remain unsupported. Existing single-agent native
contracts and the default/unselected profile are unchanged.

### Independently validated native ACA services

Add `deployment.service` for the native agent's logical inventory name and a
nonempty `deployment.ancillary_services` list in the **same** process contract.
Each record has exactly these fields:

| Field | Rule |
|---|---|
| `name`, `resource_name` | Unique logical inventory name and unique native ACA resource name. Two services may share `src: "."`; never deduplicate by directory. |
| `host`, `consumer`, `role` | `containerapp`, `containerapp-arm`, and `mcp` or `workspace` respectively. Other hosts/consumers/jobs require a separate reviewed extension. |
| `src` | Existing build-context directory, matching the canonical `deployment_manifest.services` entry after optional `./` normalization. |
| `manifest` | Path to the frozen native ARM resource JSON, not an azd wrapper or a CLI output envelope. |
| `create_entrypoint`, `lockfile`, `adapter`, `dockerfile` | Existing retained creator, exact SDK/toolchain lock, service package-test adapter and service-specific Dockerfile. All must exist; Dockerfile must be inside the build context. |
| `inputs` | Complete source directories plus explicit files outside them, relative to the project, inside the build context. At least one source directory is required. |

The canonical inventory must contain **exactly** the agent and every declared ACA
service, with matching names, hosts and build-context paths. Missing, duplicate,
extra, unsupported or mismatched entries fail; omission of the inventory fails.
Keep the real agent/MCP/workspace inventory even when a service lacks proof.
No second deployment manifest or orchestration engine is generated.

The ACA resource must have `type: Microsoft.App/containerApps`, an explicit dated
`apiVersion`, the matching `name`, `location`, managed identity and
`properties.managedEnvironmentId`. This bounded consumer supports one container,
no init containers, one immutable `repository@sha256:<digest>` image, and explicit
ingress `external`/integer `targetPort`. An optional single traffic record must
route 100 percent to the latest revision or one named revision; a named revision
must match the observed target version. Traffic-split configurations are rejected.
User-assigned identity selections require a nonempty identity map. These are
structural discriminators from the
[native ARM resource shape](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/2025-01-01/containerapps),
**not a full ARM schema validator or a new API/SDK recommendation**. The package
producer must validate the complete frozen definition against its actual declared
API version and locked native SDK/toolchain; unsupported fields/configuration must
fail that native test. Do not strip fields, change an API version or replace
identities just to fit this bounded consumer.

For a root build context, `inputs` names the real source subtrees (for example
`src/mcp`, `src/workspace`, shared `config`) and root-level imported files, not
the entire project `"."` containing mutable receipts. This separates build context
from source roots; it is not permission to hand-pick a passing subset. The
creator/adapter/Dockerfile/lock/definition and both context `.dockerignore` and
Dockerfile-specific ignore files are fingerprinted automatically when present.
The producer must verify complete packaged imports/assets against the actual
built image. Root source-file omissions cannot be inferred by a metadata reader;
they remain a producer validation responsibility. Keep execution outputs outside
declared source inputs to avoid self-referential receipt hashes.

The existing `package` and `deployment` receipts gain `facts.services`, keyed by
**every** ancillary logical name (no extra or missing names). Each entry binds
`manifest_sha256`, `adapter_sha256` and nonempty `evidence` path/SHA-256 references
to retained **service-specific** producer outputs. Package entries require all
`SERVICE_PACKAGE_CASES`: `schema_validated`, `image_runtime`, `startup`, `adapter`
set to true only after executing those checks. The adapter exercises the real MCP
request/rejection path or workspace origin/assets/MIME/download/auth boundaries,
as applicable to the promised journey. Static web preflight is not `image_runtime`.
Deployment entries require `observed: true` backed by authorized native
creation/provisioning and independently observed loaded-revision output. Old
agent-only receipts cannot stand in for these per-service results.

The current `.threadlight/presenter-target.json` retains the native agent tuple
and adds `services`, keyed by exactly those ancillary names. Each entry contains
its own `attempt`, `environment`, `version` (ACA revision), `image`, `identity`,
plus `resource_name` and `definition_sha256`. The name, immutable image and
definition hash must match the frozen service definition. Every hosted/human
receipt binds the entire target, including this map. A new ancillary attempt,
revision, configuration/definition, image or identity invalidates prior hosted
acceptance even when the agent is unchanged. Changed service sources invalidate
native and downstream evidence through the existing runtime fingerprint.

Service proof is still **recorded-not-independently-attested**: a matching hash or
boolean is not an independent attestation, permission, successful deployment or
human acceptance. Keep the real producer's SDK serialization, image identity,
UID/configuration, command/output and observation provenance under the existing
trusted evidence custody. Never fabricate new receipts from these fixtures or
borrow another process's proof. No live calls are made by this extension, and
no SDK, governance or upstream deployment pin is changed.

The bounded consumer checks support Responses/Invocations protocol `2.0.0`;
the process's exact SDK/host still needs native compatibility proof. Retained
unsupported layouts require an explicit migration/extension decision, not
automatic deletion. Neither consumer may leave active root/runtime `agent.yaml`
or `agent.manifest.yaml` definitions competing with it. Archives belong outside
active runtime roots and remain historical.

Use the same user-declared model environment name in manifest, entrypoint and
test adapter. Never declare platform-injected `FOUNDRY_*`, `AGENT_*` or
`APPLICATIONINSIGHTS_CONNECTION_STRING` in the user environment. Do not store
environment values, tokens or personal targets in the handoff/evidence index.
Local schema checks are **not** proof that runtime code reads an environment
variable: exercise that exact import/startup/dispatch path in the package test.

The unified Foundry provider can be Bicep-less. Explicitly ejected Bicep/Terraform
keeps its real infrastructure directory. The selected `deployment_manifest.services`
inventory must match the actual service names, hosts and project directories;
every declared packaged container must have its real Dockerfile. Do not generate
legacy `infra/main.bicep` just to satisfy a historical completeness heuristic.
Existing resource, reachability and selected-governance checks still apply.
Setup readiness and container startup are not requested-result verification.

Run Safe Check from a complete pinned catalog; a copied single-file checker with
no `skills/_shared` must fail the selected profile. Do not create a second local
deployment guide or a relaxed validator to bypass this dependency.

## Exact integration evidence, four different meanings

| Evidence | What actually runs | What it cannot prove |
|---|---|---|
| Offline | Schema, source packaging, deterministic fixtures, consumer/gate logic | Native SDK behavior or hosted execution |
| Native | The **packaged** framework, SDK, host/client and process adapter; deterministic model/transport only where explicitly declared | Cloud identity, mounted storage, service behavior or model quality |
| Hosted | Approved invocation through the real entry/runtime plus correlated service-side results and readback | Human usability or production-wide governance |
| Quality / human | Held-out semantic evaluation; separately, a first-time presenter's observed rehearsal and acceptance | A deployment, side-effect authorization or all-agent governance |

Record the command, selected dependency lock/cohort, imported package versions,
host/client/adapters, source inputs, execution output and limits. A stub with a
management-plane field is not a data-plane SDK conformance test. Use real SDK
objects/serialization and a loopback or injected transport at the network boundary,
not a replacement SDK model. Test the chosen client's actual extra metadata GETs,
retry count/backoff and dispatch IDs; guards must admit documented reads but must
not accidentally admit business writes. No blanket upgrade or broad rerun.

The native `package` check covers all eight cases:
`exact_sdk`, `response_shape`, `retry_dispatch`, `metadata_reads`, `pending`,
`terminal_stream`, `persistence_readback`, `recovery`. For unsupported features,
the test must verify the explicit rejection/no-effect path and document it.
Pending/approval-required is not success; timeout after send is uncertain.
Stream text is provisional until terminal validation succeeds. A late failure
must replace the successful-looking result, disable save/download and preserve
the operation correlation needed for reconciliation.

Readback must not merely echo the model response or return a client cache.
For persisted outcomes, retain result/operation IDs, query the backing store via
an independently scoped reader, then reopen from a fresh session. Compare the
stored revision/content and scope, not just a 200 response or matching result ID.
Test the real process adapter's response shapes and semantics, including rejected,
pending, interrupted, duplicate and reconciled operations. The guide does not
generate an adapter or execute these business probes for you.

Pattern 0 is still useful for local iteration. Its packaged native MAF tests
exercise real dispatch and terminal stream errors; its in-memory stores reset on
recreation. They **disprove durable save/reopen**, not satisfy it. If persistence
is promised, use the actual process store adapter rather than labeling Pattern 0
or a SQLite surrogate as Cosmos/hosted evidence.

## Business workspace and recovery are part of the first vertical

Design the workspace together with the initial journey, before calling the
backends complete. Use the chosen design tooling and existing design system;
do not replace an existing framework with the catalog's vanilla reference.
Use one fictional-company identity across entities, empty states, result titles,
script and exports. Business actions and evidence come first; technical traces,
raw JSON and diagnostics are secondary, collapsed surfaces.

Make the first action obvious, show what inputs can change, distinguish a draft
from an approved real action, and provide a stable saved-results entry with search
or result links. Show pending, failed, expired-source and uncertain states in
plain language with the correct recovery action. Keyboard navigation/focus,
screen-reader labels, text contrast, status beyond color, desktop/mobile overflow
and real long content are part of the observed script. Test downloads only when
promised. Preserve known accessibility limitations in the acceptance record.

Manage these lifetimes independently:

| Lifetime / state | Rule |
|---|---|
| Presenter access | Standing entitlement, still subject to normal auth and revocation |
| Session | Short-lived credentials; reauthenticate without deleting historical results |
| Source revision | Explicit preparation, effective time, expiry; writes recheck validity after waits |
| Historical result | Immutable receipt/revision, readable under its own authorization/retention even after source expiry |
| Operation | Process + source revision + action + operation ID; compare-and-swap/version check; uncertain until reconciled |

**Reads must not silently refresh sources, reset consumed operations or clear
uncertainty.** A failed/unknown write must not be replayed for a screenshot, log
or packaging repair. Recover by an authorized read of its operation/result/audit;
lost ACK means unknown, not failed. Concurrent retries must converge on one
effect or an explicit conflict. Compare expected revision, not global "latest".
An authorized new practice run gets a **distinct synthetic source revision**;
no reset-demo button may erase old receipts or resurrect consumed approvals.

## Evidence consumption and orchestration

The process's approved producers retain canonical receipts at immutable paths.
`.threadlight/presenter-evidence.json` is only a current index:

```json
{
  "schema": "threadlight-presenter-evidence/v1",
  "checks": {"package": {"path": "evidence/package-001.json", "sha256": "<64 hex>"}}
}
```

Each receipt has `schema: threadlight-presenter-receipt/v1`, `process_id`, `check`,
`level`, `result: pass`, timezone-aware `observed_at`, `inputs`, nonempty `evidence`
path/digest references, `predecessors`, and `facts`. Bind input hashes with
`skills._shared.presenter.fingerprints(root, contract)` **when executing the
check**, never by rewriting an old receipt after a change. Raw outputs must be
retained and their hashes verified; a fact set to true is not an attestation.

| Check | Level / facts | Input groups / predecessor receipts |
|---|---|---|
| `package` | `native`; eight executed cases above | runtime / none |
| `deployment` | `hosted`; actual deployment observation | runtime / none |
| `backend` | `hosted`; terminal useful interaction, applicable persistence/readback/reopen/download | runtime, source / package + deployment |
| `script` | `hosted`; documented entry and full journey | all five / backend |
| `human` | `human`; `accepted_by`, `first_time_presenter: true` | all five / script |

`predecessors` maps each required check to its **exact receipt SHA-256**, not
"latest". Preserve failed/superseded receipts outside the current index. For
backend/script facts require `interaction`, `terminal_success`; script also
`entry`. Persistence additionally requires `persisted`, `independent_readback`,
`reopened`, `operation_id`, `result_id`, distinct `writer_session`/`reader_session`,
and `readback_method`. Promised downloads require `download_verified`.

Deploy writes the separate current observation `.threadlight/presenter-target.json`:
`attempt`, `environment`, `version`, immutable `image` digest and actual `identity`.
Every hosted/human receipt must bind that exact object. A new attempt invalidates
hosted evidence even for the same image. This observation does not replace the
governance collector, authorize effects, or prove cloud state by its existence.

From the **catalog root**, with the pilot selected separately:

```bash
python3 -m skills._shared.presenter --root /path/to/pilot
python3 skills/threadlight-auto/references/orchestrator.py --workspace /path/to/pilot --dry-run --output json
```

The consumer is read-only and reports `recorded-not-independently-attested`.
Evidence is supplied by the process's trusted, authorized producers/reviewer;
tamper resistance requires the owner's existing signed/protected evidence store.
Do not use this metadata validator as an authorization boundary.

States are separate: **source-ready**, **deployed**, **backend-verified**,
**script-verified**, **human-accepted**. A running container, successful model
response, zero-exit worker or large test count cannot complete the profile.
`source_usable` separately reports current effective time/expiry; expiry blocks
new rehearsal readiness but does not erase historical backend proof.

Auto adds `presenter_package` before deployment and `presenter_ready` at handoff.
The selected profile uses dependency hashes instead of a blanket cascade:
script/sizing-only changes do not redeploy or replay backend operations.
An interface-only view change rechecks the script, but a workspace inside a
declared deployable service is also runtime input and needs deployment proof.
Every actual unified-azd service `project` directory and the selected ejected
infrastructure directory are included automatically, in addition to declared
inputs. The assessor and Safe Check validate the same service inventory.
Runtime changes invalidate native/deployment/backend/script/human evidence;
source changes invalidate backend/script/human; changed predecessor receipts
invalidate their downstream acceptance. New files in declared directories count.
Do not leave imported modules, rules, environment-name wiring or packaged assets
out of these source roots. Selected governance still runs its existing stronger
gates before effects and after each deployment.

Expired evidence requests an observation refresh, not a redeploy or business replay.
Reobserve the deployment or independently read the existing result; if a new
operation is genuinely necessary, the process owner resolves its authorization
and any uncertain prior effect first. Failed, uncertain or malformed deployment/
backend receipts stop automatic dispatch for reconciliation. Source expiry blocks
new backend interactions without removing historical results or receipts.
Presenter evidence never suppresses a failed/invalidated governed deployment.
Safe Check must have current passing evidence before invocation, and a zero-exit
Safe Check worker without that evidence is still blocked.

Reuse unchanged passing evidence. Resolve genuine permissions, shared resources,
material cost and uncertain effects with the owner; do not turn each routine
authorized implementation step into another approval. The planner never executes
a business operation or synthesizes a human acceptance receipt.

## Handoff, sizing and immutable publication

Canonical business receipts stay byte-for-byte unchanged. Add explanatory UI
fields in a source-bound **versioned companion** referencing the original receipt
path/digest, process/source revision and view version; never mutate history.
Comparisons capture explicit baseline/candidate receipt IDs and hashes **at
comparison creation**, with the same cases, source revision, metrics and
denominators. Interleaved activity must not move that baseline. A "latest result"
lookup is not a comparison binding. Do not mix processes or import their receipts.

The read-only `--comparison <path>` mode validates an additive
`threadlight-presenter-comparison/v1` object with `process_id`, `method`,
`baseline` and `candidate` path/digest references. It rejects changed receipts,
different processes, evidence levels/checks or source fingerprints. It checks
binding consistency, not the correctness of metric arithmetic; the process's
comparison producer still owns fixed cases and denominators.

Account separately for **model tokens** (input/output/cache/reasoning),
**model rounds**, **logical tool calls** (including failures/retries explicitly),
and deployed **resource units** (replicas, vCPU/memory, model capacity).
These units are not interchangeable. Mark sizing as proposed/measured, preserve
assumptions and unpriced components, and distinguish forecasts from actual bills.
Publisher-facing summaries consume these same facts; do not reconstruct them.

After final source and artifacts are committed, use the **exact immutable commit**:

```bash
python3 -m skills._shared.presenter --root /path/to/pilot --publication-commit <40-hex-commit>
```

The command reads the complete Git tree and its blobs, checks the committed
handoff, each promised file, and every ZIP member against the complete declared
source subtree. Extra/missing members, changed bytes, symlinks and uncommitted
promises do not pass. Retain its output as
`.threadlight/presenter-publication.json` outside that commit; do not introduce a
self-referential commit hash into the artifact. If any publication is promised,
Auto requires this companion before handoff, verifies its commit is current HEAD
and checks declared inputs/artifacts against committed bytes. Historical
publication can still be inspected explicitly by its old commit, but cannot
complete a new current handoff.
A clean worktree or a partial file index is not this check. Source archives must
contain exactly the declared subtree, with relative paths and no extra directory
entries. Exclude secrets/private evidence **before** creating the reviewed source
tree; this validator is not a data-classification or secret-scanning service.

Validate reuse on a **second compatible PoC** with a separate process ID, rules,
source data, target and receipts. The shipped tests cover this offline contract
isolation plus real native dispatch across two independent Pattern 0 datasets.
They do not establish a finished hosted PoC. A second process's real adapter,
workspace rehearsal, durable readback, quality and human acceptance remain its
own authorized checks; never inherit the first process's success evidence.

## Integration gate and limits

This profile is orthogonal to **Citadel**. Runtime/PDP/PEP/policy/pin ownership
stays with governance; synthetic scope and deliberately deferred production
governance are not defects. No hosted/business acceptance is claimed from offline
catalog verification. No live deployment, paid probe or RBAC expansion is implied.

### Citadel sequencing disposition

On 2026-09-24, the Citadel runtime owner gave an explicit technical sequencing
disposition for PR #142 at `271346751e5c8ab0b7b71744f273cd3e9f419741`:
independent completion for review is permitted. The owner inspected Citadel
`d8f97cc8d619508e0cb70ef9e82c6efcf4944c1d` and remote main
`4aaf3831a707cf47406ca1629dc6f3673c96b523`; Citadel had no PR then.
This is **not a merge authorization or general code review** of this change
or later fixes. Current main was unchanged, so no artificial merge is needed.

The shared file is `tests/blueprint/technical-guidance.test.js`; preserve both
independent additions when integrating. Presenter layout guidance must not
replace `governance-upstream-pin.json`, bypass pre-effect governance checks,
or claim coverage of Citadel's native-SDK ancillary-service deployment.
Keep Citadel's future gateway/control-plane pins and catalog version changes
with its owner; do not preempt them here. If main advances before integration,
review the actual touchpoints, realign normally and rerun affected checks and
source-bound publication verification. Do not copy incomplete runtime work or
manufacture hosted binding evidence.
