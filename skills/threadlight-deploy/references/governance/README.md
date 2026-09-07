# Generated governed deployments

These references edit a **pilot**, not this skill catalog. They never call Azure,
change an existing Foundry account, choose a different runtime, sign with a local
private key, or report enforcement from an assessment. Use Python 3.12/Linux
amd64 and the shared published pins. `generate.py --help` is the executable
entrypoint; package inputs are JSON. No customer identifiers are baked into these
templates.

## Order and required inputs

For the explicitly selected remote path, use the create-once operator contract
below instead of the legacy register/bind/azd ordering. Do not run a second agent
deployment after publishing its binding.

## Signed remote bootstrap operator contract

This is implemented local code, **not live acceptance**. `package.json` can include `remote_bootstrap` with `reference`,
`project_endpoint`, `subscription`, `resource_group`, `native_policy_digest`;
GHCP additionally requires `final_policy_version`, distinct from the original
bootstrap policy version. All values are frozen before the agent image is built.
The real generated MAF/GHCP entrypoints gate requests before application startup.
Their portable control-plane dependency exports the bootstrap modules.

1. Generate/build the immutable agent image and publish its original signed
   policy through the existing Task8 operator backend. Do not embed the image's
   own digest in its bytes. Independently retain image/source provenance.
2. Prepare a protected `threadlight-hosted-create/v1` JSON file with exactly:
   `reference`, `tenant_id`, `subscription`, `resource_group`, `project_id`,
   `project_endpoint`, `agent_name`, `image`, `cpu`, `memory`, `protocol`,
   `source_commit`, `source_digest`, `environment_variables`, and `schema`.
   `image` must be digest-pinned; protocol is `responses` or `invocations`.
   Environment keys are allowlisted: `TL_GOV_IMAGE_DIGEST`,
   `GOV_CONTROL_PLANE_URL`, `GOVERNED_TOOL_GATEWAY_URL`,
   `AZURE_AI_MODEL_DEPLOYMENT_NAME`, `TL_GOV_SPOOL_DIR`, and optional
   `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. No `FOUNDRY_*`,
   credentials, or invented identity attachments are accepted. Use a writable
   `/home/...` local spool path, never claim it is durable hosted evidence.
3. Under an explicitly approved logged-in operator, run:
   `python scripts/ci/hosted_bootstrap.py create --creation <protected-create.json>
   --attempt <protected-attempt.json> --credential-mode azure-cli`.
   An authenticated ARM project/endpoint observation precedes creation. The
   persisted creating intent precedes the sole SDK call; an ambiguous lost reply
   requires operator reconciliation and **never** automatically creates again.
4. Run `observe` with the same arguments. Independently retrieved version, image,
   principal and client—not a configured next version—drive subsequent service
   allowlists and the final gateway registry. Stage/build/deploy only those
   separate services, using their own immutable images and approved permissions.
   Publish the final gateway policy at the preselected `final_policy_version`.
5. Run `publish` with the same arguments plus `--frozen-config` pointing to the
   generated agent's unchanged JSON, `--policy-envelope` selecting the final
   signed policy, `--publisher-config` containing the existing Task8
   `AzureConfiguration`, and `--binding-output .threadlight/hosted-bootstrap.json`.
   `--lifetime-seconds` is capped by all signed policy dependencies with a small
   safety margin (60–86400 seconds); an insufficient remaining lease fails.
   Blob/key permissions belong only to the operator publisher, never the agent.
6. `wait` with `--binding-output` and `--frozen-config` checks the configured
   key and exact binding on the existing Invocations or Responses endpoint. Then
   run the existing explicit collector with independently observed target scope.
   The check itself produces no model/tool effect and cannot replace allow/deny
   noop receipts or any business-binding proof.

Native business startup verifies its embedded policy before importing the app.
For native probes, freeze `remote_bootstrap.native_probe` with `audience`,
`cosmos_url`, `cosmos_database`, `gateway_url`, `allowed_endpoints`,
`fixture_scope`, `policy_id`, `policy_version`, and `controller_digest`.
The controller digest is canonical SHA-256 of each controller's client ID and
actions, excluding the not-yet-assigned subjects; every final subject must equal
the observed agent principal. Publish using `--native-probe-assets <directory>`.
That directory contains `config.json`, `envelope.json`, and the complete signed
association under `policy/`. Configuration paths retain their legacy logical
names, but actual bytes are materialized privately under the local spool, not
mounted by Foundry. Up to 32 files/512 KiB are supported; individual files are
bounded to 64 KiB and transmitted in authenticated 8 KiB Blob chunks. Policy
code must match the frozen native bundle, not merely another operator signature.

Preparation can retry before application initialization. Activation and cleanup
remain one-time. `platform-noop` accepts the actual, service-authenticated
platform credential only for the sole registered noop. Fixture writer/counters
and business gateway identities remain separate. Actual native AgentIdentity/
Cosmos authorization and hosted controller reachability remain unverified.

`--observation-output` saves an independent SDK observation; use
`--expected-observation` for subsequent publication/wait to reject identity
races. Publication recovery reuses an identical, still-fresh immutable binding;
it never extends its expiry. The protected readiness workflow's explicit
`resume-signed-bootstrap/v1` mode consumes content-pinned prior creation and
service inputs, verifies source/identity consistency, then publishes, waits and
collects. It does not invoke azd or create the agent again.

## Legacy local-file generation ordering

### Explicit public authenticated proof networking

`public-authenticated-proof` is an operator-approved **staging/preproduction
proof only** posture, not an alias for `public-pilot` (restricted public ingress)
or `private-required`. Use exactly this network shape in both package and
infrastructure configuration:

```json
{
  "environment": "preproduction",
  "network": {
    "posture": "public-authenticated-proof",
    "environment_id": "<existing-ACA-environment-ARM-ID>",
    "proof_only": true,
    "cleanup_required": true
  }
}
```

Missing/nonboolean opt-ins, production/development, `allowed_ips`, and unrelated
network fields are rejected. The existing public IP allowlist and private-network
contracts are unchanged. This mode deliberately opens HTTPS network reachability:
ACA ingress restrictions are empty, Storage/Key Vault network default action is
Allow, and Cosmos has public access with no IP filter. **Authentication is not
optional:** existing Entra identity/app-role allowlists, policy signature checks,
one-use approvals and audit ACKs remain unchanged. Storage shared keys and
anonymous blob access stay disabled; Cosmos local auth stays disabled; Key Vault
uses RBAC; HTTPS/TLS settings remain enforced. Anonymous protected/data operations
are not introduced. Existing payload-free health/liveness routes are not proof
of authorization or enforcement.

`TL_GOV_SERVICE_INGRESS` exposes the generated HTTPS ingress object for the
operator-installed fixture to use consistently; normal generation still does
not automatically deploy a fixture. ACR pull authorization remains separate.
The generated frozen configuration, deployment declarations and collected
evidence disclose `network_evidence`, whose scope is
`runtime-governance-proof-only`: **network isolation is not established**.
Readiness cannot drop or relabel this disclosure as isolation evidence.

Explicit cleanup is an operator obligation after verification: remove only the
dedicated test resources and grants. The flag is a required commitment, not a
claim that cleanup already happened. This posture must not be promoted to
production or used to bypass the existing restricted/private requirements.

1. **Foundation, without application revisions.** Supply a dedicated governance
   resource group, existing registry and ACA environment, approved network
   topology, and the Entra application registrations described below:

   ```sh
   python <catalog>/skills/threadlight-deploy/references/governance/generate.py \
     foundation --project <pilot> --contract <contract.json> --configuration <infrastructure.json>
   ```

   This composes `infra/main.bicep`, preserving existing resource-group Bicep,
   and writes `infra/main.parameters.json` with `governancePhase=foundation`.
   Subscription-scoped entrypoints require explicit resource-group composition;
   the generator refuses to guess the scope. Infrastructure requires an explicit
   `environment`: `development`, `staging`, `preproduction`, or `production`.
   Run tenant isolation and approved
   provisioning separately. The foundation creates **zero container apps**.
   Capture its `TL_GOV_FOUNDATION` output (real object/client IDs, versioned key,
   Blob/Cosmos endpoints) and the two service URLs.

2. **Publish a Task6 bundle through the Task8 authority.** Give only the publisher
   UAMI the generated Blob upload and key sign/verify roles. Use the existing
   `ControlPlane.publish(BundleEnvelope(...))` backend under that identity, not
   a public signing endpoint. The HTTP control plane has read/verify only.
   `publish` writes the policy/version and digest-index objects with
   `overwrite=False`; preserve both. Lock the configured Blob immutability
   retention policy before production publication, and capture the observed
   retention state. A Bicep retention declaration alone is not proof of a locked
   policy. Preserve Task6's deliberately **unsigned** `bundle-metadata.json`;
   put the signed Task8 envelope alongside, never inside its digest-covered tree.

3. **Package the selected runtime:**

   ```sh
   python <catalog>/skills/threadlight-deploy/references/governance/generate.py \
     generate --project <pilot> --contract <contract.json> --configuration <package.json>
   ```

   `package.json` requires:

   | Field | Source |
   |---|---|
   | `agent_service`, `agent_id` | Existing `azure.yaml` service key and exact runtime agent ID |
   | `bundle_path`, `policy_digest` | Task6 verified immutable native bundle |
   | `signed_envelope`, `policy_id`, `policy_version` | Task8 publication, with matching digest, identity and future expiry |
   | `tenant_id`, `key_id` | Trusted tenant and full **versioned** Key Vault key URL |
   | `control_plane_scope`, `gateway_scope` | Distinct `api://<application-id>/.default` scopes |
   | `control_plane_url`, `gateway_url` | Actual foundation service URLs, known before building the agent |
   | `approver_roles` | Explicit human approval role values |
   | `environment` | Explicit deployment environment, identical to infrastructure; no default or development backfill |
   | `network` | Same topology used by foundation |
   | GHCP: `mcp_servers`, `mcp_bindings`, `gateway_url` | Original server dictionary; mapping `tool-id → {"server": original-name, "tool": tool-id}`; foundation URL |

   MAF additionally requires `src/agent/governance_application.py` exporting
   `tools`, `middleware` and a callable `safe_evidence(identity)`. Implement it
   using host-authenticated facts; an empty dictionary is rejected, not promoted
   to SAFE evidence. The generator does not invent domain tools or credentials.
   Existing skill directories retain progressive `SkillsProvider` loading.
   Local hooks cannot claim mediation of provider-hosted execution. Unsupported
   MAF gateway combinations are rejected rather than silently replaced by GHCP.

   The generator creates complete agent and **two independent** ACA Docker
   contexts. Each vendors its own required sibling packages. It merges
   `azure-services.yaml`, existing hooks/services, dependencies, agent env, and
   the Bicep module reference. Legacy `agent.yaml` files, when present, retain
   synchronized environment declarations; the authoritative current manifest is
   `azure.yaml`. Re-running into managed directories is intentionally refused:
   review a fresh generation rather than overwrite application changes.

   All five commands prepare their complete changes in a same-filesystem shadow
   before publishing. Unsupported composition, invalid inputs, and preparation
   failures leave the project unchanged, so correcting the input permits a clean
   retry. Publication replaces individual files atomically and rolls back its own
   writes on failure; it never swaps or deletes the project root. Symlinked managed
   paths are rejected. Unrelated files and concurrent unrelated additions survive.
   This is **cooperative per-file atomicity**, not crash-atomic multi-file storage:
   do not concurrently edit managed paths. A conflicting rollback preserves its
   private recovery directory and reports `generation_rollback_conflict_recovery`
   rather than overwriting the other writer's file.

4. **Build and publish the generated contexts**, supplying `PYTHON_IMAGE` as a
   digest-pinned Python 3.12/Linux amd64 base. The Dockerfiles checksum OPA against
   the shared pin; all governance Python dependencies are exact. Record the
   registry digest, source commit and source-tree digest for each build.

   Freeze the agent definition **before** bootstrap registration:

   ```sh
   python <catalog>/skills/threadlight-deploy/references/governance/generate.py \
     agent-image --project <pilot> --contract <contract.json> --configuration <agent-image.json>
   ```

   This JSON contains `agent_image` (complete digest-pinned reference) and
   `spool_directory` (absolute, host-owned local retry directory; **not** a
   persistence claim). MAF generation selects `audit_delivery: "remote-ack"`.
   Register that exact
   definition, observe its version and instance identity, and then assign its
   service app roles. Until services/roles are ready, the real agent is unhealthy;
   no placeholder revision is used.

   GHCP has an additional ordered step: build the **agent image first**, produce
   the final Task9 registry binding that agent's real image/version/identity,
   publish/sign that bundle, then run:

   ```sh
   python <catalog>/skills/threadlight-deploy/references/governance/generate.py \
     stage-gateway --project <pilot> --contract <contract.json> --configuration <gateway-stage.json>
   ```

   The stage JSON contains `gateway_bundle`, `policy_digest`, `signed_envelope`,
   and digest-pinned `agent_image`. Rebuild the gateway from this final context.
   The returned `gateway_source_digest` must accompany the gateway image.
   Registry action names alone are insufficient: every action must match the
   selected pre/post intervention points and declared native policy IDs/targets.
   Approval aliases (`approval`, `human-approval-record`) require nonempty
   registered roles, which Task9 enforces **even for a Rego allow**. Each action's
   roles remain its own subset of the configured roles, never a global union.
   Output requirements require a post-policy binding. Audit, receipt, idempotency,
   signed-bundle and authorization aliases retain Task9's mandatory controls.
   `operator-review`, non-tool lifecycle semantics, and evaluate-only GHCP
   environments are unsupported, not silently certified. GHCP currently binds
   only `preproduction`/`production`; MAF retains the four-environment matrix.
   MAF does not provide the gateway's durable transaction/idempotency guarantee
   and rejects those requirements. Requirement spelling is normalized in the
   portable contract without dropping requirements or changing caller input.
   **Do not bake the final agent image digest into that same image.** GHCP's
   registry/policy digest is deployment-supplied; MAF's local bundle must not
   contain `gateway-registry.json`. Runtime image/version values are trusted
   deployment inputs outside the source image.

   **Policy trust has three explicit phases.** Before `stage-gateway`, the
   package's `configuration.policy_digest` and `signed_policy` describe the
   **bootstrap** bundle only, not a live signed final gateway. Staging adds the
   registry and changes the bundle digest; safe-check validates the staged
   envelope's content digest, policy identity/expiry, native schema, selected
   controls and registry-to-agent-image association using the same validator as
   stage/bind. After binding, `bindings.policy_digest` must also equal the final
   envelope digest and `bindings.gateway_config.policy_digest`, with exact
   agent/version, workload identities, service scopes, endpoints and approval-role
   associations. GHCP must not carry a native-runtime policy association.

   The frozen GHCP package retains its bootstrap digest; its agent configuration
   omits that digest rather than embedding the final gateway digest and creating
   an image hash cycle. The gateway's final service configuration supplies the
   final digest. MAF instead always verifies its embedded bundle against the
   immutable package digest and envelope.

   These static sources are declarations, **not signing authorities**. Static
   results identify `bootstrap-package`, `staged-envelope`, `deployment-binding`
   (or MAF's `immutable-package`) and never claim signature verification or live
   enforcement. The collector verifies the final signature with the trusted
   versioned key, observes the exact deployed configuration/identities, and
   requires authenticated receipts and effect evidence for that final digest.
   Rehashing a modified bundle and editing local JSON cannot confer that trust;
   an unstaged or unbound bootstrap cannot produce live enforcement evidence.

5. **Bind, then deploy only the resulting pinned revisions.**

   ```sh
   python <catalog>/skills/threadlight-deploy/references/governance/generate.py \
     bind --project <pilot> --contract <contract.json> --configuration <deployment.json>
   ```

   The deployment JSON contains:
   - `images`: `agent`, `control_plane`, `gateway`, each a complete `@sha256:…`
     reference. Mutable tags and absent images fail before writing.
   - `infrastructure`: the foundation configuration.
   - `bindings`: `key_id`, `agent_principal`, `agent_client_id`, `agent_version`,
     `gateway_principal`, `gateway_client`, `downstream_principal`,
     `downstream_client`, `policy_id`, `policy_version`, `policy_digest`,
     `allowed_endpoints`, `control_workloads`, `gateway_workloads`.
     Workload maps use the exact Task8 schema: principal/object ID keys, values
     `{client_id, agent_id, policies}`. Include both agent and gateway in the
     control-plane allowlist, and only authorized agent instances at the gateway.
   - `observations`: trusted read-only exports: `environment` ARM resource,
     `foundation` outputs, `applications`, `app_role_assignments`,
     `downstream_authorizations`, `control_plane_url`, `gateway_url`; private
     mode also needs the observed `foundry_agent_subnet_id`.
     Also supply `private_dns_links`: actual ARM virtual-network-link objects
     for all three private DNS zones, with successful provisioning and the
     configured VNet ID. Missing links prevent binding.
   - `spool_directory` is fixed in the agent-image step. It is local retry safety
     storage, not an operator assertion of a persistent hosted-agent mount.
   - GHCP also requires `gateway_bundle` and `gateway_source_digest` from step 4.

   The binder loads the real Task8 `AzureConfiguration` and Task9 `Configuration`
   models before emitting serialized service configuration. Bicep passes these
   as JSON, and `service_entry.py` validates and supplies the services' existing
   `GOV_CONFIG_FILE` / `GATEWAY_CONFIG_FILE` APIs. A failed startup has no healthy
   fallback. The control plane's authenticated `/health` must be checked with
   its audience token: its TCP liveness probe is deliberately **not** governance
   readiness. The gateway returns per-action readiness.

   Provision the **bound Bicep service phase**, not `azd deploy` for these two
   ACA services: their predeploy guards prevent azd from rebuilding a new mutable
   tag after the image digests have been bound. Do not run blanket `azd up` after
   bootstrap. The service declarations retain the real package/build contexts,
   but revision deployment belongs to the digest-bound IaC.

   **The binder does not change the bootstrapped agent definition.** Changing its
   environment after discovering its instance could create another version/identity
   and an infinite bootstrap cycle. Only the ACA configurations/allowlists change.
   MAF reads the actual platform-injected `FOUNDRY_AGENT_VERSION` (never declares
   that reserved variable) and confirms its credential-token subject through the
   authenticated Task8 context-health API. Decoded token claims alone are not
   authentication. GHCP compares gateway policy health to the authenticated Task8
   signed-bundle lookup, so it does not bake a future registry digest into its
   agent image/definition. The deployment manifest still records the exact
   version, instance, image and policy digest.

   Binding rereads the **actual frozen agent configuration**, not just package
   metadata. Environment, policy ID/version and approval roles must agree with
   deployment inputs. MAF additionally verifies the embedded bundle digest,
   metadata and frozen signed envelope (tenant, key, identity and expiry).
   GHCP revalidates the final signed registry at staging and binding. Neither a
   workload allowlist for another policy with the same hash nor a production
   label on a development image is accepted. Offline checks cannot authenticate
   a signature without the authority: runtime Key Vault verification remains
   mandatory and fails closed.

## GHCP invocation cleanup

Every acquired resource has independent bounded cleanup, including partial
startup: unsubscribe, native session disconnect, SDK stop/force-stop, owned
subprocess reaping, relay join/cancellation, socket, HTTP client and credential.
The cleanup coroutine runs shielded but is always joined; caller cancellation is
propagated after cleanup attempts. Failures produce stable `governance_cleanup_*` diagnostics
without exception text or SDK cleanup tracebacks. The published SDK is unchanged.
Async deadlines depend on cooperative cancellation; uninterruptible third-party
code or an OS-level failure cannot be made crash-safe by this adapter.

The generated GHCP container installs an idempotent **process-lifetime** privacy
policy when imported, before starting SDK work. All diagnostics from `copilot.client`,
`copilot._jsonrpc`, and their descendant loggers are intentionally sanitized at every
level, not just during cleanup. Logger filters run **before direct or propagated
handlers**. A name-gated `logging.Manager.getLogger` wrapper also installs the same
filter on future descendants, including `getChild()` results; ancestor filters alone
do not protect propagated records. Installation uses logging's lock and concurrent
invocations never remove the shared policy. There is no production teardown API.
Message arguments, exception caches, stacks, and structured extras are cleared,
not searched for token patterns.
Transport diagnostics use `governance_cleanup_sdk_transport_diagnostic`; the client
keeps `governance_cleanup_sdk_diagnostic`. Severity and source locations remain
available, but raw SDK messages and timing extras do not. Unrelated application
loggers, handlers, levels, and propagation remain untouched. Other SDK namespaces
require explicit coverage when integrating/upgrading the SDK.

SDK 1.0.1 joins its stdio reader threads with native time bounds **before** killing
the child. A reader may still be alive after successful `close_invocation()` return;
an empty failure list is not proof that every SDK thread terminated. The adapter
still reaps its owned subprocess and closes the relay/socket and other resources,
subject to the failure bounds above. Lifetime sanitization protects delayed native
diagnostics after return without relying on thread-inherited `ContextVar` state or
private SDK thread-lifecycle changes. Native regression tests pause the stderr
reader before its real warning, release it after cleanup, and join readers before
the test harness alone restores logging state.

## Hosted MAF audit delivery

The pinned `azure-ai-projects` `HostedAgentDefinition` / `ContainerConfiguration`
models do not expose persistent-volume attachments. An ACA volume would not
attach storage to that Foundry container. Neither `/home`, an absolute path,
ownership, nor successful fsync proves platform persistence.

The generated host therefore uses the existing Task8 control plane's centrally
durable receipt store: **required audit must receive an authenticated `/receipts`
ACK before the native application effect**. It first writes a payload-free local
retry record. Local write failure blocks required effects; CP outage, timeout,
wrong ACK or stopped exporter also blocks them and yields dependency readiness
503. No local-only healthy fallback exists, even on a Docker overlay or bind
mount. Bind-mount detection is deliberately not an alternative durability mode.

`audit_delivery.py` owns a separate event-loop worker, workload-only short-lived
credential and `ReceiptClient` using the packaged control-plane scope. The host's
startup/shutdown context starts replay and periodic retries, then cancels retries
and bounds resource closure. Synchronous native append waits at most the client
deadline plus scheduling allowance; it never submits work to its own blocked
agent loop. This bounded wait can pause other work on that agent loop.

Native records persist `action_id` (the actual tool name or lifecycle point) and
UTC `recorded_at` once. `audit_id` becomes `receipt_id`; `policy_hash` becomes
`policy_digest`. The exact deployed version/image and bounded hashed correlation
are preserved. The canonical Task8 body is persisted before its first send and
reused unchanged on retry, including after process restart with the same files.
Only an exact remote ACK marks it delivered; Task8 deduplicates identical IDs and
bodies. `error` is a supported decision. No arguments, output or exception text
are exported. Incomplete legacy pending records remain pending/unhealthy rather
than receiving invented identities/timestamps.

Optional audit can remain locally pending during a CP outage; it is not a
durable-before-effect promise. Local pending files can be lost when the sandbox
is deleted. Required effects remain safe because their receipts were already
centrally acknowledged. Monitor pending-delivery/worker failure diagnostics;
Docker/ASGI tests are not live Azure durability or effect-closure evidence.

## Infrastructure and Entra contract

`infrastructure.json` requires `prefix`, `storage_name`, `cosmos_name`,
`vault_name`, `acr_id`, `acr_authorization: "rbac"`, `tenant_id`, `agent_id`,
`runtime`, `enable_gateway`, `control_plane_app_id`, `gateway_app_id`,
`human_clients`, `approver_subjects`, `auditor_subjects`, `approver_roles`,
and `network`. No secrets, connection strings or signing material.

The existing ACR must use classic RBAC: `AcrPull` is scoped to that registry,
and each app names its own UAMI in its registry configuration. ABAC repositories
require a separately reviewed repository-scoped module and are rejected.

Entra app registrations are **required pre-existing inputs**, not imaginary ARM
resources. Each service application must:
- expose its own `api://<appId>` identifier, use v2 access tokens, and define an
  enabled Application role `Governance.Workload`;
- have that app role assigned to every explicitly allowed workload **service
  principal** (an Azure resource role assignment is not an Entra app-role grant);
- expose the delegated `Governance.Read` and `Governance.Approve` permissions for
  the listed human clients, with admin consent and explicit approver/auditor user
  roles/assignments. No self-approval or workload approver identities;
- use the configured application ID as the v2 token audience. The model token
  (`https://ai.azure.com/.default`) is never reused as a gateway/service token.

Binding cross-checks both packaged scopes against their distinct infrastructure
application IDs and both packaged URLs against observed endpoints, including
MAF's unused gateway contract. It also checks the frozen agent endpoint/image
environment before writing any deployment artifact. Only canonical lowercase
UUIDs and exact `api://<UUID>/.default` scopes are accepted: Task8's strict v2
JWT audience comparison does not case-fold or accept an `api://` audience alias.
The gateway's control-plane client scope is taken from that same validated
package, not independently reconstructed.

Observation application entries carry `appId`, `servicePrincipalId`,
`identifierUris`, `api.requestedAccessTokenVersion`, and `appRoles`. App-role
assignment records use Graph's `principalId`, `resourceId`, `appRoleId`.
Downstream authorization entries specify `principal_id`, `scope`, and `role_id`;
only the **downstream** UAMI receives the actual downstream API role.
Review consent and human-role assignments before provisioning revisions; the
offline binder cannot prove that a supplied observation still matches Entra.

There are separate control-plane, gateway, downstream and publisher UAMIs.
The control-plane owns one `/scope` Cosmos container for **both approvals and
receipts** (the actual Task8 CAS adapter), while the gateway owns a separate
`/scope` idempotency container. No TTL, multi-writer setup, or blind redispatch of
pending records. The agent has no approval-store or downstream write permissions.
The gateway verifies the Key Vault signature because Task9 requires it, but
cannot sign. The publisher can sign/upload; the control plane cannot sign.
RS256 keys are non-exportable and runtime trust uses a pinned key version.

## Network posture

`network.posture` is required:
- `private-required`: supply `environment_id`, `vnet_id`,
  `private_endpoint_subnet_id`, `foundry_injection_subnet_id`,
  `blob_dns_zone_id`, `cosmos_dns_zone_id`, `keyvault_dns_zone_id`.
  The existing ACA environment must be VNet-integrated/internal; Foundry
  **must already be injected into a distinct reachable subnet**. Existing DNS
  zones must be linked to that VNet. Bicep creates the Blob/Cosmos/Key Vault
  private endpoints and zone groups and disables public resource access.
  ACA app ingress is enabled outside the ACA environment **inside the private
  environment's VNet**, so injected Foundry can reach it. App-internal ingress
  alone would not allow that path. Validate DNS, routing, egress rules, endpoint
  approval and HTTPS reachability from the actual hosted sandbox before promotion.
- `public-pilot`: supply `environment_id` and a nonempty restricted `allowed_ips`
  list including the actual workload/publisher egress IPs. `0.0.0.0/0`, `::/0`
  and missing lists fail. All services still authenticate Entra tokens;
  resource firewalls use no trusted-services catch-all bypass.
  The Cosmos `0.0.0.0` bypass sentinel, IPv6, and Storage-unsupported `/31`
  ranges are rejected; individual `/32` entries become plain Storage IP rules.

Missing private prerequisites are a deployment blocker, not permission to weaken
the selected posture. This generator does not mutate Foundry network settings.

### Preprovisioned private signing key

`private-required` also requires infrastructure input `existing_key_id`: the
actual versioned URI `https://<vault_name>.vault.azure.net/keys/policy/<32-hex-version>`.
Before `foundation`, provision the dedicated RBAC-enabled vault with public
access disabled, soft-delete and purge protection, its private endpoint and
linked DNS. From an authorized executor inside that network, create the
non-exportable RSA-3072 `policy` key with `sign`/`verify` operations and record
the returned versioned URI. A dedicated initial-creation identity needs only
key read/create permission at that vault; revoke its creation grant afterward.
The publisher's separate key-scoped sign permission is not key-creation authority.
Both hosted runtimes receive only key-scoped read/verify permission after their
actual identity is bound: GHCP also verifies remote bootstrap directly with Key
Vault, not solely through the gateway.

The generator rejects a missing private key prerequisite, an invalid selected
URI, or another vault/key name before editing the pilot. With `existing_key_id`,
both IaC phases reuse that key without an ARM key PUT or an ARM key-version
lookup; other resource provisioning and key-scoped runtime grants remain active.
Do not enable public access, trusted-services bypass, policy exemptions, or
exclusion tags to make an ARM key-creation request succeed. Public deployments
can also opt into an existing key; omitting the field retains their ARM key
creation path. This URI is configuration, not signature/health evidence:
runtime and collector authentication against the pinned key remain mandatory.

## What this proves

Run `python scripts/ci/run-governance-pin-tests.py --deployment` in the catalog.
The gate uses published pins, real native constructors, built portable packages,
HTTP relay tests and Bicep compilation. External authority/model transports are
test doubles; no live Azure resources are involved. A syntactically valid signed
envelope is only **packaged**, then cryptographically verified against Key Vault
at runtime. No self-reported `signature_verified` field is accepted.

Source generation, package installation and health are not effect closure.
GHCP does not expose full Agent Hooks or full-output mediation. Run the Task9/11
downstream bypass/deny/replay/approval probes and Task15 deployment proof against
the exact image, instance, policy digest and environment before calling it
action-governed. Assessment evidence cannot replace those probes.
Copilot's SDK tool-permission callback is not a human approval authority:
`PermissionHandler.approve_all` never substitutes for Task8's authenticated
human decision and atomic nonce consumption at the gateway.

## API sources

- [Hosted agent `azure.yaml`: prebuilt image and environment schema](https://learn.microsoft.com/azure/foundry/agents/concepts/azure-yaml-reference)
- [MAF `ResponsesHostServer`](https://learn.microsoft.com/agent-framework/hosting/foundry-hosted-agent?pivots=programming-language-python)
- [Container Apps resource schema](https://learn.microsoft.com/azure/templates/microsoft.app/containerapps)
- [Cosmos SQL role definitions](https://learn.microsoft.com/azure/templates/microsoft.documentdb/databaseaccounts/sqlroledefinitions)
- [Blob immutability policy schema](https://learn.microsoft.com/azure/templates/microsoft.storage/storageaccounts/blobservices/containers/immutabilitypolicies)

The native probes verify the exact installed Python APIs rather than relying on
the companion skill's historical signatures. In particular, Copilot 1.0.1 accepts
`enable_config_discovery=False` on `create_session`; `mode` belongs to the client,
not that method. The pre-MCP hook returns only `metaToUse`, not invented headers.
# Optional Task 11a producer binding

`probe_observability` is a strictly validated staging-only opt-in. Normal/off
generation is unchanged. Native probe deployment uses a separately signed,
mounted registry associated with the embedded native policy digest, avoiding an
agent-image hash cycle. Binding remains **declared-unverified** and never asserts
live enforcement. The fixture is operator-installed, not auto-added to Azure YAML.
See [configuration, wire schema and least-privilege prerequisites](../../../threadlight-safe-check/references/probe-fixture/README.md).
