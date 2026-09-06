# Coding-agent guidance

## Repository and source of truth

This is a skill catalog, not a deployed customer application. Skills contain
copyable runtimes, generators, assessors and portable packages. Read their
current READMEs before editing; archived captures are historical evidence only.

- Runtime pins: `skills/_shared/governance-upstream-pin.json`.
- Binding schema/consumers: `skills/_shared/governance.py`,
  `governance_selection.py`, `governance_readiness.py`.
- Policy and runtime: `skills/threadlight-govern/scripts/policy_bundle.py`,
  `skills/threadlight-govern/references/{runtime,control-plane,gateway}/`.
- Generation: `skills/threadlight-deploy/references/governance/generate.py`.
- Hosted collector: `skills/threadlight-safe-check/references/governance-probe.md`.
- Business example: `examples/returns-triage-governed/README.md`.
- Public lifecycle/CI input contract: `docs/production-readiness.md`.

## Evidence and implementation rules

SAFE is a method; ACS/Rego is the PDP; Agent Hooks is a host/interceptor contract;
the native host/gateway is the PEP; AGT is a toolkit; ASSERT is assurance.
Never infer whole-agent governance from policy, CI or one selected binding.
Preserve unbound reads without ACS. Invalid selected configuration is not off.
Consequential unbound acceptance must be explicit, current and scoped.

Use real generators, native SDKs and registered services, not assessment-only
checklists. Required signed/fresh policy, authenticated one-use approval,
trusted backend target/schema/facts and central audit ACK precede effects.
Recheck authorization after credential/transport waits; never patch installed
SDKs. Spool fsync is not durable hosted proof.

Offline inventory, executed LOCAL-14, native/CTK and live evidence are distinct.
The collector proves only `governance_probe_noop`, not business writes.
The canonical returns example writes a Cosmos decision/audit, not settlement.
Its `returns_apply_decision` live binding remains unverified.
Current `threadlight-governance-manifest/v1` consumers reject legacy v2 green
verdicts. Preserve archives; do not borrow their receipts. Each deployment attempt
requires fresh after-deployment evidence with the same signed envelope, bundle,
key, environment, image/version, identities and observed parent scope.

## Editing and verification

Write regression tests and observe **RED** before behavior or wording changes.
Do not add Azure resources, run paid probes, broaden RBAC, push or release without
authorization. Never put credentials or personal deployment targets in examples.
Update plugin and marketplace versions together. Generated process-library
artifact names must match `scripts/build_process_library.py`.

```sh
python3 -m pip install pytest pyyaml "jsonschema[format]==4.26.0" ./skills/threadlight-govern/references/control-plane
python3 -m pip install --no-deps ./skills/threadlight-govern/references/gateway
python3 -m pytest skills/threadlight-production-ready/tests/test_script_strings.py skills/threadlight-auto/tests/test_threadlight_auto_cost_contract.py tests/ci -q
node --test tests/blueprint/*.test.js tests/ci/*.test.js
```

Use an isolated Python 3.12+ environment. These service packages are required
for the real v1 protocol-validator tests; installing them is not native/runtime
or live proof. Do not substitute mock wire models when dependencies are absent.

For native runtime changes use `scripts/ci/run-governance-pin-tests.py` and
`--deployment` (Linux amd64 published wheels; Docker on other hosts). These are
local tests, not Azure evidence. CTK has 47 declared vectors and four undeclared
incremental-output cases; only its test oracle is corrected. Do not claim skipped
native tests passed. Task15 is the broader acceptance gate, separate from
documentation/workflow verification.
