# `threadlight-cost-reconciliation/v1` schema

> Strict v1 schema. Produced offline by
> `skills/threadlight-consumption-iq/scripts/reconcile.py`'s
> `reconcile_costs` by joining three inputs the caller has already loaded:
>
> | Input | Artifact | Produced by |
> | --- | --- | --- |
> | `forecast` | `specs/cost-manifest.json` | `consumption_iq.py` (`threadlight-cost-manifest/v1`) |
> | `actuals` | `specs/cost-actuals-manifest.json` | `cost_actuals.py` (`threadlight-cost-actuals/v1`) |
> | `policy` | `specs/SPEC.md` §14 `value_model` | `value_model.py` |
>
> `reconcile.py` performs no network access, no process execution and no
> file access. It is a pure transformation of `dict`s; Task 9's live CLI
> adapter is the only component allowed to talk to Azure.

## What this artifact is

`cost-manifest.json` says what a workload **should** cost.
`cost-actuals-manifest.json` says what was **observed**. This artifact is
the **join plus the verdicts**: variance, unit economics, attribution
coverage, and the fail-closed maturity evaluation that decides whether any
of those numbers are trustworthy enough to act on.

Verdict-shaped fields belong here and nowhere else. Do not add variance,
maturity or target comparisons to the actuals manifest — it is evidence, and
keeping evidence separate from judgement is what lets a verdict be
recomputed against a revised SPEC without re-collecting data.

## Statuses used in this schema

| Value | Meaning |
| --- | --- |
| `pass` | The named condition was checked against real evidence and held. |
| `should-fix` | Checked against real evidence and **exceeded a declared threshold**. Only `variance_status`, `unit_economics.target_status` and `drivers.payg_ptu.status` can take this value. |
| `not-verified` | The evidence needed to decide was absent, incomplete, or not comparable. **Never** an assumed pass. |

There is deliberately no `fail`. A threshold breach is `should-fix`
(actionable); missing evidence is `not-verified` (collect more, do not act).

## Top-level shape

```jsonc
{
  "schema": "threadlight-cost-reconciliation/v1",
  "generated_at": "2026-08-10T00:00:00Z",   // caller-supplied reconciled_at,
                                            // echoed verbatim; NOT the
                                            // actuals' collected_at
  "status": "not-verified",                 // mirrors maturity.status exactly
  "variance_status": "pass",                // narrow cost verdict, see below
  "forecast_ref":  { "path": "specs/cost-manifest.json",         "sha256": "…" },
  "actuals_ref":   { "path": "specs/cost-actuals-manifest.json", "sha256": "…" },
  "policy_ref":    { "path": "specs/SPEC.md", "section": 14,     "spec_sha256": "…" },
  "policy_snapshot": { /* every threshold + basis used, see below */ },
  "policy_errors": [],                      // copied verbatim from value_model
  "maturity":  { "status": "not-verified", "checks": [ /* 9 entries */ ] },
  "totals":    { /* … */ },
  "unit_economics": { /* … */ },
  "coverage":  { /* … */ },
  "drivers":   { "payg_ptu": { /* … */ } },
  "warnings":  []
}
```

Every key above is **always present**. A field whose evidence was missing is
`null` (or `not-verified`), never omitted — a consumer must never have to
distinguish "absent key" from "unknown value".

### `generated_at` — the *reconciled_at* instant

`string`, required, exactly `YYYY-MM-DDTHH:MM:SSZ` (UTC, `Z` suffix, second
precision) and a real calendar instant. A `+00:00` offset is the same moment
but a different string, and this value is hashed and compared byte-for-byte
downstream, so exactly one spelling is accepted;
`ReconciliationInputError` is raised otherwise. The value is echoed
unchanged so the artifact stays byte-reproducible for a fixed set of inputs.

**This is not the same instant as the actuals manifest's `generated_at`, and
the two must not be conflated.** Both documents spell the field
`generated_at` — what differs is *what each one generated*:

| Document | Field | Read it as | Answers |
| --- | --- | --- | --- |
| `threadlight-cost-actuals/v1` | `generated_at` | **`collected_at`** | When Azure Cost Management was read. |
| `threadlight-cost-reconciliation/v1` | `generated_at` | **`reconciled_at`** | When that evidence was re-projected against a forecast and a policy. |

The CLI supplies `reconciled_at` from the clock at the moment
`reconcile_costs` is called; it never copies the actuals' `collected_at`. The
two are equal only in the ordinary fast path where evidence is collected and
immediately reconciled (`run --all --with-actuals`). Every later
re-reconciliation of the same evidence — after a pricing refresh or a SPEC
edit, and with **no Azure call at all** — produces a strictly later
`reconciled_at` over an unchanged `collected_at`.

`reconciled_at` must therefore never precede `collected_at`: a verdict
cannot predate the evidence it judges, and the emitter rejects that pair
outright. `reconciled_at` is also what names the immutable history snapshot
(see below), so one collected actuals document legitimately appears in
several snapshots — each one bound to its exact source bytes by
`actuals_ref.sha256`, never by a matching timestamp.

### `status` vs `variance_status`

* **`status`** mirrors `maturity.status`. It is the answer to *"is this
  whole artifact trustworthy?"* and is `pass` only when **all nine**
  maturity checks pass.
* **`variance_status`** is deliberately **narrower**. It answers only *"is
  the observed cost within SPEC's declared tolerance of the forecast?"* and
  depends on four things: price bases are comparable, `variance_pct` was
  computable, `max_forecast_variance_pct` was declared, and the policy is
  **complete and anchored** (the shared `policy_complete` fact below — the
  §14 leaves are present and valid, nothing failed to parse, and
  `policy_ref.spec_sha256` is a re-derivable digest).

They are separate because a missing **interaction count** invalidates unit
economics without invalidating the cost comparison — the Cost Management
total is just as authoritative either way. Reporting `variance_status:
"pass"` alongside `status: "not-verified"` is therefore correct and
expected, and any consumer gating a decision on the *whole* artifact must
read the top-level `status`, not `variance_status` alone.

## `*_ref` blocks and hash invalidation

| Field | Type | Notes |
| --- | --- | --- |
| `forecast_ref.path` | `string` | Always `specs/cost-manifest.json`. |
| `forecast_ref.sha256` | `string` | `sha256_json(forecast)`. |
| `actuals_ref.path` | `string` | Always `specs/cost-actuals-manifest.json`. |
| `actuals_ref.sha256` | `string` | `sha256_json(actuals)`. |
| `policy_ref.path` | `string` | Always `specs/SPEC.md`. |
| `policy_ref.section` | `integer` | Always `14`. |
| `policy_ref.spec_sha256` | `string` | Caller-supplied SHA-256 of the SPEC commit the policy was read from; echoed **verbatim**, and additionally validated as a 64-character hex digest (see below). |

`sha256_json(document)` = SHA-256 over
`json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
Canonicalizing on all three axes means key insertion order, incidental
whitespace, and the host's preferred text encoding cannot change the hash of
semantically identical evidence.

**No field is excluded as "volatile."** The entire document is hashed,
including the inputs' own `generated_at`. A consequence is that a pure
**re-projection** of the forecast — same workload, new prices — changes
`forecast_ref.sha256` while `actuals_ref.sha256` stays identical, because
the observed bill did not change. That invalidation is intentional: a
reconciliation is only meaningful against the exact forecast it was computed
from. It is also cheap to resolve, since `reconcile` re-runs offline over
already-collected actuals with no new Azure calls — producing a document that
differs from its predecessor in exactly two places, `forecast_ref.sha256` and
`generated_at` (`reconciled_at`), over an unchanged `collected_at`.

### The SPEC anchor must be a real digest

`policy_ref.spec_sha256` exists so a consumer can re-hash `specs/SPEC.md` and
prove which revision of section 14 gated this verdict. A placeholder, a
truncated hash, or any string that is not 64 hex characters (either case)
cannot serve that purpose.

An unusable anchor makes **every threshold-gated verdict in the artifact
non-authoritative**, not just the maturity check. A threshold nobody can
trace back to a published SPEC revision is not a declared threshold, so it
may not produce a `pass` or a `should-fix` anywhere. Concretely, all of
these degrade together:

| Field | With an unusable anchor |
| --- | --- |
| `maturity.checks[policy_complete].status` | `not-verified` |
| `maturity.status` / top-level `status` | `not-verified` |
| `unit_economics.status` | `not-verified` |
| `unit_economics.target_status` | `not-verified` |
| `variance_status` | `not-verified` |
| `drivers.payg_ptu.status` | `not-verified` |

This is one fact with one definition, evaluated once and shared by all of
them: *the required §14 leaves are present and valid, the parser reported no
errors, and the anchor is re-derivable*. There is no second, looser
definition anywhere, so one verdict can never claim `pass` on exactly the
evidence another declared unprovable.

What survives is every number that needed **no policy** to measure:
`totals.*`, `variance_pct`, `unit_economics.successful_interactions` and
`target_usd`, and `drivers.payg_ptu`'s forecast/observed token volumes and
`observed_volume_variance_pct`. Those are observations, not verdicts.

It does **not** raise, and it is **not** rewritten: the string the caller
supplied is echoed verbatim so the artifact shows exactly what it was handed,
and every observed number is still emitted. A non-`string` value *does* raise
`ReconciliationInputError` — that is a caller type error, not evidence.

Recording all three refs makes the verdict **auditable at a commit**: given
the repository at a commit and the recorded hashes, a reviewer can prove
which forecast, which observed bill, and which SPEC revision produced the
number, even after all three have since moved on.

## `policy_snapshot`

Every numeric threshold and accounting basis the reconciliation used, copied
out of §14 at evaluation time. All keys are always present; a value is
`null` when the SPEC leaf was **absent or present-but-invalid** (an invalid
value is additionally reported in `warnings` and then treated exactly like a
missing one — a threshold that failed validation must never gate a verdict).

| Key | Type | Valid range |
| --- | --- | --- |
| `min_complete_days` | `integer` \| `null` | `>= 1` |
| `min_successful_interactions` | `integer` \| `null` | `>= 1` |
| `min_cost_settlement_age_hours` | `integer` \| `null` | `>= 0` |
| `max_window_end_age_days` | `integer` \| `null` | `>= 1` |
| `min_projection_attribution_coverage_pct` | `number` \| `null` | `0 < x <= 1` |
| `target_cost_per_successful_interaction_usd` | `number` \| `null` | `> 0` |
| `max_forecast_variance_pct` | `number` \| `null` | `0 <= x <= 1` |
| `max_token_volume_variance_pct` | `number` \| `null` | `0 <= x <= 1` |
| `actual_cost_basis` | `string` \| `null` | `"usage-pretax"` |
| `actual_billing_price_basis` | `string` \| `null` | `retail` \| `ea` \| `mca` \| `unknown` |
| `forecast_price_basis` | `string` \| `null` | `retail` \| `ea` \| `mca` |
| `allow_basis_mismatch_for_verdict` | `boolean` \| `null` | — |
| `scope_policy` | `string` \| `null` | `dedicated_resource_group` \| `tagged_allocation` |

The snapshot exists so a historical verdict stays auditable after a later
SPEC revision changes the thresholds it was rendered against.

`policy_errors` is a verbatim copy of the parser's error list. A non-empty
list forces `maturity.status`, `unit_economics.status` and
`variance_status` to `not-verified` — but the artifact is **still emitted in
full**, with every observed number intact.

## `totals`

| Field | Type | Formula |
| --- | --- | --- |
| `forecast_monthly_usd` | `number` \| `null` | `forecast.totals.monthly_cost_current_usd` |
| `forecast_window_usd` | `number` \| `null` | `forecast_monthly_usd * complete_days / 30` |
| `actual_window_usd` | `number` \| `null` | `actuals.cost.period_total_usd`, **and nothing else** |
| `actual_monthly_run_rate_usd` | `number` \| `null` | `actual_window_usd * 30 / complete_days` |
| `variance_window_usd` | `number` \| `null` | `actual_window_usd - forecast_window_usd` |
| `variance_pct` | `number` \| `null` | `variance_window_usd / forecast_window_usd` |

A 30-day month is the single normalization constant in both directions.
`monthly -> window -> monthly` therefore recovers the same value
*arithmetically*, but not necessarily the same **serialized** figure: each
leg is quantized to cents on the way out, and two cent roundings can leave
up to $0.01 of drift (e.g. `$100.00` over a 7-day window serializes as
`$23.33`, whose run rate serializes as `$99.99`). Intermediate values keep
full precision, so the drift never compounds beyond that single cent — but
a consumer comparing `actual_monthly_run_rate_usd` against a monthly
forecast should not expect bit-exact equality.

`variance_pct` is `null` — never `0`, never infinity — when
`forecast_window_usd` is `0.00`, **negative**, or unknown, with an
explanatory entry in `warnings`. A percentage against a zero baseline is
undefined, and emitting `0` would read as "on target" for a workload that
was never projected at all. A *negative* baseline is worse than undefined:
dividing by it inverts the sign, so a genuine overspend would report as a
large negative "under budget" percentage. The signed dollar figures are
retained in both cases and are the honest reading.

Refunds and credits stay **signed**: a negative `actual_window_usd` produces
a negative run rate and a negative variance. Clipping at zero would hide a
credit that materially changed the bill.

### No token cost ever enters a money total

`actual_window_usd` comes from `actuals.cost.period_total_usd` and from no
other field. Azure Monitor token reprice is **attribution** evidence: it
explains *where* spend went, it is never spend itself. Adding it to a Cost
Management total double-counts the same dollars once as an invoice line and
once as a reprice estimate.

This is enforced by the type boundary, not by convention: `reconcile_costs`
has no parameter through which a token cost could be passed, and when
`period_total_usd` is absent the total is `null` — a present
`retail_repriced_cost_usd` is **not** used as a fallback, and the
`actuals_status` maturity check fails, so the artifact reports
`not-verified` rather than a plausible-looking fabricated number.

## `unit_economics`

| Field | Type | Notes |
| --- | --- | --- |
| `status` | `pass` \| `not-verified` | Evidence gate (below). |
| `successful_interactions` | `integer` \| `null` | Observed count, reported even when `status` is `not-verified`. |
| `cost_per_successful_interaction_usd` | `number` \| `null` | `actual_window_usd / successful_interactions`, 4 decimal places. |
| `target_usd` | `number` \| `null` | `policy_snapshot.target_cost_per_successful_interaction_usd`. |
| `target_status` | `pass` \| `should-fix` \| `not-verified` | Comparison against the target. |

`status` is `pass` only when **all** of:

1. `actuals.status == "pass"`,
2. the policy is complete, parsed cleanly **and** is anchored to a
   re-derivable SPEC digest (the shared `policy_complete` fact),
3. `actuals.usage.interaction_status == "pass"`,
4. `successful_interactions > 0` (a divide-by-zero guard, not a threshold),
5. `actual_window_usd` is available.

**Token metrics are irrelevant to this gate.** Cost-per-interaction needs a
cost total and a success count; token attribution explains the cost but is
not required to compute it. Gating on it would suppress a perfectly sound
unit-economics number whenever model-level metrics happened to be missing.

`target_status` is a **separate, independent** comparison, evaluated only
when `status` is `pass`: `pass` at or under target, `should-fix` above it,
`not-verified` when no target is declared. A `pass` on evidence quality says
nothing about whether the number is *good*.

Cost per interaction keeps **4 decimal places**, unlike every other money
field. It is a rate, not a ledger amount: at cent precision a $0.004
per-interaction cost would round to `0.00` and make the target comparison
meaningless.

## `coverage`

| Field | Type | Notes |
| --- | --- | --- |
| `projection_attribution_coverage_pct` | `number` \| `null` | Computed here. Gated by `min_projection_attribution_coverage_pct`. |
| `source_resource_id_coverage_pct` | `number` \| `null` | Copied verbatim from `actuals.cost.resource_id_coverage_pct`. Diagnostic only. |
| `unmodeled_actual_usd` | `number` \| `null` | **Net** signed sum of unmatched actual resource costs. |
| `forecast_not_observed_usd` | `number` \| `null` | Unmatched forecast groups, expressed in **forecast-window USD**. |
| `matched_resources` | `array` | One entry per matched pair. |
| `unmodeled_resources` | `array` | `{resource_id, resource_type, period_cost_usd}`. |
| `forecast_not_observed_resources` | `array` | `{forecast_resource_ids, forecast_deployment_ids, resource_type, forecast_monthly_usd, forecast_window_usd}`. |

`forecast_not_observed_usd` is **window** USD, not monthly, so that it is
directly comparable with `unmodeled_actual_usd` and with
`totals.actual_window_usd` — mixing a monthly figure into a window-scoped
block is the kind of unit error that silently inflates a gap by ~4x on a
7-day window. The per-resource entries carry **both** figures for readers
who want the monthly view.

### `matched_resources[]`

| Field | Type |
| --- | --- |
| `actual_resource_id` | `string` (original casing, as billed) |
| `resource_type` | `string` (normalized, casefolded) |
| `forecast_resource_ids` | `array[string]`, sorted |
| `forecast_deployment_ids` | `array[string]`, sorted — non-empty only for AOAI roll-ups |
| `forecast_monthly_usd` | `number` |
| `forecast_window_usd` | `number` \| `null` |
| `actual_window_usd` | `number` |
| `match_method` | `resource_id` \| `aoai_account_rollup` \| `unique_type` |

### Matching rules

Resource IDs are normalized with `casefold()` and a trailing-`/` strip
before comparison; Azure IDs are case-insensitive and inconsistently cased
across APIs.

1. **Exact normalized ID** -> `match_method: "resource_id"`.
2. **AOAI account roll-up** -> `match_method: "aoai_account_rollup"`. Cost
   Management bills Azure OpenAI at the **account**, while a forecast models
   individual **deployments**. A forecast ID of the form
   `.../accounts/<account>/deployments/<deployment>` therefore rolls up to
   `.../accounts/<account>` with type
   `microsoft.cognitiveservices/accounts`. Several forecast deployments
   under one account collapse into a **single** matched entry whose
   `forecast_monthly_usd` is their **sum**; the original deployment IDs are
   preserved in `forecast_deployment_ids` so the roll-up remains reversible.
   Roll-up is strictly **account-name-scoped**: a deployment under `aoai1`
   never matches an observed `aoai2`.
3. **Unique-type fallback** -> `match_method: "unique_type"`. Applied only
   when exactly **one** unmatched forecast group and exactly **one**
   unmatched actual resource share a normalized type. Anything else stays
   unmatched, and the ambiguity is reported in `warnings`. A coin-flip
   pairing would produce a confident-looking but arbitrary per-resource
   variance. Rolled-up AOAI groups are excluded from this fallback: they
   already asserted an account-scoped identity, so falling back on type
   would re-attribute one account's forecast to another account's bill.

Each forecast group and each actual resource is used **at most once**, so no
dollar is counted twice across `matched_resources`, `unmodeled_resources`
and `forecast_not_observed_resources`. `matched_resources[].actual_window_usd`
plus `unmodeled_actual_usd` plus `actuals.cost.unattributed_usd` reconciles
back to `totals.actual_window_usd` — **provided the actuals themselves
reconcile**, which is checked explicitly (see the accounting identity gate
below).

### Why coverage uses gross absolute cost

```
projection_attribution_coverage_pct
    = sum(abs(cost) for matched actual resources)
    / (sum(abs(cost) for ALL actual resources) + abs(unattributed_usd))
```

bounded to `[0, 1]`, and `null` when the denominator is `0`.

### Coverage is gated on the actual-cost accounting identity

Before that ratio is reported at all, the actuals must add up:

```
round_cents(sum(actuals.cost.resources[].period_cost_usd)
            + actuals.cost.unattributed_usd)
    == round_cents(actuals.cost.period_total_usd)
```

If they disagree, `projection_attribution_coverage_pct` is `null`, the
`projection_attribution_coverage` maturity check is `not-verified`, and a
`warnings` entry says `actual cost rows do not reconcile to
period_total_usd`. **Every money total is still emitted unchanged** — the
numbers are the evidence of the contradiction, and suppressing them would
destroy it.

The reason is that the coverage denominator is built from the row
breakdown while the rest of the artifact is built from `period_total_usd`.
When those two disagree, a *high* coverage number is the most dangerous
possible output: ten $10 rows against a $1,000 total would report `1.0`,
"the projection explains the entire bill", while explaining 1% of it. A
coverage ratio computed over rows that do not describe the bill is not a
conservative estimate — it is unrelated to the question.

Quantization is applied to the **aggregate**, not per row, so genuine
sub-cent rows (two $0.005 rows summing to $0.01) reconcile. Absence is not
contradiction: no `period_total_usd`, or neither a `resources` breakdown nor
an `unattributed_usd`, leaves the identity unevaluated rather than failed.
A signed refund row participates in the identity like any other row.

The value is also validated as a number in `[0, 1]`: a `bool`, a `NaN` or
infinity, a string, or an out-of-range ratio degrades the check to
`not-verified` with an explanatory `detail` rather than raising or reaching
the artifact (bare `NaN` is not valid JSON).

**Absolute, not net**, on both sides. A refund on an *unmodeled* resource
shrinks a net denominator, which would push coverage upward — potentially
to `1.0` — even though the projection explains no more of the bill than it
did before. Coverage answers "how much of the observed activity does the
model explain?", and a $500 credit is $500 of activity the model must still
account for. Using gross magnitude keeps a refund from **manufacturing**
attribution quality.

The `unattributed_usd` term is in the denominator because spend that Cost
Management could not tie to any resource ID is, by definition, spend the
projection did not explain.

**Net, by contrast, for `unmodeled_actual_usd`**: that field is real money
in a ledger. It must stay signed and must keep summing back to
`actual_window_usd`.

`source_resource_id_coverage_pct` measures something different — what share
of the *source rows* carried a resource ID at all, an upstream data-quality
signal from the actuals collector. It is copied through **unrounded** and
gates nothing. High source coverage with low projection coverage is a
perfectly ordinary state: every row was well-formed, and the forecast simply
did not model those resources.

## `maturity`

```jsonc
{
  "status": "pass" | "not-verified",
  "checks": [ { "id": "…", "status": "…", "actual": …, "required": …, "detail": "…" } ]
}
```

`checks` **always** contains exactly nine entries, in the order below —
every check is declared even when its evidence is entirely absent, so a
consumer can never mistake "not evaluated" for "passed". `actual` preserves
the observed value even when the check fails, so a `not-verified` artifact
still says *how far* from mature the evidence was. `detail` is a non-empty
human-readable sentence.

`status` is `pass` **only when all nine checks pass** (fail-closed).

| `id` | Passes when | `actual` |
| --- | --- | --- |
| `policy_complete` | Every `REQUIRED_PATHS` leaf of §14 is present and valid, `policy_errors` is empty, and the SPEC anchor is a real digest | `{missing_paths, policy_error_count, policy_spec_sha256_valid}` |
| `actuals_status` | `actuals.status == "pass"` **and** `cost.period_total_usd` is present | `actuals.status` |
| `complete_days` | `complete_days >= min_complete_days` | `integer` \| `null` |
| `successful_interactions` | `interaction_status == "pass"` and count `>= min_successful_interactions` | observed count \| `null` |
| `cost_settlement_age_hours` | `settlement_age_hours >= min_cost_settlement_age_hours` | `integer` \| `null` |
| `window_end_age_days` | `window_end_age_days <= max_window_end_age_days` | `integer` \| `null` |
| `projection_attribution_coverage` | coverage `>= min_projection_attribution_coverage_pct` | `number` \| `null` |
| `cost_accounting_basis` | `actuals.cost.basis` **and** `policy.actual_cost_basis` are both `usage-pretax` | `{actuals_cost_basis, policy_actual_cost_basis}` |
| `price_basis_compatible` | Bases are equal and known, **or** `allow_basis_mismatch_for_verdict` is `true` | `{actual_billing_price_basis, forecast_price_basis, allow_basis_mismatch_for_verdict}` |

An invalid policy value counts as **missing** in `policy_complete`: a
threshold that failed validation is not a threshold, and treating it as
merely "present" would let a malformed SPEC gate a verdict. The
`success_event` leaves are checked for presence only — `value_model.py`
already validated their content and this module never builds a query from
them.

`evaluate_maturity(actuals, policy, *, policy_errors=(),
projection_attribution_coverage_pct=None, policy_spec_sha256=None)` is
callable standalone. The coverage argument is passed in because projection
coverage is a property of the forecast/actuals **join**, not of the actuals
document alone; omitting it leaves that one check `not-verified`, so a
standalone call fails closed rather than treating unknown coverage as
complete. A supplied coverage value is validated (numeric, finite, in
`[0, 1]`, not a `bool`) and an unusable one degrades the check instead of
raising — this function returns a verdict, it never refuses to.

`policy_spec_sha256` is likewise optional: `policy_spec_sha256_valid` is
`null` for a standalone call with no anchor to check, `false` only when a
supplied anchor is not a 64-character hex digest. A standalone call with no
anchor is therefore the **only** case in which an unanchored policy still
produces a verdict — and it is honest, because nothing was claimed: no
`policy_ref` block is emitted, so no consumer is told the thresholds came
from a provable SPEC revision. `reconcile_costs` always supplies an anchor
and always emits that block, so it has no such case. Passing a placeholder
here is not the same as passing nothing: a supplied-but-unusable anchor
fails `policy_complete` exactly as it does in a full reconciliation.

Standalone calls are not raise-free. `evaluate_maturity` reaches the cost
accounting-identity check, which quantizes the breakdown total, so money
whose magnitude cannot be represented at cent precision raises
`ReconciliationInputError` from here too (see **Errors** below).

### Price basis comparability

`actual_billing_price_basis` (what the invoice was priced on) is compared
against `forecast_price_basis` (what the projection was priced on).
`actual_cost_basis` is the **metric** (`usage-pretax`) and is never one side
of this comparison.

When the bases differ, or the actual basis is `unknown`, and
`allow_basis_mismatch_for_verdict` is not `true`, `variance_status` becomes
`not-verified` and a `warnings` entry names the mismatch — **but the numeric
`variance_window_usd` and `variance_pct` are still reported**. The
arithmetic is sound; what is unproven is that the two sides were priced off
the same list. An EA-discounted invoice compared against retail forecast
prices produces a real number that is not a real *finding*.

## `drivers.payg_ptu`

| Field | Type | Notes |
| --- | --- | --- |
| `status` | `pass` \| `should-fix` \| `not-verified` | |
| `observed_volume_variance_pct` | `number` \| `null` | `(observed_monthly_tokens - forecast_monthly_tokens) / forecast_monthly_tokens` |
| `forecast_monthly_tokens` | `number` \| `null` | Sum of `monthly_units_consumed.input_tokens + output_tokens` for the recommended deployments. |
| `observed_monthly_tokens` | `number` \| `null` | Observed window tokens normalized `* 30 / complete_days`. |
| `threshold_field` | `string` | Always the literal `"max_token_volume_variance_pct"`. |
| `threshold_pct` | `number` \| `null` | The value of that field. |
| `detail` | `string` | Non-empty explanation. |

The driver fires **only** for an explicit recommendation whose
`current_sku.tier` and `recommended_sku.tier` are `PAYG` and `PTU` in either
direction, on an Azure OpenAI **deployment** resource. A same-tier
recommendation, a non-AOAI resource, or an absent recommendation leaves the
driver `not-verified` — a PTU sizing decision is never inferred.

Observed tokens are read from `actuals.usage.models` rows whose `deployment`
matches the recommended deployment name, and only when
`model_attribution_status == "pass"`.

### Observed token rows are scoped to the recommended account

`deployment` is a **leaf name**, and two Azure OpenAI accounts can each own
a `chat`. Recommendations are therefore grouped by their parent account, and
a row is only counted for an account it demonstrably belongs to. A row may
carry an optional `resource_id` (a deployment or account resource ID) or
`account_resource_id`; when present it decides the row's account, a row
belonging to an unimplicated account is excluded with a warning, and rows
identified this way are summed across every implicated account.

When the recommendation implicates **more than one account** and a
name-matching row carries no identifier at all, the driver is
`not-verified` with a warning. Summing such a row would attribute one
account's traffic to another, and silently dropping it would understate the
account it really belongs to; neither is a token volume a PTU sizing
decision may rest on. With exactly one account implicated, a bare deployment
name is unambiguous and is counted as before.

A warning names the row's account as an **"Azure OpenAI account"** only when
the normalized resource ID proves it — its type segment is
`microsoft.cognitiveservices/accounts`. Any other resource (a storage
account mis-tagged into a model row, for example) is called a **"resource
account"**, so the artifact never asserts a resource kind the evidence does
not show and never sends a reader looking for an AOAI resource that does not
exist.

### A row that contradicts itself is never counted

A row may state its deployment **twice**: once as the bare `deployment` leaf
name, and again as the deployment leaf inside its `resource_id`. When both
are present they are cross-checked. If they disagree, the row identifies no
deployment at all: it is excluded from observed volume. A warning names both
claims, but only when the contradictory deployment is relevant to a
recommendation. Counting it under either name would attribute one
deployment's traffic to another on evidence the row itself contradicts — and
the dangerous case is arithmetic, not cosmetic, since a high-volume
contradictory row summed under a recommended name inflates
`observed_monthly_tokens` and can flip a `should-fix` into a `pass`.

The cross-check excludes disagreement only. A row that states the same
deployment twice is exactly as usable as one that states it once, and an
`account_resource_id` (or an account-scoped `resource_id`) carries no
deployment leaf, so there is nothing to contradict and the row's
`deployment` field stands.

### The verdict is gated by the SPEC anchor too

`status` is `not-verified` whenever the shared `policy_complete` fact is
false — an incomplete or unparsed §14, or an anchor that is not a
re-derivable digest — because `max_token_volume_variance_pct` is a
SPEC-declared threshold like any other. The measured
`forecast_monthly_tokens`, `observed_monthly_tokens` and
`observed_volume_variance_pct` are still reported: no policy was needed to
observe them.

**This block never contains a dollar figure and never reads a cost
threshold.** A PTU sizing recommendation is a function of *token throughput*,
not of spend, and a workload can absorb far more volume drift than cost
drift before the recommendation stops holding. Reusing
`max_forecast_variance_pct` here would apply a cost tolerance to a volume
question; `threshold_field` is emitted so an auditor can verify from the
artifact alone that the correct, separately declared threshold was used.

`should-fix` details instruct the reader to **rerun PAYG/PTU analysis at
observed volume** rather than asserting a new tier: the reserved-capacity
decision needs the full sizing model, not a variance ratio.

## `warnings`

`array[string]`, always present, possibly empty. Non-fatal observations —
ambiguous matching, a forecast resource with no `monthly_cost_usd`, an
invalid policy value that was ignored, an undefined `variance_pct`, a price
basis mismatch, actual cost rows that do not reconcile to
`period_total_usd`, a SPEC anchor that is not a digest, a token row that
cannot be attributed to one recommended account, a token row whose
`deployment` field contradicts its own resource identifier. Warnings never
change a status on their own; they explain one that was already degraded.
Entries are de-duplicated and ordered by first occurrence.

## Precision and rounding

| Quantity | Places | Rounding |
| --- | --- | --- |
| USD ledger amounts | 2 | `ROUND_HALF_UP` at serialization only |
| `cost_per_successful_interaction_usd` | 4 | `ROUND_HALF_UP` |
| Ratios (`variance_pct`, coverage, token variance, `observed_monthly_tokens`) | 6 | `ROUND_HALF_UP` |

All money arithmetic uses `decimal.Decimal`, parsed from `str(value)` so the
serialized decimal — not a binary float's error tail — is what gets
rounded. Rounding happens **once, at serialization**; intermediate values
keep full precision, so drift never accumulates across a chain of
window/monthly conversions *within* one computation. It can still appear
**between** two serialized figures that each rounded independently: see the
$0.01 `monthly -> window -> monthly` drift noted under `totals`.

`ROUND_HALF_UP` (ties away from zero) is the accounting convention, and
Python's built-in `round()` is never used: it applies banker's rounding to
an already-imprecise binary float, so `round(2.675, 2) == 2.67`. Negative
zero is normalized to `0.00`.

## Errors

`ReconciliationInputError` is raised **only** for structurally broken
evidence: a non-mapping document, a resource list that is not a list, a
non-mapping list entry, money that is not a finite number (including
`NaN`/`Infinity`/`bool`), money whose magnitude cannot be represented at the
required precision, a negative or non-integer interaction count, a
`complete_days` that is present but not a positive integer, an
out-of-range source coverage, a malformed `generated_at`, or a
`policy_errors` that is not a list of strings. These are producer or caller
bugs that would otherwise silently corrupt a money total.

The magnitude case is worth calling out because it is raised **late**: field
parsing checks a cell's *shape*, so a well-formed but astronomical figure
survives it and fails at the quantize step instead. The cost
accounting-identity check is one of those steps, which is why a standalone
`evaluate_maturity` call — not just `reconcile_costs` — can raise. A total
nobody can represent must not silently become a reconciled one.

Everything else **degrades to `not-verified` and is still emitted**: an
incomplete or invalid policy, an absent interaction count, an absent token
series, an unmatched resource, a missing threshold. Those are legitimate
observed states of a pilot, not exceptions.

Validation is narrow and per-field. There is no broad `except Exception` in
the module: `TypeError`/`ValueError` from `json.dumps` and `ArithmeticError`
from `Decimal.quantize` are the only caught exception types, and each is
re-raised as `ReconciliationInputError` naming the offending field.

## Purity guarantees

* Inputs are **never mutated**. `forecast`, `actuals`, `policy` and
  `policy_errors` are read-only, and every list in the result is fresh, so a
  caller mutating the output cannot reach back into its own inputs.
* Output is **deterministic**: for a fixed set of inputs and a fixed
  `generated_at`, the document is byte-identical across runs. All ordering
  is explicit (`sorted()`), never dictionary or set iteration order.
* No network access, no process execution, no file access, no clock read,
  no environment lookup. `generated_at` (the `reconciled_at` instant) is
  supplied by the caller precisely so this module has no hidden
  non-determinism; the CLI reads the clock once, on its side of the boundary.
