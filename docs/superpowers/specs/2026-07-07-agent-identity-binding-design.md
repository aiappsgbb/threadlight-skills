# Agent-identity binding gate (Entra Agent ID / NHI governance) — Design Spec

- **Status:** Draft (awaiting user review)
- **Date:** 2026-07-07
- **Author:** brainstormed with Copilot CLI
- **Related work:** PR #70 (MCP supply-chain gate `SUP-010..013` + `mcp_sbom.py` producer — the architecture this mirrors); `production-ready` Pillar 03 `references/pillars/03-identity-access.md` (`IAM-001..005` static, `IAM-101..103` live); the Part-2 runtime-governance roadmap (producer **B**, feeds the EU AI Act capstone **C**).

## 1. Problem & motivation

A deployed agent runs as a **non-human identity (NHI)**. NHI governance is an
industry-wide vacuum: a large share of NHIs go unchanged for over a year, a
meaningful fraction have **no human-ownership linkage**, and the platform itself
cannot yet reliably distinguish human from non-human accounts. When an injected
instruction or a poisoned tool *acts*, it acts under **this identity's** real
credentials and scope — so who owns it, how narrow its scope is, and whether its
credentials ever expire or get reviewed is a first-order production-readiness
question.

Threadlight already governs part of identity/access in `production-ready`
**Pillar 03** (`IAM-001..005`): no client secrets in `src/`, a user-assigned
managed identity declared in compiled ARM, RBAC not at subscription scope, no SAS
tokens, auth enabled on compute. But Pillar 03 has **no agent-identity-binding
governance**. It does not inventory the agent's identity, does not check that a
**responsible human owner** is declared, does not grade **least privilege at the
identity level** (an `Owner`/`Contributor` or wildcard Graph app-permission
granted to a workload identity), and does not check **lifecycle** (credential
expiry / review cadence — the "stale NHI" failure mode). It also emits **no
identity artifact** the EU AI Act capstone can consume for its identity (Art 12)
and deployer-obligation (Art 26) sections.

This spec adds an **agent-identity binding gate**: a small, offline, pure-stdlib
producer that inventories every workload/agent identity the repo declares, emits
an **`agent-identity.json`** (an identity AI-BOM), and surfaces four new Pillar 03
findings (`IAM-006..009`): passwordless binding, responsible owner, least-privilege
scope, and lifecycle/review. It is the second producer in the Part-2 chain (after
the MCP supply-chain gate); its artifact feeds the capstone evidence pack.

### Why this is "amplify the platform, make it trivial" (not replace)

Entra Agent ID, user-assigned managed identity, and federated identity credentials
are **first-party primitives**. The platform exposes them; wiring them with correct
governance metadata (a responsible owner, a least-privilege scope, a review/expiry
cadence) is the confusing part customers hit. This gate makes the safe path trivial
and **points remediation at platform primitives** — Entra Agent ID + managed /
federated identity for passwordless binding, PIM / access reviews for lifecycle,
`azure-tenant-isolation` for scope — exactly as the MCP gate points at
`foundry-toolbox` / Key Vault. It reimplements none of them. The emitted
`agent-identity.json` becomes the identity input to the Part-2 capstone (Art 12
identity log / Art 26 deployer obligations).

### Non-goals (v1)

- **Live tier.** Calling Microsoft Graph / Entra to enumerate the *actual* Entra
  Agent ID, its real role assignments, or sign-in / last-used activity. Documented
  as a future **live tier (`IAM-106`)**, mirroring the existing static/live split
  in Pillar 03 (`IAM-101..103`). Offline/static only here.
- A **new skill.** These are additions to the existing `production-ready` skill
  (plus one short doc subsection in `deploy`). **17 Threadlight skills stays 17.**
- **Provisioning** the identity (creating the Entra Agent ID / UAMI). The `deploy`
  leg provisions; this gate only verifies the repo *declares* the binding correctly.
- Renumbering or retitling the pre-existing `IAM-001..005` doc-vs-catalog title
  drift (a separate, unrelated defect). Left untouched; this PR only **appends**
  `IAM-006..009`.
- Any network call of any kind. Offline/static only.

## 2. Boundary with existing skills

| Concern | Owner | This change |
|---|---|---|
| No client secrets / SAS in `src/`, UAMI declared, RBAC not sub-scope, compute auth | `production-ready` Pillar 03 (`IAM-001..005`) | Unchanged |
| Live role-assignment / Entra checks | `production-ready` Pillar 03 (`IAM-101..103`) | Unchanged; agent-identity live checks are a future tier above |
| **Agent-identity inventory, passwordless binding, owner, least-priv scope, lifecycle** | — (new) | **`IAM-006..009` + `agent_identity.py` producer** in `production-ready` |
| Provisioning UAMI / federated creds into the tenant | `threadlight-deploy` | **Adds** a short "declare agent-identity governance" doc subsection (no code change) |
| Entra Agent ID / managed identity / PIM (remediation targets) | Azure platform primitives | Referenced, not reimplemented |

## 3. Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|---|---|
| D1 | Placement | **threadlight-skills**, extending **existing** `production-ready` Pillar 03. No new skill. |
| D2 | Producer shape | **One** pure-stdlib module + CLI: `skills/threadlight-production-ready/scripts/agent_identity.py` (single source of truth), mirroring `mcp_sbom.py`. |
| D3 | Consumer | Pillar 03 `_check_identity_static()` calls a new `_check_agent_identity(ctx)` → `IAM-006..009` findings + writes `agent-identity.json` into the evidence bundle (parallel to how `_check_supply_static()` calls `_check_mcp_supply(ctx)`, which wires `mcp_sbom` and writes `mcp-sbom.json`). No `cicd` consumer in v1 — the go/no-go verifier is the gate; a future `cicd` `identity_gate` knob is noted as future work. |
| D4 | What it grades | Four checks: passwordless binding · responsible owner · least-privilege scope · lifecycle/review (§4.3). |
| D5 | First-PR scope | **All four** `IAM-006..009`. |
| D6 | Severity | `IAM-006` must-fix · `IAM-007` should-fix · `IAM-008` must-fix · `IAM-009` should-fix. |
| D7 | Live tier | **Deferred** — documented as future `IAM-106`, not implemented. |
| D8 | Governance manifest | Owner / lifecycle read primarily from **Bicep/ARM resource tags** (`owner`, `ManagedBy`, `ownerEmail`, `expiresOn`, `reviewBy`) — no new required file. An **optional** committed `agent-identity.governance.json` (the analog of `mcp-lock.json`) may declare `owner`/`expiresOn`/`reviewBy`/`justification` per subject id for identities not expressible as bicep tags; when present it supplements tags. |

## 4. Architecture & components

### 4.1 Producer — `agent_identity.py` (new, pure-stdlib)

Self-contained module in `skills/threadlight-production-ready/scripts/`, importable
same-dir by `production_ready.py` (respects per-skill CI test isolation). Also a
standalone CLI. No sibling-module imports (`production_ready.py` imports THIS module
lazily, never the reverse). Discovery is best-effort and defensive: one malformed
config never aborts a scan (identical contract to `mcp_sbom.py`).

**Pipeline:** `discover → build identity-BOM → check → (write | gate)`.

- **`discover(root) -> list[IdentitySubject]`** — parse identity declarations across
  the repo, offline:
  - **Compiled ARM** (`**/*.json` under `infra/`) and **Bicep** text (`**/*.bicep`):
    - `Microsoft.ManagedIdentity/userAssignedIdentities` → a `uami` subject
      (passwordless).
    - `.../userAssignedIdentities/federatedIdentityCredentials` → marks the parent
      UAMI `federated` (passwordless, strongest).
    - `Microsoft.Authorization/roleAssignments` → `roleDefinitionId` (map the
      built-in `Owner` / `Contributor` GUIDs) + `scope` (subscription vs RG vs
      resource), associated to a subject by `principalId` where derivable.
    - Resource / resource-group **tags**: `owner`, `ownerEmail`, `ManagedBy`,
      `expiresOn`, `reviewBy`.
  - **Source / config** (`src/**`, agent configs) for **secret-based NHI** signals:
    `ClientSecretCredential`, `client_secret`, `passwordCredentials`,
    `az ad app credential`, `--password`, an app-registration client secret — these
    are the "bad NHI" the gate must catch.
  - **Wildcard Graph app-permissions** (`Directory.ReadWrite.All`, `roles: ["*"]`,
    an over-broad `.default`) → a least-privilege signal.
  - **Optional** `agent-identity.governance.json` — supplements tags (D8).

  Each subject is normalized to: `id`, `type` ∈ `{uami, federated, app-secret,
  unknown}`, `passwordless` (bool), `owner` (str | null), `scopes[]`
  (`{role, scope, builtin}`), `wildcard_scope` (bool), `lifecycle`
  (`{passwordless, expires_on, review_by, review_declared}`), `declared_in`,
  `parse_error`.

- **`build_identity_bom(subjects) -> dict`** — the `agent-identity.json` document
  (§5.1), with a `summary` roll-up.

- **`check(subjects) -> list[dict]`** — emit aggregated `IAM-006..009` (§4.3), one
  finding per id, with an `offenders[]` list (parallel to `mcp_sbom.check`).

- **`assess(root, governance_path=None) -> tuple[dict, list[dict]]`** — the top-level
  entry `production_ready.py` calls: `discover → build → check`, fold `must_fix` /
  `should_fix` counts into `summary`, attach per-subject `findings`, return
  `(identity_bom, findings)`.

- **CLI** — `python agent_identity.py --root . [--out agent-identity.json]
  [--governance agent-identity.governance.json] [--check]`:
  - default: write `agent-identity.json`.
  - `--check`: exit **1** if any **must-fix** finding, else **0** (CI/local gate).

### 4.2 Consumer — Pillar 03 in `production_ready.py`

Mirrors the MCP wiring exactly:

- `_load_agent_identity()` — lazy same-dir import (mirror `_load_mcp_sbom`), returns
  the module or `None` (never raises).
- `_check_agent_identity(ctx)` — call `mod.assess(ctx.root)`, set
  `ctx.agent_identity = bom`, map findings via `_mk_finding(f["id"], status=…,
  detail=…)`; on unavailable/failed producer, emit `_not_verified` for
  `IAM-006..009` (mirror `_check_mcp_supply`).
- `_check_identity_static()` gains `out.extend(_check_agent_identity(ctx))` before
  its `return out` (parallel to `_check_supply_static`).
- `RepoContext` gains `agent_identity: dict | None = None` (next to `mcp_sbom`).
- The output writer gains a block: `if getattr(ctx, "agent_identity", None) is not
  None:` write `agent-identity.json` next to `mcp-sbom.json`.
- Catalog: register `IAM-006..009` (title / pillar=`identity-access` / severity /
  tier=0) after `IAM-005`.

### 4.3 Checks

| ID | Check | Default |
|---|---|---|
| `IAM-006` | Agent runs as a **passwordless** managed / federated identity — no secret-based app-registration credential (`ClientSecretCredential`, `passwordCredentials`, client secret) backing an agent/workload identity | **must-fix** |
| `IAM-007` | Each discovered agent identity declares a **responsible human owner** (tag `owner`/`ownerEmail`/`ManagedBy`, or governance manifest) | should-fix |
| `IAM-008` | Agent identity scope is **least-privilege** — no `Owner`/`Contributor` and no wildcard Graph app-permission granted to a workload identity | **must-fix** |
| `IAM-009` | Agent identity **lifecycle** is declared — federated/passwordless (nothing to expire) with a review cadence, or a secret-based subject with an `expiresOn`/`reviewBy`/rotation | should-fix |

All four are **tier 0 (static/offline)**. When the repo declares **no** agent
identity at all, `IAM-006..009` = `not-applicable` (like `IAM`/`SUP` precedent), and
`agent-identity.json` is still written with `subjects: []` so downstream C has a
deterministic input.

## 5. Data model

### 5.1 `agent-identity.json` (emitted; feeds the Part-2 capstone identity/deployer sections)

```jsonc
{
  "schema": "threadlight.agent-identity/v1",
  "generator": "threadlight-production-ready/agent_identity",
  "generator_version": "0.7.0",
  "subjects": [
    {
      "id": "id-foundry-agent",
      "type": "federated",
      "passwordless": true,
      "owner": "ai-platform@contoso.com",
      "scopes": [
        { "role": "Azure AI Developer", "scope": "resourceGroup", "builtin": true }
      ],
      "wildcard_scope": false,
      "lifecycle": {
        "passwordless": true, "expires_on": null,
        "review_by": "2026-12-31", "review_declared": true
      },
      "declared_in": "infra/identity.bicep",
      "parse_error": null,
      "findings": { "IAM-006": "pass", "IAM-007": "pass",
                    "IAM-008": "pass", "IAM-009": "pass" }
    }
  ],
  "summary": {
    "subject_count": 1, "passwordless": 1, "secret_based": 0,
    "owned": 1, "over_privileged": 0,
    "must_fix": 0, "should_fix": 0
  }
}
```

### 5.2 `agent-identity.governance.json` (optional, committed — the "I've declared this" baseline)

```jsonc
{
  "schema": "threadlight.agent-identity-governance/v1",
  "subjects": {
    "id-foundry-agent": {
      "owner": "ai-platform@contoso.com",
      "expires_on": null,
      "review_by": "2026-12-31",
      "justification": "Foundry hosted agent runtime identity"
    }
  }
}
```

When present, a subject's `owner` / `expires_on` / `review_by` fall back to this
manifest if not expressed as bicep/ARM tags. Absent manifest + absent tags →
`IAM-007` / `IAM-009` should-fix advising the tag or manifest.

## 6. Error handling

- **No agent identity anywhere** → `IAM-006..009` = `not-applicable`.
  `agent-identity.json` still written with `subjects: []`.
- **Malformed ARM/JSON or governance manifest** → the offending file degrades one
  subject (surfaced as a `parse_error` on that subject, graded `IAM-006`
  should-fix), never a crash. Producer is defensive (identical contract to
  `mcp_sbom.py`).
- **Scope undeterminable** (a subject with no discoverable role assignment) →
  `IAM-008` should-fix ("scope not verifiable statically — confirm least privilege"),
  not must-fix (avoid false-blocking on incomplete static signal).
- **Gate verdict** — CLI `--check` (local/dev + the go/no-go verifier) exits
  non-zero **only** on must-fix (`IAM-006` / `IAM-008`). `should-fix` (owner /
  lifecycle) warns, never blocks.

## 7. Testing

Per-skill isolation (CI runs `pytest skills/<skill>/tests/` per skill).

**`production-ready`:**
- `tests/test_agent_identity.py` (new, mirrors `test_mcp_sbom.py`) — unit tests on
  the producer, driven by tiny on-disk fixture repos (`_write_repo(**files)` helper):
  - discovery: UAMI in compiled ARM; federated credential upgrades a UAMI to
    `federated`; a secret-based app registration → `app-secret` subject.
  - `IAM-006`: passwordless UAMI/federated ⇒ pass; `ClientSecretCredential` /
    `passwordCredentials` present ⇒ must-fix.
  - `IAM-007`: subject with `owner` tag ⇒ pass; no owner (tag or manifest) ⇒
    should-fix; governance manifest supplies owner ⇒ pass.
  - `IAM-008`: `Owner`/`Contributor` built-in GUID or wildcard Graph permission on a
    workload identity ⇒ must-fix; scoped built-in role at RG ⇒ pass; no role found
    ⇒ should-fix.
  - `IAM-009`: federated + `reviewBy` ⇒ pass; secret-based with no `expiresOn`/
    `reviewBy` ⇒ should-fix.
  - empty repo ⇒ all four `not-applicable`; malformed ARM ⇒ `parse_error`, no crash.
- `tests/test_identity_binding_pillar.py` (new, mirrors
  `test_mcp_supply_chain_pillar.py`) — integration: the Pillar 03 checker maps the
  producer's `IAM-006..009` into findings on a fixture repo; producer-unavailable
  path yields `not-verified` for the four ids.
- `tests/test_version.py` — bump the two hardcoded `0.6.1` assertions → `0.7.0`
  (test function renamed `test_version_is_070`).

**Whole-repo gates:** description-length guard (≤1024, all skills) still green.

## 8. Files touched & versioning

**production-ready → `0.6.1` → `0.7.0`:**
- `scripts/agent_identity.py` (new; `IDENTITY_VERSION = "0.7.0"`)
- `scripts/production_ready.py` (`_load_agent_identity` + `_check_agent_identity`
  + `_check_identity_static` hook + `IAM-006..009` catalog metadata + `RepoContext`
  field + `agent-identity.json` writer + `VERSION` → `0.7.0`)
- `references/pillars/03-identity-access.md` (append an "Agent-identity binding
  (NHI governance)" subsection + the `IAM-006..009` rows + remediation → Entra Agent
  ID / federated creds / access reviews)
- `references/remediation-recipes/IAM-006.md` + `IAM-008.md` (new must-fix recipes;
  `IAM-007.md` + `IAM-009.md` should-fix recipes if concise)
- `SKILL.md` (frontmatter `version: "0.7.0"` + a one-line agent-identity-gate note +
  `agent-identity.json` in the emitted-artifacts list)
- `tests/test_agent_identity.py` + `tests/test_identity_binding_pillar.py` (new) +
  `tests/test_version.py` (`0.7.0`)

**deploy (doc only, no version-critical code):**
- A short "Declare agent-identity governance" subsection in `SKILL.md` (or its
  identity reference) — `owner`/`expiresOn`/`reviewBy` tags + Entra Agent ID / UAMI
  binding, so `production-ready` `IAM-006..009` pass. Bump deploy patch version if
  its `test_version.py` pins SKILL.md.

**Repo metadata:**
- `.github/plugin/marketplace.json` + `plugin.json` — patch/minor bump; add
  agent-identity keywords (`entra agent id`, `nhi governance`, `agent identity
  binding`, `least privilege agent`).
- `THREADLIGHT.md` — add an `agent-identity.json` mention to the production-ready
  flow row (optional, if it keeps the file accurate).
- `CHANGELOG.md` — `[Unreleased]` `### Added` entry.

## 9. Future work

- **`IAM-106` live tier:** query Graph / Entra for the real Entra Agent ID, its
  actual role assignments, and last-sign-in / last-used activity; compare to the
  declared binding. Same static/live split Pillar 03 already uses.
- **`cicd` `identity_gate` knob:** render a pipeline step that reads
  `agent-identity.json` and blocks on `summary.must_fix > 0` (mirror the MCP gate).
  Deferred — the go/no-go verifier is the v1 gate.
- **Capstone wiring (Part 2C):** `agent-identity.json` → Art 12 (identity /
  logging) + Art 26 (deployer obligations) sections of the EU AI Act evidence pack.
