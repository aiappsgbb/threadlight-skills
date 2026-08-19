# Value model schema — SPEC § 14 (`threadlight-design`)

The canonical, no-default field-by-field schema for `value_model:` — the
block SPEC § 14 declares and `scripts/ci/check_pilot_contract.py` shape-checks
(`VALUE_MODEL_MARKERS`: `value_model:` / `maturity_policy:` / `success_event:`
/ `baseline:` / `accounting:`). It backs the cost-actuals reconciliation
pipeline that reconciles a pilot's *projected* per-interaction cost against
its *actual* billed cost once real usage exists.

**This file has no defaults.** Every bound below is a *validation rule*, not
a value to copy. `references/speckit-template.md` § 14 emits the same shape
with every leaf blank; `examples/returns-triage-governed/specs/SPEC.md` § 14
shows one pilot's real, worked-out numbers — those numbers are specific to
that pilot's return-triage economics and must never be copied into another
pilot's SPEC as if they were a default.

> **Comments are not values.** A YAML comment like `# int >= 1` next to a
> blank key documents type and bounds for the human/operator filling it in
> later. It is never a substitute for an actual value, and no skill or
> script may treat the presence of the comment as if the field were set.
> **Never invent a numeric value** to make a field look "complete" —
> an honest blank (with a § 13 Open Questions row) is always correct;
> a fabricated number is always wrong.

---

## `value_model.cost.maturity_policy`

Gates whether there is *enough* real data yet to compute a trustworthy
actual-vs-projection cost verdict. All fields required; no defaults.

| Field | Type | Bounds |
|---|---|---|
| `min_complete_days` | int | `>= 1` |
| `min_successful_interactions` | int | `>= 1` |
| `min_cost_settlement_age_hours` | int | `>= 0` |
| `max_window_end_age_days` | int | `>= 1` |
| `min_projection_attribution_coverage_pct` | float | `(0, 1]` (exclusive of 0, inclusive of 1) |

> **Incomplete maturity policy still writes evidence.** When these
> thresholds are not (yet) met, the reconciliation verdict is
> `not-verified` — but the projection and actuals evidence artifacts are
> **still produced and written**. `not-verified` withholds the pass/fail
> judgment; it does not withhold the underlying data. A pilot that never
> reaches maturity still accumulates an audit trail, it just never earns a
> `pass`/`fail` cost verdict.

> **`min_projection_attribution_coverage_pct` is not the same measurement as
> actual resource-id coverage.** Actual cost data (from the Cost Management
> Query API) is attributed to resources by tag/resource-group scoping
> (`accounting.scope_policy`), which is a separate, unrelated coverage
> question from whether the *projection* correctly attributes cost to the
> traced interactions. Only **projection** attribution coverage is
> policy-gated by this field — actual-side coverage has no equivalent gate
> here and is not interchangeable with this field.

---

## `value_model.cost.success_event`

Identifies which traced events count as a "successful interaction" for the
cost-per-success calculation.

> **`success_event` is unambiguous: operator-confirmed in Full mode, blank
> in Fast-PoC, never derived.** In **Full mode** the operator must state and
> confirm `name`, `trace_attribute`, and every `success_values` entry
> explicitly — this triple is never inferred from a BR-XXX business rule,
> guessed from the process description, or silently derived from any other
> field. In **Fast-PoC mode** all three stay blank (`success_values: []`)
> and the source is left open in § 13 with `Source: open-question`, exactly
> like every other § 14 leaf. A final review must treat an invented
> `success_event` leaf as a defect with the same severity as an invented
> numeric threshold — it is never acceptable to fabricate an event name
> just to make the section look complete.

| Field | Type | Constraint |
|---|---|---|
| `name` | string | nonempty; identifier grammar (see below) |
| `trace_attribute` | string | nonempty; identifier grammar (see below) |
| `success_values` | list of strings | **nonempty**; each item follows the identifier grammar |

**Identifier grammar** (applies to `name`, `trace_attribute`, and every
entry of `success_values`):

```text
^[A-Za-z][A-Za-z0-9_.:-]{0,127}$
```

> **Why this grammar is restrictive, not stylistic**: `name` and
> `trace_attribute` are compiled into a **fixed** AppTraces KQL query by the
> reconciliation code. The code never assembles arbitrary KQL from operator
> input — it always issues the same parameterized query shape, substituting
> only values that already satisfy this grammar. An identifier outside the
> grammar is rejected before it reaches the query, not sanitized inside a
> dynamically-built one. This is a security boundary, not a naming
> convention — do not relax it to accommodate an event/attribute name that
> doesn't fit; rename the event instead.

---

## `value_model.cost.baseline`

The target economics and the tolerance the actual/projected comparison is
judged against.

| Field | Type | Bounds | Applies to |
|---|---|---|---|
| `target_cost_per_successful_interaction_usd` | float | `> 0`; USD per successful interaction (as defined by `success_event`); no default | — |
| `max_forecast_variance_pct` | float | fractional, `[0, 1]` (`0.20` = 20%); no default; values `> 1` are invalid | **cost** variance only |
| `max_token_volume_variance_pct` | float | fractional, `[0, 1]` (`0.20` = 20%); no default; values `> 1` are invalid | **token volume** variance only |

> **`target_cost_per_successful_interaction_usd` is an explicit USD amount
> per successful interaction, never a percentage or a rate.** It is the
> target cost — in US dollars — of one interaction that satisfies
> `success_event`, and it must be strictly greater than `0`. There is no
> default: an operator who cannot state this number yet leaves the field
> blank and records it in § 13 Open Questions, exactly like every other
> § 14 leaf.

> **`max_forecast_variance_pct` and `max_token_volume_variance_pct` are
> fractional floats, not percentage integers, and have no default.**
> `0.20` means 20%; both fields are bounded to `[0, 1]` inclusive. A value
> greater than `1` — for example `20`, written as if "20" meant "20
> percent" — is **invalid** and must be rejected, not silently
> reinterpreted or divided by 100. When the field is unanswered it stays
> blank; there is no fallback value.

> **Cost variance and token-volume variance are distinct, independently
> tunable thresholds** — they are not the same number wearing two names, and
> one is never derived from the other. A pilot's cost can drift within
> tolerance while its token volume drifts outside tolerance (e.g. a model
> price cut absorbs a token-count regression), or the reverse. Set each
> field to the tolerance appropriate for *that specific measurement*; do not
> copy one value into both fields as a shortcut.

---

## `value_model.cost.accounting`

Declares how actual and forecast cost are measured and scoped, and how a
basis mismatch between them is handled.

| Field | Type | Allowed values |
|---|---|---|
| `actual_cost_basis` | string (literal) | `usage-pretax` |
| `actual_billing_price_basis` | string | `retail` \| `ea` \| `mca` \| `unknown` |
| `forecast_price_basis` | string | `retail` \| `ea` \| `mca` |
| `allow_basis_mismatch_for_verdict` | bool | `true` \| `false` |
| `scope_policy` | string | `dedicated_resource_group` \| `tagged_allocation` |

> **`actual_cost_basis: usage-pretax` is a metric/source declaration, not a
> price basis.** It states *what is being measured* — pre-tax, usage-derived
> actual spend, as returned by the Cost Management Query API — and is the
> only literal value this schema fixes. It answers a different question
> than `actual_billing_price_basis`/`forecast_price_basis`, which state
> *which negotiated price list* (retail / Enterprise Agreement / Microsoft
> Customer Agreement) produced the numbers. Do not conflate the two: a
> value can be `usage-pretax` under any price basis.

> **Why `actual_billing_price_basis` must be an explicit operator
> declaration, not something the checker or pipeline derives**: the Cost
> Management Query API returns actual billed amounts without indicating
> which price basis (retail, EA, MCA) generated them — that information
> does not travel with the API response. The operator who owns the billing
> relationship is the only party who can state it accurately, so this field
> is always a declared decision, never a computed default. `unknown` is a
> legitimate, conservative choice for an operator who genuinely does not
> know yet — but **`unknown` is always treated as a basis mismatch** against
> `forecast_price_basis`, exactly like a declared-but-different basis, unless
> `allow_basis_mismatch_for_verdict: true` is also set. There is no silent
> "assume retail" fallback.

---

## Summary: no invented values, ever

1. Every numeric/decision field in `references/speckit-template.md` § 14 ships
   **blank** (a bare `key:` with, at most, a bounds/type comment).
2. **Comments are not values.** A bounds comment documents the field; it does
   not populate it, and no downstream code should read the comment text as a
   value.
3. **Full mode** asks the operator for each field; an unanswerable field
   stays blank and is recorded in SPEC § 13 Open Questions with
   `Source: open-question`.
4. **Fast-PoC mode** emits the shape verbatim, leaves every leaf blank, and
   leaves the source open in § 13 — this section has no silent default,
   unlike some other SPEC sections.
5. An incomplete `maturity_policy` produces a `not-verified` verdict, **not**
   a missing artifact — projection and actuals evidence is still generated
   and written regardless of verdict.
6. Projection attribution coverage (`min_projection_attribution_coverage_pct`)
   and actual resource-id coverage are different measurements; only
   projection coverage is policy-gated here.
7. Cost variance (`max_forecast_variance_pct`) and token-volume variance
   (`max_token_volume_variance_pct`) are separate thresholds — never merge
   or derive one from the other. Both are **fractional floats in `[0, 1]`**
   (`0.20` = 20%), never a bare percentage integer; a value `> 1` is invalid.
8. `success_event` identifiers are restricted to
   `^[A-Za-z][A-Za-z0-9_.:-]{0,127}$` because the reconciliation code compiles
   them into a fixed AppTraces KQL query — it never builds arbitrary KQL from
   operator input. `success_event` is always **operator-confirmed** in Full
   mode and blank/open-question in Fast-PoC — never derived or invented.
9. `actual_billing_price_basis` is always an explicit operator declaration
   (the Query API cannot derive it); `unknown` is a valid choice and is
   always a basis mismatch unless `allow_basis_mismatch_for_verdict: true`.
10. `target_cost_per_successful_interaction_usd` is an explicit USD amount
    per successful interaction, strictly `> 0`, with no default.
