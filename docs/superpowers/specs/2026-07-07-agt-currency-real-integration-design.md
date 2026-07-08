# Design — AGT currency: align `threadlight-govern` (and the `production-ready` ripple) to the real Agent Governance Toolkit

**Date:** 2026-07-07
**Status:** approved (scope: full correct reframe across both skills, one PR)
**Skills touched:** `threadlight-govern` (primary), `threadlight-production-ready` (ripple)

## 1. Problem

`threadlight-govern` is the PROTECT leg — it is supposed to make the Agent
Governance Toolkit (AGT) *actually enforce* a committed policy, then prove it.
A ground-truth pass against the **real published package** (`pip install
agent-governance-toolkit`, imports as `agent_compliance`, ships the `agt` CLI)
found the skill's integration model is built on an **API that does not exist**:

1. **Wrong install target.** The skill implies `pip install agt`. On PyPI, `agt`
   is an unrelated GPLv3 conversational-agent library, not the governance
   toolkit. The real package is `agent-governance-toolkit`.
2. **Fictional runtime middleware.** The skill's core narrative is *"wire the
   in-process AGT middleware at the container boundary; it enforces the policy
   on every tool call (~8–12µs/eval)."* The real toolkit has **no**
   `apply_governance`, **no** `create_governance_middleware`, **no** `agent_os`,
   and **no** request-interception middleware. Real AGT enforces at
   **build / CI time** and provides **programmatic evaluators** you call in your
   own code — it does not wrap the agent at runtime.
3. **Policy templates fail the real linter.** All three shipped policy
   templates (and the `sample-wired` fixture policy) fail real
   `agt lint-policy` with *"Missing required field 'name'"* and *"Missing
   required field 'rules'"*. They also conflate the policy document's own
   `version` with the AGT *package* version.

A customer who follows the skill today installs the wrong package, writes code
against symbols that do not import, and authors a policy the real CLI rejects.
This is a credibility defect in a public skill's core value proposition.

## 2. The real AGT model (ground truth)

- **Install:** `pip install agent-governance-toolkit` → console script `agt`,
  importable package `agent_compliance`. The **policy-evaluation runtime** and
  the **framework integration components** ship as the `[core]` / framework
  extras (e.g. `pip install "agent-governance-toolkit[core]"`, or
  `[openai-agents]` / `[langchain]` / `[langgraph]` / `[crewai]` …), which
  provide the `agent_os` package. `agent_os` is **real** — it is the governance
  runtime, not a fictional symbol; only the *specific* import surface the old
  skill claimed (`from agt import apply_governance, create_governance_middleware`,
  `agent_os.policies.dynamic_context`, a `~8–12µs` in-process shim, and an
  invented `agt verify --strict` flag) was fiction.
- **CLI:**
  - `agt lint-policy <path>` — validate policy schema. **Base install; always
    available.**
  - `agt verify [--evidence FILE] [--badge]` — run **OWASP ASI 2026** governance
    verification. Checks whether the ten ASI runtime governance controls
    (`agent_os.integrations.*` — `PolicyInterceptor`, `GovernancePolicy`,
    `EscalationPolicy`, `ToolAliasRegistry`, …) are present, and emits a
    `governance-attestation/v1` JSON (`passed`, `coverage_pct`,
    `controls_passed`/`controls_total`, per-control presence, `toolkit_version`,
    `attestation_hash`). `--badge` prints a coverage badge; `--evidence FILE`
    feeds **in** a runtime-evidence JSON/YAML (it is an *input*, not an output).
    Base install; coverage reflects how much of the runtime is actually wired.
  - `agt test <policy> <fixtures>` — replay policy fixtures through the
    `agent_os.policies.evaluator.PolicyEvaluator`; **requires the `[core]`
    extra** (without `agent_os` installed it errors). Exit non-zero on verdict
    mismatch.
  - `agt red-team`, `agt integrity`, `agt doctor`.
- **Programmatic evaluators (optional, call in your own request path):**
  `from agent_compliance import PromptDefenseEvaluator, PromptDefenseConfig,
  SupplyChainGuard, SupplyChainConfig`; `from agent_compliance.verify import
  GovernanceVerifier`.
- **Policy schema** (what `agt lint-policy` enforces):
  - Required top-level fields: `version`, `name`, `rules`.
  - Each rule: `name`, `priority`, `conditions: [{field, operator, value}]`,
    `action`.
  - Valid actions: `allow | deny | audit | block | escalate | rate_limit`.
  - Valid operators: `eq ne gt lt gte lte in not_in matches contains`.
  - Default-deny posture keys: `deny_by_default: true`, `default_action: deny`,
    or `defaults: {action: deny}`.
  - `version` is the **ruleset's own** semver (e.g. `1.0.0`), *not* the AGT
    package version.
- **Test-fixture schema** (for `agt test`): `{id, input: {...},
  expected_verdict, expected_rule?}`.

The honest PROTECT story: **author a linted `policy.yaml`, gate CI on
`agt verify` (the OWASP ASI 2026 `governance-attestation/v1`) plus
`agt lint-policy`, commit the attestation, and replay fixtures with `agt test`
where the `[core]` runtime is installed.** Raising attestation coverage means
wiring the real framework governance integration (`agent_os.integrations.*` via
the matching extra) — the attestation itself names which ASI controls are still
absent. Threadlight only makes the platform's own toolkit trivial to adopt; it
never replaces it, and `foundry-agt` remains the deep upstream authoring skill.

## 3. Design — `govern-manifest` v2 capability model

`govern_check.py` stops asserting a runtime concept it cannot see (middleware
wiring) and instead scores the **real, checkable** governance signals. The
manifest `schema` id bumps `threadlight-govern-manifest/v1` → `/v2`.

| Capability | Meaning | Severity when missing |
|---|---|---|
| `policy_artefact_present` | a committed `policy.yaml` / `agt-policy.yaml` exists | must-fix |
| `policy_schema_valid` | policy carries the real required fields `version` + `name` + `rules[]` (what `agt lint-policy` requires) | must-fix |
| `policy_versioned` | `version` is pinned (a real semver, not `latest`) | should-fix |
| `policy_default_deny` | policy declares a default-deny posture (`deny_by_default` / `default_action: deny` / `defaults.action: deny`) | should-fix |
| `sensitive_action_rules_present` | at least one rule `deny`/`block`/`escalate`s a sensitive/PII-bearing or state-changing action (pillar 7 posture, action-level) | should-fix¹ |
| `policy_tests_present` | `agt test` fixtures committed (`{input, expected_verdict}`) | should-fix |
| `ci_gate_present` | a CI workflow runs `agt verify` / `agt lint-policy` / `agt test` or the `microsoft/agent-governance-toolkit` action (proof governance actually runs in CI) | should-fix |
| `attestation_present` | committed `agt verify` output (`governance-attestation/v1`) exists | should-fix |
| `attestation_fresh` | attestation within the freshness window | should-fix |
| `asi_reference_present` | OWASP ASI 2026 anchor present | should-fix |

¹ becomes `must-fix` when no policy artefact exists at all (mirrors today's
`rai_policy_present` roll-up).

**Removed capabilities:** `middleware_wired_at_boundary` and `sidecar_pattern`.
Runtime governance is *not* fiction — `agt verify` attests real
`agent_os.integrations.*` components — but a **static evidence scorer cannot
honestly assert an in-process wiring** the way the old skill did, and the real
attestation (`agt verify` → `governance-attestation/v1`, surfaced via
`attestation_present` and echoed as informational `coverage_pct`) already
carries that runtime truth. So runtime integration is **documented as the way
to raise attestation coverage**, not scored as its own capability — keeping every
scored signal statically checkable and every recommendation actionable.
`rai_policy_present` is **renamed** to `sensitive_action_rules_present` and its
detection is redefined from "content_filter / prompt_shields block" (that is the
*model*-edge guardrail, not AGT) to "the policy has real `deny`/`block`/
`escalate` rules over sensitive actions."

**Verdict enum rename** (honesty — "wired" implies middleware):
`wired | partial | not-wired` → **`governed | partial | ungoverned`**.
- any `must-fix` → `ungoverned`
- only `should-fix` / `not-verified` → `partial`
- all clear → `governed`

The top-level graceful-degradation behaviour (a validator crash still emits a
valid manifest with `verdict: partial` and no fabricated must-fix) is
preserved.

## 4. Design — real policy templates

Rewrite `references/policy-templates/{default,hitl,pii-deny}.policy.yaml` and
`references/fixtures/sample-wired/policy.yaml` to the **real AGT schema**, each
verified to pass `agt lint-policy` (exit 0). Every template expresses the same
intent as today, but as real `rules[]`:

- **default** — `deny`/`block` shell-exec + external-email tool families;
  `allow` read-only tools; default-deny posture.
- **hitl** — adds `escalate` rules on state-changing actions
  (create_ticket / send_message / place_order).
- **pii-deny** — adds `deny`/`block` rules over PII-bearing / external-egress
  actions; strictest default-deny.

`version` becomes the **ruleset** semver (`1.0.0`), with a comment clarifying it
is the policy version, not the toolkit version. A matching `fixtures/` dir
(one `agt test` fixture per template intent) ships with the `sample-wired`
fixture so `policy_tests_present` and `ci_gate_present` are demonstrable.

## 5. Design — `threadlight-production-ready` ripple

The AGT fiction leaked into pillar-02 scoring. The **v4 deep-check machinery
(`AGT-V4-*`) is already built on real ground truth** (real distribution names,
real `microsoft/agent-governance-toolkit/action@`, ACS `intervention_points`)
and is preserved; only the fictional `agent_os.policies.dynamic_context` token
is **retargeted to the real `agent_os.integrations` runtime-governance surface**
(the components `agt verify` actually attests) in the `AGT-V4-003` regex. The
**version-agnostic `AGT-001..006` layer** is reframed:

- **Finding catalog / detection** (`_check_agt_static`):
  - `AGT-001` "AGT middleware imported in src/" → **"AGT policy is schema-valid
    (lints clean)"**. Legacy heuristic: policy file carries `version` + `name` +
    `rules:` (not a middleware `import`). Manifest map → `policy_schema_valid`.
  - `AGT-002` "policy.yaml present" — unchanged. Map → `policy_artefact_present`.
  - `AGT-003` "OWASP ASI 2026 referenced" — unchanged. Map →
    `asi_reference_present`.
  - `AGT-004` "AGT version pinned" → **policy ruleset version pinned**. Map →
    `policy_versioned`.
  - `AGT-005` "policy covers tool calls + prompt shields" → **"AGT governance
    gate runs in CI (`agt verify` / `lint-policy` / `test`)"**, severity
    **recalibrated `must-fix` → `should-fix`** (a present, valid policy that is
    not yet CI-gated is a real gap but not a hard block; the load-bearing
    must-fix is now "policy present + schema-valid" = AGT-001/002). Legacy
    heuristic: grep `.github/workflows/**` for `agt verify|agt lint-policy|agt
    test|agent-governance-toolkit/action`. Map → `ci_gate_present`.
  - `AGT-006` telemetry sink — unchanged (heuristic, not in manifest).
- **Manifest map** updated to the v2 capability names; unknown/absent caps
  degrade to `not-verified` (graceful, same as today). Concern separation:
  pillar 2 (`AGT-00x`) scores the governance *mechanics* (policy present /
  schema-valid / versioned / CI-gated / ASI anchor); the
  `sensitive_action_rules_present` capability is consumed **only** by pillar 7.
- **Pillar-07 RAI consumption** (`RAI-002`) reads the renamed
  `sensitive_action_rules_present` capability.
- **Framing:** the mermaid posture node "AGT middleware in-process", the
  glossary line ("In-process middleware that enforces policy on tool calls"),
  and the posture-mismatch detail string are reworded to the real model
  (a build/CI-time governance verifier + evaluators; the posture node becomes
  the policy/CI gate, not a runtime interceptor).
- **Recipe `AGT-001.md`** rewritten: author + `agt lint-policy` + `agt test` a
  real-schema policy and wire the `agt verify` CI gate (no fictional import).
- **Pillar doc `02-agent-governance.md`** version-agnostic layer + capability
  table reframed to the real model; the `agent_os` reference retargeted to the
  real `agent_os.integrations` runtime surface; the `AGT-001..006`
  descriptions aligned to the real finding catalog.
- **Citadel fixture** `src/app.py` (`from agt import policy`) + `specs/SPEC.md`
  line + the two committed golden lines (manifest title, glossary line) updated
  to the real model.

## 6. Testing

- **`threadlight-govern`** (`tests/test_govern_check.py`, stdlib unittest):
  rewrite assertions for the v2 capability set + `governed/partial/ungoverned`
  verdict; keep the bare-fixture must-fix + graceful-degradation tests. The
  `sample-wired` fixture must roll up to `governed` (or at least `partial` with
  no must-fix).
- **Real-linter gate (author-time, not CI):** every rewritten policy template +
  fixture is validated against the real `agt lint-policy` during development and
  must return exit 0. (We do not add `agent-governance-toolkit` as a CI dep —
  the repo's tests stay stdlib-only; the lint validation is a build-time proof
  the schema is correct.)
- **`threadlight-production-ready`** (pytest): update the AGT finding tests for
  the reframed titles/detection; full suite green apart from the two known
  pre-existing time-based stale-fixture `test_end_to_end` fails
  (continue-on-error in CI).
- Adverse code-review subagent before shipping (per cadence — has caught real
  robustness bugs on every prior epic).

## 7. Versioning

- `threadlight-govern`: `0.1.1` → `0.2.0` (capability + verdict contract change).
- `threadlight-production-ready`: `0.8.0` → `0.9.0` (pillar-02 finding/detection
  behaviour change).
- `plugin.json` + `marketplace.json`: minor bump; keywords refreshed to real
  terms (`agent-governance-toolkit`, `agt verify`, `agt lint-policy`).
- Root `CHANGELOG.md` `[Unreleased]`.
- `test_version.py` syncs for both skills.

## 8. Non-goals / out of scope

- Not adding `agent-governance-toolkit` as a runtime or CI dependency of this
  repo. The skills remain stdlib-only; they *validate the customer's* AGT usage,
  they do not import AGT.
- Not rewriting the historical internal design note
  `2026-06-10-agt-v4-deep-checks-design.md` (de-published from Pages; left as an
  accurate-to-date record — only the live pillar doc + code are corrected).
- Not re-authoring the deep AGT surface (`foundry-agt`'s job). This skill stays
  the thin pipeline leg that decides *whether* governance is present, linted,
  tested, and CI-gated — and produces the manifest the scorecard consumes.

## 9. Scrub

All committed text (files, commit messages, PR body) ships as standalone
hardening — no external framing, no comparisons, no repo names. `Citadel` and
`kratos` remain legitimate product terms.
