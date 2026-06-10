---
kind: repo-edit
summary: Add eval scenarios section to SPEC (§9)
target_file: docs/SPEC.md
edit_type: insert
---

## Target file

`docs/SPEC.md` (or wherever the customer's SPEC lives; search the repo for `## § 9` or `## 9.` to locate the canonical SPEC path if it differs).

## Edit type

`insert` — append a new section to the SPEC if § 9 is missing; replace if present but empty.

## Edit recipe

1. Locate the SPEC file (commonly `docs/SPEC.md` or `specs/SPEC.md`).
2. If `## § 9 — Eval` (or `## 9 Eval` or similar) is absent, add this section after § 8 or at the end:

```markdown
## § 9 — Continuous Evals

### Eval scenarios

List each scenario that must pass evaluation:

| Scenario | Description | Grader | Pass rate target |
|----------|---|---|---|
| <scenario-1> | <description> | <grader-name, e.g., "llm-as-judge" or "regex"> | <e.g., ≥0.95> |

### Dataset location

- Dataset source: `evals/` (relative to repo root)
- Format: JSONL or YAML (`evals/scenarios-v1.yaml`)

### Scheduling plan

- **Plan A (Foundry):** Continuous evaluation via Foundry CE resource (scheduled in `infra/`).
- **Plan B (GitHub Actions):** Cron job in `.github/workflows/` (e.g., nightly).
- Selected: <Plan A or Plan B>

### Thresholds

Pass rate must remain ≥ <declared-threshold> (e.g., 0.95).
Alert fires if pass rate drops below <threshold>.
```

3. Fill in concrete scenario names, dataset paths, thresholds, and the selected plan (A or B) from the customer's needs.
4. Ensure at least one scenario is listed (count > 0).

## Verification

Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`.

EVAL-001 should flip from `fail` (or `must-fix`) to `pass` once:
- SPEC file contains a section matching `## § 9`, `## 9.`, or `## 9 Eval` (case-insensitive).
- Section is non-empty (at least one scenario declared).
