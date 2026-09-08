# Production-readiness, the threadlight way

## Optional AgentOps evidence

`threadlight-agentops` is an opt-in evidence adapter, not a readiness engine.
Only agent roots containing `agentops.yaml` participate; absence is
`not-applicable` and does not lower a pilot's score. Its normalized
`specs/agentops-manifest.json` records bound, fresh native evidence without
copying prompts, responses, tool calls or arbitrary Doctor payloads.

`threadlight-evals` owns quality, schedules and A/B evidence;
`threadlight-redteam` owns adversarial coverage. They reuse compatible evidence
without duplicating a batch or campaign. ASSERT/ACS observations are supplemental:
they cannot satisfy the signed, current, deployment-bound runtime governance
contract below. `threadlight-cicd` owns composed workflows; Safe Check and
Citadel keep their existing authority.

The `AOPS-001` finding in `sre-handover` reports residual operational gaps;
domain findings already represented by their canonical owners are not counted
again. Unknown, stale or unbound evidence cannot become a pass. A technically
complete smoke can contain failing quality thresholds and a blocked native
Doctor readiness result; neither a CLI exit nor native `ready` certifies
production readiness.

## Runtime governance lifecycle

**SAFE is the method**, **ACS/Rego is the PDP** (native OPA policy decisions),
**Agent Hooks is the host/interceptor contract SDK**, and the **native host or
governed-tool gateway is the PEP** enforcing the selected action lifecycle.
**AGT is the toolkit**; **ASSERT is assurance**, not an engine. Neither a policy
file, a CI badge nor one enforced tool makes the whole agent governed or
SAFE-complete.

### Select, implement, prove

1. Select each tool, intervention point and path in the governance contract.
   Preserve unbound reads without ACS. Report `enforced`, `observed`, `unbound`,
   `unverified`, `unsupported` and `bypassable` per binding. Consequential unbound
   actions require current owner/risk/tool/commit/environment acceptance, not a
   boolean. Invalid selected configuration is not `off`.
2. Build/validate the real ACS bundle with `policy_bundle.build_bundle`,
   `verify_bundle` and the native loader. Author host-trusted SAFE evidence from
   backend reads, target schema and dynamic facts, not model claims. Publish the
   signed bundle through the authorized control-plane publisher. Generate actual
   native host/gateway and ACA service sources using
   [generate.py and its bootstrap contract](../skills/threadlight-deploy/references/governance/README.md).
3. Enforce required signed/fresh policy, authenticated tenant/role/full-scope human
   approval with nonce CAS consumption, trusted targets/dynamic facts and central
   audit ACK **before effects**. Recheck authorization after waits at terminal
   dispatch. Local overlay fsync is retry safety, not hosted durable proof.
4. Run offline inventory and executed **LOCAL-14** plus exact native/CTK tests.
   These remain local proof. The standalone export copies actual producer,
   validator, CTK and test-only oracle sources and its own CI gate.
5. After deployment, collect ARM/Foundry-observed tenant/subscription/RG,
   version/image/principal and closed configuration before/after real hosted
   invocation. Fresh registered allow/deny nonces, authenticated producer/fixture
   counters and control-plane receipts are required; text is not an oracle.

Native MAF supports selected tool/lifecycle and buffered-output enforcement, not
automatic full SAFE. Compaction, provider-hosted tools and incremental
streaming/custom clients remain unsupported. GHCP supports registered gateway
action effect closure only: no arbitrary URL/shell routing or claimed full
lifecycle/full-output coverage. ACS is local; signed policy distribution and
registered ACA control-plane/gateway services use Cosmos, Blob, Key Vault and
separate publisher, verifier, workload and downstream UAMI permissions.

Use the [shared published pins](../skills/_shared/governance-upstream-pin.json):
`agent-governance-toolkit-core==5.0.0`, ACS `0.3.1b0`, Agent Hooks `0.1.0a5`,
MAF core `1.14.0`, Foundry `1.11.0`, hosting `1.0.0b260813`, OPA `1.18.2`.
Preview/alpha integrations and experimental defaults are explicit, not GA
guarantees. Only the CTK test oracle is corrected; the official execution SDK is
unmodified. **47 declared vectors**, four undeclared incremental-output vectors;
not “51 passed”.

### Evidence and migration

`specs/governance-manifest.json` (`threadlight-governance-manifest/v1`) is consumed
by the same strict validator/readiness evaluator in production-ready, evidence_gate
and Auto. Keep offline inventory, local conformance and live evidence separate.
Archive legacy v2 policy/verdict receipts as explicit historical provenance;
they cannot pass current readiness. Regenerate current inventory, implement the
selected runtime and collect new deployment-bound proof. Never translate an old
`governed` verdict or policy file into an empty gaps array.

The installed collector only proves reserved **`governance_probe_noop`**. Its
success cannot close business/lifecycle bindings or unsupported requirement
evidence. The current [returns example](../examples/returns-triage-governed/)
materializes real `app`/backend source, native prompt/skill loading, trusted
ordered backend reads, and Cosmos case/decision-audit persistence. It has **no
payment settlement**. `returns_apply_decision` is locally tested but live
unverified. Missing customer data can permit authenticated supervisor handoff;
refund finalization remains blocked when prerequisites are incomplete. Its
transport rechecks after credential awaits at the actual Cosmos dispatch.

Every new deployment attempt requires fresh **after-deployment** collection.
Remote attempts record start/completion timestamps and the exact bootstrap
reference/canonical digest. The strict exporter requires that linkage to match
both collected and current signed bindings. It exports only bounded summaries,
including the public-proof/no-network-isolation disclosure—not configuration,
credentials, raw diagnostics or signed-envelope payloads.
Current file mtime, reused nonces or yesterday's green cannot satisfy it.
The full signed envelope, key/signature, bundle, current environment/configuration
and exact image/version/identity must still match the verified record; changing
any of them invalidates the previous evidence. Saved JSON is not remote attestation.

### Private GHCP noop evidence snapshot (2026-09-08)

Source `62cb516fc05b37e1e89f052aa2e6b2b73c69c1bf` produced a fresh,
private-network hosted GHCP version **2** with the actual native SDK and
telemetry enabled. Collection completed at **09:35:05 UTC**:
`governance_probe_noop` **allow: 1** independent fixture effect; **deny: 0**
effects, each with a correlated central audit receipt and completed invocation
stream. Independent after-proof ARM/Foundry reads matched the exact target,
three service images, identities and configuration digests.

The signed policy/bootstrap chain and complete failed/successful reports are
retained privately, not embedded in this catalog. The independently retrieved
successful archive has SHA-256
`4af08521c8f3b83a6f78e5ea60079637501686018999fd03755131a610a6406c`.
This is a dated, binding-specific execution record, **not whole-agent**
governance, remote attestation or reusable current-readiness evidence.
`returns_apply_decision`, other business writes and hosted native MAF assurance
remain live-unverified. Cleanup or any subsequent deployment/configuration/key
change requires fresh after-deployment evidence; this snapshot grants no waiver.

Two explicit operator steps were needed and are not automatic CLI capabilities:

- **Version replacement:** `configure-endpoint` intentionally rejects an existing
  numeric route to a different version. The proof used a separately authorized,
  guarded native `update_details` transition from 1 to 2 with preserved create
  intent and repeated old/new observations, followed by the unchanged public
  `observe_endpoint` gate. It did not recreate after an ambiguous acknowledgement.
- **Session affinity:** the default collector CLI timed out on a cold bootstrap
  exchange before registering deny. That failed pair was preserved. A fresh pair
  used the public `collect_project(http=..., timeout=120)` extension, adding the
  documented `agent_session_id` only to the exact Invocations endpoint while
  preserving `api-version=v1`. Native reads under the controller identity checked
  that the same active session belonged to version 2 before and after collection.
  The session-only driver is not an installed CLI feature. It changed no runtime,
  SDK, signatures or receipts; no partial result from the failed pair was reused.

### Protected readiness-proof CI inputs

#### Public authenticated proof is not network isolation

An explicitly approved, dedicated staging/preproduction proof may select
`network.posture: public-authenticated-proof` with literal `proof_only: true`
and `cleanup_required: true`. It intentionally enables public HTTPS reachability
to proof services and public data-plane reachability to the dedicated stores/key
vault, while preserving mandatory Entra authorization and signed policy.
Storage shared keys/anonymous blobs and Cosmos local auth remain disabled.
This is **not** `public-pilot` IP restriction or `private-required` isolation.
Neither existing mode nor production defaults are relaxed.

Use the exact [generation contract](../skills/threadlight-deploy/references/governance/README.md#explicit-public-authenticated-proof-networking);
do not supply `allowed_ips` or pretend Any is restricted. Frozen configuration,
deployment and collection emit a `network_evidence` disclosure with
`network_isolation: not-established`. A successful runtime noop proof cannot
establish network isolation or certify business bindings. Cleanup remains an
explicit operator action confined to the dedicated proof resources.

#### Signed remote bootstrap implementation (not live acceptance)

The approved alternative is now implemented as **create-once**, then authenticated
observation and publication of `threadlight-hosted-bootstrap/v1`.
`scripts/ci/hosted_bootstrap.py` provides separate `create`, `observe`,
`configure-endpoint`, `publish`, and `wait` commands. They use the pinned Projects 2.3.0 SDK and either the existing
tenant-bound Azure CLI credential or an explicitly selected managed identity
(`--credential-mode managed-identity --managed-identity-client-id <client-id>`),
not `azd deploy`, a guessed version,
a mutable `"latest"` pointer, or a nonexistent start/mount API. Managed identity
mode uses only the native sync/aio credential for that client ID, never a user-cache
or default-chain fallback. Protected local attempt files are unchanged; an
external durable create-intent guard for private jobs remains operator-owned.

Before `wait` or collection, `configure-endpoint` requires the protected created
attempt and saved independent observation. Native `agents.update_details` pins
100% to that exact version and explicitly enables its declared protocol through
`protocol_configuration`; it preserves the Responses default when present,
retaining Entra-only authorization. An Invocations definition can therefore use
the documented combined Responses/Invocations endpoint without removing the
default; unrelated exposed protocols remain rejected. Read-back must confirm that
the declared protocol is actually exposed. The service's Responses-only
default does not establish that an Invocations agent is callable. Fresh reads
reject ownership, identity, image, definition, route or scope drift; the command
does not create a version, enable a disabled agent, or bypass binding/policy
registration. It checks read-back, not just the PATCH acknowledgement. `wait`
independently checks the configured endpoint before its read-only host request.
Server-populated `publish_approval_status` describes Microsoft 365 store review,
not a readiness or authorization condition. An endpoint exists from agent
creation; it need not be published to a store. This metadata is excluded from
endpoint updates and readiness comparisons, while unknown behavior-changing
extensions remain fail-closed.
The protected resume workflow runs this explicit operator mutation before wait
and live collection; collectors need no endpoint-write permission. Operators
must serialize endpoint writers where the provider supplies no ETag; re-reading
does not claim atomic compare-and-swap or hosted proof.

The frozen image selects `remote_bootstrap`: reference, project endpoint,
subscription/RG and native policy digest. GHCP also pins a **distinct**
`final_policy_version`; its original signed bootstrap bundle and final registry
can therefore both be published immutably. The signed binding covers the canonical
frozen configuration digest, exact platform version (including arbitrary `"17"`),
image, project, tenant, parent scope, principal/client, policy digests and version,
versioned key, issue time and expiry. Task8 verifies both immutable policy indexes,
fresh key state and RSA signature. Only the operator backend signs or writes Blob.
The workload-only read endpoint authenticates the receiver with EntraAuth; decoded
claims are not identity proof.

Generated Responses and Invocations entrypoints stay pending before importing
application tools or starting a Copilot process. `/liveness` may return 200;
request/readiness surfaces return 503 until verification and one-time activation.
Requests reauthenticate the binding, native effect/model boundaries recheck
expiry, and the GHCP relay rechecks after credential waits. Local-file/off
behavior remains separate. The Invocations bootstrap check is an empty-input,
read-only request on the **existing Invocations endpoint**, not a model invocation
or noop effect test.

Use `--help` and the [operator contract](../skills/threadlight-deploy/references/governance/README.md#signed-remote-bootstrap-operator-contract).
Publish to the protected `.threadlight/hosted-bootstrap.json`; the collector
re-verifies that signed chain against the running host and independent Azure
observations before invoking its registered noop. Current-readiness rejects missing,
changed or expired bootstrap proof for a remote-selected frozen package.

Native post-registration data is delivered as signed, bounded asset descriptors
and authenticated, create-only Blob chunks. There is no archive extraction or
mount attachment. Native policy code must byte-match the frozen image; only its
signed probe registry/metadata and typed configuration are post-registration
data. Cosmos target, audience, fixture endpoints/scope and controller identities
are pinned in `remote_bootstrap.native_probe` before building the image. The
Responses bootstrap check uses the actual pinned SDK and an empty-input metadata
control exchange, not a model call.

The protected workflow now accepts **only** explicit
`remote_bootstrap.mode: resume-signed-bootstrap/v1`. It resumes an acknowledged
prior SDK creation and operator-prepared service deployment; it does not create
another agent version or call azd. Its content-pinned inputs are `creation`,
`attempt`, `publisher`, `policy_bundle`, `policy_envelope`, and native-only
`native_assets`. File entries contain `{path, sha256}`; directory entries contain
`{path, tree_digest}`. Paths are relative to the protected configuration directory.
The image/source digest and original source commit must match the prepared
project. Fresh SDK identity observations must match the supplied service bindings
before publication. Publication, native protocol check and fresh collection remain
separate gates. Existing service images, app roles and registered fixture are
operator prerequisites, not resources silently provisioned by this resume mode.

**Limits:** no live Azure proof is supplied by these local tests.
**hosted-native-probe remains unverified as live assurance** until the actual
platform AgentIdentity can authorize against the native Cosmos producer and the
registered producer API is reachable by its controller. The code uses that
service-authenticated credential only for the dedicated noop; it does not attach
a UAMI or silently substitute another identity/runtime. Native business runtime
retains its embedded policy. No business effect proof is implied.

**NEEDS_CONTEXT — the legacy register/bind/start mode remains blocked.** The protected
`validate-inputs` stage refuses before login/provisioning, and direct `deploy`
also refuses before any command or project mutation unless the explicit
SDK-resume contract above is selected. Local preparation/validation remains available. This is not a completed
register → observe → bind → start lifecycle or a readiness pass.

The actual published contracts prevent the requested sequence:

- `azure.ai.agents 1.0.0-beta.10`, release commit
  [`947d8a3dd4059a0b0826adfbefaf9d4cc7f82649`](https://github.com/Azure/azure-dev/blob/947d8a3dd4059a0b0826adfbefaf9d4cc7f82649/cli/azd/extensions/azure.ai.agents/internal/project/service_target_agent.go#L3113-L3135),
  always calls `CreateAgentVersion` for container deployment, using API `v1`.
  Re-running `azd deploy` after binding therefore invalidates an exact existing
  version association; changing the protected expected version just chases it.
- Installed `azure-ai-projects==2.3.0` exposes `create_version`, `get_version`,
  `update_details` (agent endpoint routing), and agent-scoped `enable`/`disable`.
  Its `HostedAgentDefinition` includes immutable environment variables and
  `ContainerConfiguration` has only `image`. There is **no separate start**
  operation for an existing immutable version. [Official deployment guidance](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent#deploy-using-the-python-sdk)
  says creation automatically provisions the agent.
- [Drafts](https://learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-agent#create-a-draft-version-preview)
  cannot be routing targets; promotion creates a **new version**, and disabled
  subscription support can turn `draft=true` into a normal release. Neither
  drafts nor agent-scoped enable are a safe substitute for register-without-start.
- Native `ProbeConfiguration` requires a separately signed post-image registry,
  exact server version/principal/client, and files at `/mnt/governance-probe/`.
  The current native runtime reads those files, not a remote configuration
  indirection. The hosted schema has no mount attachment or version-environment
  update operation. Platform-injected `FOUNDRY_AGENT_VERSION` solves the native
  host's version lookup, **not** delivery of those post-registration files.
  Task8's existing signed-bundle lookup does not supply that native configuration.

Re-enabling this driver requires an approved lifecycle/configuration contract,
not a guessed next version, patched frozen package, fabricated mount API, or
protected selectors relabelled as server observations. GHCP has a signed final
gateway-bundle phase, but that alone does not supply register-without-activation.
No local protocol test alone authorizes the legacy all-in-one lifecycle. Select
the concrete SDK-resume contract, not an invented start/mount operation.

**Target/application staging is also unresolved, not fixed by this blocker.**
The same beta.10 implementation reads `AZURE_AI_PROJECT_ID` and
`FOUNDRY_PROJECT_ENDPOINT` from the **azd environment**, not just runner process
variables; the generated governance Bicep outputs neither. A future supported
driver must first observe the Task11-selected project ARM resource and endpoint,
match the independent tenant/subscription/RG, then stage a strictly allowlisted,
credential-free target/application configuration before provisioning. It must
resolve all required application substitutions (including Cosmos and Citadel
model/identity settings), reject missing configuration before mutation, avoid
secret CLI arguments/logs, and qualify explicit registered-version retry handles
against actual server identity and image. None of this is claimed implemented.

`.github/workflows/threadlight-e2e-foundry.yml` separates:

- `local-native-contract`: published native/CTK and deployment-contract tests,
  plus real preparation → standalone Git snapshot → native pre-deploy gate with
  explicitly local-only test inputs. No Azure proof.
- `readiness-proof`: explicit `governance_safe_probe=true`, protected
  `governance-preproduction` environment and a Linux x64 self-hosted runner
  labelled `governance-preproduction`, with private reachability and a separately
  assigned collector managed identity. No fallback to smoke identity/permissions.
- `e2e`: live-smoke/design-only/smoke-only; never a legacy readiness shortcut.

The protected environment must provide `GOVERNANCE_CI_CONFIG` (absolute path to
operator-mounted, credential-free JSON) and `GOVERNANCE_CI_CONFIG_SHA256` (exact
approved bytes), `GOVERNANCE_TENANT_ID`, `GOVERNANCE_SUBSCRIPTION_ID`, and secret
`GOVERNANCE_DEPLOY_CLIENT_ID` for scoped OIDC deployment. The runner needs Git, Docker,
Azure CLI, Bicep and the existing pinned azd/agent extension
(`azd 1.31.1`, `azure.ai.agents 1.0.0-beta.10`). The collector uses its configured
controller UAMI, not the deployment OIDC token, for authenticated service calls.
The workflow installs published Linux wheels and the portable safe-check package;
it does not rely on checkout-relative imports outside the catalog.

`scripts/ci/runtime_readiness.py` defines the closed stage driver. Its input
object (`schema: threadlight-readiness-input/v1`) requires:

| Field | Actual protected input |
|---|---|
| `environment` | `preproduction`, never implicit production or a sandbox default |
| `expected_target` | Independent canonical tenant/subscription UUIDs and resource_group; checked against account context and collector observations |
| `source_project`, `policy_source` | Paths below the protected JSON directory to a reviewed Git-tracked application/local-proof contract (resource-group infrastructure ejection required) and native policy source |
| `package` | Task10 package configuration: actual signed envelope, digest, policy identity/expiry, versioned signing key, service scopes/URLs, roles, network; paths passed to generator APIs must be absolute |
| `agent_image` | Task10 immutable image and spool configuration, matching the registered agent definition |
| `deployment` | Task10 bind input: actual immutable agent/CP/gateway images, infrastructure, identities, role assignments, observations and native probe association (or gateway registry) |
| `probe_input` | Exact Task11 `.threadlight/governance-probe.json`, not a permission-to-write boolean |
| `probe_files` | Literal project-relative destination → `{path, sha256}` for protected fixture/registry/envelope inputs. Evidence outputs, project trust metadata, traversal, symlinks and overlapping destinations are rejected. Separate config/read-only mount mappings remain supported; no supplemental input is committed or uploaded. |
| `source_digests` | Task10 `tree_digest` values for `source_project`, `policy_source`, and generated `agent`, `govern-control-plane`, `govern-gateway` contexts associated with the approved published images |
| `azd_environment`, `location` | Explicit operator-selected environment and region |
| `gateway_stage` (GHCP only) | Final Task9 signed registry/bundle and image association; not the bootstrap digest |

`package.probe_observability` must be
`{"enabled":true,"configuration_file":"/mnt/governance-probe/config.json"}`.
The preproduction fixture must already be installed, registered and reachable.
Task8 authority/policy publication, image build/publication, human roles, network
and service grants remain operator prerequisites, but supplying a bootstrap
version/instance or local mount mapping cannot unblock the API constraints above.
Native post-image probe registration must match the embedded bundle.
Missing prerequisites **fail descriptively**: CI does not
manufacture signatures, fixtures, source attestations or broad role assignments.
For returns, business Cosmos seed and protected role/token configuration are also
external prerequisites; there are no automatic business writes or seed/reset steps.

Preparation creates an isolated local Git repository over explicitly allowlisted
application/generated sources and exported validation tooling. The actual input
checkout commit, project boundary, dirty state and approved tree digest remain in
`governance/source-provenance.json`; the separate generated snapshot commit is
**not a deployment commit**. Its local origin is the input checkout, not fabricated
GitHub protection. Protected configuration, envelopes, token files, supplemental
inputs and runtime evidence stay ignored/untracked. Existing ignore rules are
preserved. The canonical native container wrapper continues to serve the same
generated `governance_host.build_host` that the native child verifies.
Local-validation export preserves unrelated workflows and accepts byte-identical
generated workflow/ownership files. Different contents fail with a
`local_validation_export_conflict` path before publishing any export changes;
preparation checks these collisions before copying the source project. Export
uses the generator's same-filesystem shadow and cooperative per-file atomic
publication/rollback. It never overwrites operator CI or imports prior proof.

The local stages run bundle build/native validation → `generate.py foundation`
and `generate` → exported CTK/native preparation and
`governed_actions.py --phase pre-deploy --emit --gate`. The previously documented
`bind` → `azd deploy` readiness path is disabled, not replaced by a simulated
activation. The existing post-deploy collector/current-readiness/scorecard gates
are retained but cannot be reached by a successful protected deployment until the
blocker is resolved. No reused version, noop invocation or old deployment receipt
can stand in for that missing lifecycle.

Artifacts are `governance-local-contract-<run_id>-<attempt>` and
`governance-readiness-<run_id>-<attempt>`. Both upload **only** a new, automatically
named export directory under `RUNNER_TEMP`, outside the project. Configuration
cannot select this directory. Git ignore rules are **not** an artifact boundary.

The local artifact contains validated workflow step outcomes only, not native
receipts, raw test logs, JUnit bodies or prepared fixture files. Native/CTK
validation still runs separately; its complete local outputs remain on the runner.

The readiness artifact contains explicit payload-free JSON **projections**:
`governance-summary.json` (offline inventory or current collection coverage),
`governed-actions-summary.json` (local finding-status counts),
`postdeploy-summary.json` (validated governance coverage and resource/governance
gap counts), `runtime-readiness.json` (binding status/live flag/gap count), and
`deployment-attempt.json` (run identity and timestamps), when produced.
Existing governance validators and the governed-actions schema validate their
source evidence; open parent reports are never copied. Names, gap text, business
payloads, original configs/envelopes, signatures, raw Markdown/stdout/stderr,
scorecard details and project input snapshots are not exported.

A private run/attempt-bound journal outside the upload tree records exact output
hashes after each producer. Declared outputs are removed before invocation;
checked-in or copied stale receipts cannot be exported as this run's proof.
Missing outputs are skipped. `export-status.json` distinguishes producer
failure, missing artifacts and invalid evidence; invalid evidence fails the
export step without disguising the original failed job. Both export and upload
run with `always()`. The export status itself is never a readiness pass.

The full parent report and generated scorecard remain **local runner files**,
not artifacts. Operators must handle those files as protected material.
Business evidence absent means **not verified and failed readiness**; current
noop-only collection cannot make the canonical business workload fully ready.
There is no automatic teardown of protected shared services; operators own
cleanup. Broader acceptance/deployment verification is separate.

> **What's new in v0.3.0** (Nov 2025). The 0.3.0 release closed an
> adversarial-review smoking gun: 16 critical static checks were
> regex-searching the concatenated raw text of every `.bicep` file in
> the repo, so a *comment* like `// virtualNetworks should be used`
> made `NET-001` pass. 0.3.0 replaces that with `BicepGraph` — a real
> ARM-graph parser that shells `az bicep build --stdout` and walks the
> compiled JSON resources (including nested `Microsoft.Resources/
> deployments` from module references). The 14 most-critical static
> checks (`NET-001/002/003/004`, `IAM-002/005`, `SEC-001/005/006`,
> `OBS-001/002`, `REL-006`, `MDL-001`) now answer the question "is the
> resource *declared*?" instead of "does the word appear *somewhere*?".
>
> Other 0.3.0 changes:
>
> - **`bicep` CLI is now a hard prerequisite.** Missing CLI exits 2
>   with `az bicep install` instructions, no silent regex fallback.
> - **`not-verified` scores 0**, not 2-of-4. A run with all gaps marked
>   "couldn't check" no longer gets a 50% honour score.
> - **`verification_debt` is a first-class manifest field** — total +
>   per-pillar count of `not-verified` findings, surfaced in the exec
>   summary so the gap "we couldn't check this" no longer hides inside
>   the percent.
> - **21 unimplemented stubs retired** to `experimental: true`,
>   excluded from scoring unless `--include-experimental` is set.
> - **5 long-stubbed live probes wired:** OBS-106 (per-account diag
>   settings), OBS-102 (App Insights KQL via `az monitor log-analytics
>   query`), SEC-106 (KV diag coverage), SRE-104 (activity-log alerts),
>   NET-501 (Citadel APIM Access Contract via `TL_CITADEL_HUB_RG`).
> - **14 new finding IDs:** Defender plans (`GOV-101/102/103`),
>   Secure Score floor (`GOV-104`), Defender recs surfaced
>   (`GOV-105`), Policy (`GOV-201/202/203`), Foundry RBAC + knowledge
>   index PE + thread policy (`MDL-009/010/011`), quota pre-flight
>   (`MDL-110/111`), restore-drill freshness (`REL-007/008`).
> - **Industrialization:** `--diff`, `--gate-preview` (exit 2 on
>   would-fail-hard-gate), `--remediate <id>` (prints bash recipe),
>   `--include-experimental`, trend CSV append per run, OIDC CI
>   recipe replaces `AZURE_CREDENTIALS`, `azd hook` install script.
>
> See `CHANGELOG.md` for the full delta. Bicep-only — Terraform is
> explicitly out of scope in this skill, forever.

> *Green `safe-check` proves a pilot is structurally complete and behaves. It does not prove the customer's CISO, SRE, FinOps and network architect can sign off on it. That conversation needs an evidence-backed artefact — produced in one command, not weeks of tribal-knowledge assembly.*

This page is the long-form companion to the [`threadlight-production-ready`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/SKILL.md) skill. It explains **what production-readiness means in the threadlight chain**, the **three postures** a pilot can target, the **thirteen cross-cutting pillars** the skill scores, the **status taxonomy** that surfaces what's actually blocking go-live, and the **two moments per pilot lifecycle** when you run it.

---

## 1. Why this skill exists

The `threadlight-*` chain ships a working agent in one session: **design → local-test → deploy → safe-check**. `safe-check --phase post-deploy` proves the pilot is *structurally complete and behaves* — every selector landed, every channel reaches, every cron ran, no placeholder image.

But **"green safe-check" ≠ "production-ready"**. The next conversation — CISO, SRE, FinOps, network architect, data protection — needs an artefact that says:

- **What posture is this in?** Citadel spoke? AGT-only? Standard AI gateway?
- **What's missing?** Per-pillar gaps with severity.
- **What would the uplift cost?** Effort estimate + named remediation skill.
- **Who owns each gap?** Pilot team / customer team / SRE / SecOps.
- **Can we go live with waivers?** Score with and without customer-accepted compensating controls.

Without that artefact, every pilot grows a tribal-knowledge answer that takes weeks to assemble. The customer defers the production phase. The pilot quietly becomes a **lab graveyard** demo.

`threadlight-production-ready` produces the artefact in one command. **Soft-advisory** — never fails a build. **Gracefully degrading** — missing Azure permissions become `not-verified` findings, not crashes.

---

## 2. What "production-ready" means here

This skill is **the artefact, not the gate.** It does not stop a deploy. It does not enforce a hard policy. It produces two outputs you can put in front of decision-makers:

| Output | Audience | Purpose |
|---|---|---|
| `docs/production-readiness-report.md` | Customer architecture review · CISO sign-off pack · pilot-to-prod handover deck | Human-readable scorecard, per-pillar findings with severity, evidence register (with `captured_at` timestamps), waiver register, residual-risk list, go-live recommendation |
| `tests/production-readiness-manifest.json` | CI · change advisory board · automated dashboards | Machine-readable manifest with `raw_score`, `score_with_waivers`, `would_fail_hard_gate`, `evidence_freshness`, full per-finding detail |

The report is **the conversation starter** with the customer's production team. It replaces the four-week scramble to assemble "is this pilot ready?" with a one-command answer that can be reviewed, waived, and iterated.

---

## 3. The three postures

A pilot's production posture is **resolved from SPEC § 12** (the production-readiness section of the threadlight SPEC). The skill ships a [`spec-section-12-template.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/spec-section-12-template.md) for authoring it. If § 12 is missing, posture falls back to `standard-ai-gateway` and an `RDY-002` finding surfaces "author § 12 before the architecture review."

### 🛡️ Citadel spoke *(default · recommended)*

The customer's **AI Citadel hub** fronts model calls via its configured APIM
controls and access contracts. Selected native host or tool-gateway enforcement
is a separate action boundary, requiring its own current policy and evidence.

**Separate boundaries.** A model gateway governs access to the model, not every
downstream action. Correlate its records with selected action-policy and durable
receipt evidence; do not assume a shared audit chain from topology alone.

**Right when** the customer tenant already has — or is provisioning — Citadel. This is the GBB AI Apps recommended posture for any new pilot.

Remediation skills: [`citadel-spoke-onboarding`](https://github.com/aiappsgbb/awesome-gbb), [`citadel-hub-deploy`](https://github.com/aiappsgbb/awesome-gbb), [`foundry-agt`](https://github.com/aiappsgbb/awesome-gbb).

### 🧬 Native selected runtime enforcement

No central model gateway available. Local ACS/Rego decisions can be enforced by
the selected native Agent Hooks host path. Required distribution, approval and
central audit services still need real configuration; policy/verifier artifacts
alone do not establish runtime enforcement. See the lifecycle above.

**Right when** the customer is in a greenfield or experimental tenant where introducing APIM mid-pilot would be premature. Still produces auditable evidence; just operates one defence layer instead of two.

Remediation skills: [`foundry-agt`](https://github.com/aiappsgbb/awesome-gbb), [`foundry-observability`](https://github.com/aiappsgbb/awesome-gbb).

### 🌐 Standard AI gateway / VNet

Brownfield or regulated estate with an **existing APIM**, NetSec-controlled **VNet injection**, or Microsoft Defender for Cloud baseline posture. The pilot **conforms to the established gateway pattern** rather than introducing Citadel mid-flight.

**Right when** the customer's NetSec team owns the perimeter and the pilot has to slot in behind it. The skill scores against that perimeter's contract (private endpoints, allowlists, JWT validation) rather than Citadel's.

Remediation skills: [`foundry-vnet-deploy`](https://github.com/aiappsgbb/awesome-gbb), [`foundry-hosted-agents`](https://github.com/aiappsgbb/awesome-gbb), [`azure-tenant-isolation`](https://github.com/aiappsgbb/awesome-gbb).

> **Hybrid is supported.** `--target hybrid` runs Citadel checks where applicable and AGT checks where Citadel artefacts are missing. Useful for pilots mid-uplift.

---

## 4. The thirteen pillars

Every pillar has its own [reference doc under `references/pillars/`](https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-production-ready/references/pillars) — prose-heavy guidance so the LLM can reason about findings, not just emit them.

| # | Pillar | What "good" looks like | Primary remediation skill |
|---|---|---|---|
| 1 | [`network-posture`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/01-network-posture.md) | Resolved posture target met (Citadel spoke / AGT / VNet / standard); **data-residency sub-scored** (model region, APIM region, data-plane regions, backups, cross-border support) | `citadel-spoke-onboarding`, `foundry-vnet-deploy` |
| 2 | [`agent-governance`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/02-agent-governance.md) | Current selected-binding v1 evidence; exact policy/config/deployment and live-proof requirements, not import or policy-file presence | `threadlight-govern`, `threadlight-governed-actions` |
| 3 | [`identity-access`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/03-identity-access.md) | Workloads use **managed identity**; **no client secrets**; RBAC least-privilege; Key Vault access via RBAC not access policies; **agent (non-human) identity governed** &mdash; passwordless binding, named owner, least-privilege scope, lifecycle/review (emits `agent-identity.json`) | `foundry-hosted-agents`, `entra-agent-id`, `foundry-agt`, `azure-tenant-isolation` |
| 4 | [`secrets`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/04-secrets.md) | Key Vault with **soft-delete + purge protection**; no hardcoded secrets in repo; rotation policy declared; control-plane vs data-plane access scoped | `azd-patterns`, `foundry-hosted-agents` |
| 5 | [`observability`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/05-observability.md) | App Insights connected at **account-level** (Foundry); OTel emit verified (recent traces); alert rules wired; workbook + retention declared | `foundry-observability` |
| 6 | [`continuous-evals`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/06-continuous-evals.md) | SPEC § 9 scenarios scheduled (Plan A or Plan B); threshold alerts wired; last run within freshness window; eval datasets stored | `foundry-evals` |
| 7 | [`responsible-ai`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/07-responsible-ai.md) | Content filters, jailbreak shields, grounded-language eval; AGT RAI policy; PII redaction declared; allow/deny tested | `foundry-agt`, `foundry-evals` |
| 8 | [`hitl-audit`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/08-hitl-audit.md) | If SPEC § 8 declares gates: wired, persistent audit trail, escalation channel reachable, idempotent | `threadlight-hitl-patterns` |
| 9 | [`supply-chain`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/09-supply-chain.md) | Container images **pinned by digest**; Bicep modules pinned; dependency scanning enabled; SBOM emitted | `azd-patterns` |
| 10 | [`cost`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/10-cost.md) | Pricing plan declared (PAYG vs PTU); budget + anomaly alerts wired; forecast vs budget cap; idle-resource sweep done | `paygo-ptu-cost-analyzer` |
| 11 | [`reliability`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/11-reliability.md) | Multi-region plan vs RTO/RPO from § 12; **backup/restore tested** (not just "configured"); runbook exists; chaos test done | `foundry-vnet-deploy` |
| 12 | [`sre-handover`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/12-sre-handover.md) | **Evidence-based:** incident owner + escalation path; runbook links; alert destinations; SRE Agent resource/recipe if selected; handoff acceptance signed | `azure-sre-agent` |
| 13 | [`model-lifecycle`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/pillars/13-model-lifecycle.md) | Model deployment **names + versions pinned** (no `latest`); fallback model declared; retirement-notice owner; A/B or rollback strategy; region/capacity documented | `paygo-ptu-cost-analyzer`, `foundry-hosted-agents` |

> **Pillars are version-agnostic where the underlying surface evolves.** Pillar 2 detects AGT v3.7 *or* v4-preview by **capability**, not version pin. AGT v4-preview deep checks (5 static + 1 live) gate on `--agt-profile v4_preview` (or `auto` resolving to v4 when v4 artefacts are present in the repo).

---

## 5. Status taxonomy

Findings aren't just pass/fail — they encode **what the operator can actually do** about them.

| Status | Meaning | Counts toward raw score? |
|---|---|---|
| `pass` | Check ran and the pillar requirement is met | ✅ |
| `should-fix` | Gap exists; not a hard blocker but should be addressed before go-live | ❌ |
| `must-fix` | Hard blocker for production go-live; would fail a v2 hard-gate | ❌ |
| `not-applicable` | Check correctly skipped (e.g., Citadel scoring against an AGT-target deployment) | ✅ (counts as pass for raw, with justification) |
| `not-verified` | Check could not run (no Azure auth, insufficient RBAC, static-only mode) | ⚪ (excluded from raw score; surfaced in `not_verified[]` with `verification_coverage`) |
| `waived` | Customer explicitly accepted the gap with a documented compensating control | ✅ in `score_with_waivers`, ❌ in `raw_score` |

The manifest reports **both** `raw_score` and `score_with_waivers`, plus a `would_fail_hard_gate` boolean that flips true if any `must-fix` finding lacks a waiver. The report's executive summary calls this out so reviewers see the unfiltered posture **and** the customer-accepted posture side-by-side.

### Evidence freshness

Every live probe stamps a `captured_at` timestamp (ISO 8601 UTC, second precision). The manifest's top-level `evidence_freshness` block surfaces the oldest evidence and flips a `stale` boolean when the oldest probe exceeds the `--freshness-hours` window (default 24h). The report's evidence register shows a `Collected` column; when stale, the executive summary surfaces an "Oldest evidence" bullet so reviewers know the report is reading from older probes.

---

## 6. When to invoke

> **Rule of thumb.** This skill runs **at most twice per pilot lifecycle**:
>
> 1. **Heading into the customer architecture review** — the artefact that lives in the deck.
> 2. **Immediately before the go-live decision** — the artefact that goes to CISO or the change advisory board.
>
> Running it every commit is noise. Running it once after the pilot has been parked for weeks is fine — `--static` mode works with no Azure auth at all.

| You are at… | Run | Get |
|---|---|---|
| `safe-check --phase post-deploy` returned green and the customer wants to talk about production | `python tests/production_ready.py` | Markdown report + JSON manifest, all 13 pillars, live + static |
| Customer architecture review in 3 days, posture is known | `python tests/production_ready.py --target citadel-spoke` | Same, scored against the declared target |
| Pilot has been parked for weeks; someone asks "could we ship this?" | `python tests/production_ready.py --static` | Pure static scorecard from repo + safe-check manifests (no Azure auth needed) |
| Inherited a pilot whose SPEC has no § 12 | Skill still runs — falls back to `standard-ai-gateway`; `RDY-002` surfaces "author § 12" | Author § 12 from the [template](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/spec-section-12-template.md), re-run for full scorecard |
| Historical AGT v4 inspection | `python tests/production_ready.py --pillar agent-governance --agt-profile v4_preview` | Legacy compatibility diagnostics only, never current v1 runtime readiness |
| Customer accepted some `must-fix` findings as risk | Author `tests/production-readiness-waivers.json`, re-run | Report shows `score_with_waivers` + `would_fail_hard_gate` flags |

---

## 7. What this skill does NOT replace

This skill is **the cross-cutting scorecard.** It does not replace any of the focused skills that produce the evidence it reads.

| Concern | Use instead |
|---|---|
| Authoring SPEC / `deployment_manifest{}` | `threadlight-design` |
| Running `azd up` | `threadlight-deploy` |
| Generating the prod CI/CD pipeline + env (UAMI / federated creds, RBAC, private-VNet runners) | `threadlight-cicd` |
| Structural / behavioural deploy gate | `threadlight-safe-check --phase post-deploy` |
| Invocation testing of the agent | `foundry-evals` |
| Wiring App Insights / OTel | `foundry-observability` |
| Provisioning Citadel hub | `citadel-hub-deploy` |
| Onboarding spoke to Citadel | `citadel-spoke-onboarding` |
| Provisioning Azure SRE Agent | `azure-sre-agent` |
| Authoring selected native policy/host/service bindings | `threadlight-govern`, `threadlight-governed-actions` |
| Generating Bicep / Terraform | `azd-patterns`, `azureterraform`, `bicepschema` |
| Deploying to a VNet-injected Foundry | `foundry-vnet-deploy` |

**This skill recommends, never executes.** Every `must-fix` and `should-fix` links to the remediation skill above. The operator (or a follow-up Copilot session) runs that skill.

---

## 8. Outputs

```
docs/production-readiness-report.md         # human-facing scorecard (markdown)
tests/production-readiness-manifest.json    # machine-readable manifest (CI / dashboards)
```

The report has a stable structure: executive summary (posture, raw score, waivered score, would-fail-hard-gate, oldest evidence) → per-pillar findings (status, evidence, remediation skill, effort estimate) → evidence register (every live probe with `captured_at`) → waiver register → residual-risk list → go-live recommendation.

The manifest is `schema_version: "1.0"` (additive evolution; no breaking changes since GA). Tool version follows semver under `VERSION` (currently `0.2.0` after the per-evidence freshness ship).

---

## 9. CLI cheatsheet

```bash
# Default — all 13 pillars, live + static, both outputs
python tests/production_ready.py

# Subset of pillars
python tests/production_ready.py --pillar network-posture,observability

# Static only (no Azure auth required; live checks all → not-verified)
python tests/production_ready.py --static

# Quick smoke (subset of checks per pillar; for iteration)
python tests/production_ready.py --quick

# Explicit posture override (overrides SPEC § 12 resolution)
python tests/production_ready.py \
  --target citadel-spoke|agt|standard-ai-gateway|hybrid

# AGT profile (capability-based, version-agnostic)
python tests/production_ready.py --agt-profile auto|v3_7|v4_preview|none

# Explicit waiver file path
python tests/production_ready.py \
  --waivers tests/production-readiness-waivers.json

# Allow stale safe-check manifest (default rejects >24h or RG/sub/hash mismatch)
python tests/production_ready.py --accept-stale-safe-check

# Override the freshness window for the evidence-staleness banner
python tests/production_ready.py --freshness-hours 48

# Override output paths
python tests/production_ready.py \
  --out tests/production-readiness-manifest.json \
  --report docs/production-readiness-report.md

# Quiet output for CI / hooks
python tests/production_ready.py --quiet
```

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Checks ran and report was written. Per-finding statuses (including `must-fix` and `not-verified`) live inside the report. **The skill never returns non-zero for findings in v1 — it is soft-advisory.** |
| `2` | Missing prerequisite: no `specs/manifest.json`, no `tests/postdeploy-manifest.json`, safe-check manifest stale (use `--accept-stale-safe-check` to override) or scope-mismatched (different subscription/RG), or unknown `--pillar` id. **Missing SPEC § 12 does NOT exit 2** — the skill emits `RDY-002` and falls back. |
| `3` | I/O failure: cannot read inputs, cannot write outputs, `az` not on PATH at all. |

Missing Azure auth or insufficient permissions for specific live probes ⇒ those checks are marked `not-verified` in the report; exit code stays `0`. **The skill never turns into a deployment blocker by accident.**

---

## 10. Where this fits in the chain

```
threadlight-design        →  threadlight-local-test  →  threadlight-deploy   →  threadlight-safe-check
(SPEC + manifest)            (mock data + smoke)        (azd up)                (--phase post-deploy)
                                                                                       │
                                                                                       ▼
                                                                       threadlight-production-ready
                                                                       (paved path to production)
                                                                                       │
                                                                                       ▼
                                                              docs/production-readiness-report.md
                                                              tests/production-readiness-manifest.json
                                                                                       │
                                                                                       ▼
                                                          customer architecture review · CISO sign-off
                                                          change advisory board · pilot-to-prod handover
```

**Soft-advisory by design.** The skill before it (`safe-check`) is the structural gate. The skills after it are conversations with humans. This skill is the **bridge** — the artefact that turns "we built something that works" into "here is the evidence the customer's production team needs."

**Then the pilot ships through a pipeline, not a laptop.** Once the scorecard is green, [`threadlight-cicd`](https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-cicd) generates the production deploy pipeline (GitHub Actions / Azure DevOps) and the env-setup runbooks the platform team runs — OIDC/WIF identity, least-privilege RBAC scoped to the spoke RG, and private-VNet runners — because in a real customer tenant the agent rarely has rights to run `azd up` itself. It is a deliberate **manual handoff** (not part of the auto chain), and it stays a **separate repo/pipeline** from the central platform: it never touches the Citadel hub, shared APIM, or platform networking — those remain `citadel-hub-deploy`.

---

## Read next

- **Full skill metadata + invocation patterns:** [`skills/threadlight-production-ready/SKILL.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/SKILL.md)
- **Author SPEC § 12 from scratch:** [`references/spec-section-12-template.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/spec-section-12-template.md)
- **Pre-go-live handoff checklist:** [`references/handoff-checklist.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/handoff-checklist.md)
- **Generate the prod CI/CD pipeline + env (UAMI/federated creds, RBAC, private-VNet runners):** [`threadlight-cicd`](https://github.com/aiappsgbb/threadlight-skills/tree/main/skills/threadlight-cicd) · [onboarding-path decision tree](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-cicd/references/onboarding-path-decision.md)
- **Per-pillar Azure RBAC for live probes:** [`references/live-probe-permissions.md`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/live-probe-permissions.md)
- **Sample CI workflow (PR comments + artefacts):** [`references/ci-github-actions.yml`](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-production-ready/references/ci-github-actions.yml)
- **End-to-end workshop (1 hour, includes a production-readiness pass):** [WORKSHOP-1H-QUICKSTART.md](WORKSHOP-1H-QUICKSTART.md)
- **The whole threadlight chain (technical briefing):** [THREADLIGHT.md](https://github.com/aiappsgbb/threadlight-skills/blob/main/THREADLIGHT.md)
- **Foundational skills (Citadel, AGT, Foundry, SRE Agent):** [awesome-gbb](https://aiappsgbb.github.io/awesome-gbb/)

---

*Maintained as part of [aiappsgbb/threadlight-skills](https://github.com/aiappsgbb/threadlight-skills). Issues and PRs welcome.*
