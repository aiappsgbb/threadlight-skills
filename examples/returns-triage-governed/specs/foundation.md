# Foundation — canonical Returns Triage

```yaml
schema: threadlight.runtime-policy/v1
framework: microsoft-agent-framework
runtime_shape: agent
protocol: responses
policy_route: explicit-supported-choice
source: provided
capability_signals:
  requires_toolbox: false
  requires_custom_python_tools: true
  requires_file_generation: false
  latency_sensitive_data_queries: false
  unresolved_signals: []
  source: provided
```

The actual application uses the Task10 `maf-container.py` factory, one native
Agent Hooks bundle before application middleware, and the shared ACS provider.
Exact distributions/versions come from `skills/_shared/governance-upstream-pin.json`
in the catalog: **agent-governance-toolkit-core** 5.0.0 (not the umbrella package),
ACS 0.3.1b0, Agent Hooks 0.1.0a5, MAF core 1.14.0, Foundry 1.11.0, hosting
1.0.0b260813, OPA 1.18.2 with its pinned release hash.

## Business boundary

The four existing decisions and five tool contracts remain. Native read-only
adapters return immutable mock OMS/customer data and Cosmos case data.
`returns_apply_decision` is the sole business write; it recommends, never settles
money. Cosmos is the authoritative case/audit store. No filesystem case store,
automatic seeding, reset, payment tool, or generic write endpoint is provided.
Single-line sample orders are in scope; ambiguous multi-line returns fail closed.

SAFE policy verifies declared case scope, ordered backend read receipts, current
case revision, order/customer correlation, eligibility, risk, disposition, and
citations. It does not attest which prose skills the model read. The native
pre-tool gate evaluates those prerequisites before any decision write. The
backend uses the exact authorized arguments and revision in one transactional
case-replace/audit-create batch; stale snapshots never overwrite a newer case.

## Existing infrastructure, explicit new governance configuration

Model: `gpt-5.4`, version 2026-03-05, through the **existing** Citadel endpoint
`https://apim-citadel-hub.azure-api.net`, contract `tl-returns-triage`.
The operator supplies its existing Foundry project proxy route. No bypass to a
direct model endpoint is accepted. UAMI-only `DefaultAzureCredential` is used
for runtime and audit tokens. Existing EU placement/90-day retention are design
requirements, not verified deployment facts.

Task8 supplies authenticated signed-policy lookup, approval request/review/
consume, and durable receipt ACK. Required approval role mappings, tenant,
versioned signing key, service endpoints, Cosmos containers and actual built
image identities are **not known offline**. Supply `deployment-config.template.json`
values before startup; never replace placeholders with invented identifiers.
The local retry spool holds payload-free delivery receipts only, not business
state or durable authority. A remote Task8 ACK precedes consequential effects.

`package-native` materializes the real shared host/control-plane sources without
provisioning infrastructure. No tool-gateway run service is needed for local
native enforcement. `--probe` optionally vendors the shared gateway *library*
because Task11's native noop producer uses its authenticated downstream protocol;
the separate fixed noop fixture must be explicitly installed by the operator.

## Evidence boundaries

`specs/governance-manifest.json` is an unsigned offline inventory: the write
binding is **unverified**, reads are deliberately **unbound**, and live probes
are empty. Local native tests use external transport/backend doubles and are not
deployment proof. Historical AGT v4/v2 reports are in `archive/`, not current truth.
The generic pre-deploy assessor may remain unverified for additional execution
modes/approval/output evidence not supplied by this single-agent example.
