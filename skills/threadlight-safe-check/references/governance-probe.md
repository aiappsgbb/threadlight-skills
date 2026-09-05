# Deployed governance collection

This is an explicit staging/preproduction noop test, not a business-operation
test, Azure deployment tool, or remote attestation system. Read
[the producer protocol](probe-fixture/README.md) before configuring it.

## Install

Use the existing pinned Linux amd64 governance environment with the published
ACS wheel and checksum-verified OPA (`ACS_OPA_PATH`). From the catalog:

```bash
python -m pip install ./skills/threadlight-govern/references/control-plane \
  ./skills/threadlight-govern/references/gateway ./skills/threadlight-safe-check
threadlight-governance-probe --help
```

The collector wheel contains its shared validators, source-reference checks and
the existing GHCP structured-command observation primitives. It works without
the catalog checkout. It pins `azure-ai-projects==2.3.0` and
`openai==2.54.0`; do not upgrade the native SDKs to make a test pass.
The exact-pin CI runner builds this package and rejects missing/skipped native,
real Copilot CLI, gateway and collector integration cases.

## Protected operator inputs

The project must already contain:

- `specs/manifest.json` with its governance contract and deployment manifest;
- Task 10 `.threadlight/governance-package.json`,
  `.threadlight/governance-deployment.json`, and the actual packaged agent/services;
- the explicitly installed fixture's mounted `ProbeConfiguration`, copied as a
  protected project-relative configuration file (never include credentials);
- the signed probe bundle and envelope. Native MAF uses the separately signed,
  **post-image** association registry whose `native_policy_digest` matches the
  independently signed embedded runtime bundle. Gateway uses its normal policy.

Create `.threadlight/governance-probe.json` with exactly these keys:

| Key | Value |
|---|---|
| `schema` | `threadlight-governance-probe-input/v1` |
| `selection` | Object containing `subscription` (GUID or unambiguous CLI subscription name), `resource_group`, `project_resource_id` (full Cognitive Services project ARM ID), `agent_name`, `requested_version` (current version, or null) |
| `controller_principal` | Dedicated controller Entra object ID |
| `controller_client_id` | Dedicated controller managed-identity application/client ID |
| `bundle_path` | Project-relative signed probe bundle directory |
| `signed_envelope_path` | Project-relative Task 8 `SignedBundle` JSON file |
| `fixture_configuration_file` | Project-relative protected fixture `ProbeConfiguration` JSON |
| `services` | `fixture` and `control_plane` entries; also `producer` for a gateway |

Every `services` entry contains exactly the expected `resource_id` (ACA ARM ID),
`url` (HTTPS origin), `principal_id`, `client_id`, and immutable `image`
(`registry.azurecr.io/repository@sha256:...`). Control-plane/gateway identities
and images must match Task 10 bindings. Fixture identity and its downstream-only
caller allowlist must match the protected fixture configuration.

Producer/control-plane URLs, audiences, allowed endpoints, policy identity and
required controller scope come from those bound configurations, not free CLI
endpoint overrides. The signed registry pins the producer origin
(`gateway_url` ending `/mcp`) and the two fixed fixture endpoints. The loader
rejects mismatches instead of rewriting the signed declarations.

Each producer, fixture and Task 8 API must configure the controller subject/client
with the requesting workload and `governance_probe_noop` action. Assign
`Governance.Probe.Control` for registration (it also permits reads), or
`Governance.Probe.Read` for independent receipt/status readers. The collecting
principal needs Control; Read alone cannot register a fresh run. The controller
must not be the agent's application or principal.

Production service tokens use managed-identity-only `DefaultAzureCredential`;
environment keys, interactive users and accidental CLI-token fallback are disabled.
The existing isolated Azure CLI context must independently have read access to
the selected ARM subscription/project/services and Foundry agent. Commands always
use `--subscription`; collection never runs `az account set` or login. Key Vault
access is limited to the configured versioned signing key's get/verify operations.

For native MAF, the registered producer origin must expose the **actual host's**
authenticated `/governance/probes/{UUID}` routes and `/readiness` on the protected
operator network. These are container routes, **not invented public Foundry
proxy endpoints**. Installing/reaching that ingress is an operator prerequisite.
No reachable producer or fixture means UNKNOWN; do not substitute local state.
Gateway uses its existing `/health`; Task 8 uses its existing public health
endpoint plus authenticated, scoped receipt reads. The fixture has no fabricated
health route: its authoritative registration and point-read APIs check storage.

## Execute

From the pilot:

```bash
threadlight-governance-probe --project . \
  --configuration .threadlight/governance-probe.json \
  --output .threadlight/governance-live.json
threadlight-safe-check --phase post-deploy --rg staging-resource-group
```

`safe_check.py` postdeploy automatically uses this exact configuration when
governance is selected. With no configuration it emits an unverified gap and
does **not** invoke any agent. `--force` never relaxes safety, signatures,
scope, freshness or terminal-state requirements.

The supported observation contract is Foundry **v1**, using the installed
2.3.0 models' `agent_endpoint.version_selector.version_selection_rules`,
`versions.latest`, `instance_identity`, and
`definition.container_configuration.image`. Only one unambiguous 100% route,
an active immutable version, and protocol 2.0.0 are accepted. Unsupported or
missing metadata is unverified, never filled from user selectors.

For native MAF the collector uses the actual project SDK's agent-bound Responses
client, consumes SSE to completion and includes the exact observed version
reference. Copilot uses the documented agent endpoint's Invocations protocol and
the generated wrapper's `input` body. It does not call the gateway noop directly.
The currently routed version is checked immediately before each invocation and
again afterwards; the request's durable producer/receipt deployment must match.
Azure and service changes invalidate the whole probe pair.

### Exact target and configuration contract

The parent safe-check resolves its tenant and subscription IDs through
`az account show`, or `az account show --subscription <ID-or-name>` when the
optional safe-check `--subscription` flag is supplied. It never changes the
active account or logs in. Its `parent_target` records only canonical observed
IDs and the resolved `--rg` / `AZURE_RESOURCE_GROUP`; collector configuration
is **not** the source of parent scope. Missing or malformed account context is
an unverified gap, with no resource reads, registrations or agent invocations.
Every parent Azure resource read is pinned with `--subscription <observed-ID>`,
so a concurrent default-account switch cannot redirect the gate.

Existing `deployment_manifest.tenant_id` / `subscription_id` (also `tenant` /
`subscription`) are constraints, not overrides of the selected account.
Subscription display names are canonicalized through `az account show
--subscription`; all declared selectors must match the observed parent account.
The parent passes all three independent constraints as
`required_target={"tenant": ..., "subscription": ..., "resource_group": ...}`
to `collect_project`/`collect`. They must match the frozen/selected target and
the independently observed ARM/Foundry target **before registration or invocation**.
An identical RG name in another subscription or tenant is not a match.
A mismatch fails without rewriting selectors or losing existing gaps.
Direct collectors require `expected_deployment` (agent ID/version,
image digest, environment, subscription and resource group), independently of
the signed registry. The reusable `evaluate_pair` requires `expected_target`:
those six fields plus `tenant`, `subject` and `client_id`. All must match the
registration and observed facts. Environment comes from frozen configuration,
**not** an invented ARM environment observation.

Static checks compare the closed generated host wiring in `env`,
`environmentVariables`, and existing legacy `agent.yaml`, including pre-image
URLs. Bound service settings and generated parameter files must agree with the
frozen deployment. Unrelated application environment values may differ.

The collector's `runtime_configuration` expectations are loaded from Task10's
generated host and bound service configuration, not copied from observations.
Foundry/ARM observations retain only `configuration_digests` over a closed
allowlist: the four generated host settings, and service `TL_GOV_SERVICE`,
`AZURE_CLIENT_ID`, `GOV_CONFIG_JSON` and `GATEWAY_CONFIG_JSON`. The two JSON
settings are parsed using their strict service schemas before hashing, binding
their roles, scopes, policy identity, mode-related settings and full endpoints.
No arbitrary environment values, API keys, raw URLs from these settings, or
provider response dumps are saved. Unknown `GOV_`-prefixed variables are not
selected. Missing or unresolved required settings (including secret references)
are unverified. First observations must match expectations; changes before each
invocation or at final observation invalidate the pair.

`configuration_evidence` separates declared and observed configuration digests.
Image-embedded native configuration and mounted native/fixture secret files are
**not readable through ARM**. Their bounded configuration digests are explicitly
`declared_file_digests`, not Azure observations of file interiors. Signed policy
checks and actual noop execution remain required; these declarations do not
establish general runtime/configuration attestation.

Both runs must be freshly registered with zero counts and null terminal at both
services. An allow needs one received/intercepted/dispatch/completed producer event
and one independent fixture effect. A deny needs one received/intercepted/completed,
zero dispatch, zero fixture effect and a durable deny receipt. Registration alone
is never success. Polling and HTTP/SDK operations are bounded; an ignored prompt
is **not verified**, not proof of a failed policy decision.

## Artifacts and limits

The postdeploy file retains every original `gaps` entry and adds
`governance_health`, `governance_probes`, `governance_gaps`, separate
`declared_selection`/`observed_target`, and payload-free authoritative states and
receipts. On completed collection, `governance_manifest` uses the shared
`threadlight-governance-manifest/v1` collection-evidence variant. It intentionally
does not fabricate wheel-provenance fields from service health. Static reports
identify pre-image facts that cannot yet be verified without calling them enforced.

`skills._shared.probe_evidence.evaluate_pair` and the shared manifest validator
are the reusable evidence evaluators. Native receipt hashes use the actual
native hook-context digest committed at interception; fixture/gateway wire action
hashes instead use Task 9 canonical facts plus exact probe arguments. They are
different codecs/inputs and must not be compared as though interchangeable.

Only `governance_probe_noop` at its exact pre-tool binding can become `enforced`.
Other selected tool/lifecycle bindings remain unverified and blocking; unbound
read tools remain explicitly unbound without becoming new blocking gaps.
These probes do not prove business idempotency, approvals, all-agent output
mediation or generalized effect closure.

Local tests use real ResponsesHostServer, native hooks/ACS/OPA, the actual
Copilot SDK/CLI, FastMCP, Task 8 and the fixture. Only external model HTTP,
Entra/JWKS/signing, ARM/Foundry responses and storage are local seams.
They are **not live Azure proof**. Saved JSON can be modified by its owner:
schema validity or a collector provenance label is not authentication or
attestation. Recollect through trusted transports rather than accepting uploaded
files as independent deployment evidence.
