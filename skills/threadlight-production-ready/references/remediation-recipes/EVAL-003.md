---
kind: repo-edit
summary: Document eval scheduling plan (Plan A or Plan B) in SPEC or CI/CD
target_file: docs/SPEC.md
edit_type: insert
---

## Target file

`docs/SPEC.md` (or `.github/workflows/` if wiring Plan B). Plan A docs in `infra/` or `evals/README.md` (where scheduling config lives).

## Edit type

`insert` — add scheduling language to SPEC § 9 or create `.github/workflows/foundry-evals.yml` for Plan B.

## Edit recipe

### Plan A (Foundry Continuous Evaluation — preferred)

1. In SPEC § 9, add:

```markdown
### Scheduling — Plan A (Foundry)

Evals run continuously via **Foundry Continuous Evaluation** resource (deployed in `infra/`).

Schedule:
- **Frequency:** Nightly at 02:00 UTC (configurable per model release cycle)
- **Dataset:** `evals/datasets/qa-holdout-v1.jsonl` (held-out, never used in training)
- **Timeout:** 15 minutes
- **Alert:** Fires if pass rate drops below 0.95

Config: `infra/foundry-ce.bicep` (Foundry CE resource with schedule property).
```

2. In `infra/foundry-ce.bicep`, ensure the resource includes:

```bicep
schedule: 'cron(0 2 * * ? *)'  // nightly 02:00 UTC
```

### Plan B (GitHub Actions / ACA Job — fallback)

1. Create `.github/workflows/foundry-evals.yml`:

```yaml
name: Foundry Evals

on:
  schedule:
    - cron: '0 2 * * *'  # nightly 02:00 UTC
  workflow_dispatch:

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run evals
        run: |
          pip install foundry-evals
          foundry-evals run evals/scenarios.yaml --output evals/runs/$(date +%Y%m%d-%H%M%S).json
      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: eval-results
          path: evals/runs/
```

2. In SPEC § 9, add:

```markdown
### Scheduling — Plan B (GitHub Actions)

Evals run on a cron schedule via GitHub Actions.

Schedule: `.github/workflows/foundry-evals.yml`
- **Frequency:** Nightly at 02:00 UTC
- **Trigger:** `schedule` event (cron) or manual dispatch
- **Results:** Uploaded to artifacts and timestamped in `evals/runs/`
```

## Verification

Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`.

EVAL-003 should flip to `pass` once:
- The SPEC or docs contain keyword matches for `schedule`, `cadence`, `nightly`, `hourly`, or `cron` (case-insensitive).
- Either Plan A (Foundry CE) or Plan B (GitHub Actions) is documented with a concrete schedule.
