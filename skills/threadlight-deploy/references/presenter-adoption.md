# Adopt presenter-ready in an existing project

This is consumer guidance for the [existing presenter contract](../../../docs/presenter-ready.md),
not another contract, validator or authority to deploy. Keep the profile an
**explicit opt-in** in `specs/manifest.json`; absent/default keeps the existing
pipeline. A request for a published, usable presenter demo requires explicit
selection under the user's mandate, or a clear statement that the standard path
does not deliver that outcome. Invalid selection is not off.

## Map the incumbent before generating anything

Do not regenerate an existing runtime or frontend to adopt the profile. Read the
current project README, Foundation, SPEC, selected manifest, lock, retained
deployment provenance and process-owned `specs/presenter-contract.json` first.
Preserve the framework, design system, synthetic identity and canonical receipts.
The process owner owns the description, journey, script, sizing and public facts;
publishers consume these views rather than creating competing descriptions.

Use the existing contract fields to map the actual files and missing evidence:

| Existing surface | Adopt into the existing contract |
|---|---|
| Runtime, imports, SDK host/client and adapter | `deployment` and complete `inputs.runtime`; retain the exact cohort |
| Current UI, packaged assets and serving configuration | `inputs.interface`; deployable service trees are also runtime inputs |
| Source producer, effective time and expiry | `availability` and `inputs.source`; separate preparation from reads |
| First useful journey, saved result, independent readback and reopen | `journey`; preserve justified read-only/nonpersistent exceptions |
| Presenter script, sizing and promised downloads/archives | `inputs.script`, `inputs.sizing`, `sizing`, `publication` |
| Existing execution outputs | Immutable producer receipts referenced by the existing evidence index |

Read the canonical `skills/_shared/presenter-deployment-pin.json` and its guide,
templates and preflight at the exact selected commit; keep `deployment.guidance`
equal to that pin. Do not copy a candidate PR, use `latest`, change SDK/governance
pins, or replace the frozen deployment authority. Retain one supported unified-azd
or native-SDK definition. An unsupported incumbent layout requires an explicit
migration/consumer-extension decision, not automatic deletion or a new validator.
Map missing fields from real process facts; never relabel old outputs as fresh proof.

From a complete pinned catalog, use the existing read-only consumer and planner:

```bash
python3 -m skills._shared.presenter --root /path/to/pilot
python3 skills/threadlight-auto/references/orchestrator.py \
  --workspace /path/to/pilot --dry-run --output json
```

These report `recorded-not-independently-attested`. Use their dependency
fingerprints and predecessor hashes, not another evidence index. Reuse unchanged
passing receipts; script/sizing changes do not replay backend operations. Changes
to a packaged workspace also affect runtime. A new deployment attempt invalidates
hosted evidence against the old target even for the same image.

## Recovery and a demo that still works tomorrow

Ordinary authorized implementation, packaging fixes and local verification remain
inside the assignment. Do not introduce approval gates for each step. New effects,
new permission/cost/resource scope, an expired authorization or unresolved UNKNOWN
effects are different: reconcile or obtain the required decision first.
Recheck authorization, source freshness and one-use approval after waits.
UNKNOWN blocks the affected effect and interfering work, not independent offline
UI/build fixes. Never replay a write to recover a screenshot or package log.

Separate presenter access, session credentials, source validity, retained history
and operation lifetime. **Valid-but-expired is not malformed configuration.**
Where the actual contract permits public shell/history reads, startup must retain
them with an explicit expired-source state; history still enforces its own auth
and retention. Private history must not become public. Malformed selected
configuration must surface an error and deny effects, not silently disable controls.

The **day-after** regression must use the process's actual adapter/UI and an
injected clock, not disable auth or extend a lease:

1. Prepare a valid synthetic revision through its designated producer; complete
   and independently read a result under authorized identities.
2. Advance beyond source expiry. Open the real shell and historical result from
   a fresh session, reauthenticating if required. Show the retained immutable
   result and expired-source explanation only where that read is permitted.
3. Attempt new work, including after a credential/transport wait: deny the new
   write and observe no backend effect. Preserve pending/UNKNOWN correlation,
   consumed operation IDs, approval state and original receipts.
4. Only a separately authorized preparation creates a distinct synthetic
   revision and new lawful operation intent. No blanket reset, forced full
   regeneration, implicit read-triggered refresh or reuse of consumed approval.

“Demo ready tomorrow” is not a longer authorization/source lease. State the
availability window and preparation needed tomorrow separately from today's
verified result. Reuse the common method on **two compatible processes**, with
independent rules, sources, adapters, targets and receipts. Offline fixture
checks prove contract behavior, not that either process's public shell, real
adapter, history permissions or hosted write boundary actually works.

## Built web artifacts, not just a healthy dev server

Use [web artifact smoke](web-artifact-smoke.md) for selected built asset bytes,
portable non-root modes and optionally a real loopback serving origin. It catches
omissions and HTTP faults without interpreting Docker build instructions.
It does not generate or build packages and cannot replace native package cases.

Before claiming hosted quality, require **actual image-runtime validation**:
start the exact built image with its configured non-root identity and real server,
exercise startup/imports, assets, module MIME, promised download and browser journey,
then bind authorized deployed observations to the actual image/configuration.
If that gate is unavailable, report it unexecuted; source-ready/static passed is
not image-runtime, backend-verified, script-verified or human-accepted.
