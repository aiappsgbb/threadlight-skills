# Canonical Returns Triage — native runtime governance

This is an **offline, unverified deployment template**, not the old July 2026
live capture. Historical AGT v4 policies and generated v2/readiness receipts are
preserved only in [`archive/`](archive/README.md). They certify nothing here.

The assistant correlates RMA → order → customer and records exactly one outcome:
`approve_refund`, `deny_refund`, `escalate_to_supervisor`, or `request_more_info`.
**There is no payment/settlement tool.** `approve_refund` is a recommendation.

## Actual enforcement path

```text
existing Citadel model proxy
  → pinned MAF Agent / ResponsesHostServer
  → one shared native Agent Hooks bundle
  → ACS + OPA returns-write-v1 at pre_tool_call
  → Task8 approval (only on escalation) + durable receipt ACK
  → exact authorized arguments + Cosmos revision
  → transactional case replace / decision-audit create
```

Only `returns_apply_decision` is bound. The four read tools keep their names and
business responses and never invoke ACS. Their trusted backend implementations
record actual read results privately in the native invocation scope. No model
`checks_complete`, evidence boolean, role claim, or pasted order is authoritative.

SAFE Rego checks declared case scope, ordered read receipts, freshly reread case
revision, order/customer correlation, completeness, eligibility, risk, disposition,
and recognized policy citations. This proves backend prerequisites, **not that a
model actually executed named prose skills**. The cooperative trusted-host seam
is not an attestation against hostile Python code. Its snapshot is bounded,
run/action/principal-bound, and expires within 20 seconds. Cosmos `if_match_etag`
prevents a later backend revision from being overwritten. Identical recorded
decisions return their existing audit id without another mutation.

Case state and decision audits live in **Cosmos**, partitioned by `/case_id`.
OMS/customer JSON is immutable mock input; `returns.json` specifies operator
seed data only. There is no automatic seed, reset, or filesystem business store.
The Task10 payload-free local receipt spool is retry safety only: required
durability is the Task8 **remote ACK before the effect**.

## Materialize real source copies

Run under the repository's provisioned Linux Python 3.12 governance environment
(see `scripts/ci/run-governance-pin-tests.py`). ACS's published pinned wheel is
Linux amd64. No Azure operations are performed by these commands.

```bash
python examples/returns-triage-governed/scripts/materialize.py \
  --output .governance-validation/returns-source
```

The script invokes Task10's `generate.py package-native` command. It copies the
real shared runtime, validator/import closure, `maf-container.py` as
`governance_host.py`, Task8 service modules, dependency pins and Dockerfiles,
then builds the real ACS bundle. It never synthesizes an allow-only provider.
The output includes:

- `src/agent/`: runnable source context, policy bundle, mock samples and skills.
- `src/governance-control-plane/`: the actual Task8 service build context.
- `source-package.json`: source digest and explicitly unverified status.

No network/IaC is generated or provisioned. Existing `infra/` files in this
checkout belong to the historical deployment and are **not copied or applied**.
No governed-tool-gateway run service is needed for native local enforcement.
The unmaterialized Dockerfile deliberately refuses to build.

## Publish and configure before production

1. Use the output's unsigned `policy/bundle-metadata.json` digest. Have the
   authorized Task8 publisher publish `returns-write-v1` version `1` with a
   bounded expiry and a real **versioned Key Vault signing key**. Retrieve its
   actual `SignedBundle` envelope. The publisher is an operator role, not an
   agent tool. No signing key or envelope is fabricated or committed here.
2. Fill [`deployment-config.template.json`](deployment-config.template.json).
   Its required types are defined in `src/agent/deployment_config.py`; placeholder
   strings are invalid configuration. Provide existing tenant/UAMI IDs, Task8
   audience and endpoint, supervisor app roles, Cosmos account/database/container,
   signed envelope path, and the existing Citadel Foundry project proxy route.
   The proxy must already serve `/api/projects/...` under
   `https://apim-citadel-hub.azure-api.net` / `tl-returns-triage`. A direct model
   endpoint is rejected; this example does not create or change that route.
3. Materialize a **new** directory with `--configuration <filled-json>`.
   Envelope identity/digest/expiry are checked during packaging; the running
   host verifies the signature with the configured Key Vault authority.
4. Build the generated Docker contexts with the operator's approved immutable
   `PYTHON_IMAGE` build argument. Publish real images, resolve their registry
   digests and actual Foundry agent version, then fill `azure.yaml` placeholders.
   **Do not use a sample SHA or version as deployment evidence.**
5. Configure the actual Task8 `AzureConfiguration` (`GOV_CONFIG_JSON`): separate
   service identity, workload subject/client allowlist for this agent/policy,
   human-client/subject/role allowlists, persistent Cosmos/Blob stores, and the
   same signing key. The generated service runs `service_entry.py`, not a health
   stub. Provision/seed the business Cosmos container externally with `/case_id`;
   the runtime validates the partition and uses UAMI-only credentials.
6. Supply runtime `AZURE_CLIENT_ID`, `FOUNDRY_PROJECT_ENDPOINT`,
   `AZURE_AI_MODEL_DEPLOYMENT_NAME`, `FOUNDRY_AGENT_VERSION`,
   `TL_GOV_IMAGE_DIGEST`, `TL_GOV_SPOOL_DIR`, and `GOV_CONTROL_PLANE_URL`.
   Missing/mismatched configuration, policy trust, approval readiness, or audit
   ACK prevents startup/effects. No diagnostic-200 fallback exists.

Deployment is intentionally not claimed. Existing private reachability, scoped
RBAC, Cosmos durability/retention, signing authority and real image/version
association require operator evidence.

## Supervisor flow

For a high-value/high-risk return, ACS requires Task8 native approval before
recording the supervisor handoff. The service creates a pending request, accepts
only an authenticated allowlisted human's decision for the **exact** intent,
consumes its nonce once, and ACKs the receipt before the Cosmos transaction.
Approval records and business audits join by action/policy hashes.

Absent, invalid, rejected, expired, replayed or changed-action approval never
writes a case. Approval is a bounded native wait, not a 24-hour Teams workflow.
After expiry a fresh invocation must reread backend facts and request fresh
approval. A Teams/workspace review frontend and asynchronous long-lived resume
are not supplied. An approved handoff still records `escalate_to_supervisor`,
not a finalized high-risk refund.

## Optional preproduction noop probe

```bash
python examples/returns-triage-governed/scripts/materialize.py \
  --output .governance-validation/returns-probe-source --probe
```

This explicitly adds reserved `governance_probe_noop` and vendors the **shared
Task11 producer/library**, not a gateway run service. The same published native
hook path evaluates allow/deny and records native interception before dispatch.
For a configured package, `--probe` additionally requires `preproduction`,
subscription and resource-group inputs. Production probe activation is rejected.

The operator must separately install the fixed
`skills/threadlight-safe-check/references/probe-fixture` package and provide the
Task11 `ProbeConfiguration`, distinct downstream identity, strong-consistency
probe stores, authenticated controller registration, and independently signed
registry binding actual image/version/environment and `native_policy_digest`.
Mount that native configuration at `/mnt/governance-probe/config.json`.
`install_probe_runtime` performs the shared signature/identity/association checks.
The fixture has only the fixed noop/outcome protocol, no business writes.

Missing configuration means **unverified**, never stale historical success.
Noop success proves only that reserved action's route, **not** the
`returns_apply_decision` binding or the whole agent.

## Validation and limitations

The existing no-skip Linux deployment CI runs `tests/` here. Native tests exercise
the actual served factory, ACS/OPA, authenticated Task8 approval and receipt APIs,
and the actual Cosmos adapter through an explicitly mocked SDK backend. They
cover denied writes, correlated allow, misordered/missing/forged evidence,
cross-case/customer scope, stale revisions, forbidden payments, idempotence,
human-review failures/replay, and the real optional noop producer/fixture.
Local RSA keys/JWTs and model/backend doubles are test-only, not tenant evidence.

`specs/governance-manifest.json` is the current canonical **offline inventory**:
write binding unverified, reads unbound, no live probes, no deployed image/version.
It can be regenerated with the shared `govern_check.py --emit` command using the
committed unsigned bundle. `tests/safe_check.py` forwards to the current gate.

```bash
PYTHONPATH=. python skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target examples/returns-triage-governed --phase pre-deploy --gate
```

**Task13 Step6 returns exit 0 for selected local proof**, not production
certification. Prepare the published Linux amd64 wheels with the existing runner:

```bash
python scripts/ci/run-governance-pin-tests.py --prepare-local
```

Docker is required outside the already-qualified Linux runner. Preparation
downloads public pinned artifacts, installs a dedicated local environment, and
observes real imported distributions and wheel bytes. It does not run Azure
commands, build an application image, deploy, or assert an image's installed
packages. Each assessor invocation rechecks the exact shared pins and reruns
the native probes; a saved `installed-packages.json` cannot replace this run.

`governance/probe-contract.json` selects **`returns_apply_decision`**, not the
reserved noop. The assessor calls the same `container.build_host` / generated
native factory with real Hooks, ACS/OPA and Task8 authenticated services.
Only upstream model HTTP, local RSA/JWKS, signature-service and SDK storage seams
are fixtures. The Cosmos adapter, its conditional batch, policy decisions,
approval requests/consume checks and durable-ACK ordering remain real code.
Interactive, batched, scheduled-background and independently governed child-agent
calls are exercised. Raw direct-tool invocation must fail at the backend's
trusted-context guard; this is explicitly **not** evidence of raw-tool Hooks
interception. Positive unbound reads do not confer coverage on writes.

Local approval evidence includes absent/rejected/wrong-role review, changed
binding on an unused grant, expiry, one-use replay, and a second fresh valid
handoff. Known risk plus missing reason/photos still escalates; ordinary missing
information cannot finalize a refund. Output evidence is the declared business
tool schema only—not model-stream/output-policy certification.
The unselected output-hook probe is `not-applicable`; selecting that requirement
without actual output-hook proof blocks the gate rather than borrowing schema evidence.

The trusted recorder writes payload-free observations over a fresh private fd,
including the assessor nonce and monotonic counter. The Docker bridge keeps
ordinary stdout/model output on a separate channel. Source hashes, imported
package bytes, native evaluations, durable receipts and actual effect counts are
checked before evidence is accepted. This is **cooperative-host evidence**, not
attestation against a malicious process that controls its own memory/descriptors.

To retain ignored, schema-validated artifacts without overwriting the committed
offline inventory, append:

```bash
--emit --manifest-path .governance-validation/local-manifest.json \
  --evidence-path .governance-validation/local-evidence.md \
  --apply-plan-path .governance-validation/local-plan.json
```

Repository-mode ownership/CI is read from the explicitly declared, Git-observed
current worktree, never an arbitrary parent or the main checkout. It covers
this local validation pipeline, not a deployed change plane. Live branch
protection, tenant bindings, deployed images, Foundry IQ retrieval, Teams UI and
production operational readiness remain unverified. Task15 needs independent
live business-action proof and cannot borrow a noop or these local results.

### Materialized standalone project

Materialization exports the same tooling under `.governance-tools/`, the real
catalog CODEOWNERS, and a pinned read-only CI template. Review the exported
ownership with the operator; it is a declaration, never evidence that reviews
are enforced. In the project's own Git checkout with its actual origin:

```bash
python .governance-tools/scripts/ci/run-governance-pin-tests.py --prepare-local
python .governance-tools/skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --phase pre-deploy --gate
```

The dedicated environment and caches are created inside that project. Neither
command needs the parent catalog's temporary environment. The exported CI also
runs the existing corrected official CTK runner; its source pin and SDK tests are
unchanged. No source package is a built image or an installed-package receipt.
