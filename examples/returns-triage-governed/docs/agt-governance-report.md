# Agent governance — offline binding inventory

**Offline evidence is not deployment enforcement.** No live probes were run.
Unknown deployment metadata is null; unsigned bundle integrity is not authenticity.
Coverage counts declared tool/lifecycle subjects, not invisible provider tools.

| Binding / subject | Status | Declared path | Evidence |
|---|---|---|---|
| `oms_get_order` | unbound | none | EV-inventory, EV-bundle |
| `returns_get_case` | unbound | none | EV-inventory, EV-bundle |
| `returns_list_open` | unbound | none | EV-inventory, EV-bundle |
| `customer_get_profile` | unbound | none | EV-inventory, EV-bundle |
| `returns_apply_decision` | unverified | local-agent-hooks | EV-inventory, EV-bundle |

## Offline evidence
- EV-inventory: contract-declared-only (specs/governance-contract.json)
- EV-bundle: native-manifest-valid-not-runtime-proof (src/agent/governance/bundle)

Legacy whole-agent verdict consumers must migrate; no legacy pass is emitted.
