# Pillar 10 — `cost`

> **What this pillar answers.** Is the pricing plan declared (PAYG vs
> PTU)? Is there a budget + anomaly alert? Has someone forecast usage
> against the cap? Are idle resources cleaned up?

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `COST-001` | SPEC § 12 declares pricing plan (`payg`, `ptu`, or `mixed`) | `must-fix` if absent |
| `COST-002` | If `ptu`: capacity declared per region (PTU count); fallback to PAYG in case of overflow declared | `should-fix` if absent |
| `COST-003` | Bicep declares a Budget resource (`Microsoft.Consumption/budgets`) OR `docs/budget.md` documents one set out-of-band | `should-fix` if absent |
| `COST-004` | Anomaly alert declared (Azure Cost Management anomaly alert OR daily-spike alert rule) | `should-fix` if absent |
| `COST-005` | Cost-projection artefact present **and fresh**: `docs/cost-projection.md` exists AND `specs/cost-manifest.json` has `schema_version >= "1.0"` AND `generated_at` within 30 days of last deploy | `should-fix` if any condition missing |
| `COST-006` | No unaddressed cost recommendations in `specs/cost-manifest.json`: walks `recommendations[]`; `>$100/mo` savings → `must-fix`, `>$25/mo` → `should-fix`. `not-verified` when manifest absent | `not-verified` if manifest missing |

### Live (tier 3 — `Cost Management Reader` on subscription)

| ID | Check | Default status |
|---|---|---|
| `COST-101` | Budget exists on subscription or RG | `should-fix` if zero |
| `COST-102` | Anomaly alert configured | `should-fix` if absent |
| `COST-103` | Last 7 days actual cost on track vs declared budget (forecast extrapolation) | `should-fix` if > 80% utilization |
| `COST-104` | Idle ACA detection (revisions with 0 requests in last 7 days) | `should-fix` if found |
| `COST-105` | Foundry model deployment tier matches declared plan (PAYG vs PTU) | `should-fix` if drift |

## PAYG vs PTU recommendation

The skill embeds a recommendation heuristic from `paygo-ptu-cost-analyzer`:

| Observed pattern | Recommendation |
|---|---|
| Predictable load > 60% of PTU capacity break-even | Move to PTU |
| Spiky / unpredictable / < 30% of break-even | Stay PAYG |
| Mix of always-on chat + bursty batch | PTU baseline + PAYG overflow |

The cost pillar surfaces the recommendation in the report's "Cost
projection" section.

## Common gaps

- "Cost is fine, we tested for a week" — but the test was 2 users; the
  production usage projection is 200x and nobody did the math.
- No budget → first anomaly is a finance ticket, not a Slack alert.
- ACA revisions accumulate; idle revisions count toward cost forever.
- PTU committed but no fallback for overflow → 429s in production peak.

## Remediation

| Finding | Skill |
|---|---|
| PAYG/PTU analysis | `paygo-ptu-cost-analyzer` |
| Budget / alert wiring | `azd-patterns` |
| Idle resource cleanup | (manual) |
| COST-005 tightened + COST-006 | `threadlight-consumption-iq` |

## Why this pillar matters

The pilot ships under a generous Azure free credit. Production ships
under a fixed budget signed by a finance director. The skill produces
the cost projection the finance director needs to sign off — and the
alert wiring that means the first anomaly doesn't become an
expense-report incident.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
