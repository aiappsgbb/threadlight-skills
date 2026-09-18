# Selected returns reference release

This **offline-tested composition is not live release acceptance**. It reuses
`release_runner.py`, `threadlight-release-policy/v1` and the existing
`evidence_gate.py`. It adds application adapters, not a second release engine.
No Azure operation, new identity, resource, role, deployment or paid evaluation
is authorized by generating the files or passing the local tests.

The selected application is the **two-tool MAF/Responses + governed MCP +
native Outlook + business-backend Cosmos** reference:

- [`returns_mcp_agent.py`](../skills/threadlight-deploy/references/governance/returns_mcp_agent.py)
- [`returns_mcp_backend.py`](../skills/threadlight-deploy/references/governance/returns_mcp_backend.py)
- [`package_returns_mcp.py`](../skills/threadlight-deploy/references/governance/package_returns_mcp.py)
- [`returns-mcp-demo.md`](../skills/threadlight-deploy/references/governance/returns-mcp-demo.md)

`returns_get_case` remains an **unbound read without ACS**.
`returns_apply_decision` records a recommendation or supervisor handoff with
an atomic Cosmos case/decision-audit transaction. It is **not settlement**.
This slice adds neither mock OMS/CRM integrations nor whole-agent governance
claims. Historical captures never substitute for this attempt's evidence.

## Generate and hand off

Use the existing generator with `--reference-application returns-mcp/v1` and
the usual explicit validation/production target flags. It emits the same
GitHub Actions or Azure DevOps workflow, the selected adapters, canonical
assessors and **non-executable** `specs/returns-release.example.json`.
Copy and complete the examples through review; never treat placeholder hashes
or owner names as observed facts. Nonselected applications retain F1 behavior.

The release policy adds:

```json
{
  "application": {
    "profile": "returns-mcp/v1",
    "configuration": "specs/returns-release.json"
  }
}
```

The generator selects `returns_release.py prepare|observe|promote` and
`evals|redteam` in the existing policy argv arrays. The configuration contains:

| Field | Actual required operator input |
|---|---|
| `source_package` | Project-relative `source-package.json` produced by the existing packager; all members must match its hashes and be committed |
| `behavior` | Package/instruction/policy-code SHA-256, exact model name/version and exact two-tool schema/binding inventory |
| `targets.validation.external` | Independently approved validation model, service, connection, Outlook, Cosmos and role-map revisions |
| `targets.production.external` | Separate production resources and their approved revisions, not copied validation receipts |
| `targets.*.operators` | Explicit reviewed executable argv for actual authenticated application operators, listed below |
| `producers` | Actual evaluator/scanner executable argv and its raw output path |
| `owners` | Application, central platform, production approver, human reviewer and incident owner |

All package members, operator entrypoints, directly named configuration files and
the application configuration join F1's committed input hashing. Additional
transitive imports, datasets, dependency locks and provider configuration must
be listed in release-policy `inputs`. Producer outputs cannot overwrite these
inputs. Raw evidence, host requests and private operator logs must be Git-ignored.
Native dependency setup is application-owned; there is no automatic installer.

## Observed application contract, not image only

The independent observation extends `threadlight-deployment-observation/v1` with
`application_contract`, schema `threadlight-returns-application/v1`:

- `behavior` must match the committed common behavior exactly in both phases:
  package and instruction hashes, model **name and version**, policy **code**
  hash and exactly two tool-schema/binding entries.
- `external` must match the phase's complete approved external contract:
  versioned signing-key ID; model deployment resource/revision; model, gateway and Outlook connection
  resource/revisions; separate control-plane/gateway/backend image digests and
  revisions; Cosmos account/database/four container roles; native Outlook
  workflow/version/definition hash/connection/recipient hash; exact scoped
  application role map.
- `binding` contains the actually verified signed envelope hash, policy/config
  digests, **versioned** Key Vault key ID and observed agent name/version/image/
  environment/principal/client. These dynamic fields are read after registration,
  not predicted before creation. The agent name is the final segment of the
  canonical target ID. The observation must correlate them to the native version.
- `identities` records separate agent, gateway, backend, control-plane and human
  subjects. The agent subject must match its signed binding and must not be the
  Cosmos writer. The agent role map must not grant Cosmos/signing-key authority.

The observer is a **required real integration**, not an echo of
`THREADLIGHT_RELEASE_REQUEST`. It must independently read native version/image/
identity, actual model deployment/version and connection revisions, registered
tool schemas, policy/binding bytes, real role assignments, native Outlook
authority and backend target configuration. It must verify signature/freshness
through the existing bootstrap/Key Vault path. A configuration hash is not proof
that a running service loaded it; report the **observed** active revision.
The adapter validates structure, equality and correlation; it cannot manufacture
these observations or attest that an arbitrary trusted operator tells the truth.

Candidate observation is repeated after producer execution. Production
observation is repeated after postchecks and before admission. Model, tool,
policy, role, connection, workflow or store drift blocks, even with the same image.
Different production identities and signed bindings are expected; different
behavior is not. Validation must not share production business/governance
services, Cosmos account or approval workflow.

## Real operator interfaces and ownership

Operators receive the existing private `THREADLIGHT_RELEASE_REQUEST` JSON and
return JSON only on stdout; diagnostics stay private. These names are **local
adapter operations, not invented HTTP endpoints**.

| Operation | Required implementation and owner |
|---|---|
| validation `prepare` | Application deployer stages/registers the reviewed immutable candidate in validation only, with synthetic cases and separate human/Outlook targets. Use existing packaging/native create/bootstrap paths; no legacy register/bind/start or second registration after binding. |
| `observe` (both phases) | Independent read authority performs the observations above; return the native F1 observation plus the full application contract. |
| production `stop` | Authenticated incident/operator path closes business traffic **and** selected-write gateway admission; stop new effects, preserve reads/audit and all resources. Must be idempotent and target-scoped. |
| production `admission` | Independent authenticated read returns `{state: closed|open, target_id, operation_id?}`. Open readback must name this promotion operation. |
| production `operation` | Read the **durable remote** operation record: `{operation_id, state: absent|prepared|unknown, image_digest?}`. Unknown includes timed-out, in-progress, conflicting or otherwise unconfirmed outcomes. |
| production `promote` | Atomically fence the supplied stable operation ID **before** any effect, reuse `candidate.image_digest` without rebuild/tag substitution, register/publish the exact binding using the existing native operators, and return `prepared` with the image digest only after independent readback. Keep admission closed. A simultaneous winner is reconciled, not reissued. |
| production `postcheck` | Independent application/platform checks described below, against the actually observed production contract. |
| production `admit` | Authenticated compare-and-swap/idempotent admission for this operation, contract and postcheck hash. Recheck current authorization after transport/credential waits; return `{operation_id, state: admitted}` only on acknowledged completion. |
| production `recover` | Incident-owner, separately approved reconciliation/rollback implementation; never inferred from pipeline failure. It is declared for handoff, **not automatically invoked**. |

The supplied `returns_gateway_operator.py` uses the actual
`govern_control_plane.operator` client for **POST /governance/operations**.
Its wire is `inspect`, then `admission` with `expected_record_hash`, followed by
independent `inspect` readback. It uses the registered
`returns_apply_decision` action and actual requester subject. `Governance.Operate`
belongs to an explicitly configured non-agent operator; neither generating the
helper nor running CI grants that role.

Native states are **`missing|stopped|open`**, not the normalized release states.
Open requires an explicit lease of at most **300 seconds**, additionally bounded
by signed policy and authenticated operator-token expiry. `returns_gateway_operator.py open` requires the current
protected postchecked release request; the native service rechecks operator
authorization, audits and CAS state. A lost reply never automatically retries.
The helper preserves actual request/response files privately and reports native
record hashes. It does **not** invent a release operation ID in the gateway
record, implement lease renewal, or treat that business-operation ledger as a
durable **deployment-promotion** ledger.

The platform/application operator must compose this concrete gateway helper with
the **real traffic controller**, map readback to the release operator's
`closed|open` states, and bind the acknowledged gateway record hash to the
durable release operation. Closure must cover both existing active and newly
registered workloads; a newly opted-in admission policy starts closed.
Admission renewal requires current scoped authority and contract checks, never
an unattended infinite lease. These remain explicit application integration
prerequisites. A missing implementation blocks before execution.

Central hub/APIM/network/Key Vault ownership remains on the separate platform
track. These application operators cannot provision or modify the central hub.
The generated workflow does not configure GitHub/ADO environment approvals;
the production environment owner must configure and verify those checks.
Validation/production deployment identities, RGs and environments are distinct.

## Actual evaluation and red-team execution

The `evals`/`redteam` adapters **execute** the explicitly approved native
producer, reject missing/stale/wrong-candidate outputs, then execute the real
`evals_check.py` / `redteam_check.py`. They never fill in passing metrics.
The raw producer output must contain its actual `provider`, `run_id`,
`started_at`, `finished_at` and original
`release_binding: {ci: request.ci, candidate: request.candidate}`.
The assessor must select exactly this output, not a newer saved report.

For evaluation, provide actual measured `pass_rate` and the tool-call/response
dataset/scenarios required by the canonical evaluator. For red teaming, provide
the scanner's real `tool`, `captured_at`, `num_attacks`, `strategies` and
`attack_success_rate` category measurements in its supported raw format.
Do not relabel a policy refusal test as a harmful-content or exfiltration scan.
All canonical required-domain failures still block; no quality waiver is added.
Thresholds and any optional adoption-control scope remain in the existing policy.

When AgentOps is opted in, the generator retains the existing
`release_agentops.py` bridge, **agentops-accelerator==0.14.0**, its native receipt,
native target mapping, scoped one-use owner approval and separate credential
context. It does not run a duplicate evaluator, alter pins or replace the
native wire models. Red-team execution remains a separately required producer.
Neither CLI availability nor a canonical assessor pass proves model execution.

The application owner's validation suite must explicitly exercise allow,
policy deny, pending, real Outlook approve/reject, exact resume and replay using
the reference's actual native Responses/MCP path. Use real authorized synthetic
case fixtures and the existing `returns_reconcile.py` independent Cosmos/central
audit reconciliation. Email delivery is not human approval; notification-only
mail is not the native approval workflow. No approval is fabricated. If a real
reviewer is unavailable, that scenario remains incomplete and cannot be labeled
passed. Production checks are not permission for new business test writes.

## Promotion and stop/recovery

1. F1 preflight validates committed configuration and executable dependencies.
2. Validation prepares and independently observes its candidate; real producers
   execute, then canonical eval/redteam/MCP acceptance and a final unchanged
   observation precede the accepted receipt. A bad candidate never reaches
   production. The exact good image and evidence hashes remain in that receipt.
3. Protected production approval and F1's receipt checksum/source/attempt/
   freshness/input checks precede promotion. The selected adapter closes
   admission, independently confirms closure, and reads the durable operation.
   `absent` may dispatch once through the durable operator; `prepared` is
   reconciled read-only; `unknown` **never retries dispatch**.
4. Production observes the same immutable agent image and approved application
   behavior. Postchecks cover `native_readiness`, `signed_binding`,
   `policy_runtime`, `tool_inventory`, `role_map`, `cosmos_target`,
   `outlook_authority`, and `central_audit`. Each returns `status: pass` with
   a SHA-256 of actual retained evidence, bound to the operation, target and
   SHA-256 of the full observed application contract, plus an `observed_at`
   within the current postcheck execution window.
5. Re-observe for drift, recheck current identity and F1 authorization/freshness,
   then explicitly admit and verify readback. A failed postcheck cannot admit.
   An admission exception triggers the real stop/readback path; if closure
   cannot be confirmed, report an incident/unknown state, never “rolled back”.

**Rollback is not `azd down`.** The incident owner keeps admission closed,
reads the durable operation plus actual version/endpoint/binding/store state,
and retains the failed attempt and last known-good image/evidence. For an
approved rollback, reselect the exact retained version/image and verify a
current signed binding/model/tool/connection/role contract using the supported
native endpoint operator. Re-run postchecks and obtain current production
approval before admission. Prior evidence alone cannot authorize reopening.
Do not delete local unknown-attempt records to bypass F1's retry protection.
Do not blindly reissue create, promotion or a business request after timeout.
Business reconciliation may require a separate compensating action; restoring
an image never undoes a Cosmos decision or an email.

Under a current protected production CI context, the existing runner's explicit
`reconcile --operation-id <exact-prior-SHA256>` action closes admission and reads
the durable deployment operation, returning observation metadata only. It never
dispatches promotion, changes an unknown attempt into a retry permit, opens
admission or invokes the declared `recover` implementation. The real promotion
operator must fence the **target** against any unresolved prior operation, even
when a new CI run would calculate a different operation ID.
Business-write unknowns use the separate native gateway `inspect`/`reconcile`
protocol with original arguments and expected record hash, not this deployment
operation selector. Neither path automatically redispatches a business action.

## Offline proof and remaining boundaries

`skills/threadlight-cicd/tests/test_returns_release.py` tests the concrete
contract and adapter sequencing with clearly labeled local fixtures. The
ordinary F1 runner tests continue to exercise subprocess execution and canonical
acceptance. These are **not live**, native hosted, gateway enforcement, Outlook
delivery/human response, Cosmos durability or end-to-end business proof.

Real deployment/independent observation/traffic and gateway admission/durable
promotion/recovery operators, a registered native evaluator/scanner, fresh
signed publication, current scoped identities and real human approval remain
required application integrations. The catalog intentionally supplies no
credentials, customer targets, fabricated producers or green example receipts.
