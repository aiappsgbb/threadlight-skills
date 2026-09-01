# Upstream pin: Agent Hooks / Agent Framework / CTK / ACS

**Status: `alpha-experimental`.** This document and the paired
[`upstream-pin.json`](./upstream-pin.json) record the *only* upstream
dependency/specification tuple this skill's MAF-first adapter and
conformance claim have been tested against. Any deviation from that exact
tuple — anywhere in it — is drift, reported as `PIN-001`, and must be
treated as `must-fix` until the tuple is re-verified.

## What this pin does and does not claim

- **Agent Hooks (`AGENT-HOOKS-0.1`, spec version `0.1.0-alpha`) is a Draft,
  alpha-stage specification.** It defines a **cooperative** hook contract:
  a compliant runtime *chooses* to call hook points and *chooses* to honor
  their results. Agent Hooks is **not a security boundary** — it cannot by
  itself prevent, sandbox, or contain an action a hostile or buggy runtime
  declines to route through it. Every governance conclusion this skill
  reaches must independently confirm mediation coverage; it must never
  assume Agent Hooks presence alone is sufficient enforcement.
- **The Microsoft Agent Framework (MAF) integration is explicitly
  experimental** (`maf.integration_status: "experimental"` in the pin).
  This is a stated Microsoft/Agent Framework project status, not a defect
  particular to this skill, but it means the integration surface itself
  may change incompatibly between releases without prior notice.
- **The conformance report attests exactly one distribution/version pair:
  `agent-framework-core==1.13.0`, resolved from source commit
  `4b1afd90520310547cb0e9cdc70f644d80161e82`.** No other version of
  `agent-framework-core` — older or newer — is covered by this claim.
  **A newer released version of `agent-framework-core` does not inherit
  this claim.** If a target resolves any other version, the assessor must
  treat the MAF tuple as unverified drift (`PIN-001`) rather than assume
  forward- or backward-compatibility with the pinned behavior.
- **Conformance is not certification.** The referenced report
  (`conformance/claims/maf/REPORT.md`, blob
  `e4a97194e7091a15555afbc541da637765d819bf`, claim `section-13.1`) is a
  Compliance Test Kit (CTK) conformance claim only —
  `conformance_report.certification` is explicitly `false` in the pin.
  It documents which test vectors were run and passed against this exact
  tuple; it is not an independent certification, audit, or warranty by any
  standards body, Microsoft, or the Agent Hooks maintainers.

## What every assessment must record

Beyond citing this pin, every assessment run against a target must record,
as evidence alongside its findings:

- the **resolved local artifact and lock-file hashes** actually observed
  on the target (e.g. `governance/installed-packages.json`,
  `uv.lock`/`poetry.lock`/`pdm.lock` hashes, or equivalent) — never the
  pin's own hashes assumed by default; and
- the **Python runtime version** actually in use on the target (the pin's
  CTK/conformance run used Python `3.12.3`; a target running a different
  Python must have that difference recorded and treated as part of the
  drift surface, not silently ignored).

## What changes the pin

**Any** change to any part of the tuple — an Agent Hooks spec revision or
commit, an SDK version/source commit/artifact hash, a CTK vector-source
commit or pass/skip counts, an `agent-framework-core` version or source
commit, an ACS policy-schema status, or a conformance report
commit/path/blob/claim — requires **all** of the following before the new
tuple may be treated as verified:

1. a **pin review** of the proposed new tuple against this document's
   scope and disclosures (including re-confirming Agent Hooks' draft/alpha
   status and MAF's experimental status still hold, or updating this
   document if they do not);
2. a **full CTK rerun** against the new tuple, with updated
   total/applicable/passed/skipped vector counts recorded in the pin;
3. a **rerun of every application-path probe** this skill exercises
   against the new tuple — drift is never assumed compatible by semantic
   version ordering alone;
4. **new evidence hashes** (artifact SHA-256, source commit, conformance
   report blob SHA) recorded in both `upstream-pin.json` and the
   assessment's own evidence trail; and
5. **customer sign-off** on the new pinned tuple before it is used to gate
   any deployment decision.

Until all five steps are complete, `compare_upstream_tuple` reports the
observed tuple as drift (`PIN-001`, `must-fix`, "rerun CTK and all
application-path probes") rather than silently accepting it.
