# MCP tool supply-chain gate — Design Spec

- **Status:** Draft (awaiting user review)
- **Date:** 2026-07-02
- **Author:** brainstormed with Copilot CLI
- **Related work:** PR #63 (SUP-008/009 skill & tool supply-chain governance, `42a62ec`); Pillar 09 `references/pillars/09-supply-chain.md`; `threadlight-cicd` eval/red-team gate pattern (`EVAL_GATE_MODE`)

## 1. Problem & motivation

Agents increasingly bind to **MCP servers** for their tools. Those servers are
supply chain — and they change more often, and with less scrutiny, than the base
image or the Bicep modules. Today a customer wires a server with a bare `npx`
/`uvx` invocation or an unpinned reference and gets **zero provenance**: no record
of which version they trust, which registry it came from, what tools it exposes,
or whether that tool surface has silently changed since they vetted it.

Threadlight already governs the *rest* of the supply chain in `production-ready`
**Pillar 09** (`SUP-001..009`: image/module/dependency pinning, SBOM, and the
skill/tool-artifact discipline shipped in PR #63). But Pillar 09 has **no
MCP-specific checks** — `SUP-009` merely regex-touches `mcpServers`/`mcp-plugin`
to ask "is *a* version pinned somewhere". It cannot answer: is *this server*
pinned, where did it come from, and **has its tool surface drifted** (the
rug-pull / tool-poisoning failure mode).

This spec adds an **MCP tool supply-chain gate**: a small, offline, pure-stdlib
producer that inventories every MCP server the repo declares, emits a signed
**`mcp-sbom.json`** (an AI-BOM for tools), detects **tool-descriptor drift**
against a committed **`mcp-lock.json`** baseline, and surfaces four new Pillar 09
findings (`SUP-010..013`). The same producer is wired by `threadlight-cicd` as a
**shift-left pipeline gate step**, so the check runs both in the go/no-go verifier
and in CI.

### Why this is "amplify the platform, make it trivial" (not replace)

MCP adoption is a first-party motion (Foundry tools, the Copilot MCP registry,
`foundry-toolbox`). Wiring it *safely* is the confusing part customers hit. This
gate makes the safe path trivial and **points remediation at platform
primitives** — `foundry-toolbox` / a curated registry for tool curation, Key
Vault for credentials — exactly as `SUP-008/009` point at `foundry-skill-catalog`
/ `foundry-toolbox`. It reimplements none of them. The emitted `mcp-sbom.json`
becomes the tool-inventory input to the Part-2 capstone evidence pack (AI-BOM /
Annex IV).

### Non-goals (v1)

- **Live re-fetch** of a running MCP server's tool list (connect + enumerate at
  gate time). Documented as a future **live tier** (`SUP-110`), mirroring the
  existing static/live split in Pillar 09 — **not built here**.
- A **new skill** in either repo. This is additions to two existing skills. 17
  Threadlight skills stays 17.
- A reusable cross-repo **MCP-provenance primitive** in `awesome-gbb`. If the
  remediation surface proves a genuine reusable gap, it is tracked as an
  **`awesome-gbb` issue** (proposing a `foundry-toolbox` MCP extension), not
  built in this PR. The SBOM generator stays with the check (`SUP-008/009`
  precedent + per-skill test isolation).
- Registry *content* trust scoring, typosquat detection, or network calls of any
  kind. Offline/static only.

## 2. Boundary with existing skills

| Concern | Owner | This change |
|---|---|---|
| Image / Bicep / dependency pinning, SBOM, provenance | `production-ready` Pillar 09 (`SUP-001..007`) | Unchanged |
| Skill/tool artifact discipline (force-publish, version pin) | `production-ready` Pillar 09 (`SUP-008/009`) | Unchanged; MCP is the tier above |
| **MCP server inventory, pin, registry, drift, inline-creds** | — (new) | **`SUP-010..013` + `mcp_sbom.py` producer** in `production-ready` |
| Prod CI/CD pipeline generation | `threadlight-cicd` | **Adds** an MCP-gate step + `mcp_gate` soft/hard knob |
| Publishing/curating tools as versioned artifacts (remediation target) | `foundry-toolbox` / curated registry (awesome-gbb) | Referenced, not reimplemented |

## 3. Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|---|---|
| D1 | Placement | **threadlight-skills**, extending **existing** `production-ready` + `cicd`. No new skill. |
| D2 | Producer shape | **One** pure-stdlib module + CLI: `skills/threadlight-production-ready/scripts/mcp_sbom.py` (single source of truth). |
| D3 | Two consumers | (1) Pillar 09 `_check_supply()` imports it → `SUP-010..013` findings + drops `mcp-sbom.json` into the evidence bundle. (2) `cicd` **renders** an MCP-gate pipeline step (text only, no cross-skill import). |
| D4 | Drift detection | **Committed baseline lockfile** `mcp-lock.json`: hash each pinned server's tool surface (tool name + description + input schema); later runs re-derive and diff. Any change on a pinned server = drift. Offline, deterministic. |
| D5 | First-PR scope | **All four checks** `SUP-010..013` (pin + registry + drift + inline-creds). |
| D6 | Severity | `SUP-010` must-fix · `SUP-011` should-fix · `SUP-012` must-fix (should-fix if no lock yet) · `SUP-013` must-fix. |
| D7 | Live tier | **Deferred** — documented as future `SUP-110`, not implemented. |
| D8 | awesome-gbb | **Issue only** (reusable MCP-provenance primitive proposal). No code there. |

## 4. Architecture & components

### 4.1 Producer — `mcp_sbom.py` (new, pure-stdlib)

Self-contained module in `skills/threadlight-production-ready/scripts/`, importable
same-dir by `production_ready.py` (respects per-skill CI test isolation). Also a
standalone CLI.

**Pipeline:** `discover → build SBOM → check → (write | diff lock | update lock)`.

- **`discover(root) -> list[McpServer]`** — parse MCP declarations across the repo:
  - `.mcp.json`, `mcp-config.json` (`mcpServers` / `servers` maps)
  - `mcpServers` blocks embedded in other JSON (e.g. copilot config)
  - `mcp_servers=[...]` params / `"url": "…/mcp"` remote refs in source & agent
    configs (best-effort, static)

  Each server is normalized to a `source` descriptor: `kind` ∈
  `{npx, uvx, docker, pip, remote}`, `ref`, `registry` (derived: `npm`, `pypi`,
  `ghcr`, host of a remote URL, or `unknown`), `pinned` (bool), `version`,
  `digest`, `declared_in[]` (file paths).

- **`build_sbom(servers) -> dict`** — the `mcp-sbom.json` document (§5.1),
  including per-tool `description_sha256` + `input_schema_sha256` when a tool
  surface is statically declared in the config; empty `tools[]` when the server
  only exposes tools at runtime (that's expected — drift for those is a live-tier
  concern, flagged in the SBOM as `tools_declared: false`).

- **`check(sbom, lock) -> list[Finding]`** — emit `SUP-010..013` (§4.3).

- **CLI** — `python mcp_sbom.py --root . [--out mcp-sbom.json]
  [--lock mcp-lock.json] [--check] [--update-lock]`:
  - default: write `mcp-sbom.json`.
  - `--update-lock`: (re)write `mcp-lock.json` from the current pinned surface
    (the explicit "I've vetted this" action).
  - `--check`: exit **1** if any **must-fix** finding, else **0** (CI gate).

### 4.2 Consumer 1 — Pillar 09 in `production_ready.py`

`_check_supply()` imports `mcp_sbom`, runs `discover`/`build_sbom`/`check` over
`ctx.root`, appends `SUP-010..013` `_mk_finding(...)` records to the go/no-go
manifest, and writes `mcp-sbom.json` into the assessment's evidence output
alongside the existing manifest. Registered in the `SUP` metadata table
(titles/severity/pillar/tier) next to `SUP-001..009`.

### 4.3 Checks

| ID | Check | Default |
|---|---|---|
| `SUP-010` | Every MCP server pinned by version/digest — no bare `npx`/`uvx`, no `@latest`, no floating tag | **must-fix** |
| `SUP-011` | Each server resolves to a **declared/known registry** (`npm`/`pypi`/`ghcr`/allowlisted remote host) — not an arbitrary/unknown source | should-fix |
| `SUP-012` | No **tool-descriptor drift** vs `mcp-lock.json` for pinned servers (rug-pull / tool-poisoning) | **must-fix** (→ should-fix + "run `--update-lock`" when no lock exists yet) |
| `SUP-013` | No **inline credentials/secrets** in server config (`env`/args) — must be env-ref or Key Vault | **must-fix** |

All four are **tier 0 (static/offline)**.

### 4.4 Consumer 2 — `threadlight-cicd` gate step

Mirror the existing eval/red-team gate exactly:

- `generate_pipeline.py`: new framing knob **`mcp_gate`** ∈ `{soft, hard}`
  (default `soft`) → context tokens `MCP_GATE_MODE` + `MCP_GATE_SOFT`
  (`continue-on-error` / `continueOnError`), same shape as `EVAL_GATE_*`.
- Both templates gain an **MCP supply-chain gate** that mirrors the eval/red-team
  gate's **two-step** shape:
  1. a runbook step that instructs running the `production-ready` producer to emit
     `mcp-sbom.json` (an `echo` instruction, like the existing "Run quality evals"
     step — no hard path dependency on where the skill lives);
  2. an **inline `python3` verdict step** (`continue-on-error: {{MCP_GATE_SOFT}}`)
     that reads `mcp-sbom.json` and `sys.exit(1)` iff `summary.must_fix > 0` — so
     soft = warn-only, hard = blocking, identical to `EVAL_GATE_*`.
  - Templates: `references/github-actions/azd-deploy-prod.yml.tmpl` (job) +
    `references/azure-devops/azure-pipelines.yml.tmpl` (`- stage:` form).
- No cross-skill import: the verdict step reads the emitted artifact, exactly as
  the eval gate reads `specs/evals-manifest.json`.

## 5. Data model

### 5.1 `mcp-sbom.json` (emitted; feeds the Part-2 capstone AI-BOM)

```jsonc
{
  "schema": "threadlight.mcp-sbom/v1",
  "generated_at": "2026-07-02T00:00:00Z",
  "generator_version": "0.6.0",
  "servers": [
    {
      "id": "github",
      "source": {
        "kind": "npx", "ref": "@modelcontextprotocol/server-github",
        "registry": "npm", "pinned": true,
        "version": "1.4.2", "digest": null
      },
      "declared_in": [".mcp.json"],
      "tools_declared": true,
      "tools": [
        { "name": "create_issue",
          "description_sha256": "…", "input_schema_sha256": "…" }
      ],
      "creds_inline": false,
      "findings": ["SUP-010:pass", "SUP-011:pass", "SUP-012:pass", "SUP-013:pass"]
    }
  ],
  "summary": {
    "servers": 1, "pinned": 1, "unpinned": 0,
    "drifted": 0, "must_fix": 0, "should_fix": 0
  }
}
```

### 5.2 `mcp-lock.json` (committed baseline)

```jsonc
{
  "schema": "threadlight.mcp-lock/v1",
  "servers": {
    "github": {
      "version": "1.4.2", "digest": null,
      "tools": { "create_issue": { "description_sha256": "…",
                                    "input_schema_sha256": "…" } }
    }
  }
}
```

Drift = for a pinned server present in both: `version`/`digest` changed, **or**
any tool's `description_sha256`/`input_schema_sha256` changed, **or** a tool
added/removed. Servers absent from the lock are "not yet vetted" → `SUP-012`
should-fix advising `--update-lock`.

## 6. Error handling

- **No MCP anywhere** → `SUP-010..013` = `not-applicable` (like `SUP-009`).
  `mcp-sbom.json` still written with `servers: []` so downstream C has a
  deterministic input.
- **Malformed MCP config** (bad JSON) → the file is reported as a `SUP-010`
  should-fix ("unparseable MCP config at <path>"), never a crash. Producer is
  defensive; a parse error degrades one file, not the run.
- **Runtime-only tool surface** (`tools_declared: false`) → drift check is skipped
  for that server (can't statically hash what isn't declared); noted in the SBOM
  and called out as a live-tier (`SUP-110`) candidate. Pin/registry/creds still
  apply.
- **Gate verdict** — the CLI `--check` (local/dev + the go/no-go verifier) exits
  non-zero **only** on must-fix. The rendered CI step instead reads
  `summary.must_fix` from the emitted `mcp-sbom.json` inline (mirroring how the
  eval gate reads `evals-manifest.json`); both honour `soft` (warn) vs `hard`
  (block).

## 7. Testing

Per-skill isolation (CI runs `pytest skills/<skill>/tests/` per skill).

**`production-ready`:**
- `tests/test_mcp_sbom.py` (new) — unit tests on the producer: discovery across
  `.mcp.json` / `mcp-config.json` / embedded `mcpServers`; pin detection
  (`npx @x` unpinned vs `npx @x@1.2.3` pinned vs digest); registry derivation;
  drift (no-lock, matching-lock, version-drift, description-drift, tool
  add/remove); inline-cred detection; empty-repo `not-applicable`.
- Fixtures under `tests/fixtures/`: `mcp-pinned/`, `mcp-unpinned/`,
  `mcp-drifted/` (config + stale lock), `mcp-inline-cred/`.
- Extend the Pillar 09 findings test to assert `SUP-010..013` appear with correct
  status on those fixtures.
- `tests/test_version.py` — bump the two hardcoded `0.5.1` assertions →
  `0.6.0` (test function renamed `test_version_is_060`).

**`cicd`:**
- `tests/test_mcp_gate.py` (new, mirrors `test_eval_gate.py`) — render with
  `mcp_gate=soft` and `hard`; assert the MCP-gate step is present and
  `continue-on-error`/`continueOnError` honours the mode in both GH + ADO
  templates.
- `tests/test_no_secrets_in_templates.py` must still pass (the new step carries no
  secret literals).
- `tests/test_version.py` enforces SKILL.md == `VERSION`; bump both `0.2.1` →
  `0.3.0`.

**Whole-repo gates:** description-length guard (≤1024, all skills); the cicd
`no-secrets-in-templates` test covers the new gate step.

## 8. Files touched & versioning

**production-ready → `0.5.1` → `0.6.0`:**
- `scripts/mcp_sbom.py` (new)
- `scripts/production_ready.py` (`_check_supply` wiring + `SUP-010..013` metadata + `VERSION`)
- `references/pillars/09-supply-chain.md` (SUP-010..013 rows, MCP section, remediation → foundry-toolbox / Key Vault)
- `references/skill-tool-supply-chain.md` (MCP paragraph + lockfile workflow)
- `SKILL.md` (frontmatter version + MCP-gate usage note)
- `tests/test_mcp_sbom.py` (new) + fixtures; `tests/test_version.py` (0.6.0)

**cicd → `0.2.1` → `0.3.0`:**
- `scripts/generate_pipeline.py` (`mcp_gate` knob → `MCP_GATE_*` tokens + `VERSION`)
- `references/github-actions/azd-deploy-prod.yml.tmpl` (MCP-gate step)
- `references/azure-devops/azure-pipelines.yml.tmpl` (MCP-gate stage)
- `SKILL.md` (version + gate knob doc)
- `tests/test_mcp_gate.py` (new)

**Repo metadata:**
- `plugin.json` + `.github/plugin/marketplace.json` — `1.6.0` → `1.7.0`; add MCP
  supply-chain keywords (`mcp supply chain`, `mcp sbom`, `tool-descriptor drift`,
  `mcp pinning`).
- `CHANGELOG.md` — `[Unreleased]` `Added` entry.

**awesome-gbb:** one **issue** (reusable MCP-provenance primitive proposal). No code.

## 9. Future work

- **`SUP-110` live tier:** connect to a running MCP server, enumerate tools,
  compare to `mcp-lock.json` — catches true runtime rug-pull. Same static/live
  split Pillar 09 already uses.
- **Extract `mcp_sbom.py` to an `awesome-gbb` primitive** if a second (non-
  Threadlight) consumer materializes (tracked by the issue above).
- **Capstone wiring (Part 2C):** `mcp-sbom.json` → Annex IV / Art 11 AI-BOM
  section of the EU AI Act evidence pack.
