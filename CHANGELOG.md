# Changelog

All notable changes to this repository are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions on individual skills live in their `SKILL.md` `metadata.version`
field.

## [Unreleased]

### Added

- **EU AI Act evidence pack — terminal aggregator in
  `threadlight-production-ready`** (0.7.0 → 0.8.0). A new stdlib-only script,
  `scripts/ai_act_evidence.py`, maps the artifacts this skill and its siblings
  already produce — the production-readiness scorecard manifest, `mcp-sbom.json`,
  `agent-identity.json`, and the govern / evals / red-team manifests — onto seven
  EU AI Act articles (9, 11 + Annex IV, 12, 14, 15, 26, 27) and emits a
  **tenant-local, offline** evidence pack: `ai-act-evidence.json`,
  `annex-iv-technical-file.md`, and an Article 27 `fria-scaffold.md` under
  `docs/compliance/`. Each article is graded `covered` / `partial` / `gap` /
  `scaffold` with a per-source SHA-256 for provenance; a missing or malformed
  source degrades to `gap` / `partial` — the pack never fabricates a `covered`. A
  `--check` flag exits 3 when a load-bearing article (Art 11 / 12 / 15) is a gap.
  It amplifies Foundry's own governance outputs into regulator-facing evidence and
  is an engineering aid, not legal advice. A new `references/eu-ai-act-mapping.md`
  documents the mapping. Bumps the plugin manifest 1.8.0 → 1.9.0.
- **Agent-identity binding — non-human-identity (NHI) governance in
  `threadlight-production-ready`** (0.6.1 → 0.7.0). The identity-access pillar now
  governs the identity the agent *is*, not just secrets in source. A new
  stdlib-only producer, `scripts/agent_identity.py`, inventories every declared
  agent identity (user-assigned managed identity / federated credential /
  app-secret) from compiled ARM, Bicep, and source signals and writes an
  `agent-identity.json` sidecar next to the report. Four new static findings score
  it: **IAM-006** passwordless binding (must-fix — managed or federated, not a
  client secret), **IAM-007** responsible owner (should-fix), **IAM-008**
  least-privilege scope (must-fix — no Owner/Contributor/User-Access-Administrator
  or wildcard `*.ReadWrite.All` Graph permission), and **IAM-009** lifecycle /
  review (should-fix — a `reviewBy`/`expiresOn` signal, with federated identities
  passing automatically). An optional `agent-identity.governance.json` manifest
  supplies owner / review metadata per subject id. Remediation recipes point at
  `entra-agent-id`, `foundry-agt`, `azure-rbac`, and Entra access reviews / PIM —
  it amplifies the platform's identity primitives, never replaces them. A producer
  error degrades the four findings to `not-verified`; the assessor never crashes.
  Bumps the plugin manifest 1.7.0 → 1.8.0.

- **Built the six `threadlight-event-triggers` receiver scaffolds for real**
  (1.1.0 → 1.2.0). The SKILL's "Reference files" table listed `aca-job-cron`,
  `aca-job-manual`, `aca-consumer`, `function-http`, `function-servicebus`, and
  `function-eventgrid` as *shipped*, but only a placeholder index existed. Each
  scaffold now carries a pure, idempotent, injectable `handle(...)` core (derive
  dedup key → skip duplicates → invoke the Foundry-hosted agent → dead-letter on
  failure without marking processed), an offline `local.test.py`, and a shared
  `pytest` suite — all runnable with no Azure SDKs installed. The `aca-*` shapes
  ship `receiver.py` + `Dockerfile` + `receiver.bicep`; the `function-*` escape
  hatches use the v2 Python model (`function_app.py` + a pure `receiver_core.py`
  + `host.json`, no legacy `function.json`). A structural guard test keeps the
  "shipped" claim self-verifying. Dead-letter strategy is per shape (Storage
  Queue poison store, Service-Bus-native dead-letter, or platform re-raise →
  native DLQ). No connection strings — managed identity throughout.

### Changed

- **Realigned the AGT (Agent Governance Toolkit) integration to the real
  toolkit model** across `threadlight-govern` (0.1.1 → 0.2.0) and
  `threadlight-production-ready` (0.8.0 → 0.9.0). Governance is authored as a
  **committed policy** — `policy.yaml` with top-level `version` + `name` +
  `rules:`, validated by `agt lint-policy` / `agt test` and attested by
  `agt verify --badge` — not an in-process middleware import. The govern PROTECT
  leg now scaffolds and scores a schema-valid policy, its default-deny posture,
  its sensitive-action rules, and its CI gate. In the production-readiness
  assessor, Pillar 02 (`AGT-001..006`) rescopes to the policy artefact: `AGT-001`
  = the policy is schema-valid (lints clean), `AGT-004` = a pinned ruleset
  `version:`, `AGT-005` = a CI workflow runs the toolkit (`agt verify` /
  `lint-policy` / `test`, now `should-fix` — the gate lives in CI, not the request
  path). `RAI-002/003` decouple from the governance manifest so a model-edge
  control (Content Safety prompt shields) is scored on its own signals. The
  `AGT-001/002/005` remediation recipes, Pillar 02 reference, report glossary /
  mermaid, and the `sample-pilot-citadel` exemplar (real schema-valid policy that
  lints clean, plus a `governance.yml` CI gate) are rewritten to match. The
  version-agnostic v4 deep-checks (`AGT-V4-*`) are unchanged. Bumps the plugin
  manifest 1.9.0 → 1.10.0.
  <br>Cross-skill hardening: policy schema-validity and the pinned `version:`
  are evaluated against a **single canonical policy file** (never merged across
  siblings, which could false-pass the governance hard gate); CI-gate detection
  requires an actual toolkit invocation (an `agt` verb or the
  `agent-governance-toolkit/action`) and ignores commented-out lines,
  identically in both skills; and the govern baseline policy templates drop a
  v4-only metadata key so a pilot that adopts them stays on the `v3_7` profile.
  The scaffolded CI gate treats `agt test` (fixture replay) as **advisory**
  (`continue-on-error: true`) while keeping `agt lint-policy` + `agt verify` as
  the required gates: shipping AGT 4.1.0's replay path binds an `agent_os`
  `PolicyDocument` model that requires a singular `condition:` and rejects the
  `escalate` action, so a human-in-the-loop policy that lints clean and is valid
  at runtime would otherwise fail its own gate. The wired exemplar, wiring
  snippet, policy-template headers, and the `AGT-005` recipe are updated to
  match, pinned by a govern regression test.


  1.6.2). Step 1 told the agent to *copy* a `references/scaffold/` directory that
  the repo never shipped. The modern flow **generates** the `azd` skeleton
  (`azure.yaml`, `agent.yaml`, vendored `infra/`) via the pinned `azd ai agent`
  extension — exactly as the Phase 5 header and `references/upstream-pin.md`
  already describe. Reworded Step 1 to attribute generation to the extension and
  reframed the tree as the generated + Phase 2 layout (no behaviour change).

- **Added an MCP supply-chain gate** across `threadlight-production-ready`
  (0.6.0) and `threadlight-cicd` (0.3.0). The production-readiness assessor now
  discovers MCP servers/tools declared in a repo, writes an `mcp-sbom.json`
  sidecar, and scores four new supply-chain findings — servers pinned to a
  version/digest (`SUP-010`), resolvable from a known registry (`SUP-011`),
  tracked in a committed `mcp-lock.json` free of undocumented server/tool drift
  (`SUP-012`), and free of inline credentials (`SUP-013`). The CI/CD generator
  gains a `--mcp-gate soft|hard` knob that adds a post-deploy gate enforcing the
  SBOM. Remediation points at `foundry-toolbox`, Key Vault, and ACR. `plugin.json`
  and `.github/plugin/marketplace.json` bump to `1.7.0` with MCP keywords.

- **Surfaced `threadlight-router-bench` as the Improve leg on the front door
  and refreshed the plugin manifests.** The README hero now counts **16 pipeline
  skills + the `threadlight-auto` orchestrator (17 total)**, adds a router-bench
  row to the leg table and the **Improve** branch to the pipeline flow diagram;
  `plugin.json` and `.github/plugin/marketplace.json` bump to `1.6.0` with the
  matching count and router-bench keywords (`router-bench`, `self-improvement`,
  `learnings-digest`, `ci-failure-taxonomy`, `model-router-cost`).
- **Added "See also — official Azure Skills" cross-references** to
  `threadlight-deploy`, `-consumption-iq`, `-evals`, `-govern`,
  `-production-ready`, and `-cicd` (PATCH bumps). Each points at the canonical
  Microsoft Azure Skills the leg leverages — `microsoft-foundry`,
  `azure-reliability`, `azure-rbac`, `entra-agent-id`, `entra-app-registration`,
  `azure-cost` — as further reading, not a dependency, so the pipeline stays a
  thin, opinionated path *over* the platform rather than a re-implementation of
  it.

### Fixed

- **Hardened the `threadlight-production-ready` readiness scorecard against
  modern ARM shapes** (0.8.0 → 0.8.1). The compiled-ARM walker (`BicepGraph`)
  assumed the top-level `resources` was always a list, so it crashed on
  **symbolic-name ARM** (`languageVersion 2.0` — the current azd/Bicep default,
  where `resources` is a `{symbolicName: object}` map), aborting the whole
  assessment before any pillar ran. `_walk` now accepts both the map and the
  list shape (and nested-template maps). The model-lifecycle static check
  (MDL-001) also crashed when a deployment's `model` / `version` was supplied via
  an ARM parameter or copy-loop expression (an expression *string* rather than an
  object); it now classifies those as `not-verified` ("verified at deploy" by the
  live MDL-101 check) rather than crashing or raising a false must-fix. It also
  no longer silently passes a model whose `version` is itself a parameter
  expression (now `not-verified`), and a genuinely absent model stays a
  `must-fix`. Finally, a per-pillar resilience guard makes any pillar whose
  static analyzer raises on an unforeseen ARM shape **fail closed**: its tier-0
  findings degrade to a **visible** gate-blocking `must-fix` (carrying the
  error) with an stderr warning, so the run always completes but the hard
  go-live gate keeps blocking until it is resolved — it never silently relaxes
  the gate. Sixteen new tests pin the map/list walk, the param-aware model
  check, and the fail-closed guard.
- **Corrected three stale companion pointers in `threadlight-deploy`** — the
  hosted-agent fallback now names the `foundry-hosted-agents` companion, and the
  `pyproject.toml` / `Dockerfile` steps point at the inline templates directly
  below them instead of a `references/` path that ships no such file.
- **Corrected stale `.github/plugin/marketplace.json` metadata** — the
  marketplace + plugin `description` said "9 Copilot skills" and `version`
  `1.0.0`; both now reflect the current 16 pipeline skills + `threadlight-auto`
  orchestrator (17 total) at plugin `1.6.0`, matching `plugin.json` and the
  README.
- **Fixed the CI/CD deploy-gate verdict check in `threadlight-cicd`** (0.3.0 →
  0.3.1). The generated GitHub Actions and Azure DevOps pipelines gated on
  eval / red-team verdict strings (`pass` / `passed` / `ok`) that the assessors
  never emit, so a clean run could still fail the gate. The gates now match the
  real enums — eval `comprehensive` / `partial` and red-team `hardened` /
  `partial` (i.e. no `must-fix`) both pass — with tests that extract and execute
  the embedded gate script against real verdict values.
- **Fixed the `threadlight-safe-check` invocation across the skills** (1.1.0 →
  1.1.1). Docs and sibling skills called `python -m threadlight.safe_check`, but
  the script is vendored into the pilot repo as `tests/safe_check.py` (there is
  no importable `threadlight` package), so the module form always raised
  `ModuleNotFoundError`. Normalised every call site to
  `python3 tests/safe_check.py` and removed a documented `--strict` flag the CLI
  never defined.
- **Fixed a contradictory Dockerfile base image in `threadlight-deploy`** (1.6.2
  → 1.6.3). The readiness checklist told operators to build the agent and bot
  images `FROM python:3.12-slim`, directly against the same skill's mandate to
  use `mcr.microsoft.com/oryx/python:3.12` (Docker Hub's unauthenticated pull
  limits break ACR Tasks builds). Both checklist rows now match the mandate.
- **Fixed `threadlight-production-ready --remediate <id>`** so it can reach every
  finding's recipe (0.6.0 → 0.6.1). The flag read only the legacy
  `remediation-recipes.yaml` (~12 IDs), leaving the 70+ per-file recipes under
  `references/remediation-recipes/{ID}.md` — the set the apply-plan machinery
  already uses — unreachable. `--remediate` now falls back to the per-file
  catalog when an ID is absent from the yaml, and the previously un-collected
  renderer test file is bridged into `pytest`.
- **Refreshed the `THREADLIGHT.md` skill inventory.** The engineering reference
  still said "sixteen" skills and omitted `threadlight-router-bench` from both its
  skill list and flow table. It now lists all seventeen skills (16 pipeline + the
  `threadlight-auto` orchestrator) and documents the router-bench IMPROVE leg,
  matching the README and marketplace metadata.

### Removed

- **Purged four committed `.pyc` bytecode files** from the `threadlight-local-test`
  quickstart `__pycache__/`. They were tracked despite the existing `__pycache__/`
  `.gitignore` rule (they were committed before it landed); Python regenerates them
  on import.

### Added

- **GitHub cloud sandbox docs.** Documented running the skills in an ephemeral,
  GitHub-hosted **cloud sandbox** (`copilot --cloud`, public preview) now that the
  org has enabled it. README gains an "In a GitHub cloud sandbox" subsection
  (launch, marketplace wiring since sandboxes ignore `.devcontainer/`, inherited
  cloud-agent policy + host allow-list, and the preview/usage-billed caveats); the
  experience site adds a "GitHub cloud sandbox" column to the private-env test
  comparison in [`docs/customize.html`](docs/customize.html) and a zero-install
  "try it first" callout to [`docs/workbook.html`](docs/workbook.html).
- **Dev Container + GitHub Codespaces quickstart.** New
  [`.devcontainer/`](.devcontainer/) (thin `base:ubuntu` image + `github-cli`
  feature) installs GitHub Copilot CLI and wires all 16 threadlight skills from
  the local checkout on create, so a Codespace boots ready to explore the
  pipeline — `copilot` → `/login` → prompt. Consumer-focused: no Python/Node
  test toolchain and no Azure deploy tooling (`azd`/`az`/`bicep`/Docker); the
  deploy and production legs still need a full local/VNet environment. README
  gains an "Open in Codespaces" badge, the marketplace-install alternative, and
  an honest limitations note (auth, workiq/MCP, deploy tooling).
- **Executable Responsible-AI-for-Foundry control plane — `threadlight-evals`,
  `threadlight-redteam`, `threadlight-govern` v0.1.0 (plugin 1.5.0).** Closes
  the gap where `path2production` *scored* its control-plane legs but never
  *ran* them. The pipeline now operationalizes the Microsoft RAI-for-Foundry
  loop — **Design → Build/Deploy → Discover → Protect → Govern → Improve** —
  with three new first-class legs wired into the spine and verified by
  `production-ready`:
  - **NEW skill: [`skills/threadlight-evals/`](skills/threadlight-evals/SKILL.md)**
    — the **Discover** evals leg. Offline batch quality evals (delegates
    invoke+score to `foundry-evals`), **online / continuous evaluation** on
    live threads (Foundry `create_agent_evaluation` → App Insights, with
    reasoning), and an **A/B champion–challenger** comparison gate before a
    model/prompt swap. Emits `specs/evals-manifest.json` consumed by
    `production-ready` pillar 6 (EVAL-001..004). 10 stdlib tests.
  - **NEW skill: [`skills/threadlight-redteam/`](skills/threadlight-redteam/SKILL.md)**
    — the **Discover** safety leg. Runs the **AI Red Teaming Agent**
    (PyRIT-based) adversarial scan for jailbreak / prompt-injection /
    data-exfiltration / harmful-content, emits `docs/redteam-report.md` +
    `specs/redteam-manifest.json`, and maps attack-success-rate to
    `production-ready` pillar 7 findings **SAFE-101..106**. Replaces the old
    static "is a jailbreak shield declared?" check with an actual scan. 10
    stdlib tests + 3 remediation recipes (SAFE-101/102/103).
  - **NEW skill: [`skills/threadlight-govern/`](skills/threadlight-govern/SKILL.md)**
    — the **Protect** leg. Wraps `foundry-agt`: scaffolds/validates the
    agent-runtime governance policy artefact, verifies in-process governance
    middleware at the container boundary, and emits a committed verifier
    report + `specs/govern-manifest.json`. Produces the artefacts
    `production-ready` pillar 2 (AGT-001..005) and pillar 7 (RAI-002/003) look
    for. 12 stdlib tests.
  - **`production-ready` flip.** Pillars 2/6/7 move from "remediate → go run
    X" to "**verify the leg ran + artefact fresh**" when the govern/evals/
    red-team manifest is present and within the 90-day window; they fall back
    to the legacy heuristics when a manifest is absent or stale.
    `_load_leg_manifest`/`_leg_cap_status` helpers added (never raise); new
    SAFE-101..106 catalog entries + a `_check_redteam_static` finding mapper.
    Backward-compatible — existing fixtures (no manifests) keep unchanged
    finding sets.
  - **Spine wiring.** `threadlight-auto` gains three resumable stages
    (`evals`, `redteam`, `govern`) after `invoke`, each gated on a fresh
    `specs/*-manifest.json` (missing/stale-24h → run, fresh → skip) so a
    re-deploy upstream cascades a fresh evaluation / scan / governance pass.
    `orchestrator.py` (STAGES + `_check_leg_manifest` probe), `state-schema.md`,
    and the auto `SKILL.md` Resumption + Sub-stages tables updated.
  - **Docs:** `README.md` (now **fifteen pipeline skills + one orchestrator,
    16 total**, skills table, Discover/Protect pipeline-flow + RAI operating
    loop), `THREADLIGHT.md` (sixteen-skill count, alphabetical list, three
    new entry-skill picker rows + chain sections 9/10/11, production-ready →
    12 / cicd → 13 / customize → 14), and
    `docs/IDEA-TO-PRODUCTION-WORKBOOK.md` (arc diagram + steps 8/8a/8b)
    updated. `plugin.json` bumped 1.4.0 → 1.5.0 with new keywords.
  - **CI:** `.github/workflows/python-pytest.yml` runs the three new skills'
    stdlib test suites as hard-fail steps (deterministic, secret-free, no
    network).
  - **`threadlight-cicd` eval + red-team gate (F6, v0.2.0).** The generated
    production pipelines now run the two Discover legs as post-deploy gates —
    `eval-gate` + `red-team-gate` jobs (GitHub Actions, `needs: deploy`, OIDC)
    and `eval_gate` + `red_team_gate` stages (Azure DevOps, `dependsOn: deploy`,
    WIF) — each enforcing the leg's `specs/{evals,redteam}-manifest.json`
    verdict. New `--eval-gate soft|hard` flag: **soft** (default) is warn-only
    so a first onboarding isn't wedged before a baseline manifest exists;
    **hard** blocks on a missing or non-pass verdict. Secret-free (OIDC + WIF
    only). 9 stdlib tests; full cicd suite 44 passing.
  - **`production-ready` outcome-KPI scorecard (F7).** Report § 8 ("Outcome KPI
    scorecard") now joins the three signals CAF asks teams to measure as a real
    outcome — eval pass-rate (`specs/evals-manifest.json`), cost-per-interaction
    (`specs/cost-manifest.json`), and live traces (foundry-observability
    wiring) — plus the declared baselines (latency / cost-per-interaction /
    success-rate) and whether a deviation alert is wired. Scored as
    **KPI-001..003** (should-fix, tier-0) under pillar 5 (observability), where
    CAF's agent-observability triad places baselines + deviation alerts.
    `_kpi_signals` join helper + `_check_kpi_static` (never raise); the
    `kpi_scorecard` block is stashed into the JSON manifest. 11 stdlib tests +
    a `KPI-002` deviation-alert remediation recipe.
  - **Deferred (truthful):** the P2 `threadlight-optimize` eval-driven
    optimization loop + central eval-catalog conventions are planned as
    fast-follow commits.
- **`threadlight-consumption-iq` v0.3.0 — pre-sales phased estimate mode
  (plugin 1.5.0).** Extends the post-deploy SKU-diff projector with the
  **pre-sales / pre-deploy** front-end it explicitly lacked: estimate Azure
  consumption for a workload that **isn't deployed yet**, across the customer's
  adoption ramp (POC → expansion → business-wide). **Cost-estimation only — no
  customer/CX specifics; a generic pilot throughout.**
  - **Phased rollout** — a new `rollout_profile{}` (`references/rollout-profile-schema.md`)
    models N phases, each its own `load_profile{}` + hardening `posture`
    (`demo` | `production` | `production-hardened`). New `scripts/rollout.py`,
    `scripts/estimate.py` orchestrator, `estimate` CLI subcommand +
    `run --all --pre-sales`.
  - **Production-hardening / estate delta** — `scripts/hardening.py` +
    `references/hardening-delta-catalog.json` add the SKUs that appear at
    production scale (Front Door + WAF, Private Endpoints, Defender, Sentinel,
    DDoS, multi-region DR, non-prod estate) as a labelled **delta**, with
    `shared_platform_billed` honesty on estate-amortised items.
  - **Observability ingestion projector** — `scripts/projectors/observability.py`
    sizes Log Analytics / App Insights GenAI-OTel ingestion (the frequently
    top-3, frequently-forgotten line), wired into the standard projector
    dispatch so the post-deploy path benefits too.
  - **EA/MCA discount multiplier** — `scripts/discount.py` applies an optional
    `--discount`/`--discount-basis` multiplier; retail is always preserved
    alongside, with a caveat that it's a planning **estimate, not a quote**.
  - **Shareable seller one-pager** — `scripts/onepager.py` +
    `references/onepager-template.html` render an HTML (best-effort PDF)
    leave-behind with estimate-framing, internal-vs-customer classification
    (a "do not share" strip + seller talk-track on the internal variant), and
    the PayGo-vs-PTU-as-SLA narrative.
  - **Manifest schema 1.1** — additive (`pre_sales`, `phases[]`, `discount{}`,
    `totals.*` — including a `monthly_cost_hardening_shared_usd` breakout of the
    estate-amortised portion — mirror the current phase **exactly**, even under
    a discount) so `threadlight-production-ready` COST-005/006 still read a
    number. New `references/cost-estimate-manifest-schema.md`.
  - **Repo-free, per-phase topology** — a rollout profile may declare its own
    `resources[]` (top-level and/or per-phase), so an estimate runs with **no
    Bicep / `azd` discovery** and the topology can *evolve* across phases — the
    real land-and-expand SKU step (AI Search Basic → S1 → S2). The CLI only
    falls back to repo discovery when no topology is declared.
  - **SKILL.md discipline** — new "Pre-sales phased estimate mode" section with
    an **estimate-framing** rationalization table + red-flags list and an
    **internal/customer classification** rule, asserted by
    `tests/test_skill_discipline.py`.
  - **Fail-fast guardrails** — `retail` basis can't carry a real discount; a
    `1.0` multiplier is a no-op for any basis; an out-of-range/invalid discount
    exits 4 (not an uncaught traceback). The seller one-pager carries the
    estate-billed caveat through to the forwarded artefact.
  - **Tests:** +100 unit/golden/discipline tests (rollout, observability,
    hardening, discount, one-pager, emitter, estimate, CLI, two e2e golden
    fixtures, skill-discipline, no-VF3/no-secrets denylist); new
    `references/fixtures/sample-presales-rollout/` and
    `references/fixtures/sample-presales-topology-rollout/` golden fixtures.

- **`threadlight-customize` v0.1.0 — fork-and-customize final leg (plugin
  1.4.0).** Closes the last unstated assumption in the pipeline: that an SE
  can stand Threadlight up **inside one specific customer's environment** and
  adapt its production onboarding. We deliberately ship **instructions, not
  automation** — per-customer prod onboarding is too high-variance to encode,
  so the deliverables are fill-in workbooks + runbooks, informed by a real
  large-European-telco AI pilot (anonymized).
  - **NEW skill: [`skills/threadlight-customize/`](skills/threadlight-customize/SKILL.md)**
    — a four-move meta-skill after `threadlight-cicd`: **Move 1 intake gate**
    (a `customer-profile.md.tmpl` workbook capturing customer documents,
    environment setup, requirements, and mandated template/starter code);
    **Move 2 customization map** (classifies every Threadlight skill as
    customer-agnostic *keep* vs needs-per-customer *override*, with the
    **production-onboarding leg flagged priority** — `deploy`, `safe-check`,
    `cicd`, `production-ready`); **Move 3 test-in-customer-env runbook** for
    fully-private VNet envs (**Azure ML compute instance + VS Code Remote**
    recommended, **GitHub Codespaces** quick-box, plus a private-VNet
    pre-flight reachability checklist); **Move 4 non-coverage boundary** +
    decision log that keeps expectations honest.
  - **Fork mechanics: [`references/fork-runbook.md`](skills/threadlight-customize/references/fork-runbook.md)**
    — fork the plugin, **pin upstream**, and keep customer changes in an
    **overlay** (not in-place forks of skill files) so upstream Threadlight
    updates still merge.
  - **Anonymized field notes** — the telco-pilot learnings ship under
    *"a large European telco AI pilot"*, never naming the customer (public
    MIT repo); enforced by `tests/test_no_secrets_in_templates.py` (secret
    literals **and** a customer-name denylist).
  - **CI:** `.github/workflows/python-pytest.yml` runs the new skill's
    instructions-only test suite (version + structure + no-secrets) as a
    hard-fail step (deterministic, secret-free, no network).
  - **Docs:** `README.md` (now **twelve pipeline skills + one orchestrator,
    13 total**, skills table, pipeline-flow + manual-handoff note) and
    `THREADLIGHT.md` (new chain section 11 + entry-skill picker row +
    thirteen-skill count) updated. `threadlight-auto` deliberately does
    **not** drive this skill — like `cicd`, it's a manual handoff leg.


  skill (plugin 1.3.0).** Closes the biggest unstated assumption in the
  production leg: that the coding agent can run `azd up` with broad rights.
  Real customer prod environments deploy through a **CI/CD pipeline**, under
  a **federated identity** with **scoped RBAC**, often from **private-VNet
  runners**. The new skill generates that pipeline plus the env-setup
  runbooks the customer's platform team runs.
  - **NEW skill: [`skills/threadlight-cicd/`](skills/threadlight-cicd/SKILL.md)**
    — opens with an **onboarding-path decision gate** (is a central platform
    env required? already deployed?) that resolves to one of three paths:
    `standalone`, `spoke-onboard` (consume an existing hub via an Access
    Contract → `citadel-spoke-onboarding`), or `hub-deploy-then-spoke`
    (stand the hub up on the **separate** central track → `citadel-hub-deploy`,
    then onboard). The resolved path is written to an auditable
    `onboarding-path.json`.
  - **Generates `.github/workflows/azd-deploy-prod.yml` (GitHub OIDC) or
    `azure-pipelines.yml` (Azure DevOps Workload Identity Federation)** plus
    `docs/threadlight-cicd/env-setup/` runbooks + `.sh` scripts — `01` UAMI +
    federated credentials, `02` least-privilege RBAC (scoped to the
    target/spoke RG only), `03` private-VNet runners (managed **and**
    self-hosted) — and a `central-platform-boundary.md`. Generation is
    **deterministic, offline, and secret-free** (OIDC/WIF only; no
    `AZURE_CREDENTIALS`, client secret, or PAT ever emitted — enforced by the
    test suite).
  - **Parallel-track boundary (the must-tell).** The pilot pipeline is a
    **separate repo/pipeline** from central-platform deployment. It deploys
    **only** use-case resources into the spoke/target RG and **must never**
    deploy or modify the Citadel hub, shared APIM, shared networking, or
    platform Key Vault — those are owned by `citadel-hub-deploy` (awesome-gbb).
    Documented in `SKILL.md`, the generated boundary doc, and `THREADLIGHT.md`.
  - **`threadlight-production-ready`** — Phase 3 (`--scaffold-cicd`) keeps its
    *basic* GitHub-Actions-only scaffold for backward-compat but now
    **delegates** to `threadlight-cicd` as the authoritative/expanded home
    (stderr pointer after the scaffold write, SKILL.md Phase 3 pointer, and a
    new **section G** in `references/handoff-checklist.md`: "The production
    deploy path exists (CI/CD)"). No behavior change — existing tests stay
    green.
  - **CI:** `.github/workflows/python-pytest.yml` runs the new skill's 35
    tests as a hard-fail step (deterministic, secret-free, no network).
  - **Hardening (pre-merge adversarial review).** Two adverse passes (security
    review + a fresh-agent apply-test against a private-VNet bank scenario)
    drove fixes now pinned by tests: (1) **RBAC** — the deploy identity also
    gets *Role Based Access Control Administrator* at the **same target-RG
    scope** so keyless Foundry `azd provision` can perform
    `roleAssignments/write` (Contributor alone → `AuthorizationFailed`);
    (2) **azd env seeding** — pipelines run `azd env new ... || true` before
    `provision` so a clean CI checkout doesn't abort; (3) **ADO** now uses
    **separate** provision and deploy `AzureCLI@2` tasks (matches the
    checklist) and reuses the az session (`auth.useAzCliAuth`); (4) **GitHub**
    drops the redundant `azd auth login` token exchange; (5) **path-aware
    boundary doc** — on the spoke-onboard path it explicitly says *do not run
    `citadel-hub-deploy`* and surfaces the hub coordinates + Access Contract
    product (`--hub-sub` / `--hub-apim-id` / `--access-contract-product`);
    (6) **private-runner runbook** now spells out Managed DevOps Pool / subnet
    delegation / egress / private-DNS prerequisites and adds `--ado-pool-name`;
    (7) **RBAC runbook** ensures the target RG exists first.
  - **Docs:** `README.md` (now **eleven pipeline skills + one orchestrator**,
    skills table, pipeline-flow note) and `THREADLIGHT.md` (new chain
    section 10 + entry-skill picker row + twelve-skill count) updated.
    `threadlight-auto` deliberately does **not** drive this skill — it's a
    manual handoff step, not part of the pilot-driver state machine.

- **Kratos-export bridge (issue #39)** — Threadlight skills now compose
  cleanly on a **Kratos-exported agent project** (`<use-case>-foundry-agent.zip`:
  `src/hosted-agent/` + `use-cases/<x>/` + trimmed `infra/`), so an SE can
  `azd up` the export and then layer in production-hardening skills without a
  rewrite. Additive — the existing `threadlight-design`-driven flow is unchanged.
  - **NEW doc: [`docs/KRATOS-BRIDGE.md`](docs/KRATOS-BRIDGE.md)** — canonical
    bridge reference: export shape, detection signal (`src/hosted-agent/` +
    `use-cases/<x>/`), the **skills-root convention** (auto-detect
    `use-cases/<x>/skills/` vs `src/agent/skills/`, `--skills-root` override —
    no symlinks, option (b)), the 3-command deploy, what's intentionally trimmed
    (APIM / multi-tenant FE / `evals/`), and the TL-skill invocation order.
  - **`threadlight-deploy` v1.6.0** — detects Kratos-export mode and
    **skips Phase 2 generation** (Dockerfile / `main.py` / `azure.yaml` already
    shipped); enrich/validate only. NEW
    [`references/kratos-export-mode.md`](skills/threadlight-deploy/references/kratos-export-mode.md)
    with the do-NOT-regenerate list, skills-root resolution, and **one-command
    `evals/` backfill** (Kratos's exporter excludes `evals/` per `_SKIP_DIRS`;
    running still delegates to `foundry-evals`).
  - **`threadlight-safe-check` v1.1.0** — recognizes the Kratos-export shape as a
    valid input without `specs/manifest.json` (derives `expected_resource_types`
    from the export's own `infra/` + `azure.yaml`); trimmed infra (no APIM /
    multi-tenant FE) is intentional, never a "missing module" finding.
  - **`threadlight-production-ready`** (v0.5.0, additive — `v0.6.0` is reserved
    for the deferred milestone) — Kratos-export mode: framing wizard supplies
    SPEC § 12 when there is no SPEC (no exit 2); trimmed infra scored
    `not-applicable` (informational), not `must-fix`, so the scorecard is real
    uplift items rather than a wall of findings.
  - **`threadlight-consumption-iq` v0.2.0** — discovers resources from the
    export's `infra/` + `azd env` (no SPEC manifest), tolerates Kratos resource
    naming (match by ARM type), and persists `load_profile{}` to
    `use-cases/<x>/load-profile.yml`.
  - **`threadlight-hitl-patterns` v1.1.0**, **`threadlight-event-triggers`
    v1.1.0**, **`threadlight-workspace-ui` v1.1.0** — scaffold next to the
    existing `use-cases/<x>/skills/` via the skills-root convention; take their
    contract from the operator / `SYSTEM_PROMPT.md` when there is no SPEC.
  - **`threadlight-local-test` v1.3.0** — Pattern 0 boots a Kratos export
    (skills from `use-cases/<x>/skills/`, seed from `mocks/`).
  - **`threadlight-auto` v1.1.0** — NEW "start from Kratos export" entry path:
    skips Design, runs deploy in enrich-only mode, then safe-check → cost →
    invoke → production-ready.
  - **`threadlight-design`** — docs-only note: it is the from-scratch path; the
    Kratos-export path bypasses it (no behavioral change).

- **NEW skill: `threadlight-consumption-iq` v0.1.0** — post-deploy
  Azure cost projection + SKU-diff recommender. Walks Bicep + `azd env`,
  reads SPEC § 12 `load_profile{}` (wizard writes it back if absent),
  hits the Azure Retail Prices API for each deployed SKU + 2–3
  alternatives per resource, emits `docs/cost-projection.md` (human) +
  `specs/cost-manifest.json` (machine).
  - **v0.1.0 is the first feature-complete release.** Full
    `SKILL.md`, CLI dispatcher (`scripts/consumption_iq.py` with
    `discover`, `load-profile`, `price`, `project`, `recommend`, `emit`,
    `run --all`), full implementations of:
    - `discover.py` — `az bicep build` + ARM walker + 7 per-kind
      extractors + `az resource list` cross-check + drift warnings
      against `deployment_manifest.expected_resource_types[]`.
    - `load_profile_wizard.py` — hand-rolled YAML parser (no pyyaml
      dep), workload_class-keyed defaults, idempotent on complete SPEC,
      back-fills SPEC § 12 markdown.
    - `pricing_client.py` — public Azure Retail Prices API
      (`prices.azure.com`) via urllib + 24h cache + 7-resource fixture
      fallback (AOAI populated; others empty skeletons ready for
      live-fetch).
    - 7 projectors (AOAI, Foundry hosted-agent, ACA, Cosmos NoSQL,
      Storage, APIM, AI Search) implementing the formulas in
      `references/consumption-formulas.md`.
    - `recommender.py` — constraint scoring + ranking by savings desc,
      honors `pinned_region` and `min_redundancy` (with
      `REDUNDANCY_RANK` ordering: none/lrs < zrs < grs < gzrs).
    - `emitter.py` — both `_build_manifest` (strict v1 schema) and
      `_render_markdown` (totals table + mermaid pie + recommendations
      table with priority badges + per-resource breakdown).
  - **Test suite: 121 passing tests** across 11 test files
    (`test_scaffold`, `test_recommender`, `test_emitter`,
    `test_discover`, `test_load_profile`, `test_pricing_client`,
    `test_projector_{aoai,foundry,aca,cosmos,storage,apim,ai_search}`).
  - **Stdlib only.** No new repo dependencies. `az bicep` CLI is a hard
    prereq (consistent with `threadlight-production-ready` v0.3.0).
- Plugin manifest bumped to **1.2.0** with new keywords:
  `consumption-iq`, `cost-projection`, `sku-recommendation`,
  `azure-retail-pricing`, `load-profile`.
- README updated: skill table (10 pipeline skills + 1 orchestrator = 11
  total), pipeline diagram now inserts `consumption-iq` between
  `safe-check` and `foundry-evals`.
- **CI: `.github/workflows/python-pytest.yml`** — lightweight pytest
  gate on every PR + push to main. Runs `threadlight-consumption-iq`
  (125 tests, hard-fail), `threadlight-production-ready` and
  `threadlight-auto` (continue-on-error to tolerate the 2 pre-existing
  stale-safe-check failures while keeping visibility). Complements the
  expensive `threadlight-e2e-foundry.yml` (workflow_dispatch only).
  Doc at `docs/ci/python-pytest.md`.
- **Golden e2e fixture** at
  `skills/threadlight-consumption-iq/references/fixtures/sample-pilot-consumption/expected/`
  — `cost-manifest.json` + `cost-projection.md` populated with
  deterministic numbers from a mock pricing client (`generated_at`
  pinned). Drift-detected by `tests/test_e2e.py`. To regenerate after
  an intentional change:
  `CONSUMPTION_IQ_REGENERATE_GOLDEN=1 python3 -m pytest
  skills/threadlight-consumption-iq/tests/test_e2e.py -v`.
- **Defensive fix in emitter**: per-resource breakdown table now
  renders `"N/A"` instead of crashing when an alternative has
  `monthly_cost_usd: None` (e.g. AOAI's sentinel alternative when the
  Retail Prices API and fixture both miss the current SKU). Sort order
  pushes None-cost alternatives to the bottom of the table.

### Fixed

- **`threadlight-consumption-iq` — pre-sales reference-repo mode crashed over
  real repos using the canonical Bicep `cpu: json('x')` ACA idiom.** Every azd
  Container-Apps template declares container CPU as `cpu: json('0.5')`, which
  `az bicep build --stdout` renders as the ARM expression string
  `"[json('0.5')]"`. Discovery passed that string straight through as `vcpu`,
  and the ACA projector then did arithmetic on it (`str * int` → `str - int`)
  and died with an uncaught `TypeError` the moment a phased estimate ran in
  reference-repo / expansion mode against an undeployed real repo. Now
  `discover._parse_vcpu` resolves the `json('x')` idiom (and plain numeric
  strings) to a float, and `projectors/aca.py` defensively coerces `vcpu` /
  `memory_gib` with a safe fallback so any stray non-numeric value falls back to
  the 0.5 vCPU / 1.0 GiB default instead of crashing. +5 tests (discover idiom
  resolution + `_parse_vcpu` unit table; projector string/unresolved/None
  coercion). Surfaced by a real-repo smoke of the hardened pre-sales path.

- **`threadlight-consumption-iq` — four robustness/accuracy fixes from a final
  adverse review of the pre-sales mode.**
  - **Discovered Consumption ACA was mis-costed as flat Dedicated.** Discovery
    emits the tier lower-cased (`"consumption"`) but the projector compared
    `tier == "Consumption"`, so a discovered Consumption Container App fell
    through to the flat Dedicated branch (`0.20 × 730 = $146/mo`) instead of the
    free-grant usage-based formula — inflating every reference-repo estimate.
    The tier check is now case-insensitive.
  - **Parameterized replica bounds crashed the projector.** Real Bicep
    parameterizes `minReplicas`/`maxReplicas`, which render as ARM expression
    strings; discovery passed them through raw and the projector's
    `max()`/`math.ceil` arithmetic raised an uncaught `TypeError` (exit 1,
    breaking the 2/3/4 exit contract). Discovery now resolves replica values to
    ints (with int fallback for unresolvable `parameters()` refs), the projector
    defensively int-coerces replica bounds, and declared-topology numeric
    `current_sku.extra` fields are type-checked at load time (→ exit 4).
  - **Negative/non-numeric load fields produced negative totals.** The
    rollout JSON/YAML path bypassed the wizard's `>= 0` guard, so a
    hand-authored `peak_requests_per_second: -5` yielded a negative monthly
    total. `validate_rollout_profile` now rejects non-numeric or negative
    required load fields (→ exit 4).
  - **Partial per-phase topology silently projected a $0-compute phase.** When
    one phase declared `resources` (globally skipping discovery) but another
    omitted them with no top-level fallback, that phase resolved to an empty
    topology and silently showed $0 compute. It is now rejected fail-fast
    (→ exit 4). +11 tests; full consumption-iq suite 241 → 252.

### Pending for v0.2

- Live-pricing population for the 6 non-AOAI resource-kind fixtures
  (currently empty skeletons; projectors fall back to hardcoded matrix).
- Cosmos failover / multi-write redundancy modelling beyond v1 single-write.
- Storage egress-tier math (currently treats first 100 GB free; v2
  tiered pricing).
- AOAI projector should surface a `should-fix` finding (instead of $0)
  when current-SKU pricing is unavailable, so operators don't see the
  resource as "free" in the report.

### Changed

- **`threadlight-design` v1.10.0 — Fast-PoC scoped to basic scenarios +
  mode-selection triage**: Fast-PoC is no longer a blanket default. The skill now runs a
  **complexity triage** before locking a mode — a scenario is "basic"
  (Fast-PoC OK) only when it is read-only/trivially-reversible, stateless or
  session-based, agent-shaped, free of heavy compliance weight, narrow-surface,
  and not facing SME review. If any non-basic signal is present (regulated
  domain, consequential actions, case lifecycle, multi-phase workflow), the
  skill asks **one triage question** naming the signal and defaults to Full
  mode (Step 1.5) so the extra round improves the outcome; the user can still
  force Fast-PoC. Docs updated to match: `THREADLIGHT.md` mode summary +
  quick-ref, and `docs/WORKSHOP-1H-QUICKSTART.md` basic-scenario note.
- **`threadlight-production-ready` COST-005 tightened**: previously checked
  only `docs/cost-projection.md` existence. Now requires ALL of:
  `docs/cost-projection.md` present AND `specs/cost-manifest.json` present
  with `schema_version >= "1.0"` AND `generated_at` within 30 days of the
  latest deploy timestamp (`AZURE_LAST_DEPLOY_AT` from `azd env`, or current
  time if absent). Status when any condition is missing: `should-fix`.
- **`threadlight-auto` new `cost-projection` phase**: inserted between
  `safe_check` and `invoke`. Runs
  `threadlight-consumption-iq scripts/consumption_iq.py run --all`. Exit 4
  (load profile incomplete) surfaces a wizard prompt and sets
  `cost-projection: needs-wizard` in state (advisory, does not block chain).
  Exit 3 (pricing unavailable) sets `cost-projection: degraded-no-pricing`
  and continues. Resumability: skips phase when SPEC § 12 `load_profile{}`
  is complete AND `cost-manifest.json.generated_at > AZURE_LAST_DEPLOY_AT`.
- **`threadlight-design` SPEC § 12 skeleton**: the SpecKit template now emits
  a `load_profile{}` subsection (with placeholder comments) that
  `threadlight-consumption-iq` wizard fills in on first run.

### Added

- **`threadlight-production-ready` COST-006**: new finding that walks
  `specs/cost-manifest.json → recommendations[]` and reports each entry
  where the deployed SKU still matches `current_sku` (i.e., recommendation
  not yet applied). `monthly_savings_usd > $100` → `must-fix`;
  `> $25` → `should-fix`; `≤ $25` → `pass`. `not-verified` when manifest
  absent (message: "Run threadlight-consumption-iq to populate
  cost-manifest.json before scoring COST-006.").
- **`skills/threadlight-production-ready/references/remediation-recipes/COST-006.md`**:
  remediation recipe explaining what COST-006 checks, how to address
  recommendations, and why auto-apply is not appropriate for PTU commitments.

## 2026-06-11 — `threadlight-production-ready` v0.5.0 — "production-ready cleanup"

Closes the v0.5.0 cleanup buckets on top of v0.4.0's 3-phase onboarding
flow: customer-specific policy overrides, an 8-question framing wizard,
idempotent assessor input discovery, GitHub-Actions-only CI/CD scope, and a
truthful v0.6.0+ deferral boundary.

### Added

- **Per-customer overrides** (`--customer-overrides PATH`) with
  `references/customer-overrides-schema.md`,
  `references/customer-overrides.example.yaml`, status-flip audit fields
  (`override_customer`, `override_reason`), and must-fix bypass rejection.
- **8th framing question**: `azure_tenant_id`, a required UUID tying the
  production subscription to its tenant (closes #33).
- **Assessor-output exclusions** via `EXCLUDE_GLOBS`, so generated
  production-readiness manifests/reports/trends are not re-ingested on the
  next run.
- **Sibling-skill flip runbook** at
  `references/runbooks/sibling-skill-flip-protocol.md` for promoting manual
  recipes once upstream awesome-gbb skills land.
- **New stdlib tests**:
  `tests/test_customer_overrides.py`, `tests/test_idempotent_assess.py`,
  `tests/test_no_ado_gitlab_in_recipes.py`,
  `tests/test_sacred_rule_wording.py`, and `tests/test_script_strings.py`.
- **5 experimental promotions** to must-fix coverage: `NET-502`,
  `EVAL-101`, `EVAL-102`, `SUP-101`, and `SRE-103`.

### Changed

- `IAM-101` and `OBS-106` recipes now declare `kind: manual` until their
  upstream sibling-skill contracts land.
- SKILL.md and this CHANGELOG now acknowledge the sacred architectural rule
  with the documented `--scaffold-cicd` exception while preserving
  agent-driven remediation (closes #29).
- `REL-102` and the catalog gate were stripped of ADO/GitLab CI/CD guidance;
  v0.5.0 remains GitHub Actions only (closes #32).
- Stale `deferred to v0.5.0` wording now points to `v0.6.0+`.
- `SUP-101` and `SRE-103` catalog titles were re-aimed at the new repo-edit
  gates (`SUPPORT.md` and `docs/sre/runbook.md`).
- `_load_customer_overrides` is now strict-mode: rejects tab indentation,
  block scalars (`|`/`>`), unquoted `<space>#` in values, duplicate
  top-level keys, duplicate `recipe_id` entries, and unknown top-level keys.
  `_validate_customer_overrides` rejects unknown per-override keys.
  Rationale: silent text loss on an override's `reason` field would corrupt
  the audit trail.
- `--customer-overrides` now rejects combinations where the overrides would
  be silently dropped (`--remediate`, `--onboard`, standalone
  `--scaffold-cicd` without a manifest). Exits 2 with a loud error.

### Deferred to v0.6.0+

- **Bucket 2 / `gateway-resilience` pillar** — cross-region failover scoring,
  ~25-40 new recipes, and a new framing question; deferred to ship as its
  own themed release.
- **ADO and GitLab `--scaffold-cicd` targets** — v0.5.0 remains GitHub
  Actions only pending field-test demand.
- **4 remaining sibling-skill flips**: awesome-gbb#267 (`REL-007`), #269,
  #270, and #272 (`SRE-104`); gated on upstream landings, then the
  sibling-skill flip protocol applies.
- **~19 remaining experimental recipes** still marked `"experimental": True`
  in `FINDING_CATALOG`; promote one-by-one as field signal arrives.
- **Real-customer field-test execution** — Phase G is protocol-only
  (`references/field-test-protocol.md`); actual customer engagement is
  post-v0.5.0 follow-up work.

## 2026-06-10 — `threadlight-production-ready` v0.4.0 — "production onboarding (3-phase)"

Flips the skill from a pure assessor into a 3-phase production-onboarding
executor: Phase 1 (Assess, Python script) → Phase 2 (Refine + Deploy,
agent-driven via Edit/Write + sibling skills) → Phase 3 (CI/CD Handoff,
scaffolded GitHub Actions workflow + central-team UAMI runbook). The
sacred architectural rule from v0.3.0 holds for remediation findings:
fixes are dispatched through the Copilot agent so `git diff` + PR review
remain the audit trail. The lone Python write exception is the
`--scaffold-cicd` opt-in flag, which writes 2 deterministic template files
into the customer repo so the production-onboarding pipeline can run. No
`--apply FINDING_ID` flag was added or planned.

### Added — major
- **3-phase onboarding flow.** Phase 1 (Assess) emits an `apply-plan.json`
  describing what needs to change and how. Phase 2 (the Copilot agent)
  consumes the plan and dispatches by `kind`: `repo-edit` → Edit/Write,
  `sibling-skill` → invoke another skill via the Skill tool, `manual` →
  surface to the user, `deferred-to-pipeline` → leave for Phase 3.
  Phase 3 renders `.github/workflows/azd-deploy-prod.yml` plus a
  central-team UAMI/FedCred runbook from templates.
- **Apply-plan output** (`--apply-plan-out PATH`) with 4 `kind` values:
  `repo-edit`, `sibling-skill`, `manual`, `deferred-to-pipeline`.
  Includes `manifest_sha256` so Phase 2 detects a stale plan.
- **Framing wizard** (`--onboard` for TTY, `--framing-file PATH` for
  headless) — 7 questions covering target subscription, RG, posture,
  provisioning rights, central platform team, restricted-environment
  flag, and CI/CD target.
- **61 must-fix remediation recipes** under
  `references/remediation-recipes/{ID}.md` (one per non-experimental
  finding ID). Each declares `kind` and provides exact edit /
  sibling-skill / manual / pipeline instructions. Catalog drift gated
  by `tests/test_recipe_catalog.py`.
- **Provisioning-rights probe + phase-decision banner.** Live
  `az role assignment list` against the target RG; classifies operator
  rights as `full | constrained | none | unknown` and decides Phase-2
  mode (`self-service` vs `central-team handoff` vs `blocked`). Skippable
  via `--no-rights-probe`. Restricted-environment flag (Framing-Q6)
  overrides rights class and forces central-team handoff.
- **CI/CD scaffold** (`--scaffold-cicd`) — renders
  `.github/workflows/azd-deploy-prod.yml` (deploy + smoke-failover +
  threadlight-postdeploy jobs) and
  `docs/threadlight-cicd/central-team-uami-readme.md` (UAMI provisioning
  runbook) from templates under `references/cicd-templates/`. Federated
  credentials only — no long-lived secrets in GitHub.
- **Sibling-skill invocation map** at `references/sibling-skills-map.md`
  — single source of truth for which awesome-gbb skill the agent
  invokes when a recipe is `kind: sibling-skill`.
- **Restricted-environment demotion.** When framing declares
  `restricted_environment: true`, `build_apply_plan` demotes any
  `kind: repo-edit` recipe to `kind: manual` so the agent surfaces the
  task to the user instead of editing the repo — honoring the
  central-team-owns-onboarding contract.
- **`sample-pilot-restricted` fixture** modeling a central-team-owned
  citadel-spoke environment (Reader-only rights, restricted=true,
  provisioning_rights=false).

### Changed — major
- **SKILL.md contract flipped.** v0.3.0's "recommends, never executes"
  → v0.4.0's "Phase 1 assesses, Phase 2 (agent) executes, Phase 3
  (script) scaffolds." The Python script remains assessor-only by
  design — execution flows through Copilot agent tools so `git diff`
  stays the audit trail.

### Added — minor
- `VERSION = "0.4.0"` pin (`tests/test_version.py`).
- CLI flags: `--onboard`, `--framing-file`, `--apply-plan-out`,
  `--scaffold-cicd`, `--no-rights-probe`, `--repo-full-name`.
- Per-pillar cross-reference footer from each
  `references/pillars/{N}-*.md` to its remediation recipes.
- `phase_decision`, `rights_probe`, and `version` keys added to
  `production-readiness-manifest.json` when `--framing-file` is set.
- `build_apply_plan` walks `pillars[].findings[]` when top-level
  `findings` is absent (back-compat preserved); includes
  `not-verified` items alongside `fail`/`warn` (real gaps in
  restricted environments).

### Tests
- 9 new stdlib test files (~30 new test functions). Continues v0.3.0's
  stdlib-only commitment — no pytest, no third-party deps. Total now
  19 test files covering apply-plan schema, bicep graph, CI/CD
  scaffold, diff mode, end-to-end, evidence freshness, experimental
  exclusion, framing wizard, gate preview, phase decision, recipe
  catalog, remediate renderer, rights probe, scoring,
  secure-score-floor, sibling-skill map, smoking-gun regression, trend
  CSV, version pin.

### Deferred to v0.5.0+
- `gateway-resilience` pillar (`GW-001..103`) — not yet authored.
- Full SPEC §12 per-customer enforcement of
  `defender_plans_required` + `required_policy_ids`.
- 24 experimental stub closures (continuous-evals EVAL-101..105, NSG
  flow logs, cost actuals, AppInsights replay).
- Awesome-gbb upstream landings for #267–272 (recipes currently
  `kind: manual` until each sibling skill ships).
- Real-customer field test of the 3-phase flow (currently exercised
  only via the 4 fixtures).

### Migration notes for v0.3.0 users
- No breaking script-CLI changes for existing
  `--target-sub`/`--target-rg`/`--static`/`--gate-preview` usage; v0.3.0
  invocations continue to work unchanged. To opt into the v0.4.0 flow,
  add `--onboard` (TTY) or `--framing-file <path>` (headless), plus
  `--apply-plan-out` to capture the agent dispatch plan and
  `--scaffold-cicd` to render the Phase 3 templates.
- Existing fixtures (`sample-pilot`, `sample-pilot-citadel`,
  `sample-pilot-broken`) score identically under v0.3.0 invocation; the
  new `version`/`rights_probe`/`phase_decision` keys only appear when
  `--framing-file` is set.

## 2026-06-10 — `threadlight-production-ready` v0.3.0 — "the real way to land in prod"

The first end-to-end overhaul of the production-readiness skill since
its initial drop. Replaces the v0.2.0 regex-over-Bicep-text parser with
a real ARM-graph (`BicepGraph` compiles via `az bicep build` and walks
the resulting JSON), wires 5 long-stubbed live probes, marks 24
unimplemented stubs as `experimental: true` (excluded from scoring
unless `--include-experimental`), adds 15 new non-experimental finding
IDs covering Defender / Policy / Foundry RBAC / quota / restore-drill
surface area, fixes the scoring bug that gave `not-verified` 50%
credit, and ships new operator surface area (`--diff`,
`--gate-preview`, `--remediate`, `--trend-csv`).

Closes the regression that motivated the rewrite:

> A pilot whose `infra/main.bicep` mentions `Microsoft.Network/virtualNetworks`
> only inside a comment scored as **`READY WITH WAIVERS`** under v0.2.0. The
> regex parser matched substrings; the comment-only fixture passed
> `NET-001` (vnet exists), `NET-002`, and `SEC-001`. Under v0.3.0
> the same fixture exits with `🔴 NOT READY`, `raw=31%`,
> `verification_debt=42`, and 12 of 16 critical IDs in a non-pass state.
> Pinned by `tests/test_smoking_gun_regression.py`.

### Added

- **`BicepGraph` parser** (`scripts/production_ready.py:878-1020`) — compiles
  every top-level `infra/main.bicep` via `az bicep build --stdout`, walks
  `Microsoft.Resources/deployments` to flatten module-nested resources,
  exposes `by_type / has_type / count / property_values`.
- **`PrerequisiteError`** — raised when the `az` or `bicep` CLI is missing
  or every top-level main fails to compile. `main()` catches it and exits 2
  with an `az bicep install` hint. **There is no regex fallback.**
- **5 live probes wired** (were no-op stubs in v0.2.0):
  `OBS-106` (Foundry diag settings → LA), `OBS-102` (KQL trace freshness),
  `SEC-106` (KV diag settings → LA), `SRE-104` (RG activity-log alerts),
  `NET-501` (Citadel APIM Access Contract via `TL_CITADEL_HUB_RG`).
- **15 new non-experimental finding IDs** for governance + capacity surface area:
  `REL-007` / `REL-008` (restore-drill freshness + live recovery-point sampling),
  `GOV-101..105` (Defender for AI Services / KV / Servers + Secure Score floor + top recommendations),
  `GOV-201..203` (required Policy assignments + compliance + ASB-v3 initiative),
  `MDL-009/010/011` (project-level RBAC + private-endpointed knowledge index + thread retention),
  `MDL-110` / `MDL-111` (TPM headroom + Foundry account quota).
  Plus 23 additional IDs flagged `experimental: True` (24 total experimental
  in catalog: 23 newly retired in v0.3.0 + 1 inherited from v0.2.0; all
  excluded from scoring unless `--include-experimental` — see Changed
  section below).
- **`--diff <prior.json>`** — prints a markdown diff of pillar / finding deltas
  between two manifests; useful in PR comments. Pinned by `test_diff_mode.py`.
- **`--gate-preview`** — exit 2 if any must-fix would block go-live. The flag
  the v0.3.0 GitHub Actions workflow uses on `push`. Pinned by `test_gate_preview.py`.
- **`--remediate <FINDING_ID>`** — prints the bash recipe from
  `references/remediation-recipes.yaml` to stdout. 12 recipe entries covering
  the highest-frequency findings. Pinned by `test_remediate_renderer.py`.
- **`--trend-csv`** (default `tests/production-readiness-trend.csv`) — appends
  a single row per run for trending score / verification_debt / posture /
  recommendation. Header on first write, append after. Pinned by `test_trend_csv.py`.
- **OIDC GitHub Actions recipe** (`references/ci-github-actions.yml`) —
  rewritten to use `azure/login@v2` federated credentials, `az bicep install`,
  `--gate-preview` on push, `--trend-csv` upload, `TL_CITADEL_HUB_RG` passthrough.
- **`references/remediation-recipes.yaml`** — 12 bash recipes (one per high-impact
  finding ID).
- **`references/azd-hooks/install-azd-hook.sh`** — idempotent installer that
  registers the skill as an `azd` postdeploy hook.
- **Two fixture directories**:
  - `references/fixtures/sample-pilot-broken/` — the smoking-gun regression
    fixture (comment-only Bicep) the suite asserts must NEVER be reported
    as production-ready.
  - `references/fixtures/sample-pilot-citadel/` — Citadel-spoke happy-path
    fixture for posture-resolution coverage.
- **Test suite** (9 files, 57 test functions, stdlib-only):
  - `test_bicep_graph.py` — 7 BicepGraph contract tests
  - `test_smoking_gun_regression.py` — 5 end-to-end regression tests
  - `test_scoring_no_verification_inflation.py` — 10 scoring tests
  - `test_experimental_excluded.py` — 3 experimental-filter tests
  - `test_diff_mode.py` — 6 `--diff` renderer tests
  - `test_gate_preview.py` — 3 `--gate-preview` tests
  - `test_remediate_renderer.py` — 5 `--remediate` tests
  - `test_trend_csv.py` — 5 trend-csv tests
  - (`test_evidence_freshness.py` — 13 pre-existing tests, still green)

### Changed

- **Scoring contract** — `not-verified` is now `+0` (was `+2` in v0.2.0, which
  inflated unverifiable pilots to ~50%). The unverified count is surfaced as
  a first-class `verification_debt {total, by_pillar}` block in the manifest
  + exec-summary banner in the markdown report. Pin: `test_scoring_no_verification_inflation.py`.
- **24 finding IDs** flagged `experimental: True` in `FINDING_CATALOG` and
  filtered from both the score and the pillars block by default (23 newly
  added or re-flagged in v0.3.0, 1 inherited from v0.2.0). Opt in with
  `--include-experimental`. The manifest carries the flag as a top-level field
  so SE-side trends can tell "with" from "without".
- **`SKILL.md` over-claims** — 6 sentences that said the skill _does_ a thing
  it actually only _plans to_ rewritten to match the catalog.
- **`docs/production-readiness.md`** — H1 callout summarising the v0.3.0
  contract changes, scoring delta, smoking-gun regression.
- **`skills/threadlight-production-ready/SKILL.md` `metadata.version`** —
  `1.0.0` → `0.3.0` (semver reset: the skill was self-tagged 1.0.0 at the
  v0.2.0 cut even though scoring was demonstrably wrong; v0.3.0 is the
  first version with a tested scoring contract).

### Fixed

- The comment-only-Bicep smoking-gun regression (see above).
- `not-verified` 50% credit (scoring contract above).
- `_build_manifest` no longer leaks experimental findings into
  `pillars[].findings` when the flag is off — they were excluded from
  the *score* but still surfaced, misleading operators.
- `_build_manifest` now records `include_experimental` as a top-level
  manifest field so trend rows can be partitioned by it.

### Cross-references

Closes / progresses the following [`awesome-gbb`](https://github.com/aiappsgbb/awesome-gbb) issues. Each upstream issue is the canonical owner of a helper or skill this PR's tier-1 checks vendor a stand-in for until the upstream lands:

- [#245 — foundry-observability: reusable KQL probe helpers](https://github.com/aiappsgbb/awesome-gbb/issues/245) — `OBS-102` is a vendored copy of the trace-freshness probe
- [#246 — citadel-spoke-onboarding: hub-side Access Contract probe helper](https://github.com/aiappsgbb/awesome-gbb/issues/246) — consumed by `NET-501` via `TL_CITADEL_HUB_RG`
- [#247 — foundry-evals: last-run introspection API](https://github.com/aiappsgbb/awesome-gbb/issues/247) — `EVAL-101..105` are experimental stubs waiting on this
- [#248 — foundry-agt: canonical capability-detector](https://github.com/aiappsgbb/awesome-gbb/issues/248) — `AGT-102`, `AGT-V4-101` are experimental stubs waiting on this
- [#249 — NEW SKILL azure-policy-compliance](https://github.com/aiappsgbb/awesome-gbb/issues/249) — `GOV-201/202/203` are tier-1 stand-ins until the dedicated skill ships
- [#250 — azure-sre-agent: threadlight-production-handover recipe](https://github.com/aiappsgbb/awesome-gbb/issues/250) — consumed by `SRE-102/103/104` paths
- [#251 — NEW SKILL azure-quota-preflight](https://github.com/aiappsgbb/awesome-gbb/issues/251) — `MDL-110/111` are tier-1 stand-ins until the dedicated skill ships
- [#252 — NEW SKILL azure-defender-posture](https://github.com/aiappsgbb/awesome-gbb/issues/252) — `GOV-101..105` are tier-1 stand-ins until the dedicated skill ships
