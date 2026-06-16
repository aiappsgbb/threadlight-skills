# Kratos-export mode — enrichment runbook

This reference is consumed by `threadlight-deploy` when it detects a
**Kratos-exported project**. It is the deploy-side companion to the repo-level
[`docs/KRATOS-BRIDGE.md`](../../../docs/KRATOS-BRIDGE.md). In this mode the skill
**enriches and validates in place** — it never regenerates the runtime Kratos
already shipped.

## 1. Detection

Kratos-export mode is active when **both** are present at the project root:

- `src/hosted-agent/` — `main.py`, `Dockerfile`, `pyproject.toml`, `agent.yaml`,
  `agent.manifest.yaml` (Kratos verbatim, persona slug rendered).
- `use-cases/<x>/` — `SYSTEM_PROMPT.md`, `apm.yml`, `.mcp.json`, `skills/`.

If only `AGENTS.md` + `src/agent/skills/` exist (no `src/hosted-agent/`), you are
in the default **design mode** — ignore this file.

## 2. Do-NOT-regenerate list

These artifacts ship in the export and are authoritative. **Never overwrite them
in Kratos-export mode** (doing so clobbers Kratos's verbatim runtime):

| Artifact | Why it already exists |
|----------|----------------------|
| `src/hosted-agent/main.py` | Kratos hosted-agent entrypoint |
| `src/hosted-agent/Dockerfile` | Kratos container build |
| `src/hosted-agent/pyproject.toml` | Kratos runtime deps |
| `src/hosted-agent/agent.yaml`, `agent.manifest.yaml` | Rendered with persona slug |
| `azure.yaml` | Single hosted-agent azd service |
| `infra/` (trimmed Bicep) | Intentional — no APIM, no multi-tenant FE |
| `use-cases/<x>/SYSTEM_PROMPT.md`, `apm.yml`, `.mcp.json` | The use-case bundle |

The deploy skill's **Phase 2 is skipped entirely** in this mode. Resume at
Phase 3 (Validate) after the enrichment steps below.

## 3. Resolve the skills root

Precedence (option (b) — auto-detect + override):

1. `--skills-root <path>` (or `THREADLIGHT_SKILLS_ROOT`) if provided.
2. `use-cases/<x>/skills/` if present → **Kratos-export skills root**.
3. `src/agent/skills/` otherwise.

When more than one `use-cases/<x>/` exists, pick the one named in `azure.yaml` /
`azd env` (e.g. `AZURE_ENV_NAME` or the use-case slug baked into
`agent.manifest.yaml`), or ask the operator.

## 4. Backfill `evals/` (one command)

Kratos's exporter excludes `evals/` (its `_SKIP_DIRS`), so eval scenarios do not
travel with the bundle. Regenerate them into `use-cases/<x>/evals/`:

```
use-cases/<x>/evals/
├── eval_config.json          # model + grader config
└── scenarios/
    ├── <scenario-1>.json     # one per success criterion / skill
    └── <scenario-2>.json
```

Source of truth for regeneration, in order:

1. **Original Kratos source** — if the operator still has the Kratos repo /
   use-case checkout, import `use-cases/<x>/evals/` verbatim. Highest fidelity.
2. **Regenerate from the bundle** — otherwise synthesize scenarios from
   `use-cases/<x>/SYSTEM_PROMPT.md` (success criteria, tone, refusal rules) +
   each `use-cases/<x>/skills/*/SKILL.md` (one happy-path + one edge scenario
   per skill). Mirror Kratos's `evals/scenarios/*.json` shape so `foundry-evals`
   and Kratos both consume them.

**Running** the backfilled scenarios is delegated to `foundry-evals`
(awesome-gbb) — the same engine the README pipeline references. This skill only
backfills the directory; it does not run evals.

## 5. Validate (then resume Phase 3)

Before handing off, cross-check the deployed project:

- `azd env get-values` resolves and names match `infra/` outputs.
- The resolved skills root has a `SKILL.md` per skill referenced by
  `SYSTEM_PROMPT.md` / `agent.manifest.yaml`.
- `use-cases/<x>/evals/` now exists (post-backfill).
- The trimmed `infra/` (no APIM, no multi-tenant FE) is recorded as
  **intentional** so downstream `threadlight-safe-check` /
  `threadlight-production-ready` treat it as informational, not a finding.

Then continue with the standard Phase 3 → Phase 7 validation/handoff, treating
the runtime as pre-built.

## 6. Hand-off order

After enrichment, the recommended downstream order is in
[`docs/KRATOS-BRIDGE.md` § 5](../../../docs/KRATOS-BRIDGE.md):
`threadlight-safe-check` → `foundry-evals` → `threadlight-consumption-iq` →
`threadlight-production-ready`, then on-demand `threadlight-hitl-patterns` /
`threadlight-event-triggers` / `threadlight-workspace-ui` /
`citadel-spoke-onboarding`.
