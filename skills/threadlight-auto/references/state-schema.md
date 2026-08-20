# `.threadlight/auto-state.json` — schema

`.threadlight/auto-state.json` is owned by the `threadlight-auto` guidance
contract. The Python planner in `references/orchestrator.py` reads this file to
decide what can resume; it does **not** write or migrate
`.threadlight/auto-state.json`. When invoked with `--commit`, `orchestrator.py`
writes `.threadlight/auto-next.json` for the coding agent to consume. Format:
pretty-printed JSON written by the guidance/agent side.

> **Schema.** Stage names are `preflight / design / deploy / safe_check /
> cost_projection / invoke`; artifact paths are `specs/SPEC.md`,
> `docs/safe-check-post.md`, `specs/cost-manifest.json`,
> `docs/invoke-results.md`.

## Top-level shape

```json
{
  "version": 1,
  "workspace": "/Users/me/Repos/contoso-claim-triage",
  "tenant_alias": "acme",
  "subscription_name": "MCAPS-Subscription-Acme-1",
  "azd_env": "dev",
  "region": "westus3",
  "started_at": "2026-06-01T15:42:11Z",
  "last_updated_at": "2026-06-01T16:21:38Z",
  "preflight":  { "...stage shape (below)..." },
  "design":     { "..." },
  "deploy":     { "..." },
  "safe_check": { "..." },
  "cost_projection": { "...stage shape (below), plus last_deploy_at/passed_at — see § Cost-projection fields..." },
  "invoke":     { "..." },
  "evals":      { "..." },
  "redteam":    { "..." },
  "govern":     { "..." },
  "recovery_events": [ "...event shape (below)..." ]
}
```

## Per-stage shape

```json
{
  "status": "done",
  "started_at": "2026-06-01T15:42:34Z",
  "ended_at":   "2026-06-01T15:48:55Z",
  "duration_seconds": 381,
  "artifact_hash": "0a7a29be18bb...",
  "artifact_paths": ["specs/SPEC.md"],
  "tool_invocations": [
    {"tool": "threadlight-design", "duration_seconds": 372, "exit_code": 0}
  ],
  "skipped_reason": null,
  "failure_signature": null
}
```

### Per-stage `artifact_hash` semantics

| Stage | Primary artifact hashed | Why |
|---|---|---|
| preflight | `.threadlight/preflight-passed.json` | Marker freshness |
| design | `specs/SPEC.md` | Drives all downstream gates (NEEDS CLARIFICATION scan, hash drift) |
| deploy | `infra/main.bicep` | Bicep authoring is the load-bearing artifact for safe-check |
| safe_check | `docs/safe-check-post.md` + `tests/postdeploy-manifest.json` | End-state evidence pair for resumption-aware invoke; docs alone are insufficient |
| cost_projection | `specs/cost-manifest.json` | Feeds `orchestrator.py`'s `_check_cost_projection` freshness/resumability check (trusted only when `schema_version` starts with `1.` and `generated_at` is newer than last deploy) |
| invoke | `docs/invoke-results.md` | Demo-scenario evidence; freshness gates re-run after spec change |
| evals | `specs/evals-manifest.json` | Discover leg — offline + online (Foundry CE) + A/B eval evidence consumed by production-ready pillar 6 |
| redteam | `specs/redteam-manifest.json` | Discover leg — AI Red Teaming Agent scan evidence consumed by production-ready pillar 7 (SAFE-1xx) |
| govern | `specs/govern-manifest.json` | Protect leg — AGT runtime-governance artefact consumed by production-ready pillar 2 + pillar 7 (RAI-002/003) |

### Cost-projection fields

`orchestrator.py::_check_cost_projection` reads two fields off the
`cost_projection` stage entry directly — `last_deploy_at` (fallback: `azd env`'s
`AZURE_LAST_DEPLOY_AT`) and `passed_at` — to decide whether a fresh
`specs/cost-manifest.json` can be reused instead of re-running
`scripts/consumption_iq.py run --all`. Those two are real, orchestrator-read
fields and are documented here for that reason.

The stage entry may also carry a `cost-reconciliation` status
(`pass` / `degraded-source` / `not-verified`) when the optional actuals
subphase has run. That key is written by agent guidance, not read by
`_check_cost_projection` or any other orchestrator code path, so it is
intentionally left out of a fixed shape here — see `SKILL.md` § "Cost-projection
stage — optional reconciled-actuals subphase" for its informal contract.

## `recovery_events` shape

Every auto-recovery action `threadlight-auto` fires is appended to this list.

```json
{
  "recovery_events": [
    {
      "stage": "deploy",
      "fired_at": "2026-06-01T16:08:42Z",
      "signature": "InsufficientQuota for 'gpt-5.4-mini' in swedencentral (F-03)",
      "action": "switched AZURE_LOCATION → westus3",
      "rationale": "az cognitiveservices usage list --location westus3 reported 0/1000 headroom",
      "retry_succeeded": true
    }
  ]
}
```

Recovery events are also rendered into `docs/auto-run.md` (operator-facing
markdown log) so the operator can review what auto-magic happened during
their run without parsing JSON.

## Migrations

There are no planner-side migrations. If guidance bumps `version`, the
guidance/agent that owns `.threadlight/auto-state.json` must write the new
shape before the planner reads it. `orchestrator.py` treats the file as
read-only state input.

## What this file is NOT

- Not a backup of every tool's stdout/stderr — that's in `docs/auto-run.md`
- Not a substitute for `azd env get-values` or `azd ai agent show` — both queried live during Deploy
- Not a security boundary — contains no secrets (only resource names + hashes)
