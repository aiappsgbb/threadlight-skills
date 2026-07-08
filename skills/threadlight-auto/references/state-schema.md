# `.threadlight/auto-state.json` — schema

The `threadlight-auto` orchestrator reads (and writes, when `--commit` flag is set)
this file to track which stages have run, what artifact hashes they produced,
and which auto-recovery actions fired. Format: pretty-printed JSON.

> **Schema.** Stage names are `preflight / design / deploy / safe_check /
> invoke`; artifact paths are `specs/SPEC.md`, `docs/safe-check-post.md`,
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
| safe_check | `docs/safe-check-post.md` | End-state record for resumption-aware invoke |
| invoke | `docs/invoke-results.md` | Demo-scenario evidence; freshness gates re-run after spec change |
| evals | `specs/evals-manifest.json` | Discover leg — offline + online (Foundry CE) + A/B eval evidence consumed by production-ready pillar 6 |
| redteam | `specs/redteam-manifest.json` | Discover leg — AI Red Teaming Agent scan evidence consumed by production-ready pillar 7 (SAFE-1xx) |
| govern | `specs/govern-manifest.json` | Protect leg — AGT runtime-governance artefact consumed by production-ready pillar 2 + pillar 7 (RAI-002/003) |

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

When the schema bumps `version`, the orchestrator reads the old version,
applies a migration (in-process), and re-writes the file at the new version.

## What this file is NOT

- Not a backup of every tool's stdout/stderr — that's in `docs/auto-run.md`
- Not a substitute for `azd env get-values` or `azd ai agent show` — both queried live during Deploy
- Not a security boundary — contains no secrets (only resource names + hashes)
