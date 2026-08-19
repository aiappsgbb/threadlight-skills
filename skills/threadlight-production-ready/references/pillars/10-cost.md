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
| `COST-007` | vNext cost-manifest **meter coverage** — every detected resource/meter line in `specs/cost-manifest.json` is priced or explicitly flagged. Reads `meter_coverage.status` + per-line `pricing_status` (produced by `threadlight-consumption-iq`'s `cost_api.project_profile`). State semantics: any line `pricing_status: not-priceable` → `must-fix`; else `meter_coverage.status: not-verified` (or a v1 manifest with no `meter_coverage` key) → `not-verified`; `meter_coverage.status: complete` and every line priced → `pass`. Never changes COST-005/006 outcomes. Remediation recipe: `references/remediation-recipes/COST-007.md` | `not-verified` if no vNext `meter_coverage` |

### Live (tier 3 — `Cost Management Reader` on subscription)

| ID | Check | Default status |
|---|---|---|
| `COST-101` | Budget exists on subscription or RG | `should-fix` if zero |
| `COST-104` | Idle ACA detection (revisions with 0 requests in last 7 days) | `should-fix` if found |
| `COST-105` | Foundry model deployment tier matches declared plan (PAYG vs PTU) | `should-fix` if drift |

### Reconciled actuals (artifact-driven, tier 3, experimental)

`COST-102` / `COST-103` are **consumers** of the reconciliation artifact
published by `threadlight-consumption-iq` (`actuals` → `reconcile`). They add
no Cost Management query of their own: production-ready reads
`specs/cost-reconciliation-manifest.json` and reports the verdicts the
reconciler already computed. Both are emitted exactly once, in every mode.

They are emitted by the pillar runner **before** the Bicep/ARM cost static
analyzer runs, so their existence does not depend on it: when that analyzer
hits an ARM shape it cannot parse and the pillar fails closed, the tier-0 cost
controls still degrade to a gating `must-fix` while these two keep reporting
the artifact evidence that was actually available.

The bundle is only trusted when all of the following still hold at assess
time — otherwise both findings are `not-verified`, never `pass`:

- exact schemas (`threadlight-cost-reconciliation/v1`,
  `threadlight-cost-actuals/v1`, forecast `schema_version >= "1.0"`);
- canonical-JSON SHA-256 of `specs/cost-actuals-manifest.json` and
  `specs/cost-manifest.json` matches `actuals_ref` / `forecast_ref`, and the
  raw bytes of `specs/SPEC.md` match `policy_ref.spec_sha256` (a placeholder
  or non-hex anchor is unusable). Only these canonical paths are read — a
  `*_ref.path` is provenance, never a read instruction;
- `generated_at` on both documents is a real UTC instant and the verdict does
  not predate the evidence;
- the observed window is still fresh **today** against the declared
  `policy_snapshot.max_window_end_age_days`;
- top-level `status` **and** `maturity.status` are `pass`;
- each relayed verdict agrees with its own numbers (below).

**Verdict integrity.** A `pass` / `should-fix` is only relayed when the
artifact's own ratio supports it: `|variance| <= tolerance` ⇒ `pass`, else
`should-fix` (inclusive at the boundary, magnitude only — a 60% *underspend*
has left the band as surely as a 60% overspend). A verdict that contradicts
its own numbers — a `pass` on 400% variance against a 5% tolerance, or a
`should-fix` from well inside the band — is withheld as `not-verified` with
`reconciliation verdict contradicts its numeric variance/tolerance`, never
relayed. This is not a second threshold: the check can only ever withhold a
decided verdict, never author or upgrade one. A `not-verified` from the
reconciler stays `not-verified`, and unusable numbers (bool, NaN, infinite,
non-numeric, missing tolerance, or a JSON integer hundreds of digits long)
are reported as malformed evidence rather than as a contradiction — no
ratio conversion in either finding raises on hostile input.

**Driver threshold integrity (COST-103).** `drivers.payg_ptu.threshold_pct`
must be the *same producer value* as the SPEC-anchored
`policy_snapshot.max_token_volume_variance_pct` — a driver free to declare
its own number independent of the snapshot could carry a band nobody
anchored to SPEC § 14. Missing, out-of-range, or numerically mismatched is
withheld as `not-verified` naming the internal inconsistency, before the
volume-variance-vs-band comparison above ever runs.

| ID | Check | Default status |
|---|---|---|
| `COST-102` | Observed cost variance (`totals.variance_pct`) against the SPEC § 14 declared tolerance `policy_snapshot.max_forecast_variance_pct`. The verdict is consumed from the reconciler's `variance_status` — no second threshold is applied here, and no percentage is hardcoded; a `variance_status` that contradicts the artifact's own variance/tolerance is withheld as `not-verified` | `not-verified` without a provable (or self-consistent) artifact; `should-fix` when `variance_status: should-fix` |
| `COST-103` | PAYG/PTU recommendation still holds at observed **token volume**, read from `drivers.payg_ptu`. Requires `threshold_field: max_token_volume_variance_pct`, a `threshold_pct` in [0, 1] that numerically matches `policy_snapshot.max_token_volume_variance_pct`, and a finite `observed_volume_variance_pct` — the cost tolerance is never applied to a volume question, no dollar figure appears in this finding, and a driver status its own volume variance does not support is withheld as `not-verified` | `not-verified` without a driver verdict; `should-fix` → rerun PAYG/PTU analysis at observed volume |

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
| COST-007 meter coverage (not-priceable / not-verified lines) | `threadlight-consumption-iq` (recipe `references/remediation-recipes/COST-007.md`) |
| COST-102 / COST-103 reconciled actuals (missing, stale, or immature bundle) | `threadlight-consumption-iq` (`actuals` then `reconcile`) |

## Why this pillar matters

The pilot ships under a generous Azure free credit. Production ships
under a fixed budget signed by a finance director. The skill produces
the cost projection the finance director needs to sign off — and the
alert wiring that means the first anomaly doesn't become an
expense-report incident.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.

## Live-leg gap evidence (Task 7)

These findings are **advisory, tier-0** evidence propagated from the executable
threadlight-loadtest leg(s). production-ready reads each same-named finding from the leg's
shared-envelope manifest under `specs/`. Absent a fresh, **complete** leg
manifest they stay `not-verified` (verification debt) — an incomplete, stale,
or `aborted` leg never inflates this pillar's score or readiness, and a
`must-fix` in the leg's evidence dominates regardless of envelope freshness.

| ID | Verified when the leg reports it (fresh + complete) | Severity |
|---|---|---|
| `LOAD-002` | `threadlight-loadtest` kept the projected load within the budget ceiling | `should-fix` |
