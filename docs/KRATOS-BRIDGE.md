# Kratos Bridge — composing Threadlight skills on a Kratos-exported agent project

> **One artifact, two surfaces, one-way flow.** **Kratos** is the SE / sales-side
> surface (web UI, deploy button). **Threadlight skills** are the SE-expert / CSU
> surface (VS Code, pro-code, production-hardening). They speak the **same
> use-case bundle** (`skills/` + `SYSTEM_PROMPT.md` + `apm.yml` + `.mcp.json` +
> `evals/`) on the **same runtime** (GHCP SDK + Foundry hosted agent +
> Invocations). When a customer's scope outgrows Kratos, the SE **exports the
> bundle once** and hands it permanently to the expert/CSU workflow. There is no
> round-trip back into Kratos.

This document is the canonical reference for the **Kratos-export starting
point**. It is additive: the existing `threadlight-design`-driven "from scratch"
flow is unchanged. Every Threadlight skill that can start from a Kratos export
links back here for the detection signal and the skills-root convention.

---

## 1. What a Kratos export is

The Kratos `Agent Manager → Deploy tab` ships a `GET /{use_case}/export`
endpoint that streams `<use-case>-foundry-agent.zip` — a **full-clone Foundry
hosted-agent project**, not just a skills bundle:

```
<use-case>-agent/
├── azure.yaml                  # single hosted-agent azd service (already exists)
├── README.md, .env.template, .gitignore, .dockerignore
├── src/
│   ├── hosted-agent/           # main.py, pyproject.toml, Dockerfile (Kratos verbatim)
│   │                           # agent.yaml + agent.manifest.yaml (rendered with persona slug)
│   └── backend/app/            # whole Kratos backend, recursive
├── use-cases/<chosen>/         # apm.yml, .mcp.json, SYSTEM_PROMPT.md, skills/
│                               # ⚠️ evals/ EXCLUDED by Kratos exporter (_SKIP_DIRS)
├── mocks/                      # verbatim
└── infra/                      # trimmed Bicep — NO APIM, NO multi-tenant frontend (intentional)
```

The export is already deployable. From the Deploy tab UI:

```bash
# 1. unzip
unzip <use-case>-foundry-agent.zip && cd <use-case>-agent
# 2. authenticate
azd auth login
# 3. provision + deploy
azd up -e <use-case>-prod
```

---

## 2. Detection signal

A project is in **Kratos-export mode** when **both** of these are present:

- `src/hosted-agent/` (Kratos's verbatim hosted-agent runtime: `main.py`,
  `Dockerfile`, `pyproject.toml`, `agent.yaml`), **and**
- `use-cases/<x>/` (a use-case bundle with `SYSTEM_PROMPT.md`, `apm.yml`,
  `.mcp.json`, `skills/`).

When this signal is present, Threadlight skills **enrich and validate** the
project — they do **not** regenerate the runtime that Kratos already shipped.

The alternative (default, back-compat) shape is the **threadlight-design** shape:
`AGENTS.md` + `src/agent/skills/` + `specs/SPEC.md`. Skills detect that and keep
their existing behaviour.

---

## 3. Skills-root convention

Kratos puts skills under `use-cases/<x>/skills/`; `threadlight-design` puts them
under `src/agent/skills/`. Threadlight skills resolve a **skills root** with this
precedence (option (b): auto-detect + override — no symlinks, no moving files,
no source-of-truth conflict):

1. **Explicit override** — `--skills-root <path>` (or the `THREADLIGHT_SKILLS_ROOT`
   env var) wins if provided.
2. **Kratos-export mode** — if `use-cases/<x>/skills/` exists, use it. When more
   than one `use-cases/<x>/` directory exists, the operator picks (or the skill
   uses the one named in `azure.yaml` / `azd env`).
3. **threadlight-design mode** — otherwise fall back to `src/agent/skills/`.

Any skill that scaffolds a new skill (HITL gate, event trigger, workspace) writes
**next to the existing skills** at the resolved skills root, so the new skill
travels with the same bundle Kratos exported.

---

## 4. What is intentionally trimmed (do NOT flag as findings)

The Kratos exporter ships a **trimmed `infra/`** on purpose. `threadlight-safe-check`
and `threadlight-production-ready` recognize the Kratos-export shape and treat the
following as **intentional**, not as "missing module" / "missing posture" findings:

- **No APIM / AI Gateway** in the export's Bicep. (Add it later via
  `citadel-spoke-onboarding` only if the customer needs a governance hub.)
- **No multi-tenant frontend** module. (The export is a single hosted-agent
  service; the operator workspace is added on demand via
  `threadlight-workspace-ui`.)
- **`evals/` absent** from `use-cases/<x>/` — excluded by the Kratos exporter's
  `_SKIP_DIRS`. Backfill it (see § 6); do not report it as a deploy defect.

These are informational notes in the scorecard, not gate failures.

---

## 5. Recommended Threadlight-skill invocation order

After `azd up` succeeds on the export, layer Threadlight skills in this order:

```
azd up (Kratos export)
   │
   ├─ threadlight-safe-check        # post-deploy gate — validates selectors, accepts trimmed infra
   ├─ threadlight-deploy            # Kratos-export mode: enrich/validate only, backfill evals/
   ├─ foundry-evals                 # run the backfilled eval scenarios
   ├─ threadlight-consumption-iq    # cost projection off the exported Bicep + azd env
   ├─ threadlight-production-ready  # 13-pillar advisory scorecard (trimmed infra = informational)
   │
   └─ on-demand extensions (any order, all write to the resolved skills root):
        threadlight-hitl-patterns       # Teams Adaptive Card approval gates
        threadlight-event-triggers      # ACA Jobs / Event Grid / cron receivers
        threadlight-workspace-ui        # operator dashboard behind Easy Auth
        citadel-spoke-onboarding        # governance hub, only if required
```

`threadlight-auto` exposes this same chain behind one prompt via its
**"start from Kratos export"** entry path.

---

## 6. Evals backfill (one command)

Kratos's exporter excludes `evals/`, so eval scenarios do not travel with the
bundle. `threadlight-deploy` (Kratos-export mode) backfills them:
`use-cases/<x>/evals/{scenarios/*.json, eval_config.json}` regenerated from the
use-case's `SYSTEM_PROMPT.md` + `skills/`. See
[`threadlight-deploy/references/kratos-export-mode.md`](../skills/threadlight-deploy/references/kratos-export-mode.md).
Running the backfilled scenarios is delegated to `foundry-evals` (awesome-gbb) —
the same engine the README pipeline already references.

---

## 7. Out of scope (decided in the June 16 closing-sprint call)

- ❌ **Modifying Kratos's exporter.** If `evals/` should ship in the export, file
  that against `kmavrodis/kratos-agent`. Here, `threadlight-deploy` backfills.
- ❌ **Round-trip back to Kratos.** Once out of Kratos, the bundle lives in the
  expert/CSU workflow permanently.
- ❌ **Re-pitching Threadlight as a web wizard.** This bridge is about *composing
  on existing exports*, not building a parallel UX.

---

## See also

- [`README.md`](../README.md) — pipeline overview and install.
- [`THREADLIGHT.md`](../THREADLIGHT.md) — full technical briefing.
- Kratos repo (export feature, PR #27): https://github.com/kmavrodis/kratos-agent
