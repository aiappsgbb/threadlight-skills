---
name: threadlight-govern
description: >-
  Use when a Threadlight pilot needs native ACS/Rego policy, SAFE business
  invariants, selected tool or lifecycle bindings, approval requirements,
  signed policy distribution, or runtime governance before deployment.
  Not for model content filtering, red-team scans or quality evaluations.
metadata:
  version: "2.0.0"
---

# Threadlight Govern — policy into selected runtime enforcement

SAFE is the method; ACS/Rego through native OPA is the PDP. Agent Hooks is the
host/interceptor contract SDK; the native host/gateway is the PEP. AGT is the
toolkit, ASSERT is assurance. Evidence is per binding, **not whole-agent**.

## Produce, then assess

1. Read `specs/governance-contract.json` and the framework selection. Preserve
   unbound reads, including their tool names and responses; never enroll every
   tool to make a report green. Invalid selected config is not `off`.
2. Author real `manifest.yaml` and Rego invariants from
   [native-bundles.md](references/native-bundles.md). Use `build_bundle` and
   `verify_bundle` from `scripts/policy_bundle.py`; native loading is mandatory.
   These produce unsigned integrity metadata, **not signing authority**.
3. Route explicit runtime implementation to
   [generate.py](../threadlight-deploy/references/governance/generate.py) and its
   [input contract](../threadlight-deploy/references/governance/README.md):
   `foundation` → authorized Task8 publication → `generate` → immutable images /
   registered agent → `agent-image` (GHCP also `stage-gateway`) → `bind`.
   Follow the documented bootstrap order, not blanket `azd up` after binding.
   Produce actual host/application and ACA service sources, not just an assessment.
4. Supply host-trusted backend facts to SAFE, never model booleans. Required
   signed fresh policy, authenticated tenant/role/full-scope human approval with
   one-use CAS nonce, trusted target/schema/dynamic checks and central receipt ACK
   precede effects. Use actual Task8 services; text saying “escalate” is not approval.
   Recheck at terminal dispatch after waits. Local spool fsync is not durability.
5. Emit offline inventory, then run `threadlight-governed-actions`' real
   pre-deploy `--emit --gate`. For hosted evidence use `threadlight-safe-check`
   with explicit protected preproduction fixture/controller configuration.

```sh
python3 skills/threadlight-govern/scripts/govern_check.py \
  --target ../my-pilot --emit
```

This writes `specs/governance-manifest.json`
(`threadlight-governance-manifest/v1`) and `docs/agt-governance-report.md`.
Declared bindings remain `unverified`; unbound tools remain `unbound`.
`--gate` exits 2: offline inventory cannot pass a runtime gate.

## Boundaries under pressure

| Temptation | Required behavior |
|---|---|
| “Policy/CI is green; ship governed.” | Report offline/local scope; require fresh hosted proof for selected bindings. |
| “MAF means full SAFE automatically.” | Only selected supported points execute policy; compaction, provider-hosted tools and incremental streaming/custom clients are unsupported. |
| “Approve all” or pasted supervisor text | Require authenticated human service decision and atomic one-time consume, not SDK permission callbacks. |
| “Reuse yesterday's receipt.” | New deployment attempt needs after-deployment evidence; full signed envelope/key and current bundle/config must match. |
| “Noop passed, so refund is verified.” | `governance_probe_noop` proves only its registered route; business bindings stay unverified. |

Native MAF is selected local enforcement; GHCP supports registered gateway action
effect closure only, not lifecycle/full-output coverage or arbitrary URL/shell
routing. Control-plane/gateway ACA services, Cosmos, Blob, Key Vault and separate
publisher/verify/workload/downstream permissions are real deployment prerequisites.
Do not substitute broad RBAC or health stubs.

## Pins, migration and verification

Use `skills/_shared/governance-upstream-pin.json`:
`agent-governance-toolkit-core==5.0.0`, ACS `0.3.1b0`, Hooks `0.1.0a5`,
MAF core `1.14.0`, Foundry `1.11.0`, hosting `1.0.0b260813`, OPA `1.18.2`.
Preview/alpha APIs and experimental defaults are explicit limitations.
The corrected test-only CTK oracle covers 47 declared vectors; four incremental
vectors are undeclared. Never modify the published execution SDK.

Legacy v2 policies/verdict fixtures remain historical provenance in `references/fixtures/`;
they cannot pass current readiness. See [migration and CI](../../docs/production-readiness.md#runtime-governance-lifecycle).

```sh
python3 -m pytest skills/threadlight-govern/tests -q
python3 scripts/ci/run-governance-pin-tests.py
```

Ordinary native skips are not proof. The pinned runner executes local native/CTK
tests only; it does not attest Azure deployment.
