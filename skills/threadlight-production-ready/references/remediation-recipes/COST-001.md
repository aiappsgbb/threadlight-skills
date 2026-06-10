---
kind: repo-edit
summary: Add pricing plan section to SPEC (§10)
target_file: docs/SPEC.md
edit_type: insert
---

## Target file

`docs/SPEC.md` (or the canonical SPEC location; search for `## § 10` or `## 10.` if path differs).

## Edit type

`insert` — append a new cost section to the SPEC if § 10 is missing; expand if empty.

## Edit recipe

1. Locate the SPEC file (commonly `docs/SPEC.md` or `specs/SPEC.md`).
2. If `## § 10 — Cost` (or `## 10 Cost`, `## 10 Pricing`, etc.) is absent, add this section after § 9:

```markdown
## § 10 — Cost & Capacity Planning

### Pricing plan

Select one:
- **PAYG (Pay-As-You-Go):** Consumption-based, no commitment. Suitable for variable or low-volume workloads.
- **PTU (Provisioned Throughput Units):** Fixed capacity reservation. Suitable for predictable, sustained workloads (≥ 60% of capacity utilization).
- **Mixed:** Baseline PTU + PAYG overflow. Suitable for predictable baseline + bursty peaks.

**Selected plan:** <PAYG | PTU | Mixed>

### Capacity (if PTU or Mixed)

| Region | Model | PTU count | Notes |
|--------|-------|-----------|-------|
| <region> | <model, e.g., gpt-4o> | <N> | <e.g., baseline + 20% headroom> |

### Fallback for overflow (if PTU)

If PTU capacity is exceeded: <switch to PAYG | reject request | queue and retry>

### Budget threshold

- **Monthly budget cap:** EUR <amount> (or equivalent in local currency)
- **Alert threshold:** Fires at 80% utilization
- **Owner:** <team or email>

### Cost projection

- **Estimated monthly cost (baseline):** EUR <amount>
- **Peak-case cost (150% load):** EUR <amount>
- **Projection source:** <e.g., paygo-ptu-cost-analyzer, manual load testing>
```

3. Fill in:
   - The chosen plan (PAYG, PTU, or Mixed)
   - PTU capacity per region (if applicable)
   - Budget cap in local currency
   - Estimated costs based on load testing or the `paygo-ptu-cost-analyzer` skill

## Verification

Re-run threadlight: `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>`.

COST-001 should flip from `fail` to `pass` once:
- SPEC file contains a section matching `## § 10`, `## 10.`, `## 10 Cost`, or `## 10 Pricing` (case-insensitive).
- Section is non-empty and declares a pricing plan.
