# Citadel local governance example

Synthetic single-tenant producer/consumer input and an executable **unsigned**
native bundle generator. No sample identity, image digest or policy key is
deployment evidence. No S2/S3 target is contacted, no consent is simulated as
live authority and no payment settles.

In the prepared Linux amd64 native environment:

```sh
python examples/citadel-governance/build.py \
  --output .governance-validation/citadel-example
```

This writes two independent native ACS bundles, the strict effective binding
and JSON schema, a third atomic generation, and opt-in Publish/Access
`policyXml`. Producer denies `deny_refund`; consumer denies reason
`consumer-denied`; both evaluate the same input. These deliberately small
rules are illustrative. The producer's independent return eligibility/risk/CAS
checks still apply even when both policies allow.

The executable producer is
[`returns_mcp_backend.py`](../../skills/threadlight-deploy/references/governance/returns_mcp_backend.py),
not a new HTTP executor. Its optional `citadel_binding` must equal the generated
binding, and its existing `policy_digest` must select the effective generation.
It independently reconstructs the full action hash, validates all three
Citadel provenance headers, authenticates only the distinct downstream writer,
and uses the existing Cosmos case/decision-audit transaction and outcome route.
Unselected configuration keeps existing behavior; it cannot accept Citadel
provenance accidentally.

The existing portable source packager includes the adapter and producer:

```sh
python skills/threadlight-deploy/references/governance/package_returns_mcp.py \
  --output .governance-validation/citadel-source
```

The main generator's generic package flow is not a Citadel hub deploy command.
Use the [runtime runbook and ADR](../../docs/citadel-governance.md) for the
explicit configuration, external signing, image, OAuth/Host contract, ownership,
rollback and pending **W2-I** gate. This fixture does not authorize provisioning,
registry publishing, a source API bypass, or upstream changes.
