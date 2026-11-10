# Generate-only mode: layout, ownership and contracts

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when running `generate_only.py` or reviewing its output.

**Canonical source: one layout, no alternatives.** The GHCP pilot scaffold under
`references/hosted-agent/ghcp/references/pilot` (`references/pilot`), together
with its parent runtime files, is the ONLY source in generate-only mode for:

- `azure.yaml`, which holds the hosted agent definition **inline** on the agent
  service (`host: azure.ai.agent`, `kind: hosted`, `protocols`, `container.resources`);
- `infra/main.bicep` and `infra/main.parameters.json`;
- `hooks/postdeploy.sh`;
- `src/agent/` and `src/mcp/`.

There is no `agent.yaml`, no `src/agent/agent.yaml` and no `infra/core/`. The
generator refuses to run if any of them already exists. `agent_type: prompt`
or `agent_type: voice` is refused with exit 4, because the scaffold is hosted-only.

An opted-in **voice front-end** is the exception that stays hosted: with
`voice_frontend: hosted-voice` and the user opt-in fields (`voice_opt_in_source: user`,
`voice_opt_in_evidence`, `preview_acknowledged: true`) in SPEC § 11e, the
generator keeps the hosted agent, adds `invocations_ws` `1.0.0` and the Voice
Live metadata to it, and adds a managed `kind: voice` wrapper `<agent>-voice`
that uses it as its conversation engine ([Microsoft Learn](https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent)). Without
a complete user opt-in it generates the agent text-only, prints why and records
the gap. The self-check accepts exactly one such wrapper and fails one that
carries model, tools, protocols or code. Deploy with
`AZD_ALPHA_ENABLE_AGENTS_VOICE=true` exported (azure.ai.agents 1.0.0 gates
`kind: voice`). Prove it after deploy with the official
`microsoft-foundry` `smoke_test_voice.py`; barge-in, audio quality and latency
remain not validated until measured. See the
[GHCP README](hosted-agent/ghcp/README.md#optional-voice-front-end-foundry-hosted-voice-preview).

#### Ownership between threadlight-design and threadlight-deploy

The workshop chain is design, then `generate_only.py` **without `--force`**.
Ownership is explicit, so the deploy step never discards design code:

| Owner | Files |
|---|---|
| threadlight-design | `specs/` (SPEC, foundation, sample data, knowledge), `AGENTS.md`, `src/agent/skills/`, design `tests/`, and optionally `src/mcp/server.py`: the mock MCP tool logic with its safeguards (filters, field allow-lists, validation, required citations) |
| threadlight-deploy (generate-only) | everything in the output table below, plus the MCP **packaging**: `src/mcp/Dockerfile`, the hashed `src/mcp/requirements.lock` and, when design owns `server.py`, the entry point `src/mcp/threadlight_mcp_entry.py` |

- **Adopt, never rewrite.** A `src/mcp/server.py` without the generate-only
  marker is design-owned. The generator keeps it byte-identical and writes
  `src/mcp/threadlight_mcp_entry.py`, which imports the design's SPEC § 6
  functions unchanged and serves them with the pinned `mcp` SDK
  (`from mcp.server.fastmcp import FastMCP`, `mcp==1.29.1` in the lock). It also
  answers `logging/setLevel`. The Dockerfile runs the entry point. The adoption
  is recorded in `specs/mcp-adoption.json` (owner, sha256, tools).
- **Deploy contract for the design server** (otherwise **exit 6**, nothing
  written): every non-knowledge SPEC § 6 tool is a module-level function, and
  every import that runs at import time (including class, loop, `with`, `try`
  and `match` bodies) is the standard library or a package the hashed
  `src/mcp/requirements.lock` installs (`mcp` and its pinned dependencies such
  as `pydantic`, `starlette`, `anyio`, `httpx`). Imports inside functions,
  `if TYPE_CHECKING:` blocks and the `try` body of a `try/except ImportError`
  fallback are allowed; the fallback branch is still checked.
- **Packaging a design run left behind** (`src/mcp/Dockerfile`,
  `pyproject.toml`, `requirements.txt` or an unhashed lock, for example an
  unpinned `fastmcp>=2.10`) is replaced by the pinned packaging. The original
  text is kept in `specs/mcp-adoption.json` under `replaced_design_packaging`.
- **`--force`** only rewrites files generate-only owns. Exit 5 still refuses to
  overwrite earlier generate-only output; the design `server.py` is never in
  that list.
- **Knowledge tools.** A SPEC § 6 tool whose **Backed by** names Foundry IQ, a
  knowledge base or AI Search is not expected in the design server. Until
  `threadlight-ground` provisions the knowledge base, the MCP server answers it
  read-only from the packaged `specs/knowledge/*.md` (a labelled local
  stand-in, not Foundry IQ evidence). When `specs/knowledge/*.md` exists but
  § 6 declares no knowledge tool, the MCP server also exposes the read-only
  `knowledge_search(query)` (the same name Pattern 0 uses) and
  `specs/deferred-steps.md` lists it until Foundry IQ is provisioned.
- **Image layout.** `src/mcp/Dockerfile` mirrors the repo layout
  (`/app/src/mcp`, `/app/specs/sample-data`, `/app/specs/knowledge`), so a design
  server that resolves repo-relative paths (`Path(__file__).parents[2]`) works
  in the image. The generated offline tests replay that layout and import the
  server under it; a flat `/app` copy raised `IndexError` in the 2026-11-07
  live deploy.

| Input (design workspace) | Output |
|---|---|
| `specs/SPEC.md`, `specs/manifest.json`, `specs/sample-data/`, `AGENTS.md`, `src/agent/skills/`, optional `specs/foundation.md`, `specs/knowledge/`, design-owned `src/mcp/server.py` | `azure.yaml`, `infra/`, `hooks/`, `src/agent/` (runtime + `requirements.lock` + offline `tests/`), `src/mcp/server.py` (real SPEC § 6 tools over the sample data, only when design wrote none) or `src/mcp/threadlight_mcp_entry.py` (serves the adopted design server), `src/mcp/Dockerfile` + `requirements.lock`, `specs/mcp-adoption.json` (when adopting), `evals/` (`run_evals.py`, `eval_dataset.jsonl`, `eval-config.json`, `requirements.lock`), `scripts/record_deploy_evidence.py`, `specs/deployment-posture.md`, `specs/deferred-steps.md`, `specs/project-tools.json`, `specs/foundry-package-manifest.json`, `tests/postdeploy-manifest.json`, `DEPLOY.md` |

**Generated contracts:**

- **Posture.** The default is `demo-sandbox`, written to
  `specs/deployment-posture.md`.
- **Deferred steps.** Everything that needs Azure is recorded as deferred in
  `specs/deferred-steps.md`: T-0, Phase 3.5, the Azure steps of Phase 6.5 and
  the Phase 7 Citadel handoff.
- **Pins.** Every dependency is pinned with exact `==` versions and locked with
  `--hash=sha256:` lockfiles for the agent, the MCP server and the evals.
- **Model.** `infra/main.parameters.json` sets `modelName`/`modelVersion` from
  the design (`specs/foundation.md` § 2 `model.default` + `version`, else the
  SPEC § 7b **Model + version** line) as `${AZURE_AI_MODEL_NAME=<model>}` and
  `${AZURE_AI_MODEL_VERSION=<version>}`, so the template default
  (`gpt-5.4-mini`) applies only when the design names no model. A model without
  a version is a deferred step.
- **Capacity.** `modelCapacity` is GlobalStandard units (1 unit = 1,000 TPM,
  [Learn](https://learn.microsoft.com/azure/foundry/openai/how-to/automate-quota-deployments)),
  derived from `specs/foundation.md` § 2 `capacity_tpm` (`50K`, `1M`,
  `400000`) as `${AZURE_AI_MODEL_CAPACITY=<units>}`. Without a value, or below
  300 units, it uses 300: the GHCP runtime and the evaluation judge share the
  deployment and 50 units returned 429 in live runs. A raise is a
  `specs/deferred-steps.md` row with the `azd env set` command to follow the
  foundation exactly.
- **Pilot RBAC and telemetry.** The pilot `infra/main.bicep` grants the deployer
  Foundry User at project scope and at account scope (evaluations invoke the
  judge model on the account,
  [Learn](https://learn.microsoft.com/azure/foundry/observability/how-to/evaluation-permissions)),
  and creates the project's App Insights connection so Foundry injects
  `APPLICATIONINSIGHTS_CONNECTION_STRING` into the hosted agent
  ([Learn](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-hosted-agent-telemetry)).
- **Recommend-only pilot.** A threadlight-design Fast-PoC declares
  `"action_mode": "recommend-only"` in `specs/manifest.json`: the agent
  proposes each decision and a person records it, governance is not selected
  (SPEC § 11a `mode: off`, reads unbound). generate_only packages it with no
  write tool and no governance gap, and says so in `DEPLOY.md` and
  `specs/deferred-steps.md`. A recommend-only SPEC that still exposes a write
  tool (SPEC § 6 or § 11a) or selects a binding is refused with exit 6 and
  nothing written, because it would deploy an unbound or unpackaged
  consequential write. Letting the agent write is the Production path
  (Full-mode design, `threadlight-govern`).
- **Governed-write pilot (workshop).** `"action_mode": "governed-write"` binds
  exactly one SPEC § 6 write to the governed tool gateway (SPEC § 11a
  `mode: selective`); every other tool is an unbound read. generate_only
  packages it as selected governance (below) and keeps the bound write out of
  the mock MCP server, `specs/project-tools.json` (listed under
  `governed_gateway_tools`), `expected_tools` and the generated tests: the
  write runs only through the governed gateway, never as a directly callable
  mock effect. `DEPLOY.md` and `specs/deferred-steps.md` state that it is not
  live until the shared governance foundation exists, `threadlight-govern`
  builds the signed policy bundle and the binding is registered. A
  governed-write SPEC that is not consistent (an unbound or second write,
  missing gateway requirements, missing policy rules, downstream service or
  foundation record) is refused with exit 6 and nothing written.
- **Selected governance.** generate_only does not build a governance package.
  When SPEC § 11a selects governance (any mode but `off`), the bound tools are
  listed as **not packaged** in `specs/deferred-steps.md`, `DEPLOY.md` and the
  post-deploy gaps. `threadlight-safe-check` reports
  `governance: selected-governance-not-packaged` in both `--phase pre-deploy`
  and `--phase post-deploy`, with or without the native governance packages
  installed, until `threadlight-govern` builds it. Post-deploy also reports
  `governance: no explicit signed safe probe configuration; not verified`.
  Both are expected for a generate_only pilot with selected governance. An
  invalid contract is disclosed too; it is never treated as off.
- **Selected modules not provisioned.** The pilot `infra/main.bicep` provisions
  Foundry, ACR, the identity, App Insights and the MCP Container App only. Any
  other SPEC § 11c module marked `yes` (for example `ai-search`, `storage-blob`,
  `foundry-iq-index`) is listed as **not provisioned** in
  `specs/deferred-steps.md`, `DEPLOY.md` and the post-deploy manifest gaps, with
  `threadlight-ground` as the next step. Generate-only does not invent that
  infrastructure.
- **MCP protocol.** Generated servers and the adoption entry point answer
  `logging/setLevel` (camelCase, see `threadlight-mcp-aca`).
- **Tools.** `src/mcp/server.py` takes each tool's arguments from the SPEC § 6
  `` ### `tool` `` subsection **Inputs** (table or `` `name: type` required ``
  prose), falling back to the summary table. Placeholders such as `none` or
  `see below` never become arguments. Read/write comes from § 11a
  `consequence:`, then § 5b Read/Write, then the idempotency column, and only
  then from the tool-name prefix.
- **Evals.** `evals/eval_dataset.jsonl` reuses threadlight-design's
  `tests/eval_dataset.jsonl` verbatim when present (killer-prompt literals stay
  identical), otherwise it is derived from the SPEC § 9 scenario table. The
  canonical input field is `query`; rows that use `prompt` (or `input`) are
  accepted by both the generator and `run_evals.py`.
  `python evals/run_evals.py` runs after deploy as a single command.
  It creates a Foundry eval with built-in evaluators plus a custom score-model
  rubric derived from the SPEC business rules (1–5 anchors, threshold 4). The
  judge prompt carries each `### BR-nnn` rule's full bullet text (Condition,
  Action, Exception, Output; KPI lines dropped), not only its title: a
  title-only prompt scored correct hand-off answers 1 in a live run.
  The target input is
  `{"input": "{{item.query}}", "stream": false, "new_conversation": true}`: the
  GHCP runtime then returns only the final answer text (the full SSE stream
  caused judge 429s and a 413 on result upload) and answers every row in a fresh
  conversation. The Foundry eval sent all rows with one `agent_session_id` in
  the 2026-11-07 live run, and Microsoft Learn documents no per-row session
  setting for `azure_ai_target_completions`; the freeform invocations body is
  forwarded to the container untouched
  ([targets](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-targets#hosted-agent-invocations-protocol),
  [sessions](https://learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-sessions)). A transient network error while
  polling is retried; the run continues server-side.
  `--dry-run` prints the eval payload offline.
- **Deploy evidence.** After `azd up` and an `azd ai agent invoke`,
  `python scripts/record_deploy_evidence.py [--trace-id <id>]` records the agent
  id/version (`azd ai agent show`), the project's App Insights connection (azd
  env `AZURE_AI_APPINSIGHTS_CONNECTION_ID`, recorded by the `postprovision`
  hook that creates the connection) and a trace id (the invoke's
  `Trace ID`, else the latest `invoke_agent` request in App Insights). It never
  invents a value; anything unread stays null and it exits 3.
- **Foundry package gate.** `specs/foundry-package-manifest.json` is
  schema-valid `threadlight-foundry-package/v1` with unevidenced sections.
  `skills/_shared/foundry_package.py` therefore exits **3 (INCOMPLETE)**, never
  2, until real deploy evidence exists.

**Skill acceptance: the offline self-check.** `generate_only_selfcheck.py` runs
these checks:

- `azure.yaml` validates against the vendored azd schema;
- the inline hosted agent definition is present, with no `agent.yaml` or
  `infra/core`;
- the lockfiles are pinned and hashed;
- the Foundry package gate exits 3;
- a secret and personal-target scan finds nothing;
- the generated offline agent tests pass, including the MCP image-layout
  checks (Dockerfile paths mirror the repo, `parents[N]` stays inside the
  image, and the server imports under the replayed layout);
- `bicep build infra/main.bicep` succeeds with no BCP037 (unknown property)
  warning;
- `docker build` succeeds for both images with an empty temporary
  `DOCKER_CONFIG`, so no credentials are stored and nothing is pushed. Only
  public base images are used;
- no `__pycache__` directory or `.pyc`/`.pyo` file exists in the output.

Docker and bicep are reported as `skipped` when unavailable, unless you pass
`--require-docker` or `--require-bicep`. CI runs the self-check with both
required.

Deployment is then the user's job, run with their own credentials:
`azd auth login`, `azd up`, `python evals/run_evals.py`. After that, run the
deferred steps.
