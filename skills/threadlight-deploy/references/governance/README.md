# Generated governed deployments

These references edit a **pilot**, not this skill catalog. They never call Azure,
change an existing Foundry account, choose a different runtime, sign with a local
private key, or report enforcement from an assessment. Use Python 3.12/Linux
amd64 and the shared published pins. `generate.py --help` is the executable
entrypoint; package inputs are JSON. No customer identifiers are baked into these
templates.

## Order and required inputs

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
Cleanup runs shielded but is always joined; caller cancellation is propagated
after resources close. Failures produce stable `governance_cleanup_*` diagnostics
without exception text or SDK cleanup tracebacks. The published SDK is unchanged.
Async deadlines depend on cooperative cancellation; uninterruptible third-party
code or an OS-level failure cannot be made crash-safe by this adapter.

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
