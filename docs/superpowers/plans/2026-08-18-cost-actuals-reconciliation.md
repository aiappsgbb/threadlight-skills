# Cost Actuals Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Threadlight's complete Azure cost projection while adding
read-only post-deploy actuals, forecast reconciliation, and complete-Azure cost
per successful interaction.

**Architecture:** Keep `specs/cost-manifest.json` strict-v1 and unchanged.
`threadlight-consumption-iq` emits separate actuals and reconciliation
manifests from Cost Management, Azure Monitor metrics, Log Analytics workspace
interaction logs, and SPEC section 14
policy. `threadlight-production-ready` assesses those artifacts instead of
reimplementing live cost math. Every new path is opt-in and fail-closed.

**Tech Stack:** Python 3.13 stdlib, pytest, Azure CLI (`az rest`,
`az monitor metrics list`, `az monitor log-analytics query`), Cost Management
Query REST API `2025-03-01`, Markdown/JSON contracts.

**Approved design:** `docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`

---

## Contents

- [Delivery model](#delivery-model)
- [File map](#file-map)
- [PR 1: Land the approved RFC and plan](#pr-1-land-the-approved-rfc-and-plan)
- [PR 2: Add SPEC section 14 `value_model`](#pr-2-add-spec-section-14-value_model)
- [PR 3: Add pure actuals and reconciliation core](#pr-3-add-pure-actuals-and-reconciliation-core)
- [PR 4: Wire the read-only live CLI](#pr-4-wire-the-read-only-live-cli)
- [PR 5: Consume reconciliation in production-ready and auto](#pr-5-consume-reconciliation-in-production-ready-and-auto)
- [Final acceptance checklist](#final-acceptance-checklist)

### Task index

| Task | PR | Subject |
|---:|---:|---|
| 1 | 1 | Validate and publish the design-only change |
| 2 | 2 | Make section 14 a generated artifact contract (opt-in enforcement) |
| 3 | 2 | Add the canonical value-model schema without defaults |
| 4 | 2 | Final validation, changelog, and design-only E2E |
| 5 | 3 | Parse section 14 into a policy/errors result |
| 6 | 3 | Parse Cost Management evidence and validate the daily window |
| 7 | 3 | Add safe workspace-interaction and token evidence parsers |
| 8 | 3 | Implement fail-closed reconciliation |
| 9 | 4 | Build read-only Azure source adapters with injected runners |
| 10 | 4 | Emit latest and immutable history atomically |
| 11 | 4 | Add CLI commands without changing `run --all` |
| 12 | 4 | Perform the read-only live shape probe |
| 13 | 5 | Make `COST-102` and `COST-103` real artifact checks |
| 14 | 5 | Read actual cost per interaction in the KPI scorecard |
| 15 | 5 | Keep auto advisory and non-blocking |
| 16 | 5 | Run regression gates and close PR 5 |

---

## Delivery model

Do **not** leave a dependent PR stack open. The operator asked whether PRs are
closed; optimize for one review surface at a time:

| PR | Scope | Merge gate |
|---|---|---|
| 1 | RFC + this plan | Docs review; no runtime change |
| 2 | SPEC section 14 `value_model` contract | Unit tests + real `design-only` E2E |
| 3 | Schemas + pure parsers/reconciler | Offline fixtures; existing forecast golden byte-equivalent |
| 4 | Consumption IQ live CLI and emitters | Offline CLI tests + read-only live shape probe |
| 5 | Production-ready and auto consumers | Targeted suites + full E2E remains green/advisory |

For each PR: create from current `origin/main`, run the listed gates, open the
PR, wait for CI, squash-merge, verify it is closed, fetch `main`, then start the
next. Do not open PR N+1 until PR N is merged.

## File map

### PR 2 - value model

- Modify `skills/threadlight-design/references/speckit-template.md` - canonical
  section 14 skeleton with no numeric defaults.
- Create `skills/threadlight-design/references/value-model-schema.md` - field
  semantics and restricted identifier grammar.
- Modify `skills/threadlight-design/SKILL.md` - generation and review rules.
- Modify `scripts/ci/check_pilot_contract.py` - require section 14 shape, not
  threshold values, behind an opt-in `--require-value-model` flag.
- Modify `scripts/ci/tests/test_pilot_contract.py` - fail-first contract tests.
- Modify `examples/returns-triage-governed/specs/SPEC.md` - explicit
  workload-owned policy for the golden example.
- Modify `CHANGELOG.md`.

### PR 3 - pure evidence core

- Create `skills/threadlight-consumption-iq/references/cost-actuals-manifest-schema.md`.
- Create `skills/threadlight-consumption-iq/references/cost-reconciliation-manifest-schema.md`.
- Create `skills/threadlight-consumption-iq/scripts/value_model.py` - stdlib
  section 14 parser and validation.
- Create `skills/threadlight-consumption-iq/scripts/cost_actuals.py` - pure
  Cost Management and trace-result parsing plus manifest construction.
- Create `skills/threadlight-consumption-iq/scripts/reconcile.py` - maturity,
  window normalization, resource matching, and unit economics.
- Create `skills/threadlight-consumption-iq/scripts/token_evidence.py` - shared
  Azure Monitor token payload parser.
- Modify `skills/threadlight-router-bench/scripts/metrics.py` - delegate pure
  parsing to `token_evidence.py`; retain benchmark fetch orchestration.
- Create `skills/threadlight-consumption-iq/tests/test_value_model.py`.
- Create `skills/threadlight-consumption-iq/tests/test_cost_actuals.py`.
- Create `skills/threadlight-consumption-iq/tests/test_reconcile.py`.
- Create sanitized fixtures under
  `skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals/`.
- Modify `skills/threadlight-router-bench/tests/test_cost.py` or its metric
  test file to pin backward compatibility.
- Modify `CHANGELOG.md`.

### PR 4 - live CLI

- Modify `skills/threadlight-consumption-iq/scripts/consumption_iq.py` - add
  `actuals`, `reconcile`, and opt-in `run --all --with-actuals`.
- Create `skills/threadlight-consumption-iq/scripts/actuals_sources.py` -
  injected subprocess runners and read-only Azure query builders.
- Create `skills/threadlight-consumption-iq/scripts/reconciliation_emitter.py` -
  canonical/latest and immutable-history writes plus Markdown report.
- Create `skills/threadlight-consumption-iq/tests/test_actuals_sources.py`.
- Create `skills/threadlight-consumption-iq/tests/test_cli_actuals.py`.
- Create `skills/threadlight-consumption-iq/tests/test_reconciliation_emitter.py`.
- Modify `skills/threadlight-consumption-iq/SKILL.md` and schema references.
- Modify `CHANGELOG.md`.

### PR 5 - consumers

- Modify `skills/threadlight-production-ready/scripts/production_ready.py` -
  consume reconciliation for `COST-102`, `COST-103`, and KPI scorecard.
- Create `skills/threadlight-production-ready/tests/test_cost_reconciliation.py`.
- Modify `skills/threadlight-production-ready/tests/test_kpi_scorecard.py`.
- Modify `skills/threadlight-production-ready/SKILL.md`.
- Create `skills/threadlight-auto/tests/test_cost_actuals_guidance.py` - pin
  opt-in and advisory instructions without changing the state machine.
- Modify `skills/threadlight-auto/SKILL.md`.
- Modify `CHANGELOG.md`.

---

## PR 1: Land the approved RFC and plan

### Task 1: Validate and publish the design-only change

**Files:**
- Existing: `docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
- Create: `docs/superpowers/plans/2026-08-18-cost-actuals-reconciliation.md`

- [ ] **Step 1: Scan both documents for unresolved design text**

Run:

```bash
python3 - <<'PY'
from pathlib import Path

patterns = (
    "T" + "BD",
    "TO" + "DO",
    "FIX" + "ME",
    "NEEDS " + "CLARIFICATION",
    "<place" + "holder>",
    "to be " + "decided",
    "open " + "question",
)
paths = (
    Path("docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md"),
    Path("docs/superpowers/plans/2026-08-18-cost-actuals-reconciliation.md"),
)
hits = [
    f"{path}: {pattern}"
    for path in paths
    for pattern in patterns
    if pattern.casefold() in path.read_text(encoding="utf-8").casefold()
]
assert not hits, "\n".join(hits)
PY
```

Expected: no matches. Text inside code that intentionally demonstrates an
empty field must use comments, not placeholder tokens.

- [ ] **Step 2: Verify documentation-only scope**

Run:

```bash
git diff --check
git diff --name-only origin/main...HEAD
```

Expected: only the RFC and plan files.

- [ ] **Step 3: Commit the final plan**

```bash
git add \
  docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md \
  docs/superpowers/plans/2026-08-18-cost-actuals-reconciliation.md
git commit -m "docs: plan actuals-first cost reconciliation"
```

- [ ] **Step 4: Open, review, and close PR 1**

The PR body must state:

- the complete Azure projection remains unchanged;
- actuals are additive sidecars;
- Cost Management is the sole authoritative observed total;
- threshold policy has no defaults and lives in SPEC section 14;
- this PR changes no runtime behavior.

Expected: squash-merged PR and no open PR for this branch.

---

## PR 2: Add SPEC section 14 `value_model`

### Task 2: Make section 14 a generated artifact contract (opt-in enforcement)

This task lands the checker, retrofits every existing test that rewrites
`SPEC.md`, and updates the shipped golden example — all in one commit — so no
intermediate commit knowingly leaves `scripts/ci/tests/test_pilot_contract.py`
red. Splitting the checker change from the golden-example update would leave
`test_shipped_example_satisfies_the_contract()` failing between commits,
because the real example has no section 14 until this task adds one.

**Files:**
- Modify: `scripts/ci/tests/test_pilot_contract.py`
- Modify: `scripts/ci/check_pilot_contract.py`
- Modify: `examples/returns-triage-governed/specs/SPEC.md`
- Test: `scripts/ci/tests/test_pilot_contract.py`

- [ ] **Step 1: Add a reusable value-model fixture and failing section 14 tests**

Add a module-level constant so every test that needs a valid section 14 uses
the exact same text — no drifting copies:

```python
VALUE_MODEL_MARKERS = (
    "value_model:",
    "maturity_policy:",
    "success_event:",
    "baseline:",
    "accounting:",
)

VALUE_MODEL_BLOCK = (
    "## 14. Value Model\n\n"
    "```yaml\n"
    "value_model:\n"
    "  cost:\n"
    "    maturity_policy:\n"
    "      min_complete_days:\n"
    "      min_successful_interactions:\n"
    "      min_cost_settlement_age_hours:\n"
    "      max_window_end_age_days:\n"
    "      min_projection_attribution_coverage_pct:\n"
    "    success_event:\n"
    "      name:\n"
    "      trace_attribute:\n"
    "      success_values: []\n"
    "    baseline:\n"
    "      target_cost_per_successful_interaction_usd:\n"
    "      max_forecast_variance_pct:\n"
    "      max_token_volume_variance_pct:\n"
    "    accounting:\n"
    "      actual_cost_basis:\n"
    "      actual_billing_price_basis:\n"
    "      forecast_price_basis:\n"
    "      allow_basis_mismatch_for_verdict:\n"
    "      scope_policy:\n"
    "```\n"
)


def test_missing_spec_section_14_is_rejected_only_when_required(
    pilot: Path,
) -> None:
    # The `pilot` fixture (updated below) already carries exactly one
    # `VALUE_MODEL_BLOCK`. Do NOT append another copy here: with two `## 14.`
    # headings present, the corrected extractor in Step 3 stops only at a
    # heading whose number is *strictly greater* than 14, so a second `## 14.`
    # would not terminate the first one's extraction and this test would
    # start truncating the wrong thing. `spec_text.count(...) == 1` guards
    # that invariant before every mutation in this file.
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    spec.write_text(text.split("## 14.")[0], encoding="utf-8")
    assert "design.spec.no-section-14" in rules(
        check(pilot, require_value_model=True)
    )


def test_legacy_pilot_without_section_14_still_passes_by_default(
    pilot: Path,
) -> None:
    """Enforcement is opt-in: a pilot authored before this design must keep
    passing unchanged unless the caller explicitly asks for the new contract.
    """
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    spec.write_text(text.split("## 14.")[0], encoding="utf-8")
    assert "design.spec.no-section-14" not in rules(check(pilot))


@pytest.mark.parametrize("marker", VALUE_MODEL_MARKERS)
def test_section_14_requires_value_model_shape(pilot: Path, marker: str) -> None:
    # Same reasoning as above: appending a second `VALUE_MODEL_BLOCK` here
    # would leave an intact, unmodified copy of `marker` sitting right after
    # the mutated one, and the extractor would fold both into one section 14
    # body — silently backfilling the marker this test just removed and
    # defeating the assertion below without ever failing loudly.
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    spec.write_text(text.replace(marker, f"# removed {marker}", 1), encoding="utf-8")
    assert "design.spec.value-model-shape" in rules(check(pilot))


@pytest.mark.parametrize("marker", VALUE_MODEL_MARKERS)
def test_present_but_malformed_section_14_fails_without_the_flag(
    pilot: Path, marker: str
) -> None:
    """Opt-in applies to *absence* only. A pilot that ships a half-written
    section 14 is asserting the new contract and is validated by default."""
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    assert text.count("## 14. Value Model") == 1
    spec.write_text(text.replace(marker, f"# removed {marker}", 1), encoding="utf-8")
    assert "design.spec.value-model-shape" in rules(
        check(pilot, require_value_model=False)
    )


def test_section_14_does_not_require_numeric_defaults(pilot: Path) -> None:
    spec = pilot / "specs" / "SPEC.md"
    assert spec.read_text(encoding="utf-8").count("## 14. Value Model") == 1
    failures = check(pilot, require_value_model=True)
    assert "design.spec.value-model-shape" not in rules(failures)
```

`check(pilot, ...)` above is the existing test helper
(`scripts/ci/tests/test_pilot_contract.py`); give it a
`require_value_model: bool = False` keyword that it forwards, unchanged, to
`run_checks(pilot, ALL_STAGES, profile, target, require_value_model=...)`
(`scripts/ci/check_pilot_contract.py`) — `check()` never calls `check_design`
directly. `run_checks` itself gains the same
`require_value_model: bool = False` keyword and passes it straight through to
its own `check_design(pilot, profile, fail, require_value_model=...)` call.
Every existing call site keeps working untouched, which is the point of the
opt-in.

None of the tests above append a second `VALUE_MODEL_BLOCK`: the
shared `pilot` fixture (updated next) already carries exactly one copy, and
every retrofit below that appends its own copy does so only after slicing
away everything from `"## 13."` onward — which also discards the fixture's
already-appended block — so the written file still ends up with exactly one
`## 14. Value Model` heading. Where a retrofit below is shown only as prose
rather than full code, apply the same rule when implementing it: read the
current text, append `VALUE_MODEL_BLOCK` at most once, and assert
`text.count("## 14. Value Model") == 1` immediately before writing.

Update the `pilot` fixture (the hand-built, from-scratch fixture, not the
shipped example) to append `VALUE_MODEL_BLOCK` after its existing section 13
text, so it stays fully valid once section 14 becomes mandatory.

Then retrofit every existing test that rewrites `SPEC.md` and would otherwise
lose section 14 in the process, so the suite never goes red for a reason
unrelated to what each test is actually asserting:

- `test_missing_spec_section_13_is_rejected` — this is also the regression
  test for the extractor refactor in Step 3: append `VALUE_MODEL_BLOCK` after
  the truncated head so the case under test is "section 13 missing, section
  14 present," not "both missing." Assert `"design.spec.no-section-13" in
  rules(check(pilot))` continues to hold — i.e. the generic extractor must
  still surface `None` (not an empty string that happens to look falsy for
  the wrong reason) for a genuinely absent section 13 even when a sibling
  section 14 parses cleanly.
- `test_fast_poc_callout_needs_all_markers` — append `VALUE_MODEL_BLOCK` after
  the rebuilt `## 13.` body.
- `test_governed_profile_does_not_demand_the_fast_poc_callout` — append
  `VALUE_MODEL_BLOCK` after the rebuilt `## 13.` body. This is also the
  regression coverage for "section 14 validation runs for governed profiles
  too" (Step 3): the fixture must carry a fully valid section 14, or this
  `assert not check(pilot, profile="governed")` would start failing the
  moment the checker is implemented, for a reason unrelated to the fast-poc
  callout this test targets.
- `test_section_13_extraction_stops_at_section_14` — this test's fake
  `## 14. Appendix` heading now collides with the real, mandatory section 14.
  Insert the real `VALUE_MODEL_BLOCK` as section 14 and renumber the test's
  probe heading to `## 15. Appendix`, keeping its Fast-PoC-looking phrase
  there. The assertion is unchanged (`"design.spec.fast-poc-callout" in
  rules(check(pilot))`): a callout phrase outside section 13 — whether the
  next heading is numbered 14 or 15 — must not satisfy the section 13 check.
- `test_section_13_extraction_keeps_lettered_subsections` — append
  `VALUE_MODEL_BLOCK` after the `## 13b.` body so `assert not check(pilot)`
  (zero failures) still holds with section 14 mandatory.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest scripts/ci/tests/test_pilot_contract.py \
  -k 'section_14 or value_model' -v
```

Expected: failures because section 14 has no checker yet.

- [ ] **Step 3: Implement a generic top-level section extractor**

In `scripts/ci/check_pilot_contract.py`, replace section-specific slicing with
a generic extractor that preserves the existing `None`-on-missing contract —
`extract_section_13` already returns `str | None` and its caller already
tests `if section13 is None:`; the refactor must not change that caller to a
truthiness check, because an extracted section whose body happens to be
empty (blank line only) is a different, valid case from a genuinely absent
heading:

The existing implementation already applies an integer rule — it stops at a
later heading whose number is greater than 13 — but that rule is expressed
inline and hard-coded to section 13. The refactor **generalizes the existing
`> number` comparison to an arbitrary section number**; it is not repairing a
digit-count shortcut, because there is no digit-count shortcut to repair. The
generalized rule is stated once, precisely: "stop at the first later top-level
heading whose leading integer is strictly greater than `number`," evaluated by
parsing each later heading's number. Two consequences are worth pinning in
tests because they are easy to get wrong when generalizing: a lower-numbered
stray heading (`## 12.` inside section 13's body) is not a boundary, and a
lettered subsection (`## 13b.`) shares section 13's integer and is not a
boundary either.

```python
_TOP_LEVEL_HEADING = re.compile(r"^##[ \t]+(\d+)[.\w]*\.", re.MULTILINE)


def extract_section(spec_text: str, number: int) -> str | None:
    """Return the body of top-level SPEC section `number`, or None if absent.

    Scans every later top-level numbered heading (`## N.` or a lettered
    subsection such as `## 13b.`) in document order and stops at the first
    one whose leading integer is strictly greater than `number`. A heading
    with an equal or lower integer — even one that appears out of order,
    such as a stray `## 12.` inside section 13's body — does NOT stop the
    section: only a strictly greater number is a boundary. A lettered
    subsection such as `## 13b.` shares section 13's leading integer (13),
    so it is never a boundary either and stays inside the section.
    """
    start = re.search(
        rf"^##[ \t]+{number}\.[^\n]*$",
        spec_text,
        flags=re.MULTILINE,
    )
    if start is None:
        return None
    tail = spec_text[start.end():]
    for later in _TOP_LEVEL_HEADING.finditer(tail):
        if int(later.group(1)) > number:
            return tail[:later.start()]
    return tail


def extract_section_13(spec_text: str) -> str | None:
    return extract_section(spec_text, 13)
```

Add two direct unit tests for `extract_section` itself, alongside the
`check()`-level tests retrofitted above. These use ad hoc strings rather than
the `pilot` fixture specifically so they exercise the boundary rule in
isolation, without depending on (or colliding with) section 14 becoming
mandatory in the fixture:

```python
def test_extract_section_ignores_an_out_of_order_lower_numbered_heading() -> None:
    """A stray `## 12.` inside section 13's body must not truncate it — only
    a heading numbered strictly greater than 13 is a boundary."""
    text = (
        "## 13. Assumptions\n"
        "Body line one.\n\n"
        "## 12. Stray heading from a bad merge\n"
        "Still section 13's body.\n\n"
        "## 14. Next section\n"
        "Not part of section 13.\n"
    )
    section13 = mod.extract_section(text, 13)
    assert section13 is not None
    assert "## 12. Stray heading" in section13
    assert "Still section 13's body." in section13
    assert "Not part of section 13." not in section13


def test_extract_section_stops_at_the_next_strictly_greater_heading() -> None:
    text = (
        "## 13. Assumptions\n"
        "Body line one.\n\n"
        "## 14. Next section\n"
        "Not part of section 13.\n"
    )
    section13 = mod.extract_section(text, 13)
    assert section13 is not None
    assert "Body line one." in section13
    assert "Not part of section 13." not in section13
```

Add the section 14 check to `check_design` **before** the existing
`if profile != "fast-poc": return` early return, so governed profiles are
checked for section 14 too — only the Fast-PoC callout text check further
below is profile-gated, not the section 14 shape check. `check_design` gains a
`require_value_model: bool = False` keyword. `run_checks(pilot, stages,
profile, expected_target)` — the aggregator that already calls
`check_design(pilot, profile, fail)` — gains the identical
`require_value_model: bool = False` keyword on its own signature and forwards
it unchanged to `check_design(pilot, profile, fail, require_value_model=...)`.
That keyword is threaded from a new `--require-value-model` argparse flag on
`check_pilot_contract.py`, through `main()`'s call to `run_checks(...)`. The default
is `False` on purpose: an absent section 14 must stay valid for pilots
authored before this design (RFC §14.1). A **present** section 14 is always
shape-checked, flag or not:

```python
section13 = extract_section_13(spec_text)  # unchanged existing check, shown for ordering
if section13 is None:
    fail.add(
        "design.spec.no-section-13",
        "SPEC.md § 13 (Assumptions & Open Questions) not found — "
        "threadlight-design >= 1.7.0 must emit it",
    )
    return

section14 = extract_section(spec_text, 14)
if section14 is None:
    if require_value_model:
        fail.add(
            "design.spec.no-section-14",
            "SPEC.md section 14 Value Model is missing",
        )
else:
    required = (
        "value_model:",
        "maturity_policy:",
        "success_event:",
        "baseline:",
        "accounting:",
    )
    missing = [marker for marker in required if marker not in section14]
    if missing:
        fail.add(
            "design.spec.value-model-shape",
            "SPEC section 14 is missing: " + ", ".join(missing),
        )

if profile != "fast-poc":
    return
```

Document the flag in the script's `--help` text as the migration switch: it is
opt-in now, becomes the default in a later release once downstream pilots have
adopted section 14, and is deprecated after that. Do not change any existing
call site's behavior in this PR — the repository's own contract gate and the
design-only E2E are the two callers that opt in (Step 5, Task 4).

The section 13 `fail.add("design.spec.no-section-13", ...)` call above is the
existing, unchanged check already in `check_pilot_contract.py` — do not alter
its message text. It is shown here only for ordering: retain the existing
inline `fail.add("design.spec.no-section-13", ...)` block and its `return`
statement as-is, then insert the new section 14 block immediately after that
return and before the profile-gated early return (`if profile != "fast-poc": return`).

(The Fast-PoC callout text check that already follows the profile early
return is unchanged.) Do not validate numeric values here. Incomplete values
are a valid design state and become `not-verified` in Consumption IQ.

- [ ] **Step 4: Add section 14 to the shipped golden example**

Once Step 3 lands, `test_shipped_example_satisfies_the_contract()` runs the
checker with `require_value_model=True` against
`examples/returns-triage-governed`, so the shipped example must carry a real
section 14. That test's existing direct call to `run_checks` (it does not go
through the `check()` helper) becomes:

```python
def test_shipped_example_satisfies_the_contract() -> None:
    failures = mod.run_checks(
        EXAMPLE, ["design", "deploy"], "governed", "customer-pilot",
        require_value_model=True,
    )
    assert not failures, rules(failures)
```

Update that example's `SPEC.md` now, in this same task, with
values labeled as decisions for this example, not defaults:

```yaml
value_model:
  cost:
    maturity_policy:
      min_complete_days: 7
      min_successful_interactions: 100
      min_cost_settlement_age_hours: 48
      max_window_end_age_days: 14
      min_projection_attribution_coverage_pct: 0.95
    success_event:
      name: return_decision_completed
      trace_attribute: decision.outcome
      success_values: [approved, denied, escalated]
    baseline:
      target_cost_per_successful_interaction_usd: 0.18
      max_forecast_variance_pct: 0.20
      max_token_volume_variance_pct: 0.25
    accounting:
      actual_cost_basis: usage-pretax
      actual_billing_price_basis: retail
      forecast_price_basis: retail
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group
```

`actual_billing_price_basis: retail` is an explicit decision for this example
and is what makes its variance verdict `pass`-eligible: it equals
`forecast_price_basis`, so RFC §9.5's mismatch rule does not fire. A real EA or
MCA workload declares `ea`/`mca` here and accepts `not-verified` unless it also
sets `allow_basis_mismatch_for_verdict: true`.

- [ ] **Step 5: Run contract tests**

Run:

```bash
python -m pytest scripts/ci/tests/test_pilot_contract.py -q
python scripts/ci/check_pilot_contract.py \
  examples/returns-triage-governed \
  --stage design --stage deploy \
  --profile governed \
  --require-value-model \
  --expect-deployment-target customer-pilot
```

Expected: all tests pass — including the existing section 13 boundary tests,
`test_shipped_example_satisfies_the_contract()`, the legacy-default-pass test,
and the new section 14 tests — and the static contract check against the
shipped example passes with enforcement on.

- [ ] **Step 6: Commit the checker, tests, and golden example together**

```bash
git add \
  scripts/ci/check_pilot_contract.py \
  scripts/ci/tests/test_pilot_contract.py \
  examples/returns-triage-governed/specs/SPEC.md
git commit -m "test(design): require the SPEC value-model shape"
```

Committing all three together — rather than the checker first and the
example later — is what keeps the suite green at every commit boundary.

### Task 3: Add the canonical value-model schema without defaults

**Files:**
- Create: `skills/threadlight-design/references/value-model-schema.md`
- Modify: `skills/threadlight-design/references/speckit-template.md`
- Modify: `skills/threadlight-design/SKILL.md`
- Test: `skills/threadlight-design/tests/test_skill_contract_check.py`

- [ ] **Step 1: Add a failing template contract test**

Add:

```python
def test_speckit_template_declares_section_14_value_model() -> None:
    repo = Path(__file__).resolve().parents[3]
    template = (
        repo / "skills" / "threadlight-design" / "references" /
        "speckit-template.md"
    ).read_text(encoding="utf-8")
    assert "## 14. Value Model" in template
    for marker in (
        "value_model:",
        "maturity_policy:",
        "success_event:",
        "baseline:",
        "accounting:",
    ):
        assert marker in template
```

- [ ] **Step 2: Run and verify failure**

```bash
python -m pytest \
  skills/threadlight-design/tests/test_skill_contract_check.py::test_speckit_template_declares_section_14_value_model \
  -v
```

Expected: FAIL because section 14 is absent.

- [ ] **Step 3: Write `value-model-schema.md`**

The reference must contain this exact required shape:

```yaml
value_model:
  cost:
    maturity_policy:
      min_complete_days:                 # int >= 1; no default
      min_successful_interactions:       # int >= 1; no default
      min_cost_settlement_age_hours:     # int >= 0; no default
      max_window_end_age_days:           # int >= 1; no default
      min_projection_attribution_coverage_pct:  # float in (0, 1]; no default
    success_event:
      name:                              # restricted identifier
      trace_attribute:                   # restricted identifier
      success_values: []                 # >= 1 restricted identifier
    baseline:
      target_cost_per_successful_interaction_usd:  # float > 0
      max_forecast_variance_pct:         # float in [0, 1]; cost variance only
      max_token_volume_variance_pct:     # float in [0, 1]; token volume only
    accounting:
      actual_cost_basis: usage-pretax    # v1 literal; metric/source, not a price basis
      actual_billing_price_basis:        # retail | ea | mca | unknown
      forecast_price_basis: retail       # retail | ea | mca
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group  # or tagged_allocation
```

State explicitly:

- comments are not values;
- generation must not invent numeric values;
- identifier grammar is `^[A-Za-z][A-Za-z0-9_.:-]{0,127}$`;
- incomplete policy is allowed but produces `not-verified` **with artifacts
  still written** — an incomplete policy never suppresses evidence;
- `min_projection_attribution_coverage_pct` gates the *reconciliation* measure
  (actual cost mapped onto projected resources / total actual cost), not the
  actuals manifest's `cost.resource_id_coverage_pct` (actual rows carrying a
  nonblank resource ID / total cost). The two are different numbers and only
  the first is ever policy-gated;
- `max_forecast_variance_pct` bounds the **cost** variance verdict and
  `max_token_volume_variance_pct` bounds the **token volume** driver check;
  the token driver must never reuse the cost threshold;
- `usage-pretax` names the Cost Management Query API's
  `Usage`/`PreTaxCost` contract — it is the *metric and source* and must not be
  described as an invoice, and it is **not** a price basis;
- `actual_billing_price_basis` is the price basis of the actual charges and is
  the only field compared against `forecast_price_basis` (RFC §9.5). It is not
  derivable from the Query API response, so the operator declares it;
  `unknown` is permitted and is treated as a mismatch.

- [ ] **Step 4: Append section 14 to the speckit template**

Use the same shape, leaving numeric fields blank. Add prose requiring the
design agent to fill only values supplied or explicitly agreed by the
operator.

- [ ] **Step 5: Update `threadlight-design/SKILL.md`**

Add section 14 to:

- generated artifacts review;
- Fast-PoC behavior: emit the shape but leave unknown policy fields blank;
- Full mode: ask for or explicitly defer the policy;
- cross-cutting final review.

Do not lengthen the frontmatter description; it is already near the 1024-char
limit. Bump `metadata.version` and
`skills/threadlight-design/scripts/skill_contract_check.py::VERSION` together
from `1.11.0` to `1.12.0`; the existing version test requires equality.

- [ ] **Step 6: Run tests and description guard**

```bash
python -m pytest skills/threadlight-design/tests -q
python scripts/ci/check-skill-description-length.py
```

Expected: all pass.

- [ ] **Step 7: Commit schema and generation rules**

```bash
git add \
  skills/threadlight-design/references/value-model-schema.md \
  skills/threadlight-design/references/speckit-template.md \
  skills/threadlight-design/SKILL.md \
  skills/threadlight-design/scripts/skill_contract_check.py \
  skills/threadlight-design/tests/test_skill_contract_check.py
git commit -m "feat(design): add the SPEC value-model contract"
```

### Task 4: Final validation, changelog, and design-only E2E

The golden example already gained its section 14 value policy in Task 2 (folded
in there so the checker and the example land in the same commit and the suite
is never red). This task does not make the example valid for the first time —
it is the closing validation gate for PR 2: full-suite confirmation, the
changelog entry, and the real E2E dispatch before squash-merge.

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full static and unit-test surface**

```bash
python scripts/ci/check_pilot_contract.py \
  examples/returns-triage-governed \
  --stage design --stage deploy \
  --profile governed \
  --require-value-model \
  --expect-deployment-target customer-pilot
python -m pytest scripts/ci/tests -q
python -m pytest skills/threadlight-design/tests -q
```

Expected: contract OK with enforcement on; all CI-script and
threadlight-design tests pass, confirming Task 2's checker/example commit and
Task 3's schema/template commit are consistent with each other.

- [ ] **Step 2: Update changelog and commit**

```bash
git add CHANGELOG.md
git commit -m "docs: note SPEC section 14 value-model contract in changelog"
```

- [ ] **Step 3: Run a real design-only E2E**

Dispatch from the PR branch:

```bash
PR_BRANCH=feat/cost-value-model
gh workflow run threadlight-e2e-foundry.yml \
  --repo aiappsgbb/threadlight-skills \
  --ref "$PR_BRANCH" \
  -f mode=design-only \
  -f workload=returns-triage
```

The design-only E2E passes `--require-value-model` in its contract-check step
(update the workflow's checker invocation in this task), so the shipped
example proves the new contract end to end while third-party legacy pilots
stay unaffected.

Expected:

- Phase 1 and Phase 2 pass;
- contract checker passes section 14 with enforcement on;
- Phase 3+ skip;
- no Azure resource group is created.

Only after this run and PR CI are green, squash-merge PR 2.

---

## PR 3: Add pure actuals and reconciliation core

### Task 5: Parse section 14 into a policy/errors result

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/value_model.py`
- Create: `skills/threadlight-consumption-iq/tests/test_value_model.py`

- [ ] **Step 1: Write failing parser tests**

The parser **does not raise on bad policy content**. RFC §12 requires that an
incomplete or invalid policy still produce evidence: raw actuals must be
collected and written, and the reconciliation manifest must be emitted as
`not-verified`. A parser that raises makes that impossible, because the caller
never reaches the emit step. So the contract is a result object carrying both
whatever parsed cleanly and every validation error found:

```python
@dataclasses.dataclass(frozen=True)
class ValueModelResult:
    policy: dict[str, object]   # possibly partial; never None
    errors: list[str]           # empty means complete and valid
```

`errors` entries are human-readable strings that begin with the exact dotted
path, e.g. `"cost.maturity_policy.max_window_end_age_days is missing"`. The
only exception that escapes is a genuine file I/O failure in
`load_value_model` (unreadable/absent SPEC file), which is an environment
fault rather than a policy statement.

Use this complete fixture and test matrix:

````python
from pathlib import Path

import pytest

from value_model import ValueModelResult, load_value_model, parse_value_model


COMPLETE = """\
## 14. Value Model
```yaml
value_model:
  cost:
    maturity_policy:
      min_complete_days: 7
      min_successful_interactions: 100
      min_cost_settlement_age_hours: 48
      max_window_end_age_days: 14
      min_projection_attribution_coverage_pct: 0.95
    success_event:
      name: return_decision_completed
      trace_attribute: decision.outcome
      success_values: [approved, denied, escalated]
    baseline:
      target_cost_per_successful_interaction_usd: 0.18
      max_forecast_variance_pct: 0.20
      max_token_volume_variance_pct: 0.25
    accounting:
      actual_cost_basis: usage-pretax
      actual_billing_price_basis: retail
      forecast_price_basis: retail
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group
```
"""


def errors_for(text: str) -> list[str]:
    return parse_value_model(text).errors


def test_complete_policy_parses_with_no_errors() -> None:
    result = parse_value_model(COMPLETE)
    assert isinstance(result, ValueModelResult)
    assert result.errors == []
    assert result.policy["cost"]["maturity_policy"]["min_complete_days"] == 7
    assert result.policy["cost"]["success_event"]["success_values"] == [
        "approved", "denied", "escalated"
    ]
    assert result.policy["cost"]["baseline"]["max_token_volume_variance_pct"] == 0.25
    assert result.policy["cost"]["accounting"]["actual_billing_price_basis"] == "retail"


def test_missing_field_reports_exact_dotted_path_without_raising() -> None:
    partial = COMPLETE.replace("      max_window_end_age_days: 14\n", "")
    result = parse_value_model(partial)
    assert any(
        e.startswith("cost.maturity_policy.max_window_end_age_days")
        for e in result.errors
    )
    # Everything that *did* parse is still returned, so the caller can emit a
    # partially-populated policy snapshot alongside the not-verified verdict.
    assert result.policy["cost"]["maturity_policy"]["min_complete_days"] == 7


def test_numeric_comment_is_not_a_value() -> None:
    template = COMPLETE.replace(
        "      min_complete_days: 7",
        "      min_complete_days:  # int >= 1",
    )
    assert any(
        e.startswith("cost.maturity_policy.min_complete_days")
        for e in errors_for(template)
    )


@pytest.mark.parametrize("value", ["0", "-1"])
def test_zero_or_negative_threshold_is_rejected(value: str) -> None:
    invalid = COMPLETE.replace("min_complete_days: 7", f"min_complete_days: {value}")
    assert any("min_complete_days" in e for e in errors_for(invalid))


def test_coverage_must_be_at_most_one() -> None:
    invalid = COMPLETE.replace(
        "min_projection_attribution_coverage_pct: 0.95",
        "min_projection_attribution_coverage_pct: 1.01",
    )
    assert any(
        "min_projection_attribution_coverage_pct" in e for e in errors_for(invalid)
    )


def test_token_volume_variance_is_its_own_field() -> None:
    invalid = COMPLETE.replace("      max_token_volume_variance_pct: 0.25\n", "")
    result = parse_value_model(invalid)
    assert any(
        e.startswith("cost.baseline.max_token_volume_variance_pct")
        for e in result.errors
    )
    # The cost threshold must never be silently substituted for the token one.
    assert "max_token_volume_variance_pct" not in result.policy["cost"]["baseline"]


@pytest.mark.parametrize("basis", ["retail", "ea", "mca", "unknown"])
def test_actual_billing_price_basis_accepts_the_declared_enum(basis: str) -> None:
    text = COMPLETE.replace(
        "actual_billing_price_basis: retail",
        f"actual_billing_price_basis: {basis}",
    )
    assert errors_for(text) == []


def test_actual_billing_price_basis_rejects_a_metric_name() -> None:
    # `usage-pretax` is the metric/source (`actual_cost_basis`), never a price
    # basis. Accepting it here would silently make every EA/MCA workload look
    # like it matched a retail forecast.
    invalid = COMPLETE.replace(
        "actual_billing_price_basis: retail",
        "actual_billing_price_basis: usage-pretax",
    )
    assert any("actual_billing_price_basis" in e for e in errors_for(invalid))


def test_identifier_rejects_kql_fragment() -> None:
    attack = 'approved") | union AppRequests | where ("x" == "x'
    invalid = COMPLETE.replace(
        "success_values: [approved, denied, escalated]",
        f"success_values: [{attack}]",
    )
    errors = errors_for(invalid)
    assert any("success_event.success_values[0] invalid" in e for e in errors)


def test_success_values_must_be_nonempty() -> None:
    invalid = COMPLETE.replace(
        "success_values: [approved, denied, escalated]",
        "success_values: []",
    )
    assert any("success_values" in e for e in errors_for(invalid))


def test_section_14_boundary_stops_at_section_15() -> None:
    invalid = COMPLETE.replace("      scope_policy: dedicated_resource_group\n", "")
    invalid += "\n## 15. Appendix\nscope_policy: dedicated_resource_group\n"
    assert any("scope_policy" in e for e in errors_for(invalid))


def test_absent_section_14_is_an_error_not_an_exception() -> None:
    result = parse_value_model("## 13. Something else\n\nno policy here\n")
    assert result.policy == {}
    assert any("section 14" in e for e in result.errors)


def test_malformed_fence_is_an_error_not_an_exception() -> None:
    """A truncated/unterminated code fence is malformed *content*, so it
    behaves like any other invalid policy: errors, artifacts still emitted."""
    broken = COMPLETE.replace("```yaml", "```yam l").rstrip("\n").removesuffix("```")
    result = parse_value_model(broken)
    assert result.errors
    assert isinstance(result.policy, dict)


def test_load_value_model_propagates_io_failure(tmp_path: Path) -> None:
    """File I/O is the one failure that is *not* a policy statement."""
    with pytest.raises(OSError):
        load_value_model(tmp_path / "does-not-exist" / "SPEC.md")
````

The malicious identifier fixture must include:

```text
approved") | union AppRequests | where ("x" == "x
```

and expect an error string containing
`success_event.success_values[0] invalid`. Downstream, an unsafe identifier is
a validation error that causes the workspace interaction query to be **skipped**
(RFC §8.2) — never interpolated, and never a hard abort of collection.

- [ ] **Step 2: Run and verify import failure**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_value_model.py -v
```

Expected: FAIL because `value_model.py` does not exist.

- [ ] **Step 3: Implement the parser**

Public interface:

```python
@dataclasses.dataclass(frozen=True)
class ValueModelResult:
    policy: dict[str, object]
    errors: list[str]

    @property
    def is_complete(self) -> bool:
        return not self.errors


REQUIRED_PATHS = (
    "cost.maturity_policy.min_complete_days",
    "cost.maturity_policy.min_successful_interactions",
    "cost.maturity_policy.min_cost_settlement_age_hours",
    "cost.maturity_policy.max_window_end_age_days",
    "cost.maturity_policy.min_projection_attribution_coverage_pct",
    "cost.success_event.name",
    "cost.success_event.trace_attribute",
    "cost.success_event.success_values",
    "cost.baseline.target_cost_per_successful_interaction_usd",
    "cost.baseline.max_forecast_variance_pct",
    "cost.baseline.max_token_volume_variance_pct",
    "cost.accounting.actual_cost_basis",
    "cost.accounting.actual_billing_price_basis",
    "cost.accounting.forecast_price_basis",
    "cost.accounting.allow_basis_mismatch_for_verdict",
    "cost.accounting.scope_policy",
)

PRICE_BASES = ("retail", "ea", "mca", "unknown")


def parse_value_model(spec_text: str) -> ValueModelResult:
    """Return every value that parsed plus every validation error found.

    Never raises for missing, malformed, or unsafe policy content. Collects
    all errors rather than stopping at the first, so one run reports the whole
    policy gap instead of one field at a time.
    """


def load_value_model(spec_path: Path) -> ValueModelResult:
    # Only the read may raise; parsing never does.
    return parse_value_model(spec_path.read_text(encoding="utf-8"))
```

Validation rules that produce errors rather than exceptions: absent section
14; unterminated or unparsable fenced block; missing required path; comment
used where a value is required; non-positive integers and floats where
positive is required; `min_projection_attribution_coverage_pct` outside
`(0, 1]`; empty `success_values`; any identifier failing
`^[A-Za-z][A-Za-z0-9_.:-]{0,127}$`; `actual_billing_price_basis` or
`forecast_price_basis` outside their enums (note `usage-pretax` is a metric
name and is rejected as a price basis).

Follow the existing stdlib-only indentation parser pattern in
`load_profile_wizard.py`. Do not add PyYAML. Parse only the fixed four-level
schema, booleans, numbers, and inline string lists.

- [ ] **Step 4: Run tests**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_value_model.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add \
  skills/threadlight-consumption-iq/scripts/value_model.py \
  skills/threadlight-consumption-iq/tests/test_value_model.py
git commit -m "feat(consumption-iq): parse SPEC value policy"
```

### Task 6: Parse Cost Management evidence and validate the daily window

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/cost_actuals.py`
- Create: `skills/threadlight-consumption-iq/tests/test_cost_actuals.py`
- Create: `skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals/cost-query-page-1.json`
- Create: `skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals/cost-query-page-2.json`
- Create: `skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals/cost-query-costusd-alias.json`
- Create: `skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals/cost-query-aoai-account.json`
- Create: `skills/threadlight-consumption-iq/references/cost-actuals-manifest-schema.md`

- [ ] **Step 1: Write failing response parser tests**

The query is issued with `granularity: "Daily"` and groups by `UsageDate`
(Task 9), so every page carries a `UsageDate` column and the parser is the
component that proves the returned rows actually lie inside the requested
window. Use sanitized Query API responses shaped as:

```json
{
  "properties": {
    "columns": [
      {"name": "UsageDate", "type": "Number"},
      {"name": "ResourceType", "type": "String"},
      {"name": "PreTaxCost", "type": "Number"},
      {"name": "Currency", "type": "String"},
      {"name": "ResourceId", "type": "String"},
      {"name": "ServiceName", "type": "String"}
    ],
    "rows": [
      [20260801, "microsoft.app/containerapps", 12.5, "USD",
       "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pilot/providers/Microsoft.App/containerApps/agent",
       "Azure Container Apps"]
    ],
    "nextLink": null
  }
}
```

Use:

```python
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from cost_actuals import (
    ActualsEvidenceError,
    aggregate_cost_rows,
    build_actuals_manifest,
    rows_from_query_page,
)


COLUMNS = [
    {"name": "UsageDate", "type": "Number"},
    {"name": "ResourceType", "type": "String"},
    {"name": "PreTaxCost", "type": "Number"},
    {"name": "Currency", "type": "String"},
    {"name": "ResourceId", "type": "String"},
    {"name": "ServiceName", "type": "String"},
]
RID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/"
    "resourceGroups/rg-pilot/providers/Microsoft.App/containerApps/agent"
)
WINDOW = dict(
    start=datetime(2026, 8, 1, tzinfo=timezone.utc),
    end=datetime(2026, 8, 8, tzinfo=timezone.utc),
)


def page(rows, columns=None):
    return {
        "properties": {
            "columns": deepcopy(columns or COLUMNS),
            "rows": rows,
            "nextLink": None,
        }
    }


def aggregate(rows, columns=None, **overrides):
    kwargs = {**WINDOW, **overrides}
    return aggregate_cost_rows([page(rows, columns)], **kwargs)


def test_columns_are_mapped_by_name_not_position() -> None:
    rows = rows_from_query_page(
        page([[20260801, "microsoft.app/containerapps", 12.5, "USD", RID, "ACA"]])
    )
    assert rows[0]["pretaxcost"] == 12.5
    assert rows[0]["resourceid"] == RID
    assert rows[0]["usagedate"] == 20260801


def test_rows_for_same_resource_are_summed() -> None:
    result = aggregate([
        [20260801, "microsoft.app/containerapps", 12.5, "USD", RID, "ACA"],
        [20260802, "microsoft.app/containerapps", 2.5, "USD", RID, "ACA"],
    ])
    assert result.resources == [{
        "resource_id": RID,
        "resource_type": "microsoft.app/containerapps",
        "service_name": "ACA",
        "period_cost_usd": 15.0,
    }]
    assert (result.total_usd, result.currency, result.unattributed_usd) == (
        15.0, "USD", 0.0
    )
    assert result.cost_column == "PreTaxCost"


def test_blank_resource_id_remains_in_total_and_is_unattributed() -> None:
    result = aggregate([
        [20260801, "microsoft.app/containerapps", 12.5, "USD", RID, "ACA"],
        [20260801, "", 3.0, "USD", "", "Bandwidth"],
    ])
    assert sum(r["period_cost_usd"] for r in result.resources) == 12.5
    assert result.total_usd == 15.5
    assert result.unattributed_usd == 3.0


def test_mixed_currency_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="multiple currencies"):
        aggregate([
            [20260801, "x", 1.0, "USD", RID, "A"],
            [20260801, "x", 1.0, "EUR", RID, "A"],
        ])


def test_missing_cost_column_is_rejected() -> None:
    columns = [c for c in COLUMNS if c["name"] != "PreTaxCost"]
    with pytest.raises(ActualsEvidenceError, match="no cost column"):
        rows_from_query_page(page([[20260801, "x", "USD", RID, "A"]], columns))


@pytest.mark.parametrize("alias", ["CostUSD", "Cost", "cost", "costusd"])
def test_cost_column_aliases_are_accepted(alias: str) -> None:
    columns = [
        dict(c, name=alias) if c["name"] == "PreTaxCost" else c for c in COLUMNS
    ]
    result = aggregate(
        [[20260801, "x", 4.0, "USD", RID, "A"]], columns
    )
    assert result.total_usd == 4.0
    assert result.cost_column == alias


def test_primary_cost_column_wins_when_several_are_present() -> None:
    columns = COLUMNS + [{"name": "CostUSD", "type": "Number"}]
    result = aggregate(
        [[20260801, "x", 4.0, "USD", RID, "A", 999.0]], columns
    )
    assert result.cost_column == "PreTaxCost"
    assert result.total_usd == 4.0


def test_non_numeric_cost_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="cost value is not numeric"):
        aggregate([[20260801, "x", "free", "USD", RID, "A"]])


def test_malformed_row_is_rejected_not_dropped() -> None:
    with pytest.raises(ActualsEvidenceError, match="row does not match columns"):
        rows_from_query_page(page([[20260801, 1.0]]))


def test_negative_refund_is_retained() -> None:
    result = aggregate([
        [20260801, "x", 10.0, "USD", RID, "A"],
        [20260802, "x", -2.0, "USD", RID, "A"],
    ])
    assert result.total_usd == 8.0
    assert result.resources[0]["period_cost_usd"] == 8.0


def test_total_equals_resources_plus_unattributed() -> None:
    result = aggregate([
        [20260801, "x", 10.0, "USD", RID, "A"],
        [20260801, "", 4.0, "USD", "", "Tax"],
    ])
    assert result.total_usd == sum(
        r["period_cost_usd"] for r in result.resources
    ) + result.unattributed_usd


def test_resource_id_coverage_is_a_source_quality_measure() -> None:
    result = aggregate([
        [20260801, "x", 9.0, "USD", RID, "A"],
        [20260801, "", 1.0, "USD", "", "Tax"],
    ])
    # 9 of 10 USD carry a resource ID. This measures the *source*, and is not
    # the reconciliation's projection-attribution coverage (Task 8).
    assert result.resource_id_coverage_pct == pytest.approx(0.9)


def test_missing_usage_date_column_is_rejected() -> None:
    columns = [c for c in COLUMNS if c["name"] != "UsageDate"]
    with pytest.raises(ActualsEvidenceError, match="UsageDate column missing"):
        aggregate([["x", 1.0, "USD", RID, "A"]], columns)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (20260801, date(2026, 8, 1)),
        ("20260801", date(2026, 8, 1)),
        ("2026-08-01", date(2026, 8, 1)),
        ("2026-08-01T00:00:00Z", date(2026, 8, 1)),
    ],
)
def test_usage_date_is_normalized_from_every_observed_shape(raw, expected) -> None:
    result = aggregate([[raw, "x", 1.0, "USD", RID, "A"]])
    assert result.usage_dates == {expected}


def test_unparseable_usage_date_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="UsageDate is not a date"):
        aggregate([["last tuesday", "x", 1.0, "USD", RID, "A"]])


def test_row_before_window_start_is_rejected_not_dropped() -> None:
    with pytest.raises(ActualsEvidenceError, match="outside the requested window"):
        aggregate([[20260731, "x", 1.0, "USD", RID, "A"]])


def test_row_on_window_end_is_rejected_because_end_is_exclusive() -> None:
    # The request body sends `end` at UTC midnight; the Query API's own
    # inclusivity is not relied upon. The parser enforces
    # `start <= usage_date < end` itself, so a row dated 2026-08-08 in a
    # window ending 2026-08-08T00:00:00Z is a contract violation.
    with pytest.raises(ActualsEvidenceError, match="outside the requested window"):
        aggregate([[20260808, "x", 1.0, "USD", RID, "A"]])


def test_last_in_window_day_is_accepted() -> None:
    result = aggregate([[20260807, "x", 1.0, "USD", RID, "A"]])
    assert result.usage_dates == {date(2026, 8, 7)}


def test_non_positive_window_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="end must be after start"):
        aggregate(
            [[20260801, "x", 1.0, "USD", RID, "A"]],
            end=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )


def _manifest_kwargs(*, start, end, generated_at):
    return dict(
        scope={"subscription_id": "00000000-0000-0000-0000-000000000000"},
        start=start,
        end=end,
        generated_at=generated_at,
        cost_pages=[page([[20260801, "x", 10.0, "USD", RID, "A"]])],
        token_series=None,
        interaction_counts=None,
        provenance={"query_api_version": "2025-03-01"},
        warnings=[],
    )


def test_settlement_age_hours_is_generated_at_minus_window_end() -> None:
    manifest = build_actuals_manifest(
        **_manifest_kwargs(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
            generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
    )
    assert manifest["window"]["settlement_age_hours"] == 48
    assert manifest["window"]["window_end_age_days"] == 2
    assert manifest["window"]["complete_days"] == 7


def test_generated_at_before_window_end_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="generated_at.*before.*end"):
        build_actuals_manifest(
            **_manifest_kwargs(
                start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end=datetime(2026, 8, 8, tzinfo=timezone.utc),
                generated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
        )


def test_top_level_status_is_pass_without_optional_evidence() -> None:
    """RFC §7.2: top-level status is produced by Cost Management source,
    scope, and window alone. Absent token metrics and absent interaction
    counts are recorded as `not-verified` sub-statuses and never demote it."""
    manifest = build_actuals_manifest(
        **_manifest_kwargs(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 8, tzinfo=timezone.utc),
            generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
    )
    assert manifest["status"] == "pass"
    assert manifest["usage"]["interaction_status"] == "not-verified"
    assert manifest["usage"]["model_attribution_status"] == "not-verified"
    assert manifest["usage"]["total_interactions"] is None
    assert manifest["usage"]["successful_interactions"] is None
```


`build_actuals_manifest` is the only place `settlement_age_hours` and
`window_end_age_days` are computed; these two tests pin that computation
directly (rather than only through the higher-level reconciliation fixtures
in Task 8) and pin fail-closed behavior for a `generated_at` that precedes
`window.end`, which would otherwise silently produce a negative settlement
age.

- [ ] **Step 2: Run and verify failure**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_cost_actuals.py -v
```

Expected: FAIL because `cost_actuals.py` does not exist.

- [ ] **Step 3: Implement pure parsing**

Public interface:

```python
class ActualsEvidenceError(RuntimeError):
    pass


# Official v1 primary. `CostUSD` and `Cost` are accepted only as defensive
# compatibility with responses observed in the field; this is not a claim that
# any particular account type returns them.
COST_COLUMN_PRIORITY = ("pretaxcost", "costusd", "cost")


def select_cost_column(names: list[str]) -> str:
    """Return the casefolded name of the single cost column to use.

    Exactly one column is used. If several of the accepted names are present,
    the highest-priority one wins and the chosen name is recorded in the
    manifest as `cost.cost_column` so a reader can tell which contract the
    numbers came from. If none is present this is an error, never a zero.
    """
    for candidate in COST_COLUMN_PRIORITY:
        if candidate in names:
            return candidate
    raise ActualsEvidenceError(
        "Cost Management response has no cost column "
        f"(expected one of {', '.join(COST_COLUMN_PRIORITY)})"
    )


def normalize_usage_date(value: object) -> date:
    """Accept 20260801, "20260801", "2026-08-01", "2026-08-01T00:00:00Z"."""
    # Anything else raises ActualsEvidenceError("UsageDate is not a date: ...").


def rows_from_query_page(page: dict[str, object]) -> list[dict[str, object]]:
    props = page.get("properties")
    if not isinstance(props, dict):
        raise ActualsEvidenceError("Cost Management response has no properties")
    columns = props.get("columns")
    rows = props.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ActualsEvidenceError("Cost Management response has no columns/rows")
    if any(
        not isinstance(col, dict) or not isinstance(col.get("name"), str)
        for col in columns
    ):
        raise ActualsEvidenceError("Cost Management columns are malformed")
    names = [str(col["name"]).casefold() for col in columns]
    select_cost_column(names)  # raises when no accepted cost column is present
    if "usagedate" not in names:
        raise ActualsEvidenceError("UsageDate column missing")
    parsed = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            raise ActualsEvidenceError("Cost Management row does not match columns")
        parsed.append(
            {names[index]: value for index, value in enumerate(row)}
        )
    return parsed


class CostAggregate(NamedTuple):
    resources: list[dict[str, object]]
    total_usd: float
    currency: str
    unattributed_usd: float
    cost_column: str            # original-cased name actually used
    usage_dates: set[date]      # distinct in-window days observed
    resource_id_coverage_pct: float


def aggregate_cost_rows(
    pages: list[dict[str, object]],
    *,
    start: datetime,
    end: datetime,
) -> CostAggregate:
    """Aggregate paged Query API rows and validate the daily window.

    Raises ActualsEvidenceError when `end <= start`, when any row's UsageDate
    is unparseable, or when any row falls outside `start <= usage_date < end`.
    Out-of-window rows are never silently dropped: a response that disagrees
    with the request is a contract violation, and dropping rows would quietly
    understate the period total.
    """


def build_actuals_manifest(
    *,
    scope: dict[str, object],
    start: datetime,
    end: datetime,
    generated_at: datetime,
    cost_pages: list[dict[str, object]],
    token_series: list[dict[str, object]] | None,
    interaction_counts: tuple[int, int] | None,
    provenance: dict[str, object],
    warnings: list[str],
) -> dict[str, object]:
    """Build `threadlight-cost-actuals/v1` without issuing live calls."""
```

Normalize resource IDs with `.casefold().rstrip("/")`, but retain the original
ID for reporting. Sum with `decimal.Decimal` internally and round only at
manifest serialization.

Window rules (RFC §8.1):

- `complete_days = (end - start).days`, computed from the *requested* window,
  not from the number of days that happened to return rows — a day with no
  charges is still a complete day;
- `end <= start` is rejected;
- every row must satisfy `start.date() <= usage_date < end.date()`;
- the request body sends `end` at UTC midnight, and the parser enforces
  end-exclusivity itself rather than trusting the Query API's own boundary
  semantics. Document that assumption next to the check: the API is
  *documented* as inclusive of the period it returns, so the guard exists
  precisely so a boundary-semantics change surfaces as a loud failure instead
  of a silently inflated total.

Compute `settlement_age_hours` and `window_end_age_days` from
`generated_at - end`; the source does not claim an unobservable Cost
Management refresh timestamp. Reject with `ActualsEvidenceError` when
`generated_at` is before `end`: a negative settlement age is evidence of a
caller bug (a stale or misordered `generated_at`), not a value to silently
clamp to zero.

Status rules (RFC §7.2):

- top-level `status` is `pass` when the Cost Management source parsed, the
  scope is present, and the window validated. Nothing else can demote it;
- `usage.interaction_status` and `usage.model_attribution_status` are
  `pass` only when their evidence was actually collected; otherwise
  `not-verified` with `null` counts. Never write `0` for a count that was not
  observed — a real zero and an unobserved value must not be confusable;
- `cost.resource_id_coverage_pct` is source quality only and never gates
  anything.

- [ ] **Step 4: Write the actuals schema**

Pin:

- schema name `threadlight-cost-actuals/v1`;
- `status: pass | not-verified`, produced by Cost Management source, scope,
  and window only;
- `usage.interaction_status` and `usage.model_attribution_status`, each
  `pass | not-verified`;
- `basis: usage-pretax`, described as the metric and source, not a price basis
  and not an invoice;
- `cost.cost_column`, the column name actually used;
- start-inclusive/end-exclusive UTC window with `complete_days`;
- `period_total_usd`, resources, unattributed, and
  `cost.resource_id_coverage_pct` (actual rows carrying a nonblank resource ID
  divided by total cost) — explicitly *not* the reconciliation's
  `projection_attribution_coverage_pct`;
- optional model usage and interaction counts, `null` when not verified;
- source timestamps and exact API version;
- no unknown top-level keys.

Reference:
<https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage?view=rest-cost-management-2025-03-01>

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_cost_actuals.py -q
git add \
  skills/threadlight-consumption-iq/scripts/cost_actuals.py \
  skills/threadlight-consumption-iq/tests/test_cost_actuals.py \
  skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals \
  skills/threadlight-consumption-iq/references/cost-actuals-manifest-schema.md
git commit -m "feat(consumption-iq): parse observed Azure spend"
```

### Task 7: Add safe workspace-interaction and token evidence parsers

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/token_evidence.py`
- Modify: `skills/threadlight-consumption-iq/scripts/cost_actuals.py` — owns
  `build_success_kql` and `parse_interaction_counts`; the KQL text and the
  count parsing live here, next to the rest of the evidence parsing.
- Modify: `skills/threadlight-router-bench/scripts/metrics.py`
- Modify: `skills/threadlight-router-bench/tests/test_cost.py`
- Modify: `skills/threadlight-consumption-iq/tests/test_cost_actuals.py`

- [ ] **Step 1: Pin current router behavior in a failing delegation test**

Keep the existing public result:

```python
{
    "gpt-5.4": {"input": 7048336, "output": 111473},
    "gpt-5.5": {"input": 313389, "output": 13201},
}
```

Add tests for lowercase dimensions, missing totals, unknown metrics, and
multiple time series. Also assert that the richer operational parser preserves
`ModelDeploymentName` separately from `ModelName`, so a spillover deployment is
not merged into the primary deployment.

- [ ] **Step 2: Create the shared pure parser**

Move only pure payload parsing:

```python
INPUT_METRICS = frozenset({"inputtokens", "prompttokens"})
OUTPUT_METRICS = frozenset(
    {"outputtokens", "completiontokens", "generatedtokens"}
)


def parse_token_series(doc: dict[str, object]) -> list[dict[str, object]]:
    """Preserve deployment, model, token axes, and optional cached input."""


def parse_token_metrics(
    doc: dict[str, object],
) -> dict[str, dict[str, int]]:
    """Backward-compatible router-bench collapse by model."""
```

Keep `fetch_metrics` in router-bench. `token_evidence.py` lives in
`threadlight-consumption-iq` and is imported by `threadlight-router-bench`
because **this repository is the deployment unit**: the two skills are always
installed together from the same plugin, never independently versioned, so a
repository-relative import is a real dependency and not an assumption about
the user's environment. Keeping one source of truth matters more here than
skill-level isolation, since a divergent copy of the token parser would make
router-bench and consumption-iq disagree about the same Azure Monitor payload.

In `metrics.py`, import the sibling skill module by resolving the
repository-relative path once, and fail loudly and by name when it is absent:

```python
CONSUMPTION_SCRIPTS = (
    Path(__file__).resolve().parents[2] /
    "threadlight-consumption-iq" / "scripts"
)
if not (CONSUMPTION_SCRIPTS / "token_evidence.py").is_file():
    raise ImportError(
        "threadlight-router-bench requires the sibling skill "
        "threadlight-consumption-iq from the same plugin install; "
        f"token_evidence.py not found under {CONSUMPTION_SCRIPTS}"
    )
sys.path.insert(0, str(CONSUMPTION_SCRIPTS))
from token_evidence import parse_token_metrics  # noqa: E402


def parse_metrics(doc):
    return parse_token_metrics(doc)
```

Add a test in `skills/threadlight-router-bench/tests/test_cost.py` that proves
the failure is explicit rather than a confusing `ModuleNotFoundError` or a
silent fallback:

```python
def test_missing_sibling_skill_raises_a_named_error(monkeypatch, tmp_path) -> None:
    import importlib
    import sys

    import metrics

    monkeypatch.setattr(metrics, "CONSUMPTION_SCRIPTS", tmp_path / "absent")
    monkeypatch.delitem(sys.modules, "token_evidence", raising=False)
    with pytest.raises(ImportError, match="threadlight-consumption-iq"):
        importlib.reload(metrics)
```

Do not silently fall back to duplicate code. Missing cached-token metrics must
remain `None` in `parse_token_series`; never turn absence into a zero-percent
cache rate.

- [ ] **Step 3: Add safe KQL construction tests**

The transport is `az monitor log-analytics query --workspace <customerId>`
(Task 9), so the query runs against the **Log Analytics workspace schema**:
`AppTraces`, `TimeGenerated`, `Message`, `Properties`. The App Insights
resource-centric surface (`traces`, `timestamp`, `message`,
`customDimensions`) is a *different* query surface reached through the
Application Insights API, and must not be used here — those names simply do
not resolve in the workspace and the query fails.

In `test_cost_actuals.py`:

```python
def test_success_kql_targets_the_workspace_table_not_app_insights() -> None:
    query = build_success_kql(
        "2026-08-01T00:00:00Z",
        "2026-08-08T00:00:00Z",
        "return_decision_completed",
        "decision.outcome",
        ["approved", "denied", "escalated"],
    )
    assert query.startswith("AppTraces\n")
    assert "TimeGenerated >= datetime(2026-08-01T00:00:00Z)" in query
    assert "TimeGenerated < datetime(2026-08-08T00:00:00Z)" in query
    assert 'Message == "return_decision_completed"' in query
    assert 'Properties["decision.outcome"]' in query
    assert '"approved", "denied", "escalated"' in query
    assert "union" not in query.casefold()
    # The App Insights surface must never leak into a workspace query.
    for forbidden in ("traces\n", "timestamp", "customDimensions"):
        assert forbidden not in query


def test_success_kql_rejects_arbitrary_fragment() -> None:
    with pytest.raises(ActualsEvidenceError, match="identifier"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T00:00:00Z",
            'event") | union AppRequests',
            "decision.outcome",
            ["approved"],
        )


LOG_ANALYTICS_RESPONSE = {
    "tables": [
        {
            "name": "PrimaryResult",
            "columns": [
                {"name": "total_interactions", "type": "long"},
                {"name": "successful_interactions", "type": "long"},
            ],
            "rows": [[120, 113]],
        }
    ]
}


def test_interaction_counts_parse_the_apptraces_response_shape() -> None:
    """`az monitor log-analytics query` returns tables/columns/rows, not a
    list of dicts. Pin the real shape so the parser is not written against an
    imagined one."""
    assert parse_interaction_counts(LOG_ANALYTICS_RESPONSE) == (120, 113)


def test_interaction_counts_map_columns_by_name_not_position() -> None:
    swapped = deepcopy(LOG_ANALYTICS_RESPONSE)
    swapped["tables"][0]["columns"].reverse()
    swapped["tables"][0]["rows"] = [[113, 120]]
    assert parse_interaction_counts(swapped) == (120, 113)


def test_interaction_counts_accept_stringified_longs() -> None:
    stringy = deepcopy(LOG_ANALYTICS_RESPONSE)
    stringy["tables"][0]["rows"] = [["120", "113"]]
    assert parse_interaction_counts(stringy) == (120, 113)


def test_empty_result_set_is_zero_interactions_not_unverified() -> None:
    """An empty `summarize` result is a real observation of zero, distinct
    from never having run the query (which yields `None` counts and
    `interaction_status: not-verified` upstream)."""
    empty = deepcopy(LOG_ANALYTICS_RESPONSE)
    empty["tables"][0]["rows"] = []
    assert parse_interaction_counts(empty) == (0, 0)


def test_missing_expected_column_is_rejected() -> None:
    broken = deepcopy(LOG_ANALYTICS_RESPONSE)
    broken["tables"][0]["columns"] = [{"name": "total_interactions", "type": "long"}]
    broken["tables"][0]["rows"] = [[120]]
    with pytest.raises(ActualsEvidenceError, match="successful_interactions"):
        parse_interaction_counts(broken)
```

Implement in `cost_actuals.py`:

```python
_KQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


def build_success_kql(
    start_iso: str,
    end_iso: str,
    event_name: str,
    trace_attribute: str,
    success_values: list[str],
) -> str:
    """Build the fixed AppTraces query; reject every non-identifier input."""


def parse_interaction_counts(doc: object) -> tuple[int, int]:
    """Return total and successful counts from the workspace query response.

    Reads `tables[0].columns`/`rows`, mapping by column name. Raises
    ActualsEvidenceError when the response shape or expected columns are
    missing; returns (0, 0) only for a well-formed empty result set.
    """
```

The KQL shape is fixed:

```kusto
AppTraces
| where TimeGenerated >= datetime(2026-08-01T00:00:00Z)
    and TimeGenerated < datetime(2026-08-08T00:00:00Z)
| where Message == "return_decision_completed"
| extend outcome = tostring(Properties["decision.outcome"])
| summarize total_interactions=count(),
            successful_interactions=countif(
                outcome in ("approved", "denied", "escalated")
            )
```

Only validated identifiers are interpolated. Dates come from parsed
`datetime` values and are reserialized as ISO UTC. If the value model reported
a validation error for `success_event.name`, `success_event.trace_attribute`,
or any `success_values` entry, the caller **skips this query entirely** and
records `usage.interaction_status: not-verified` — the unsafe value is never
interpolated, and skipping the query never blocks cost collection.

- [ ] **Step 4: Run both skill suites**

```bash
python -m pytest skills/threadlight-consumption-iq/tests -q
python -m pytest skills/threadlight-router-bench/tests -q
```

Expected: both pass; router-bench outputs unchanged.

- [ ] **Step 5: Commit**

```bash
git add \
  skills/threadlight-consumption-iq/scripts/token_evidence.py \
  skills/threadlight-consumption-iq/scripts/cost_actuals.py \
  skills/threadlight-consumption-iq/tests/test_cost_actuals.py \
  skills/threadlight-router-bench/scripts/metrics.py \
  skills/threadlight-router-bench/tests
git commit -m "refactor(cost): share Azure Monitor token parsing"
```

### Task 8: Implement fail-closed reconciliation

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/reconcile.py`
- Create: `skills/threadlight-consumption-iq/tests/test_reconcile.py`
- Create: `skills/threadlight-consumption-iq/references/cost-reconciliation-manifest-schema.md`

- [ ] **Step 1: Write failing invariant tests**

Tests must cover:

```python
from copy import deepcopy

import pytest

from reconcile import reconcile_costs, sha256_json


RID = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/containerApps/a"
AOAI_ACCOUNT = (
    "/subscriptions/s/resourceGroups/rg/providers/"
    "Microsoft.CognitiveServices/accounts/aoai1"
)
AOAI_DEPLOYMENT = AOAI_ACCOUNT + "/deployments/chat"
GENERATED = "2026-08-10T00:00:00Z"
# A real 64-character SHA-256 hex digest. A placeholder here would fail the
# `policy_complete` anchor check and make every threshold-gated verdict
# `not-verified`, so the fixture could never exercise a `pass`.
SPEC_SHA256 = "a" * 64


def forecast(total=300.0):
    return {
        "schema_version": "1.0",
        "price_basis": "retail",
        "totals": {"monthly_cost_current_usd": total},
        "resources": [{
            "resource_id": RID,
            "resource_kind": "Microsoft.App/containerApps",
            "monthly_cost_usd": total,
        }],
        "recommendations": [],
    }


def actuals(total=70.0, successes=100):
    return {
        "schema": "threadlight-cost-actuals/v1",
        "status": "pass",
        "window": {
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-08T00:00:00Z",
            "complete_days": 7,
            "settlement_age_hours": 48,
            "window_end_age_days": 2,
        },
        "cost": {
            "basis": "usage-pretax",
            "cost_column": "PreTaxCost",
            "currency": "USD",
            "period_total_usd": total,
            "resources": [{
                "resource_id": RID,
                "resource_type": "Microsoft.App/containerApps",
                "service_name": "Azure Container Apps",
                "period_cost_usd": total,
            }],
            "unattributed_usd": 0.0,
            "resource_id_coverage_pct": 1.0,
        },
        "usage": {
            "interaction_status": "pass",
            "model_attribution_status": "not-verified",
            "total_interactions": successes + 5,
            "successful_interactions": successes,
            "models": [],
        },
    }


def policy():
    return {
        "cost": {
            "maturity_policy": {
                "min_complete_days": 7,
                "min_successful_interactions": 100,
                "min_cost_settlement_age_hours": 48,
                "max_window_end_age_days": 14,
                "min_projection_attribution_coverage_pct": 0.95,
            },
            "success_event": {
                "name": "return_decision_completed",
                "trace_attribute": "decision.outcome",
                "success_values": ["approved"],
            },
            "baseline": {
                "target_cost_per_successful_interaction_usd": 1.0,
                "max_forecast_variance_pct": 0.20,
                "max_token_volume_variance_pct": 0.25,
            },
            "accounting": {
                "actual_cost_basis": "usage-pretax",
                "actual_billing_price_basis": "retail",
                "forecast_price_basis": "retail",
                "allow_basis_mismatch_for_verdict": False,
                "scope_policy": "dedicated_resource_group",
            },
        }
    }


def run(f=None, a=None, p=None, errors=None):
    return reconcile_costs(
        f or forecast(),
        a or actuals(),
        p or policy(),
        policy_errors=errors or [],
        generated_at=GENERATED,
        policy_spec_sha256=SPEC_SHA256,
    )


def test_token_reprice_is_never_added_to_actual_total() -> None:
    a = actuals()
    a["usage"]["models"] = [{
        "model": "gpt-5.4",
        "input_tokens": 1000,
        "output_tokens": 100,
        "retail_repriced_cost_usd": 999.0,
    }]
    assert run(a=a)["totals"]["actual_window_usd"] == 70.0


def test_unmodeled_resource_of_a_different_type_remains_in_actual_total() -> None:
    """The unmatched actual resource must be a genuinely *different* ARM type,
    or the type fallback below would legitimately pair it with the forecast
    resource and this would stop testing unmodeled cost at all."""
    a = actuals()
    a["cost"]["resources"][0]["resource_id"] = (
        "/subscriptions/s/resourceGroups/rg/providers/"
        "Microsoft.Storage/storageAccounts/unmodeled"
    )
    a["cost"]["resources"][0]["resource_type"] = "Microsoft.Storage/storageAccounts"
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["coverage"]["unmodeled_actual_usd"] == 70.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.0


def test_unique_type_fallback_attributes_a_renamed_resource() -> None:
    """Exactly one unmatched forecast resource and exactly one unmatched
    actual resource share the normalized type, so the pairing is unambiguous
    and the fallback applies."""
    a = actuals()
    a["cost"]["resources"][0]["resource_id"] = RID + "-renamed"
    result = run(a=a)
    assert result["coverage"]["unmodeled_actual_usd"] == 0.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 1.0


def test_ambiguous_same_type_actuals_are_not_attributed() -> None:
    """Two unmatched actual resources share the forecast resource's type, so
    there is no unique pairing. Guessing one would silently attribute the
    wrong spend, so neither is attributed."""
    a = actuals()
    a["cost"]["resources"] = [
        {
            "resource_id": RID + "-one",
            "resource_type": "Microsoft.App/containerApps",
            "service_name": "Azure Container Apps",
            "period_cost_usd": 40.0,
        },
        {
            "resource_id": RID + "-two",
            "resource_type": "Microsoft.App/containerApps",
            "service_name": "Azure Container Apps",
            "period_cost_usd": 30.0,
        },
    ]
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["coverage"]["unmodeled_actual_usd"] == 70.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.0


def test_aoai_deployment_forecast_rolls_up_to_the_account_level_actual() -> None:
    """Cost Management bills AOAI at the account, while the forecast models
    per-deployment. Normalize the forecast ID to its parent account so the
    two meet (RFC §9.4)."""
    f = forecast()
    f["resources"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_cost_usd": 300.0,
    }]
    a = actuals()
    a["cost"]["resources"] = [{
        "resource_id": AOAI_ACCOUNT,
        "resource_type": "Microsoft.CognitiveServices/accounts",
        "service_name": "Azure OpenAI",
        "period_cost_usd": 70.0,
    }]
    result = run(f=f, a=a)
    assert result["coverage"]["unmodeled_actual_usd"] == 0.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 1.0
    matched = result["coverage"]["matched_resources"][0]
    assert matched["actual_resource_id"] == AOAI_ACCOUNT
    # Deployment detail is preserved for token diagnostics, not discarded.
    assert matched["forecast_deployment_ids"] == [AOAI_DEPLOYMENT]


def test_aoai_rollup_does_not_match_a_different_account() -> None:
    """Roll-up is scoped by account name. A deployment under `aoai1` must not
    absorb the bill for a second account `aoai2`."""
    f = forecast()
    f["resources"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_cost_usd": 300.0,
    }]
    a = actuals()
    a["cost"]["resources"] = [{
        "resource_id": AOAI_ACCOUNT.replace("aoai1", "aoai2"),
        "resource_type": "Microsoft.CognitiveServices/accounts",
        "service_name": "Azure OpenAI",
        "period_cost_usd": 70.0,
    }]
    result = run(f=f, a=a)
    assert result["coverage"]["unmodeled_actual_usd"] == 70.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.0


def test_unattributed_cost_reduces_projection_coverage_not_total() -> None:
    a = actuals()
    a["cost"]["unattributed_usd"] = 7.0
    a["cost"]["period_total_usd"] = 77.0
    a["cost"]["resource_id_coverage_pct"] = 70.0 / 77.0
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 77.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == pytest.approx(
        70.0 / 77.0
    )
    # The source measure is carried through unchanged, for diagnosis only.
    assert result["coverage"]["source_resource_id_coverage_pct"] == pytest.approx(
        70.0 / 77.0
    )


def test_full_resource_id_coverage_can_still_be_low_projection_coverage() -> None:
    """Every actual row carries a resource ID (source coverage 1.0), but half
    the spend is on a resource the forecast never projected. The two coverage
    measures are different numbers and only the projection one is gated."""
    a = actuals()
    a["cost"]["period_total_usd"] = 140.0
    a["cost"]["resources"].append({
        "resource_id": "/subscriptions/s/resourceGroups/rg/providers/"
                       "Microsoft.Storage/storageAccounts/unmodeled",
        "resource_type": "Microsoft.Storage/storageAccounts",
        "service_name": "Storage",
        "period_cost_usd": 70.0,
    })
    result = run(a=a)
    assert result["coverage"]["source_resource_id_coverage_pct"] == 1.0
    assert result["coverage"]["projection_attribution_coverage_pct"] == 0.5
    # And the maturity gate reads the projection measure, not the source one.
    assert result["maturity"]["status"] == "not-verified"


def test_window_and_monthly_normalization() -> None:
    result = run()
    assert result["totals"]["forecast_window_usd"] == 70.0
    assert result["totals"]["actual_monthly_run_rate_usd"] == 300.0
    assert result["totals"]["variance_pct"] == 0.0


def test_zero_forecast_yields_null_variance_pct() -> None:
    result = run(f=forecast(0.0))
    assert result["totals"]["variance_pct"] is None


def test_zero_successes_yields_not_verified_unit_economics() -> None:
    result = run(a=actuals(successes=0))
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] is None
    assert result["unit_economics"]["target_status"] == "not-verified"


def test_unverified_actuals_collection_yields_not_verified_unit_economics() -> None:
    """A `not-verified` Cost Management collection means there is no verified
    actual total to divide by successful_interactions, independent of policy
    completeness or how many successful interactions were observed. Token
    metrics (`usage.models`) are irrelevant to this gate either way."""
    a = actuals()
    a["status"] = "not-verified"
    result = run(a=a)
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] is None
    assert result["unit_economics"]["target_status"] == "not-verified"


def test_unit_economics_target_status_pass_when_within_baseline() -> None:
    """`total=70.0` over `successes=100` is 0.70/interaction; baseline target is 1.0."""
    result = run()
    assert result["unit_economics"]["status"] == "pass"
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] == 0.70
    assert result["unit_economics"]["target_status"] == "pass"


def test_unit_economics_target_status_should_fix_when_above_baseline() -> None:
    p = policy()
    p["cost"]["baseline"]["target_cost_per_successful_interaction_usd"] = 0.50
    result = run(p=p)
    assert result["unit_economics"]["status"] == "pass"
    assert result["unit_economics"]["target_status"] == "should-fix"


def test_incomplete_policy_yields_not_verified() -> None:
    p = policy()
    del p["cost"]["maturity_policy"]["min_complete_days"]
    result = run(p=p)
    assert result["maturity"]["status"] == "not-verified"
    # An incomplete declared policy leaves unit economics with no mature
    # policy to gate against either — it must not silently report `pass`
    # just because actuals are verified and successful_interactions > 0.
    assert result["unit_economics"]["status"] == "not-verified"


def test_policy_errors_still_emit_a_full_manifest() -> None:
    """RFC §12: an incomplete or invalid policy never suppresses evidence.
    `reconcile_costs` accepts the parser's error list and emits a
    `not-verified` manifest rather than refusing to produce one."""
    result = run(p={}, errors=["cost.maturity_policy.min_complete_days is missing"])
    assert result["schema"] == "threadlight-cost-reconciliation/v1"
    assert result["maturity"]["status"] == "not-verified"
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["policy_errors"] == [
        "cost.maturity_policy.min_complete_days is missing"
    ]
    # Observed evidence is still reported, because it was still observed.
    assert result["totals"]["actual_window_usd"] == 70.0


def test_unverified_interactions_block_unit_economics_but_not_cost_totals() -> None:
    """RFC §7.2/§9.3: a failed workspace query leaves the cost artifact fully
    valid, and only unit economics goes `not-verified`."""
    a = actuals()
    a["usage"]["interaction_status"] = "not-verified"
    a["usage"]["total_interactions"] = None
    a["usage"]["successful_interactions"] = None
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["variance_status"] == "pass"
    assert result["unit_economics"]["status"] == "not-verified"
    assert result["unit_economics"]["cost_per_successful_interaction_usd"] is None


def test_window_too_recent_to_settle_yields_not_verified() -> None:
    a = actuals()
    a["window"]["settlement_age_hours"] = 12
    assert run(a=a)["maturity"]["status"] == "not-verified"


def test_window_too_old_for_policy_yields_not_verified() -> None:
    a = actuals()
    a["window"]["window_end_age_days"] = 30
    assert run(a=a)["maturity"]["status"] == "not-verified"


def test_low_projection_attribution_coverage_yields_not_verified() -> None:
    a = actuals()
    a["cost"]["period_total_usd"] = 100.0
    a["cost"]["unattributed_usd"] = 30.0
    assert run(a=a)["maturity"]["status"] == "not-verified"


def test_price_basis_mismatch_reports_delta_without_verdict() -> None:
    """The comparison is `accounting.actual_billing_price_basis` against
    `forecast_price_basis`. `actual_cost_basis: usage-pretax` is the metric
    and source, and is never one side of this comparison."""
    p = policy()
    p["cost"]["accounting"]["actual_billing_price_basis"] = "ea"
    result = run(p=p)
    assert result["totals"]["variance_pct"] == 0.0
    assert result["variance_status"] == "not-verified"
    assert result["policy_snapshot"]["actual_billing_price_basis"] == "ea"


def test_unknown_actual_price_basis_is_treated_as_a_mismatch() -> None:
    p = policy()
    p["cost"]["accounting"]["actual_billing_price_basis"] = "unknown"
    assert run(p=p)["variance_status"] == "not-verified"


def test_matching_price_basis_permits_a_verdict() -> None:
    assert run()["variance_status"] == "pass"


def test_allow_basis_mismatch_restores_the_verdict() -> None:
    p = policy()
    p["cost"]["accounting"]["actual_billing_price_basis"] = "ea"
    p["cost"]["accounting"]["allow_basis_mismatch_for_verdict"] = True
    assert run(p=p)["variance_status"] == "pass"


def test_forecast_and_actual_hashes_are_recorded() -> None:
    f, a = forecast(), actuals()
    result = run(f=f, a=a)
    assert result["forecast_ref"]["sha256"] == sha256_json(f)
    assert result["actuals_ref"]["sha256"] == sha256_json(a)
    assert result["policy_ref"]["spec_sha256"] == SPEC_SHA256
    assert result["policy_snapshot"]["max_forecast_variance_pct"] == 0.20
    assert result["policy_snapshot"]["max_token_volume_variance_pct"] == 0.25
    assert result["policy_snapshot"]["min_projection_attribution_coverage_pct"] == 0.95


def test_reprojection_invalidates_the_reconciliation_by_design() -> None:
    """A re-projected forecast changes `forecast_ref.sha256`, so the previous
    reconciliation no longer describes the current forecast and must be
    treated as invalid. This is intentional: the fix is a cheap `reconcile`
    rerun over the *already collected* raw actuals, with no Azure calls and no
    re-collection, because the window and scope did not change."""
    a = actuals()
    first = run(f=forecast(), a=a)
    reprojected = forecast(360.0)
    second = run(f=reprojected, a=a)
    assert second["forecast_ref"]["sha256"] != first["forecast_ref"]["sha256"]
    # Same raw actuals: the actuals hash is unchanged, which is exactly what
    # lets the rerun skip collection.
    assert second["actuals_ref"]["sha256"] == first["actuals_ref"]["sha256"]
    assert second["totals"]["actual_window_usd"] == first["totals"]["actual_window_usd"]


def test_payg_ptu_driver_compares_observed_and_forecast_token_volume() -> None:
    f, a = forecast(), actuals()
    f["resources"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_units_consumed": {
            "input_tokens": 80000,
            "output_tokens": 10000,
        },
    }]
    f["recommendations"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "current_sku": {"tier": "PAYG"},
        "recommended_sku": {"tier": "PTU"},
    }]
    a["usage"]["model_attribution_status"] = "pass"
    a["usage"]["models"] = [{
        "deployment": "chat",
        "model": "gpt-5.4",
        "input_tokens": 19000,
        "output_tokens": 2000,
    }]
    assert run(f=f, a=a)["drivers"]["payg_ptu"]["status"] == "pass"


def test_payg_ptu_driver_uses_the_token_threshold_not_the_cost_threshold() -> None:
    """Observed volume lands 22% above forecast: outside the 20% *cost*
    tolerance but inside the declared 25% *token volume* tolerance. Reusing
    `max_forecast_variance_pct` here would wrongly report `should-fix`."""
    f, a = forecast(), actuals()
    f["resources"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_units_consumed": {"input_tokens": 90000, "output_tokens": 10000},
    }]
    f["recommendations"] = [{
        "resource_id": AOAI_DEPLOYMENT,
        "current_sku": {"tier": "PAYG"},
        "recommended_sku": {"tier": "PTU"},
    }]
    a["usage"]["model_attribution_status"] = "pass"
    a["usage"]["models"] = [{
        "deployment": "chat",
        "model": "gpt-5.4",
        "input_tokens": 25200,
        "output_tokens": 3266,
    }]
    driver = run(f=f, a=a)["drivers"]["payg_ptu"]
    assert driver["status"] == "pass"
    assert driver["threshold_field"] == "max_token_volume_variance_pct"


def test_payg_ptu_driver_is_not_verified_without_a_token_threshold() -> None:
    p = policy()
    del p["cost"]["baseline"]["max_token_volume_variance_pct"]
    assert run(p=p)["drivers"]["payg_ptu"]["status"] == "not-verified"


def test_payg_ptu_driver_is_not_verified_without_recommendation() -> None:
    assert run()["drivers"]["payg_ptu"]["status"] == "not-verified"
```

- [ ] **Step 2: Run and verify failure**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_reconcile.py -v
```

Expected: FAIL because `reconcile.py` does not exist.

- [ ] **Step 3: Implement pure calculations**

Public interface:

```python
def sha256_json(document: dict[str, object]) -> str:
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluate_maturity(
    actuals: dict[str, object],
    policy: dict[str, object],
) -> dict[str, object]:
    """Return every named check plus overall pass/not-verified."""


def reconcile_costs(
    forecast: dict[str, object],
    actuals: dict[str, object],
    policy: dict[str, object],
    *,
    policy_errors: list[str],
    generated_at: str,
    policy_spec_sha256: str,
) -> dict[str, object]:
    """Return `threadlight-cost-reconciliation/v1`.

    `policy` may be partial and `policy_errors` non-empty; this function never
    refuses to produce a manifest. Errors are copied to `policy_errors` in the
    output and force `maturity.status` and `unit_economics.status` to
    `not-verified`, but every observed number is still reported.
    """
```

Use `Decimal` for money. Do not accept a token-cost argument in
`reconcile_costs`; the type boundary itself prevents double counting.

`unit_economics` carries two independent fields, matching RFC §9.3/§7.3, and
both must be present in every emitted manifest:

- `status` (`pass | not-verified`) is an evidence/maturity signal: whether
  `cost_per_successful_interaction_usd` could be computed at all. It is
  `pass` only when **all four** hold:
  1. `actuals["status"] == "pass"` — a verified Cost Management total, not a
     `not-verified` collection;
  2. the SPEC-declared `maturity_policy` is complete — every field the
     policy schema requires (§8 of the RFC; the same completeness check
     `evaluate_maturity` already performs for `maturity.status`) is present
     and `policy_errors` is empty, independent of whether its thresholds are
     actually met;
  3. `actuals["usage"]["interaction_status"] == "pass"` — the denominator was
     actually observed, rather than absent because the workspace query was
     skipped or failed;
  4. `successful_interactions > 0` — a divide-by-zero guard, not a
     threshold comparison; division is never attempted at zero.

  It is `not-verified` if any of the four fails. Token metrics
  (`usage.models`, summarized by `usage.model_attribution_status`) are optional
  evidence for model-level attribution only (see RFC §7.3/§9.3) and are never
  part of this gate: missing or incomplete token metrics must never flip
  `status` away from `pass`.
- `target_status` (`pass | should-fix | not-verified`) is evaluated only when
  `status` is `pass`, and separately compares the computed cost against
  `policy.cost.baseline.target_cost_per_successful_interaction_usd`.
  It is `not-verified` whenever `status` is `not-verified` (there is nothing to
  compare), `pass` when at or under the target, and `should-fix` when over it.

Resource matching order:

1. **Exact ID.** Case-insensitive normalized resource ID, after AOAI
   normalization (below).
2. **AOAI parent roll-up.** A forecast resource whose ID is
   `.../Microsoft.CognitiveServices/accounts/<name>/deployments/<deployment>`
   normalizes to its parent `.../Microsoft.CognitiveServices/accounts/<name>`
   for matching, because Cost Management bills Azure OpenAI at the account
   level and reports the account-level ID and the
   `Microsoft.CognitiveServices/accounts` type. Roll-up is scoped by account
   name, so a deployment under one account can never match a different
   account. Multiple forecast deployments under the same account collapse into
   one matched account entry whose `forecast_window_usd` is their sum, and the
   original deployment IDs are preserved in
   `matched_resources[].forecast_deployment_ids` so token diagnostics and the
   PAYG/PTU driver keep per-deployment detail.
3. **Unique type fallback.** Normalized ARM resource type may pair a forecast
   resource with an actual resource **only when exactly one unmatched forecast
   resource and exactly one unmatched actual resource share that normalized
   type.** With two or more candidates on either side the pairing is
   ambiguous, and guessing would silently attribute spend to the wrong
   projected resource — so nothing is paired and the cost stays unmodeled.
4. Otherwise unmodeled/unattributed.

Coverage is two distinct measures and they must not be conflated (RFC §9.4):

- `coverage.source_resource_id_coverage_pct` is copied through from the
  actuals manifest's `cost.resource_id_coverage_pct`. It answers "did Cost
  Management tell us which resource each charge belongs to?" and is diagnostic
  only.
- `coverage.projection_attribution_coverage_pct` is computed here as *actual
  cost successfully mapped onto a projected forecast resource / total actual
  cost*. It answers "how much of the bill does our projection explain?" and is
  the **only** coverage number the maturity gate reads, against
  `min_projection_attribution_coverage_pct`.

A workload can have `source_resource_id_coverage_pct == 1.0` and a low
projection coverage at the same time — every charge is labeled, but the
forecast never modeled those resources.

Price basis (RFC §9.5): compare
`policy.cost.accounting.actual_billing_price_basis` with
`policy.cost.accounting.forecast_price_basis`. `actual_cost_basis`
(`usage-pretax`) is the metric and source and is never one side of this
comparison. When the two bases differ, or `actual_billing_price_basis` is
`unknown`, and `allow_basis_mismatch_for_verdict` is `false`, set
`variance_status: not-verified` while still reporting `variance_pct`.

For the experimental PAYG/PTU driver, do not invent new pricing:

```text
forecast_monthly_tokens =
    forecast AOAI monthly input tokens + output tokens

observed_monthly_tokens =
    observed window input tokens + output tokens, normalized to 30 days

observed_volume_variance_pct =
    (observed_monthly_tokens - forecast_monthly_tokens)
    / forecast_monthly_tokens
```

Emit `drivers.payg_ptu.status = pass` only when the forecast contains an
explicit PAYG-to-PTU or PTU-to-PAYG recommendation and observed token volume is
inside SPEC's declared `max_token_volume_variance_pct` — a token-volume
tolerance, deliberately separate from the cost tolerance
`max_forecast_variance_pct`, since a workload can absorb far more volume drift
than cost drift before its sizing recommendation stops holding. Record the
field used as `drivers.payg_ptu.threshold_field`. Outside the band, emit
`should-fix` with "rerun PAYG/PTU analysis at observed volume". Missing
recommendation, missing `max_token_volume_variance_pct`, zero forecast tokens,
or missing token metrics is `not-verified`. This checks whether the
recommendation's load assumption still matches reality; it does not call the
token reprice billed actual.

Forecast re-projection invalidation is intentional. `forecast_ref.sha256`
pins the exact forecast that was reconciled, so re-running the projection
invalidates the reconciliation by design rather than by accident. The remedy
is deliberately cheap: rerun `reconcile` only. Because the raw actuals are
canonical and window-scoped, the previously collected actuals are reused
verbatim, no Azure call is issued, and only the forecast side is recomputed.

- [ ] **Step 4: Write the reconciliation schema**

Pin the shape approved in the RFC, including:

- references and SHA-256 hashes;
- `policy_errors`, the parser's error list, empty when the policy is complete;
- maturity checks;
- forecast monthly/window totals;
- actual window/monthly run-rate totals;
- variance and `variance_status`;
- cost per successful interaction;
- both coverage measures under their distinct names
  (`projection_attribution_coverage_pct` and
  `source_resource_id_coverage_pct`) and drivers;
- immutable history semantics.

- [ ] **Step 5: Prove existing forecast output is unchanged**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_e2e.py -q
```

Expected: golden manifest and Markdown both pass without regeneration.

- [ ] **Step 6: Run PR 3 suites and commit**

```bash
python -m pytest skills/threadlight-consumption-iq/tests -q
python -m pytest skills/threadlight-router-bench/tests -q
git add \
  skills/threadlight-consumption-iq/scripts/reconcile.py \
  skills/threadlight-consumption-iq/tests/test_reconcile.py \
  skills/threadlight-consumption-iq/references/cost-reconciliation-manifest-schema.md
git commit -m "feat(consumption-iq): reconcile forecast with observed spend"
```

Before merging PR 3, run all skill test directories separately and
`scripts/ci/tests`; expected zero failures.

---

## PR 4: Wire the read-only live CLI

### Task 9: Build read-only Azure source adapters with injected runners

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/actuals_sources.py`
- Create: `skills/threadlight-consumption-iq/tests/test_actuals_sources.py`

- [ ] **Step 1: Write failing command-construction tests**

Tests must assert:

```python
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from actuals_sources import (
    ActualsSourceError,
    assert_azure_context,
    collect_sources,
    cost_query_body,
    fetch_cost_pages,
    fetch_interaction_result,
    resolve_workspace_customer_id,
)


WORKSPACE_RESOURCE_ID = (
    "/subscriptions/sub-1/resourceGroups/rg-pilot/providers/"
    "Microsoft.OperationalInsights/workspaces/law-pilot"
)


class FakeRunner:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return next(self.responses)


def result(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.fixture
def az_config_dir(monkeypatch, tmp_path: Path) -> str:
    """Isolated az state inside the test's own temp dir — never a shared,
    hardcoded path that could collide between concurrent runs or leak state
    across tests."""
    isolated = str(tmp_path / "isolated-az")
    monkeypatch.setenv("AZURE_CONFIG_DIR", isolated)
    return isolated


def test_cost_query_uses_custom_window_and_daily_grouping() -> None:
    body = cost_query_body(date(2026, 8, 1), date(2026, 8, 8))
    assert body["timePeriod"] == {
        "from": "2026-08-01T00:00:00Z",
        "to": "2026-08-08T00:00:00Z",
    }
    # Daily granularity is what makes the window verifiable downstream: the
    # parser can only check `start <= usage_date < end` if rows carry a date.
    assert body["dataset"]["granularity"] == "Daily"
    assert [g["name"] for g in body["dataset"]["grouping"]] == [
        "ResourceId", "ResourceType", "ServiceName"
    ]


def test_cost_query_uses_rg_scope_and_explicit_subscription(az_config_dir) -> None:
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        result(json.dumps({"properties": {"columns": [], "rows": [], "nextLink": None}})),
    ])
    fetch_cost_pages(
        "sub-1", "rg-pilot", date(2026, 8, 1), date(2026, 8, 8),
        runner=runner, sleep=lambda _: None,
    )
    command = runner.calls[1]
    url = command[command.index("--url") + 1]
    assert "/subscriptions/sub-1/resourceGroups/rg-pilot/" in url


def test_cost_query_follows_next_link(az_config_dir) -> None:
    first = {"properties": {
        "columns": [], "rows": [], "nextLink": "https://next.example/page-2"
    }}
    second = {"properties": {"columns": [], "rows": [], "nextLink": None}}
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        result(json.dumps(first)),
        result(json.dumps(second)),
    ])
    pages = fetch_cost_pages(
        "sub-1", "rg-pilot", date(2026, 8, 1), date(2026, 8, 8),
        runner=runner, sleep=lambda _: None,
    )
    assert pages == [first, second]
    assert "https://next.example/page-2" in runner.calls[2]


def test_429_uses_bounded_exponential_retry(az_config_dir) -> None:
    """`az rest` does not reliably surface response headers, so `Retry-After`
    is not consumed. Backoff is a fixed bounded schedule driven only by the
    observed 429 in stderr / non-zero return."""
    sleeps = []
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        result(stderr="(429) Too Many Requests", code=1),
        result(stderr="(429) Too Many Requests", code=1),
        result(json.dumps({"properties": {
            "columns": [], "rows": [], "nextLink": None
        }})),
    ])
    fetch_cost_pages(
        "sub-1", "rg-pilot", date(2026, 8, 1), date(2026, 8, 8),
        runner=runner, sleep=sleeps.append,
    )
    assert sleeps == [2, 4]


def test_retry_is_bounded_and_then_fails(az_config_dir) -> None:
    sleeps = []
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        *[result(stderr="(429) Too Many Requests", code=1) for _ in range(4)],
    ])
    with pytest.raises(ActualsSourceError, match="429"):
        fetch_cost_pages(
            "sub-1", "rg-pilot", date(2026, 8, 1), date(2026, 8, 8),
            runner=runner, sleep=sleeps.append,
        )
    assert sleeps == [2, 4, 8]


def test_other_az_failure_surfaces_stderr(az_config_dir) -> None:
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        result(stderr="Forbidden", code=1),
    ])
    with pytest.raises(ActualsSourceError, match="Forbidden"):
        fetch_cost_pages(
            "sub-1", "rg-pilot", date(2026, 8, 1), date(2026, 8, 8),
            runner=runner, sleep=lambda _: None,
        )


def test_missing_azure_config_dir_fails_before_az(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    runner = FakeRunner([])
    with pytest.raises(ActualsSourceError, match="AZURE_CONFIG_DIR"):
        assert_azure_context("sub-1", runner=runner)
    assert runner.calls == []


def test_active_subscription_must_match_requested_subscription(az_config_dir) -> None:
    runner = FakeRunner([
        result(json.dumps({"id": "sub-other", "tenantId": "tenant-1"}))
    ])
    with pytest.raises(ActualsSourceError, match="subscription mismatch"):
        assert_azure_context("sub-1", runner=runner)


def test_workspace_customer_id_is_resolved_from_the_arm_resource_id() -> None:
    """Callers supply the ARM resource ID they already know from deployment
    outputs; the GUID `customerId` the query API needs is resolved here, once,
    instead of being demanded from the operator."""
    runner = FakeRunner([result("11111111-2222-3333-4444-555555555555\n")])
    customer_id = resolve_workspace_customer_id(WORKSPACE_RESOURCE_ID, runner)
    assert customer_id == "11111111-2222-3333-4444-555555555555"
    assert runner.calls[0] == [
        "az", "monitor", "log-analytics", "workspace", "show",
        "--ids", WORKSPACE_RESOURCE_ID,
        "--query", "customerId",
        "-o", "tsv",
    ]


@pytest.mark.parametrize("stdout", ["", "   \n", "None\n", "not-a-guid\n"])
def test_blank_or_malformed_customer_id_degrades_to_none(stdout: str) -> None:
    runner = FakeRunner([result(stdout)])
    assert resolve_workspace_customer_id(WORKSPACE_RESOURCE_ID, runner) is None


def test_workspace_resolution_failure_degrades_to_none() -> None:
    runner = FakeRunner([result(stderr="ResourceNotFound", code=1)])
    assert resolve_workspace_customer_id(WORKSPACE_RESOURCE_ID, runner) is None


def test_interaction_query_uses_the_resolved_workspace_customer_id() -> None:
    response = {"tables": [{
        "name": "PrimaryResult",
        "columns": [{"name": "total_interactions", "type": "long"}],
        "rows": [[1]],
    }]}
    runner = FakeRunner([result(json.dumps(response))])
    assert fetch_interaction_result(
        "workspace-customer-id", "AppTraces | count", runner=runner
    ) == response
    assert "workspace-customer-id" in runner.calls[0]


def test_unresolvable_workspace_degrades_interactions_not_cost(az_config_dir) -> None:
    """A workspace that cannot be resolved is a warning on the interaction
    evidence only. Cost collection must complete and stay verified."""
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        result(json.dumps({"properties": {
            "columns": [], "rows": [], "nextLink": None
        }})),
        result(json.dumps({"value": []})),
        result(stderr="ResourceNotFound", code=1),
    ])
    bundle = collect_sources(
        subscription_id="sub-1",
        resource_group="rg-pilot",
        start=date(2026, 8, 1),
        end=date(2026, 8, 8),
        monitor_resource_id="/resource/model",
        workspace_resource_id=WORKSPACE_RESOURCE_ID,
        kql="AppTraces | count",
        runner=runner,
        sleep=lambda _: None,
    )
    assert len(bundle["cost_pages"]) == 1
    assert bundle["interaction_result"] is None
    assert any("workspace" in w for w in bundle["warnings"])


def test_monitoring_failure_does_not_erase_valid_cost_evidence(az_config_dir) -> None:
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        result(json.dumps({"properties": {
            "columns": [], "rows": [], "nextLink": None
        }})),
        result(stderr="metrics forbidden", code=1),
        result("11111111-2222-3333-4444-555555555555\n"),
        result(stderr="logs forbidden", code=1),
    ])
    bundle = collect_sources(
        subscription_id="sub-1",
        resource_group="rg-pilot",
        start=date(2026, 8, 1),
        end=date(2026, 8, 8),
        monitor_resource_id="/resource/model",
        workspace_resource_id=WORKSPACE_RESOURCE_ID,
        kql="AppTraces | count",
        runner=runner,
        sleep=lambda _: None,
    )
    assert len(bundle["cost_pages"]) == 1
    assert bundle["token_doc"] is None
    assert bundle["interaction_result"] is None
    assert len(bundle["warnings"]) == 2
```

- [ ] **Step 2: Implement the Cost Management body**

```python
COST_API_VERSION = "2025-03-01"


class ActualsSourceError(RuntimeError):
    pass


Runner = Callable[
    [list[str]],
    subprocess.CompletedProcess[str],
]


def cost_query_body(start: date, end: date) -> dict[str, object]:
    return {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T00:00:00Z",
        },
        "dataset": {
            "granularity": "Daily",
            "aggregation": {
                "totalCost": {
                    "name": "PreTaxCost",
                    "function": "Sum",
                }
            },
            "grouping": [
                {"type": "Dimension", "name": "ResourceId"},
                {"type": "Dimension", "name": "ResourceType"},
                {"type": "Dimension", "name": "ServiceName"},
            ],
        },
    }
```

`granularity: "Daily"` is what makes the window verifiable. The response then
carries a `UsageDate` column per row, and `cost_actuals.py` validates that
every returned day lies inside the requested half-open window and computes
`complete_days` (Task 6, RFC §8.1). With `"None"` the response is a single
undated aggregate and nothing downstream can prove the window was honored.

Document the assumption alongside the body: the Query API's `timePeriod` is
documented as covering the requested period, and the `to` bound is sent at UTC
midnight of the exclusive end day. Rather than depending on that boundary
semantic, the daily rows are validated locally as `start <= usage_date < end`,
so any change in API behavior surfaces as a loud parse failure instead of a
silently wrong total.

Query URL:

```python
scope = (
    f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
)
url = (
    "https://management.azure.com"
    f"{scope}/providers/Microsoft.CostManagement/query"
    f"?api-version={COST_API_VERSION}"
)
```

Execute with:

```python
["az", "rest", "--method", "post", "--url", url,
 "--body", json.dumps(body), "--output", "json"]
```

Pass subscription in the URL and first assert:

```python
["az", "account", "show", "--query", "{id:id,tenantId:tenantId}", "-o", "json"]
```

The parent process must already carry `AZURE_CONFIG_DIR`; absence is an error.
This collector is read-only, but tenant isolation remains mandatory.

- [ ] **Step 3: Implement bounded retry and pagination**

Retry only transient return codes/messages (429/5xx), detected from the
runner's non-zero return code and its stderr text, using bounded exponential
delays `2, 4, 8` seconds and then failing with `ActualsSourceError`.

Do **not** attempt to read `Retry-After`. The service does return that header,
but `az rest` and `az monitor ...` do not reliably surface response headers to
the caller — stdout carries the parsed body and stderr carries a rendered
error string, neither of which is a dependable header channel. Parsing a
header out of stderr text would be inventing a contract the CLI does not
offer, so the schedule is fixed and self-contained. If honoring the service's
own backoff hint becomes necessary, the correct change is to move this
collector to the Azure SDK, where the response headers are actually available;
that is a deliberate future option, not a gap to paper over here.

Inject both `runner` and `sleep` so tests never wait.

Follow `properties.nextLink` with the same POST body until null. Detect repeated
nextLink and stop with `ActualsSourceError`.

Public source interfaces:

```python
def assert_azure_context(
    subscription_id: str,
    *,
    runner: Runner,
) -> dict[str, str]:
    """Require isolated az state and the explicitly requested subscription."""


def fetch_cost_pages(
    subscription_id: str,
    resource_group: str,
    start: date,
    end: date,
    *,
    runner: Runner,
    sleep: Callable[[float], None],
) -> list[dict[str, object]]:
    """Fetch every Cost Management Query page or raise."""


def resolve_workspace_customer_id(
    resource_id: str,
    runner: Runner,
) -> str | None:
    """Resolve an ARM workspace resource ID to its query `customerId` GUID.

    Returns None — never raises — when the lookup fails or returns a blank,
    literal "None", or non-GUID value. A workspace that cannot be resolved
    degrades interaction evidence to a warning and `not-verified`; it never
    affects Cost Management collection.
    """


def fetch_interaction_result(
    workspace_resource_id: str,
    kql: str,
    *,
    runner: Runner,
) -> object:
    """Run `kql` against the workspace and return the parsed JSON document.

    Internally resolves `workspace_resource_id` to its query `customerId` via
    `resolve_workspace_customer_id` before issuing the query — callers never
    supply a GUID directly. Returns `None` — never raises — when resolution
    or the query itself fails; `collect_sources` turns that `None` into
    `bundle["interaction_result"] = None` plus a warning, and never lets it
    affect Cost Management collection.
    """


def collect_sources(
    *,
    subscription_id: str,
    resource_group: str,
    start: date,
    end: date,
    monitor_resource_id: str | None,
    workspace_resource_id: str | None,
    kql: str | None,
    runner: Runner,
    sleep: Callable[[float], None],
) -> dict[str, object]:
    """Require cost pages; degrade token/interaction evidence to warnings."""
```

`collect_sources` takes the **ARM workspace resource ID** and calls
`resolve_workspace_customer_id` internally. No caller — CLI, tests, or
downstream skill — ever supplies a `customerId` directly: the GUID is an
implementation detail of the Log Analytics query API, while the resource ID is
what deployment outputs and `az` already expose.

- [ ] **Step 4: Implement Monitor and interaction adapters**

Keep the existing `az monitor metrics list` dimensions and call the shared
parser. Resolve the workspace first:

```python
[
    "az", "monitor", "log-analytics", "workspace", "show",
    "--ids", workspace_resource_id,
    "--query", "customerId",
    "-o", "tsv",
]
```

then query it:

```python
[
    "az", "monitor", "log-analytics", "query",
    "--workspace", customer_id,
    "--analytics-query", safe_kql,
    "--output", "json",
]
```

Because this is the workspace query surface, `safe_kql` is the `AppTraces`
query built in Task 7 — never an App Insights `traces` query.

Cost Management failure prevents a verified actual total. Monitor, workspace
resolution, or interaction-query failure preserves valid cost evidence and adds
warnings.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_actuals_sources.py -q
git add \
  skills/threadlight-consumption-iq/scripts/actuals_sources.py \
  skills/threadlight-consumption-iq/tests/test_actuals_sources.py
git commit -m "feat(consumption-iq): collect read-only Azure cost evidence"
```

### Task 10: Emit latest and immutable history atomically

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/reconciliation_emitter.py`
- Create: `skills/threadlight-consumption-iq/tests/test_reconciliation_emitter.py`

- [ ] **Step 1: Write failing emitter tests**

Cover:

```python
import json

import pytest

import reconciliation_emitter as emitter


def documents(generated_at="2026-08-10T00:00:00Z"):
    actuals = {
        "schema": "threadlight-cost-actuals/v1",
        "generated_at": generated_at,
        "window": {
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-08T00:00:00Z",
        },
        "cost": {"period_total_usd": 70.0},
    }
    reconciliation = {
        "schema": "threadlight-cost-reconciliation/v1",
        "generated_at": generated_at,
        "status": "pass",
        "variance_status": "pass",
        "maturity": {"status": "pass", "checks": []},
        "totals": {
            "forecast_monthly_usd": 300.0,
            "actual_window_usd": 70.0,
            "actual_monthly_run_rate_usd": 300.0,
        },
        "unit_economics": {
            "status": "pass",
            "target_status": "pass",
            "cost_per_successful_interaction_usd": 0.70,
        },
    }
    return actuals, reconciliation


def emit(tmp_path, actuals=None, reconciliation=None):
    a, r = documents()
    emitter.emit_reconciliation(
        actuals=actuals or a,
        reconciliation=reconciliation or r,
        report_path=tmp_path / "docs" / "cost-reconciliation.md",
        actuals_path=tmp_path / "specs" / "cost-actuals-manifest.json",
        reconciliation_path=(
            tmp_path / "specs" / "cost-reconciliation-manifest.json"
        ),
        history_root=tmp_path / "specs" / "cost-history",
    )


def test_writes_canonical_and_timestamped_history(tmp_path) -> None:
    emit(tmp_path)
    canonical = tmp_path / "specs" / "cost-actuals-manifest.json"
    snapshot = (
        tmp_path / "specs" / "cost-history" /
        "2026-08-01--2026-08-08" / "2026-08-10T000000Z" / "actuals.json"
    )
    assert json.loads(canonical.read_text())["cost"]["period_total_usd"] == 70.0
    assert snapshot.is_file()


def test_same_window_new_collection_creates_new_snapshot(tmp_path) -> None:
    emit(tmp_path)
    actuals, reconciliation = documents("2026-08-11T00:00:00Z")
    emit(tmp_path, actuals, reconciliation)
    history = tmp_path / "specs" / "cost-history" / "2026-08-01--2026-08-08"
    assert sorted(p.name for p in history.iterdir()) == [
        "2026-08-10T000000Z", "2026-08-11T000000Z"
    ]


def test_refuses_to_overwrite_different_snapshot_payload(tmp_path) -> None:
    emit(tmp_path)
    actuals, reconciliation = documents()
    actuals["cost"]["period_total_usd"] = 99.0
    with pytest.raises(emitter.HistoryConflictError):
        emit(tmp_path, actuals, reconciliation)


def test_same_payload_is_idempotent(tmp_path) -> None:
    emit(tmp_path)
    emit(tmp_path)
    history = list((tmp_path / "specs" / "cost-history").rglob("actuals.json"))
    assert len(history) == 1


def test_partial_write_cannot_publish_a_false_completed_pair(
    tmp_path, monkeypatch
) -> None:
    emit(tmp_path)
    actuals_path = tmp_path / "specs" / "cost-actuals-manifest.json"
    reconciliation_path = (
        tmp_path / "specs" / "cost-reconciliation-manifest.json"
    )
    old_reconciliation = reconciliation_path.read_bytes()
    actuals, reconciliation = documents("2026-08-11T00:00:00Z")
    real_replace = emitter.os.replace

    def fail_on_canonical(source, destination):
        if str(destination).endswith("cost-reconciliation-manifest.json"):
            raise OSError("simulated replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(emitter.os, "replace", fail_on_canonical)
    with pytest.raises(OSError, match="simulated"):
        emit(tmp_path, actuals, reconciliation)
    assert json.loads(actuals_path.read_text())["generated_at"] == (
        "2026-08-11T00:00:00Z"
    )
    assert reconciliation_path.read_bytes() == old_reconciliation
    assert emitter.canonical_pair_is_complete(
        actuals_path, reconciliation_path
    ) is False


def test_report_keeps_four_headlines_separate(tmp_path) -> None:
    emit(tmp_path)
    report = (tmp_path / "docs" / "cost-reconciliation.md").read_text()
    for heading in (
        "Projected monthly Azure cost",
        "Observed Azure spend",
        "Observed monthly run-rate",
        "Cost per successful interaction",
    ):
        assert heading in report
    assert "token reprice" not in report.casefold()
    assert "actual billed cost" not in report.casefold()
```

- [ ] **Step 2: Implement atomic writes**

Public interface:

```python
def emit_reconciliation(
    *,
    actuals: dict[str, object],
    reconciliation: dict[str, object],
    report_path: Path,
    actuals_path: Path,
    reconciliation_path: Path,
    history_root: Path,
) -> None:
    """Validate all payloads, write history, then atomically replace latest."""


def canonical_pair_is_complete(
    actuals_path: Path,
    reconciliation_path: Path,
) -> bool:
    """True only when reconciliation commits the exact actuals payload."""
```

Write history in the concrete shape
`specs/cost-history/2026-08-01--2026-08-08/2026-08-10T000000Z/{actuals,reconciliation}.json`.
Write temp files in the destination directory, `flush`, `os.fsync`, then
`os.replace`. Refuse to replace an existing timestamped history file with a
different hash. Replace canonical actuals first and canonical reconciliation
last; reconciliation is the commit marker. Clean only the specific temp files
created by this call. Production-ready must call
`canonical_pair_is_complete`-equivalent hash validation before consuming.

- [ ] **Step 3: Render four separate headline numbers**

Markdown order:

1. projected monthly Azure total;
2. observed spend in the period;
3. mature monthly run-rate or `not-verified`;
4. mature cost per successful interaction or `not-verified`.

Then show maturity checks, variance, resource mapping, unmodeled cost,
unattributed cost, model usage, warnings, and provenance.

Label the two coverage numbers distinctly and never merge them into one
"coverage" line: `coverage.projection_attribution_coverage_pct` is the gated
measure ("actual cost mapped to projected resources"), while
`coverage.source_resource_id_coverage_pct` is the ungated source-quality
measure ("actual cost rows carrying a resource ID"). Render
`usage.interaction_status` and `usage.model_attribution_status` alongside the
counts so a `null` count reads as "not verified" rather than as zero, and
render any `policy_errors` verbatim — an incomplete policy still produces this
report.

- [ ] **Step 4: Run and commit**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_reconciliation_emitter.py -q
git add \
  skills/threadlight-consumption-iq/scripts/reconciliation_emitter.py \
  skills/threadlight-consumption-iq/tests/test_reconciliation_emitter.py
git commit -m "feat(consumption-iq): emit auditable cost reconciliation"
```

### Task 11: Add CLI commands without changing `run --all`

**Files:**
- Modify: `skills/threadlight-consumption-iq/scripts/consumption_iq.py`
- Create: `skills/threadlight-consumption-iq/tests/test_cli_actuals.py`
- Modify: `skills/threadlight-consumption-iq/SKILL.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write failing parser and dispatch tests**

Tests:

```python
from datetime import date

import pytest

import consumption_iq
from actuals_sources import ActualsSourceError
from value_model import ValueModelResult


def test_parser_accepts_actuals_window_and_scope() -> None:
    args = consumption_iq.build_parser().parse_args([
        "actuals",
        "--start", "2026-08-01",
        "--end", "2026-08-08",
        "--subscription", "sub-1",
        "--resource-group", "rg-pilot",
        "--spec", "SPEC.md",
        "--actuals-manifest", "actuals.json",
    ])
    assert args.phase == "actuals"
    assert args.start == date(2026, 8, 1)
    assert args.end == date(2026, 8, 8)
    assert args.subscription == "sub-1"
    assert args.resource_group == "rg-pilot"
    assert str(args.spec) == "SPEC.md"
    assert str(args.actuals_manifest) == "actuals.json"
    # `--workspace-resource-id` is optional and was not passed above.
    assert args.workspace_resource_id is None


def test_actuals_requires_start_and_end() -> None:
    with pytest.raises(SystemExit) as exc:
        consumption_iq.build_parser().parse_args([
            "actuals",
            "--subscription", "sub-1",
            "--resource-group", "rg-pilot",
        ])
    assert exc.value.code == 2


def test_actuals_subscription_and_resource_group_default_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-from-env")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-from-env")
    args = consumption_iq.build_parser().parse_args([
        "actuals", "--start", "2026-08-01", "--end", "2026-08-08",
    ])
    assert args.subscription == "sub-from-env"
    assert args.resource_group == "rg-from-env"
    # Defaults are per-parser-build; every command still gets the canonical
    # spec/manifest paths without needing to repeat them on the CLI.
    assert args.spec == consumption_iq.DEFAULT_SPEC_PATH
    assert args.actuals_manifest == consumption_iq.DEFAULT_ACTUALS_MANIFEST


@pytest.mark.parametrize(
    "env_overrides",
    [
        {"AZURE_SUBSCRIPTION_ID": None, "AZURE_RESOURCE_GROUP": "rg-from-env"},
        {"AZURE_SUBSCRIPTION_ID": "sub-from-env", "AZURE_RESOURCE_GROUP": None},
        {"AZURE_SUBSCRIPTION_ID": None, "AZURE_RESOURCE_GROUP": None},
    ],
)
def test_actuals_missing_scope_after_env_resolution_exits_2(
    monkeypatch, env_overrides
) -> None:
    """Neither `--subscription`/`--resource-group` nor their environment
    fallbacks were provided; the command must exit 2 before attempting any
    Azure call, not raise or silently proceed with a `None` scope."""
    for key, value in env_overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    rc = consumption_iq.main([
        "actuals", "--start", "2026-08-01", "--end", "2026-08-08",
    ])
    assert rc == 2


def test_parser_accepts_reconcile_paths() -> None:
    args = consumption_iq.build_parser().parse_args([
        "reconcile",
        "--forecast", "forecast.json",
        "--actuals-manifest", "actuals.json",
        "--spec", "SPEC.md",
        "--reconciliation-manifest", "reconciliation.json",
        "--report", "report.md",
    ])
    assert str(args.forecast) == "forecast.json"
    assert str(args.actuals_manifest) == "actuals.json"
    assert str(args.spec) == "SPEC.md"
    assert str(args.reconciliation_manifest) == "reconciliation.json"
    assert str(args.report) == "report.md"


def test_parser_reconcile_defaults_use_module_constants() -> None:
    args = consumption_iq.build_parser().parse_args(["reconcile"])
    assert args.forecast == consumption_iq.DEFAULT_OUTPUT_MANIFEST
    assert args.actuals_manifest == consumption_iq.DEFAULT_ACTUALS_MANIFEST
    assert args.spec == consumption_iq.DEFAULT_SPEC_PATH
    assert (
        args.reconciliation_manifest
        == consumption_iq.DEFAULT_RECONCILIATION_MANIFEST
    )
    assert args.report == consumption_iq.DEFAULT_RECONCILIATION_REPORT


def test_run_all_default_does_not_call_actuals(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: calls.append("projection"))
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: (_ for _ in ()).throw(AssertionError("actuals must be opt-in")),
    )
    assert consumption_iq.main(["run", "--all"]) == 0
    assert calls == ["projection"]


def test_run_all_with_actuals_requires_start_and_end(monkeypatch) -> None:
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    rc = consumption_iq.main([
        "run", "--all", "--with-actuals",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ])
    assert rc == 2


def test_run_all_with_actuals_calls_projection_then_actuals(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: calls.append("projection"))
    monkeypatch.setattr(consumption_iq, "_phase_actuals", lambda args: calls.append("actuals"))
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: calls.append("reconcile") or {"status": "pass"},
    )
    # `_phase_actuals` above is a stub that returns `None` and never writes
    # anything; both real writer helpers the dispatch path calls after it
    # must also be stubbed, or a real writer would receive that `None` (or
    # attempt to write to the real CWD's `specs/` paths) during this test.
    monkeypatch.setattr(consumption_iq, "_emit_actuals", lambda args, result: None)
    monkeypatch.setattr(consumption_iq, "_emit_reconciliation", lambda args, result: None)
    rc = consumption_iq.main([
        "run", "--all", "--with-actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ])
    assert rc == 0
    assert calls == ["projection", "actuals", "reconcile"]


def test_incomplete_maturity_returns_exit_5_after_emit(monkeypatch) -> None:
    emitted = []
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    monkeypatch.setattr(consumption_iq, "_phase_actuals", lambda args: {})
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: {"status": "not-verified"},
    )
    # `_phase_actuals` here also returns a stub value that must never reach
    # a real writer; stub `_emit_actuals` as a no-op alongside the
    # `_emit_reconciliation` capture below.
    monkeypatch.setattr(consumption_iq, "_emit_actuals", lambda args, result: None)
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result: emitted.append(result),
    )
    rc = consumption_iq.main([
        "run", "--all", "--with-actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ])
    assert rc == 5
    assert emitted == [{"status": "not-verified"}]


def test_cost_source_failure_returns_exit_3(monkeypatch) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: (_ for _ in ()).throw(ActualsSourceError("forbidden")),
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 3


def test_interaction_query_failure_still_returns_exit_0_for_actuals(monkeypatch) -> None:
    """A failed workspace interaction query does not invalidate the cost
    artifact (RFC §7.2/§11): the `actuals` command emitted a valid
    `status: pass` manifest with `usage.interaction_status: not-verified`, so
    it returns 0. Only `reconcile` returns 5, because unit economics could not
    be verified there."""
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: {
            "status": "pass",
            "usage": {"interaction_status": "not-verified"},
            "warnings": ["logs forbidden"],
        },
    )
    # The `actuals` command dispatch itself writes the actuals manifest via
    # `_emit_actuals` before returning; without this stub the real writer
    # would receive the stub dict above and attempt to write to the real
    # CWD's `specs/cost-actuals-manifest.json`.
    monkeypatch.setattr(consumption_iq, "_emit_actuals", lambda args, result: None)
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 0


def test_interaction_query_failure_returns_exit_5_for_reconcile(monkeypatch) -> None:
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: {
            "status": "not-verified",
            "unit_economics": {"status": "not-verified"},
        },
    )
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result: emitted.append(result),
    )
    assert consumption_iq.main(["reconcile"]) == 5
    # Emit happens first; the non-zero exit only reports the verdict.
    assert emitted and emitted[0]["status"] == "not-verified"


def test_unverified_actuals_status_returns_exit_5_after_emit(monkeypatch) -> None:
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: {"status": "not-verified", "warnings": ["cost parse failed"]},
    )
    monkeypatch.setattr(
        consumption_iq, "_emit_actuals", lambda args, result: emitted.append(result)
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 5
    assert emitted


def test_incomplete_policy_emits_before_exiting_5(monkeypatch) -> None:
    """RFC §12 / Task 5: an incomplete or invalid section 14 is no longer an
    early exit. The policy errors flow into `reconcile_costs`, a
    `not-verified` manifest is written, and only then is 5 returned."""
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_load_policy",
        lambda args: ValueModelResult(policy={}, errors=["cost.baseline is missing"]),
    )
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: {
            "status": "not-verified",
            "policy_errors": ["cost.baseline is missing"],
        },
    )
    monkeypatch.setattr(
        consumption_iq,
        "_emit_reconciliation",
        lambda args, result: emitted.append(result),
    )
    assert consumption_iq.main(["reconcile"]) == 5
    assert emitted[0]["policy_errors"] == ["cost.baseline is missing"]


def test_unsafe_success_identifier_skips_the_query_without_aborting(monkeypatch) -> None:
    """An unsafe identifier is a policy validation error, so the interaction
    query is skipped — but raw actuals are still collected and emitted."""
    emitted = []
    monkeypatch.setattr(
        consumption_iq,
        "_load_policy",
        lambda args: ValueModelResult(
            policy={},
            errors=['cost.success_event.success_values[0] invalid'],
        ),
    )
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: {
            "status": "pass",
            "usage": {"interaction_status": "not-verified"},
            "warnings": ["success event identifier rejected; query skipped"],
        },
    )
    monkeypatch.setattr(
        consumption_iq, "_emit_actuals", lambda args, result: emitted.append(result)
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 0
    assert emitted[0]["status"] == "pass"
```

- [ ] **Step 2: Add constants and parser surface**

```python
DEFAULT_ACTUALS_MANIFEST = Path("specs/cost-actuals-manifest.json")
DEFAULT_RECONCILIATION_MANIFEST = Path(
    "specs/cost-reconciliation-manifest.json"
)
DEFAULT_RECONCILIATION_REPORT = Path("docs/cost-reconciliation.md")
DEFAULT_COST_HISTORY = Path("specs/cost-history")
```

`actuals`, `reconcile`, and `run --all --with-actuals` share one required/
default contract; canonicalize it exactly like this — do not improvise
per-command variations:

| Flag | `actuals` | `reconcile` | `run --all --with-actuals` |
|---|---|---|---|
| `--start` / `--end` | required | n/a | required only when `--with-actuals` is passed |
| `--subscription` | default `AZURE_SUBSCRIPTION_ID`; exit 2 if still unset | n/a | same as `actuals` |
| `--resource-group` | default `AZURE_RESOURCE_GROUP`; exit 2 if still unset | n/a | same as `actuals` |
| `--workspace-resource-id` | optional, default `None`, no env fallback | n/a | optional, default `None` |
| `--spec` | default `DEFAULT_SPEC_PATH` | default `DEFAULT_SPEC_PATH` | already added by the existing `_common_args(run)` call — do not re-add |
| `--actuals-manifest` | default `DEFAULT_ACTUALS_MANIFEST` | default `DEFAULT_ACTUALS_MANIFEST` | new flag, default `DEFAULT_ACTUALS_MANIFEST` |
| `--forecast` | n/a | default `DEFAULT_OUTPUT_MANIFEST` | n/a |
| `--report` / `--manifest` | n/a | n/a | already added by `_common_args(run)`; these stay the *projection* report/manifest paths, unrelated to actuals/reconciliation |
| `--reconciliation-report` | n/a | n/a (`reconcile` uses its own `--report`) | new flag, default `DEFAULT_RECONCILIATION_REPORT` |
| `--reconciliation-manifest` | n/a | default `DEFAULT_RECONCILIATION_MANIFEST` | new flag, default `DEFAULT_RECONCILIATION_MANIFEST` |

`run` already has `--all` (`run.add_argument("--all", ...)`) and `--spec`
(via `_common_args(run)`) from the existing pre-deploy/post-deploy projection
flow — this task must not call a helper that re-adds `--spec` to `run`, and
must not add a second `--all`. `run`'s existing `--report`/`--manifest` (also
from `_common_args`) remain the projection outputs; the actuals and
reconciliation sidecars get their own, differently named
`--actuals-manifest` / `--reconciliation-report` / `--reconciliation-manifest`
flags so the two output families never collide.

`--start`/`--end` on `actuals` use argparse's own `required=True`, which
already exits `2` on a missing flag — no extra code needed there. But
`--subscription`/`--resource-group` cannot use `required=True`, because they
have an environment-variable fallback; they need `default=os.environ.get(...)`
plus an explicit post-parse check, since argparse has no built-in concept of
"required unless an environment variable resolves it":

The snippet below shows only the *additions* this task makes inside the
existing `build_parser()` (`skills/threadlight-consumption-iq/scripts/consumption_iq.py`).
It reuses the function's existing `sub = parser.add_subparsers(dest="phase", required=True)`
variable — already created earlier in `build_parser()` and already used by
every existing phase parser and by `run = sub.add_parser("run")` — do not
recreate `parser.add_subparsers(...)` under a new `subparsers` name, and do
not drop or recreate any of the existing subcommands (`discover`,
`load-profile`, `price`, `project`, `recommend`, `emit`, `run`, `estimate`):

```python
    def _add_scope_args(sp: argparse.ArgumentParser, *, start_end_required: bool) -> None:
        """`--start`/`--end`/`--subscription`/`--resource-group`/
        `--workspace-resource-id` only. Deliberately does NOT add `--spec` or
        any output-path flag: `actuals` adds its own `--spec` and
        `--actuals-manifest` right below, and `run` already has `--spec` from
        the existing `_common_args(run)` call, so re-adding it here would hit
        argparse's "conflicting option string" error.
        """
        sp.add_argument(
            "--start", type=date.fromisoformat, required=start_end_required,
        )
        sp.add_argument(
            "--end", type=date.fromisoformat, required=start_end_required,
        )
        sp.add_argument(
            "--subscription", default=os.environ.get("AZURE_SUBSCRIPTION_ID"),
        )
        sp.add_argument(
            "--resource-group", default=os.environ.get("AZURE_RESOURCE_GROUP"),
        )
        sp.add_argument("--workspace-resource-id", default=None)

    actuals_p = sub.add_parser("actuals")
    _add_scope_args(actuals_p, start_end_required=True)
    actuals_p.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    actuals_p.add_argument(
        "--actuals-manifest", type=Path, default=DEFAULT_ACTUALS_MANIFEST,
    )

    reconcile_p = sub.add_parser("reconcile")
    reconcile_p.add_argument("--forecast", type=Path, default=DEFAULT_OUTPUT_MANIFEST)
    reconcile_p.add_argument(
        "--actuals-manifest", type=Path, default=DEFAULT_ACTUALS_MANIFEST,
    )
    reconcile_p.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    reconcile_p.add_argument(
        "--reconciliation-manifest", type=Path,
        default=DEFAULT_RECONCILIATION_MANIFEST,
    )
    reconcile_p.add_argument(
        "--report", type=Path, default=DEFAULT_RECONCILIATION_REPORT,
    )

    # `run` is the existing subparser created above by `sub.add_parser("run")`,
    # with `_common_args(run)` (already supplying `--spec`, `--report`,
    # `--manifest`, `--pre-deploy`, etc.) and `run.add_argument("--all", ...)`
    # already applied, unchanged. Do NOT call `_common_args(run)` again and do
    # NOT add a second `--all` here — only add what `run` does not already
    # have.
    run.add_argument("--with-actuals", action="store_true")
    _add_scope_args(run, start_end_required=False)
    run.add_argument(
        "--actuals-manifest", type=Path, default=DEFAULT_ACTUALS_MANIFEST,
    )
    run.add_argument(
        "--reconciliation-report", type=Path,
        default=DEFAULT_RECONCILIATION_REPORT,
    )
    run.add_argument(
        "--reconciliation-manifest", type=Path,
        default=DEFAULT_RECONCILIATION_MANIFEST,
    )
    # The existing `return parser` at the end of `build_parser()` is
    # unchanged; nothing above needs to run before it.
```

```python
def _resolve_scope_or_exit(args: argparse.Namespace) -> int | None:
    """Shared post-parse validation for `actuals` and `run --with-actuals`.

    Returns an exit code if the scope is invalid, or `None` if it is valid
    and dispatch should continue.
    """
    if getattr(args, "phase", None) == "run" and not getattr(args, "with_actuals", False):
        return None
    if getattr(args, "phase", None) == "run" and (args.start is None or args.end is None):
        print("--with-actuals requires --start and --end", file=sys.stderr)
        return 2
    if not args.subscription or not args.resource_group:
        print(
            "--subscription/AZURE_SUBSCRIPTION_ID and --resource-group/"
            "AZURE_RESOURCE_GROUP must resolve to a value",
            file=sys.stderr,
        )
        return 2
    return None
```

`main()` calls `_resolve_scope_or_exit(args)` immediately after parsing, for
both the `actuals` phase and the `run` phase, and returns its result directly
if it is not `None`, before dispatching to `_phase_actuals`/`_run_projection`
or any Azure-touching code. `reconcile` never calls it — that command has no
scope arguments to validate.

`--spec` and `--actuals-manifest` on `actuals` default to the same canonical
paths every other command uses (`DEFAULT_SPEC_PATH`,
`DEFAULT_ACTUALS_MANIFEST`) purely for a consistent, low-surprise CLI and to
fail fast if the SPEC path doesn't exist; `actuals` itself still never
evaluates section 14 policy or writes a `policy_ref` — only `reconcile`
reads and hashes SPEC's `value_model` (RFC §7.3/§9). `--workspace-resource-id`
takes the **ARM resource ID** of the Log Analytics workspace; the CLI resolves
its query `customerId` internally via `resolve_workspace_customer_id` (Task 9)
and never asks a caller for the GUID. It has no environment fallback and stays
optional everywhere: an omitted or unresolvable workspace only degrades token
attribution and interaction evidence — recorded as
`usage.model_attribution_status` / `usage.interaction_status: not-verified` —
never the Cost Management total itself, so there is nothing to fail closed on.

`reconcile` issues **no Azure calls at all**. It reads the already-collected
canonical actuals manifest at `--actuals-manifest`, the forecast at
`--forecast`, and the policy at `--spec`. Raw actuals are reused verbatim, with
no re-collection, when all of the following hold:

1. the manifest's `schema` is `threadlight-cost-actuals/v1`;
2. its `status` is `pass`;
3. its `window.start`/`window.end` equal the window being reconciled;
4. its scope (subscription and resource group) equals the scope being
   reconciled.

If any of the four does not hold, `reconcile` does not silently fall back to
collecting: it fails closed and tells the operator to rerun `actuals` for the
window it needs. This is what makes re-projection cheap — a changed forecast
hash invalidates the reconciliation but not the collected evidence, so the fix
is a single `reconcile` rerun with zero Azure traffic and zero rate-limit cost.

`--with-actuals` is false by default. `--pre-deploy --with-actuals` is rejected
with exit 2, same as an unresolved `--subscription`/`--resource-group`.

Before adding branches, extract today's lines 363-370 (the
`resources = _phase_discover(args)` ... `_phase_emit(projected, recs, profile,
args)` body of the `run` phase's dispatch) into:

```python
def _run_projection(args: argparse.Namespace) -> None:
    resources = _phase_discover(args)
    profile = _phase_load_profile(args)
    pricing = PricingClient(cache_path=args.cache)
    projected = _phase_project(
        resources, profile, pricing, only=getattr(args, "only", None)
    )
    recs = _phase_recommend(projected, profile)
    _phase_emit(projected, recs, profile, args)
```

The existing verbose block immediately after that (today's lines 371-375 —
`if args.verbose: print(f"emitted {args.report} and {args.manifest}",
file=sys.stderr)`) is **not** part of this extraction and is **not** moved
into `_run_projection`: it stays in `main()`'s `run` phase, called
immediately after `_run_projection(args)`, unchanged:

```python
_run_projection(args)
if args.verbose:
    print(
        f"emitted {args.report} and {args.manifest}",
        file=sys.stderr,
    )
return 0
```

Add a test asserting that verbose behavior survives the extraction:

```python
def test_run_all_verbose_prints_emitted_paths(monkeypatch, capsys) -> None:
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: None)
    rc = consumption_iq.main(["run", "--all", "--verbose"])
    assert rc == 0
    assert "emitted" in capsys.readouterr().err
```

Pin that `run --all` calls only this helper unless `--with-actuals` is present.

Introduce an explicit `_emit_actuals(args, result)` helper in
`consumption_iq.py`, alongside the existing `_emit_reconciliation(args, result)`
CLI-layer wrapper (the thin adapter around `reconciliation_emitter.emit_reconciliation`,
Task 10, that the tests below already monkeypatch), and call it
everywhere the `actuals` phase's result is produced and needs to be written:
by the `actuals` command's own dispatch, and by `run --all --with-actuals`
immediately after `_phase_actuals` and before `_phase_reconcile`/
`_emit_reconciliation` run. `_emit_actuals` performs a plain canonical write
of just the actuals manifest (`DEFAULT_ACTUALS_MANIFEST` or `--actuals-manifest`);
it does not write history, because `reconciliation_emitter.emit_reconciliation`
(Task 10) only writes the atomic actuals+reconciliation history pair once
both documents exist, which is not yet true when `actuals` runs standalone.
Every test above that stubs `_phase_actuals` also stubs `_emit_actuals` (and
`_emit_reconciliation`, where reconcile also runs) for exactly this reason:
without that stub, the real writer would receive the stub's return value —
often `None` or an incomplete dict — and either raise or write bogus content
to the real working directory's `specs/` paths.

- [ ] **Step 3: Add narrow exception mapping**

```python
except ActualsSourceError as exc:
    print(f"actuals source unavailable: {exc}", file=sys.stderr)
    return 3
```

There is deliberately **no** `ValueModelError` branch: the parser no longer
raises for policy content (Task 5). An incomplete or invalid section 14 is
carried as `ValueModelResult.errors` into `reconcile_costs`, which emits a
`not-verified` manifest; the CLI prints the errors to stderr and returns 5
*after* that emit. An early exit here would destroy exactly the evidence the
operator needs to fix the policy.

Do not add a broad `except Exception`. Reconciliation itself returns a manifest
with `status`. Emit that evidence first, then return:

```python
_emit_reconciliation(args, reconciliation)
return 0 if reconciliation.get("status") == "pass" else 5
```

The same ordering applies to `actuals`: `_emit_actuals` runs before any
non-zero return. And the two commands answer different questions, so their
exit codes differ for the same failure — a failed workspace interaction query
leaves `actuals` at 0 (its Cost Management artifact is complete and
`status: pass`, with `usage.interaction_status: not-verified` recorded inside),
while `reconcile` returns 5 because unit economics could not be verified.

- [ ] **Step 4: Update SKILL.md**

Document:

- projection remains always available and unchanged;
- actuals are opt-in;
- required RBAC: Cost Management Reader, Monitoring Reader, Log Analytics
  Reader at the narrowest practical scope;
- daily maximum query cadence and four-hour refresh guidance;
- `usage-pretax` terminology, as the metric and source rather than a price
  basis or an invoice;
- `--workspace-resource-id` takes the ARM resource ID; the query `customerId`
  is resolved internally and never asked for;
- exit 5 is advisory and always follows a successful artifact write;
- no polling for billing ingestion;
- tenant-isolation preflight.

Bump `skills/threadlight-consumption-iq/SKILL.md` `metadata.version` from
`0.3.1` to `0.4.0`.

References:

- <https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage?view=rest-cost-management-2025-03-01>
- <https://learn.microsoft.com/azure/cost-management-billing/costs/manage-automation#data-latency-and-rate-limits>

- [ ] **Step 5: Run targeted and golden tests**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_cli_actuals.py \
  skills/threadlight-consumption-iq/tests/test_e2e.py -q
python scripts/ci/check-skill-description-length.py
```

Expected: all pass; forecast golden unchanged.

- [ ] **Step 6: Commit**

```bash
git add \
  skills/threadlight-consumption-iq/scripts/consumption_iq.py \
  skills/threadlight-consumption-iq/tests/test_cli_actuals.py \
  skills/threadlight-consumption-iq/SKILL.md \
  CHANGELOG.md
git commit -m "feat(consumption-iq): expose actuals reconciliation CLI"
```

### Task 12: Perform the read-only live shape probe

**Files:**
- Create after sanitization:
  `skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals/live-shape.json`

- [ ] **Step 1: Establish isolated Azure context**

Do not guess an alias. Set `AZURE_CONFIG_DIR` and `AZD_CONFIG_DIR` from
`~/.azure-tenants/index.json`, then verify:

```bash
test -n "$AZURE_CONFIG_DIR"
test -n "$AZD_CONFIG_DIR"
az account show --query '{id:id,tenantId:tenantId,name:name}' -o json
```

Confirm the subscription is in the alias's `allowed_subscriptions`.

- [ ] **Step 2: Run the collector against a dedicated mature pilot RG**

```bash
: "${COST_WINDOW_START:?set COST_WINDOW_START to YYYY-MM-DD}"
: "${COST_WINDOW_END:?set COST_WINDOW_END to YYYY-MM-DD}"
: "${AZURE_SUBSCRIPTION_ID:?set verified subscription id}"
: "${PILOT_RESOURCE_GROUP:?set dedicated pilot resource group}"
: "${LOG_ANALYTICS_RESOURCE_ID:?set workspace resource id}"
: "${PILOT_ROOT:?set pilot workspace path}"
: "${RAW_EVIDENCE_DIR:?set a private, out-of-repo directory for raw billing evidence; there is no default}"

mkdir -p "$RAW_EVIDENCE_DIR"
RAW_EVIDENCE_DIR="$(cd "$RAW_EVIDENCE_DIR" && pwd)"
REPO_ROOT="$(cd "$PILOT_ROOT" && git rev-parse --show-toplevel)"
case "$RAW_EVIDENCE_DIR" in
  "$REPO_ROOT"|"$REPO_ROOT"/*)
    echo "RAW_EVIDENCE_DIR ($RAW_EVIDENCE_DIR) resolves inside the git repository ($REPO_ROOT); refusing to write raw billing evidence there." >&2
    exit 1
    ;;
esac

python skills/threadlight-consumption-iq/scripts/consumption_iq.py actuals \
  --start "$COST_WINDOW_START" \
  --end "$COST_WINDOW_END" \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$PILOT_RESOURCE_GROUP" \
  --workspace-resource-id "$LOG_ANALYTICS_RESOURCE_ID" \
  --spec "$PILOT_ROOT/specs/SPEC.md" \
  --actuals-manifest "$RAW_EVIDENCE_DIR/threadlight-cost-actuals.json"
```

Expected: read-only calls only; no Azure mutation.

`--actuals-manifest "$RAW_EVIDENCE_DIR/threadlight-cost-actuals.json"` here is
deliberate, not a placeholder to fill in casually: this is the one place in
the whole plan where the manifest holds real, unsanitized billing evidence
(live resource IDs, subscription/tenant IDs, actual prices) from a customer
or internal pilot subscription. `RAW_EVIDENCE_DIR` is a **required**,
operator-provided, private, out-of-repo directory — there is no default and
it must never be `/tmp` or any other shared/ephemeral location. The guard
block above enforces that: `RAW_EVIDENCE_DIR` must be set (the `:?` fails
loudly otherwise), the directory is created if it does not exist
(`mkdir -p`), its path is resolved to a canonical absolute form, and the
script fails closed (`exit 1`, with an explicit stderr message) if that
resolved path is the repository root or nested inside it. It must never land
inside the repository working tree, where an accidental `git add -A` or
editor autosave could pick it up. Step 3 below sanitizes a copy before
anything derived from it is committed; the raw file under
`$RAW_EVIDENCE_DIR` stays outside any repository and is discarded once
sanitization is complete. Every other `--actuals-manifest` use in this plan
(CLI tests, fixtures, `reconcile`) writes inside the repository because those
manifests are synthetic or already-sanitized fixtures.

- [ ] **Step 3: Sanitize before committing**

Replace subscription IDs, tenant IDs, resource group names, resource names,
and prices with deterministic synthetic values while preserving:

- column names/order, including the selected cost column name (`PreTaxCost` or
  whichever alias the live account actually returned) and `UsageDate`;
- number/string/null types, including `UsageDate`'s live representation
  (integer `YYYYMMDD` vs. ISO string) — that is exactly what
  `normalize_usage_date` must handle;
- daily granularity: one row per resource per day across the whole window, so
  the end-exclusive window validation is exercised against real data;
- pagination shape;
- blank ResourceId behavior;
- dimensions;
- the Log Analytics response envelope (`tables[].name`, `columns[].name`,
  `rows`) if an interaction result was captured, since the parser maps rows by
  column name rather than by position.

Run the parser test against the sanitized fixture. Never commit raw customer
or internal billing data.

- [ ] **Step 4: Complete PR 4 validation**

```bash
python -m pytest skills/threadlight-consumption-iq/tests -q
python -m pytest skills/threadlight-router-bench/tests -q
```

Only then squash-merge PR 4.

---

## PR 5: Consume reconciliation in production-ready and auto

### Task 13: Make `COST-102` and `COST-103` real artifact checks

**Files:**
- Create: `skills/threadlight-production-ready/tests/test_cost_reconciliation.py`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py`

- [ ] **Step 1: Write failing finding tests**

Cover:

```python
import hashlib
import json

import production_ready as pr


def canonical_hash(data):
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def make_ctx(
    tmp_path,
    *,
    reconciliation=True,
    maturity="pass",
    variance_status="pass",
    variance_pct=0.10,
    payg_ptu="pass",
    unit_economics="pass",
):
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    spec_text = "## 14. Value Model\npolicy: explicit\n"
    (specs / "SPEC.md").write_text(spec_text)
    forecast = {"schema_version": "1.0", "totals": {}}
    actuals = {"schema": "threadlight-cost-actuals/v1", "cost": {}}
    (specs / "cost-manifest.json").write_text(json.dumps(forecast))
    (specs / "cost-actuals-manifest.json").write_text(json.dumps(actuals))
    if reconciliation:
        document = {
            "schema": "threadlight-cost-reconciliation/v1",
            "status": maturity,
            "maturity": {"status": maturity, "checks": []},
            "forecast_ref": {"sha256": canonical_hash(forecast)},
            "actuals_ref": {"sha256": canonical_hash(actuals)},
            "policy_ref": {
                "spec_sha256": hashlib.sha256(spec_text.encode()).hexdigest()
            },
            "policy_snapshot": {
                "max_forecast_variance_pct": 0.20,
                "max_token_volume_variance_pct": 0.25,
                "min_projection_attribution_coverage_pct": 0.95,
                "actual_billing_price_basis": "retail",
                "forecast_price_basis": "retail",
            },
            "totals": {"variance_pct": variance_pct},
            "variance_status": variance_status,
            "coverage": {
                "projection_attribution_coverage_pct": 1.0,
                "source_resource_id_coverage_pct": 1.0,
            },
            "unit_economics": {"status": unit_economics},
            "drivers": {"payg_ptu": {"status": payg_ptu}},
        }
        (specs / "cost-reconciliation-manifest.json").write_text(
            json.dumps(document)
        )
    return pr.RepoContext(
        root=tmp_path,
        bicep_files=[],
        src_files=[],
        test_files=[],
        spec_text=spec_text,
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text="",
        azd_env={},
        manifest={},
        bicep_text="",
        src_text="",
        bicep_graph=pr.BicepGraph([], []),
    )


def findings(ctx):
    return {f.id: f for f in pr._check_cost_reconciliation_static(ctx)}


def test_cost102_not_verified_without_reconciliation(tmp_path) -> None:
    assert findings(make_ctx(tmp_path, reconciliation=False))["COST-102"].status == (
        "not-verified"
    )


def test_cost102_not_verified_when_maturity_is_not_verified(tmp_path) -> None:
    assert findings(make_ctx(tmp_path, maturity="not-verified"))["COST-102"].status == (
        "not-verified"
    )


def test_cost102_passes_within_declared_variance(tmp_path) -> None:
    assert findings(make_ctx(tmp_path))["COST-102"].status == "pass"


def test_cost102_should_fix_outside_declared_variance(tmp_path) -> None:
    ctx = make_ctx(tmp_path, variance_status="should-fix", variance_pct=0.30)
    assert findings(ctx)["COST-102"].status == "should-fix"


def test_cost102_does_not_use_hardcoded_twenty_percent(tmp_path) -> None:
    """Superseded below: forcing `variance_status="pass"` here and then
    asserting `.status == "pass"` is self-defeating — `COST-102` consumes
    the reconciler's `variance_status` verbatim (it never recomputes a
    threshold), so this only restates the input and proves nothing about
    whether `20%` is hardcoded anywhere. The two direct checks that replace
    it are `test_cost102_title_and_detail_do_not_hardcode_twenty_percent`
    (catalog title/detail contain no literal `20%`) and
    `test_live_probe_no_longer_emits_duplicate_stub_findings` (combined
    findings carry exactly one `COST-102` and one `COST-103` in both tier
    states) below.
    """


def test_cost102_title_and_detail_do_not_hardcode_twenty_percent(tmp_path) -> None:
    """`FINDING_CATALOG["COST-102"]` must describe declared tolerance, not 20%.

    The variance threshold is read from `policy_snapshot.max_forecast_variance_pct`
    (SPEC section 14), which is per-workload; the finding's static title text
    must not bake in the one example value used in fixtures and docs, and no
    emitted finding's dynamically built detail text may either.
    """
    catalog_entry = pr.FINDING_CATALOG["COST-102"]
    assert "20%" not in catalog_entry["title"]
    assert catalog_entry["title"] == "Live actuals vs forecast within declared tolerance"
    for finding in findings(make_ctx(tmp_path)).values():
        assert "20%" not in finding.title
        assert "20%" not in finding.detail


def test_cost103_not_verified_without_driver(tmp_path) -> None:
    assert findings(make_ctx(tmp_path, payg_ptu="not-verified"))[
        "COST-103"
    ].status == "not-verified"


def test_cost103_passes_when_observed_usage_supports_recommendation(
    tmp_path,
) -> None:
    assert findings(make_ctx(tmp_path, payg_ptu="pass"))["COST-103"].status == "pass"


def test_cost103_uses_the_token_volume_threshold_not_the_cost_threshold(
    tmp_path,
) -> None:
    """The PAYG/PTU driver compares *token volume*, so its tolerance is
    `max_token_volume_variance_pct` (RFC §9.3). Reusing
    `max_forecast_variance_pct` — a cost-variance tolerance — would silently
    apply the wrong number to a different unit."""
    ctx = make_ctx(tmp_path)
    path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    data = json.loads(path.read_text())
    assert data["policy_snapshot"]["max_token_volume_variance_pct"] == 0.25
    for finding in findings(ctx).values():
        assert "max_forecast_variance_pct" not in finding.detail or (
            finding.id == "COST-102"
        )


def test_cost103_not_verified_when_unit_economics_is_not_verified(tmp_path) -> None:
    """Unit economics is gated on four conditions (RFC §9.3); when the
    reconciliation reports it as `not-verified` — for example because
    interaction evidence was unavailable — `COST-103` must not pass on the
    driver status alone."""
    ctx = make_ctx(tmp_path, unit_economics="not-verified")
    assert findings(ctx)["COST-103"].status == "not-verified"


def test_cost102_not_verified_below_projection_attribution_coverage(tmp_path) -> None:
    """Only the reconciliation coverage measure gates the verdict; a healthy
    `source_resource_id_coverage_pct` must not rescue it."""
    ctx = make_ctx(tmp_path)
    path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    data = json.loads(path.read_text())
    data["coverage"]["projection_attribution_coverage_pct"] = 0.40
    data["coverage"]["source_resource_id_coverage_pct"] = 1.0
    data["maturity"]["status"] = "not-verified"
    data["status"] = "not-verified"
    path.write_text(json.dumps(data))
    assert findings(ctx)["COST-102"].status == "not-verified"


def test_garbage_manifest_never_raises_or_passes(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    (tmp_path / "specs" / "cost-reconciliation-manifest.json").write_text("{")
    result = findings(ctx)
    assert result["COST-102"].status == "not-verified"
    assert result["COST-103"].status == "not-verified"


def test_hash_mismatch_never_raises_or_passes(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    (tmp_path / "specs" / "cost-actuals-manifest.json").write_text(
        '{"changed": true}'
    )
    result = findings(ctx)
    assert result["COST-102"].status == "not-verified"
    assert result["COST-103"].status == "not-verified"


def test_missing_spec_never_raises_and_is_not_verified(tmp_path) -> None:
    """A deleted or never-committed SPEC.md must fail closed, not raise.

    `_read_cost_reconciliation` hashes the current SPEC.md against
    `policy_ref.spec_sha256`; without a guard for a missing file, `_read_text`
    returns `None` and `None.encode(...)` raises `AttributeError` instead of
    producing a `not-verified` finding.
    """
    ctx = make_ctx(tmp_path)
    (tmp_path / "specs" / "SPEC.md").unlink()
    result = findings(ctx)
    assert result["COST-102"].status == "not-verified"
    assert result["COST-103"].status == "not-verified"


def test_live_probe_no_longer_emits_duplicate_stub_findings(
    tmp_path, monkeypatch
) -> None:
    """`_check_cost_reconciliation_static` must be the sole producer of
    `COST-102`/`COST-103` in both `_check_cost_live` branches: with tier 3
    available (the reconciliation-manifest path runs) and with tier 3
    unavailable (the early-return budget-unavailable path runs). Neither
    branch may also emit its own stub `not-verified` for those two IDs —
    the combined finding list must carry exactly one of each.
    """
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(pr, "_az_json", lambda *args: [])
    for tiers in ({3: True}, {3: False}):
        live, _ = pr._check_cost_live(ctx, tiers, "sub-1", "rg-pilot")
        ids = [f.id for f in live]
        assert ids.count("COST-102") == 1
        assert ids.count("COST-103") == 1
```

- [ ] **Step 2: Add strict manifest reader**

```python
def _canonical_json_sha256(data: dict[str, Any]) -> str:
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_cost_reconciliation(ctx: RepoContext) -> dict[str, Any] | None:
    path = ctx.root / "specs" / "cost-reconciliation-manifest.json"
    raw = _read_text(path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != "threadlight-cost-reconciliation/v1":
        return None
    refs = data.get("actuals_ref"), data.get("forecast_ref")
    ref_specs = (
        (refs[0], ctx.root / "specs" / "cost-actuals-manifest.json"),
        (refs[1], ctx.root / "specs" / "cost-manifest.json"),
    )
    for ref, source_path in ref_specs:
        if not isinstance(ref, dict) or not isinstance(ref.get("sha256"), str):
            return None
        source_raw = _read_text(source_path)
        try:
            source = json.loads(source_raw) if source_raw else None
        except (TypeError, ValueError):
            return None
        if not isinstance(source, dict):
            return None
        if _canonical_json_sha256(source) != ref["sha256"]:
            return None
    policy_ref = data.get("policy_ref")
    if not isinstance(policy_ref, dict):
        return None
    expected_spec_hash = policy_ref.get("spec_sha256")
    if not isinstance(expected_spec_hash, str):
        return None
    current_spec = _read_text(ctx.root / "specs" / "SPEC.md")
    if current_spec is None:
        # SPEC.md missing entirely (deleted, moved, or never committed) is a
        # fail-closed case, not a crash: there is nothing to hash, so this
        # cannot be treated as a verified reconciliation either way.
        return None
    if hashlib.sha256(current_spec.encode("utf-8")).hexdigest() != expected_spec_hash:
        return None
    return data
```

- [ ] **Step 3: Replace only the `COST-102/103` stubs, in both `_check_cost_live` branches**

First, change `FINDING_CATALOG["COST-102"]["title"]` from the current
`"Live actuals vs forecast within 20%"` to
`"Live actuals vs forecast within declared tolerance"`. `20%` is only ever
the example value used in this plan's and the RFC's fixtures/examples; the
real tolerance is `policy_snapshot.max_forecast_variance_pct`, read from
SPEC section 14 and unique per workload, so the catalog's static title text
must not name one specific number.

`_check_cost_reconciliation_static` must be the **sole** producer of
`COST-102`/`COST-103` — call it exactly once, unconditionally, as the first
statement in `_check_cost_live`, before the tier-3/subscription/resource-group
gate:

```python
def _check_cost_live(ctx, tiers, sub, rg):
    findings: list[Finding] = []
    evidence: list[EvidenceEntry] = []
    findings.extend(_check_cost_reconciliation_static(ctx))
    if not tiers.get(3) or not sub or not rg:
        for fid in ("COST-101", "COST-104", "COST-105"):
            findings.append(_not_verified(fid, "Tier 3 Cost Management Reader unavailable"))
        return findings, evidence
    ...  # unchanged COST-101/104/105 live probing below
```

Both call sites that previously duplicated `COST-102`/`COST-103` must be
removed, not just one:

- the tier-3-unavailable early-return loop currently iterates
  `("COST-101", "COST-102", "COST-103", "COST-104", "COST-105")`; narrow it
  to `("COST-101", "COST-104", "COST-105")` only — `COST-102`/`COST-103` are
  already in `findings` from the unconditional static call above, regardless
  of which branch runs;
- the tier-3-available branch's own
  `for fid in ("COST-102", "COST-103"): findings.append(_not_verified(fid, ...))`
  stub loop is deleted entirely, not replaced — the static call above already
  supplied both findings before this branch's code even runs.

`COST-102`:

- `not-verified` if manifest missing, stale, immature, or basis-mismatched;
- validate that `variance_pct` is numeric and the policy snapshot contains a
  numeric `max_forecast_variance_pct`;
- consume the reconciler's `variance_status` (`pass` or `should-fix`) rather
  than introducing a second threshold;
- `not-verified` for any unknown status or malformed value.

Note that `maturity.status` already folds in
`coverage.projection_attribution_coverage_pct` versus
`min_projection_attribution_coverage_pct`, so this checker never recomputes
coverage. It also never reads `source_resource_id_coverage_pct`: that number
describes Cost Management source quality and is deliberately not a gate.

`COST-103`:

- read `drivers.payg_ptu` from reconciliation;
- `not-verified` when that driver is absent or not verified;
- `not-verified` when `unit_economics.status` is not `pass` — the four-condition
  gate in RFC §9.3 is the reconciler's job, and this checker must not pass on
  the driver status alone;
- `pass` when observed monthly token volume remains inside the SPEC-declared
  `max_token_volume_variance_pct` band used by the forecast recommendation —
  a *token volume* tolerance, never the cost tolerance
  `max_forecast_variance_pct`;
- `should-fix` when volume is outside the band, instructing the operator to
  rerun PAYG/PTU analysis at observed volume.

Remain experimental in v1.

- [ ] **Step 4: Run tests**

```bash
python -m pytest \
  skills/threadlight-production-ready/tests/test_cost_reconciliation.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add \
  skills/threadlight-production-ready/scripts/production_ready.py \
  skills/threadlight-production-ready/tests/test_cost_reconciliation.py
git commit -m "feat(production-ready): assess reconciled Azure cost"
```

### Task 14: Read actual cost per interaction in the KPI scorecard

**Files:**
- Modify: `skills/threadlight-production-ready/tests/test_kpi_scorecard.py`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py`

- [ ] **Step 1: Change tests to the new source**

Add `import hashlib`. Replace the helper that writes CPI into
`cost-manifest.json` with a hash-consistent bundle:

```python
def _canonical_hash(data: dict) -> str:
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cost_bundle(
    cpi: float | None = 0.012,
    status: str = "pass",
) -> dict[str, dict]:
    forecast = _cost_manifest(None)
    actuals = {
        "schema": "threadlight-cost-actuals/v1",
        "cost": {"period_total_usd": 1.2},
    }
    unit = {}
    if cpi is not None:
        unit["status"] = "pass"
        unit["target_status"] = "pass"
        unit["cost_per_successful_interaction_usd"] = cpi
    else:
        unit["status"] = "not-verified"
        unit["target_status"] = "not-verified"
    reconciliation = {
        "schema": "threadlight-cost-reconciliation/v1",
        "status": status,
        "maturity": {"status": status, "checks": []},
        "forecast_ref": {"sha256": _canonical_hash(forecast)},
        "actuals_ref": {"sha256": _canonical_hash(actuals)},
        "policy_ref": {
            "spec_sha256": hashlib.sha256(b"# SPEC\n").hexdigest()
        },
        "unit_economics": unit,
    }
    return {
        "cost-manifest.json": forecast,
        "cost-actuals-manifest.json": actuals,
        "cost-reconciliation-manifest.json": reconciliation,
    }
```

Change every passing joined-scorecard fixture from
`"cost-manifest.json": _cost_manifest(0.012)` to
`**_cost_bundle(0.012)`.

Add:

```python
def test_forecast_manifest_cpi_is_no_longer_treated_as_actual() -> None:
    ctx = _make_ctx(
        src_text=_OBS_SRC,
        manifests={
            "evals-manifest.json": _evals_manifest(0.97),
            "cost-manifest.json": _cost_manifest(0.012),
        },
    )
    assert pr._kpi_signals(ctx)["cost_per_interaction_usd"] is None
    assert _by_id(pr._check_kpi_static(ctx))["KPI-003"].status != "pass"


def test_immature_reconciliation_does_not_pass_kpi003() -> None:
    ctx = _make_ctx(
        src_text=_OBS_SRC,
        manifests={
            "evals-manifest.json": _evals_manifest(0.97),
            **_cost_bundle(0.012, status="not-verified"),
        },
    )
    assert pr._kpi_signals(ctx)["cost_per_interaction_usd"] is None
    assert _by_id(pr._check_kpi_static(ctx))["KPI-003"].status != "pass"


def test_unverified_unit_economics_does_not_pass_kpi003() -> None:
    """A top-level `pass` reconciliation can still carry
    `unit_economics.status: not-verified` — for example when interaction
    evidence was unavailable, so `successful_interactions` is `null`. The KPI
    reader must gate on the unit-economics status, not just the envelope."""
    bundle = _cost_bundle(0.012)
    # `_cost_bundle` values are already plain dicts, not JSON strings —
    # `_make_ctx` does the `json.dumps` serialization itself when it writes
    # each manifest to disk. Copy and mutate the dict directly instead of
    # round-tripping through `json.loads`/`json.dumps` here.
    reconciliation = dict(bundle["cost-reconciliation-manifest.json"])
    reconciliation["unit_economics"] = dict(reconciliation["unit_economics"])
    reconciliation["unit_economics"]["status"] = "not-verified"
    bundle["cost-reconciliation-manifest.json"] = reconciliation
    ctx = _make_ctx(
        src_text=_OBS_SRC,
        manifests={"evals-manifest.json": _evals_manifest(0.97), **bundle},
    )
    assert pr._kpi_signals(ctx)["cost_per_interaction_usd"] is None
    assert _by_id(pr._check_kpi_static(ctx))["KPI-003"].status != "pass"
```

- [ ] **Step 2: Run and verify failures**

```bash
python -m pytest \
  skills/threadlight-production-ready/tests/test_kpi_scorecard.py -v
```

Expected: failures because `_read_cost_per_interaction` still reads forecast.

- [ ] **Step 3: Change the reader**

```python
def _read_cost_per_interaction(ctx: RepoContext) -> float | None:
    data = _read_cost_reconciliation(ctx)
    if not data or data.get("status") != "pass":
        return None
    maturity = data.get("maturity")
    if not isinstance(maturity, dict) or maturity.get("status") != "pass":
        return None
    unit = data.get("unit_economics")
    if not isinstance(unit, dict) or unit.get("status") != "pass":
        # RFC §9.3: the four-condition gate lives in the reconciler. A number
        # may still be present alongside a `not-verified` status (for example
        # when interaction counts were unavailable and successes are `null`);
        # reading it anyway would republish an unverified figure as a KPI.
        return None
    value = unit.get("cost_per_successful_interaction_usd")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
```

Update report source text from `specs/cost-manifest.json` to
`specs/cost-reconciliation-manifest.json`.

- [ ] **Step 4: Run KPI and cost tests**

```bash
python -m pytest \
  skills/threadlight-production-ready/tests/test_kpi_scorecard.py \
  skills/threadlight-production-ready/tests/test_cost_reconciliation.py \
  skills/threadlight-production-ready/tests/test_cost_006.py -q
```

Expected: all pass. `COST-005/006` still use forecast.

- [ ] **Step 5: Commit**

```bash
git add \
  skills/threadlight-production-ready/scripts/production_ready.py \
  skills/threadlight-production-ready/tests/test_kpi_scorecard.py
git commit -m "feat(production-ready): join actual unit cost into KPIs"
```

### Task 15: Keep auto advisory and non-blocking

**Files:**
- Create: `skills/threadlight-auto/tests/test_cost_actuals_guidance.py`
- Modify: `skills/threadlight-auto/SKILL.md`
- Modify: `skills/threadlight-production-ready/SKILL.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write a failing guidance contract test**

Create a stdlib-only test:

```python
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


def test_cost_actuals_are_opt_in_advisory_and_never_polled() -> None:
    text = SKILL.read_text(encoding="utf-8")
    required = (
        "opt-in",
        "--with-actuals",
        "exit 5",
        "not-verified",
        "continue",
        "do not poll",
        "cost management ingestion",
    )
    missing = [token for token in required if token.casefold() not in text.casefold()]
    assert not missing, f"threadlight-auto cost guidance missing: {missing}"
```

- [ ] **Step 2: Run and verify failure**

```bash
python -m pytest \
  skills/threadlight-auto/tests/test_cost_actuals_guidance.py -v
```

Expected: FAIL because the guidance is absent.

- [ ] **Step 3: Update the cost-projection subphase instructions**

Keep `orchestrator.py` and `STAGES` unchanged. Actuals are an optional subphase
of `cost_projection`, not a new resumability stage.

In `threadlight-auto/SKILL.md`, state:

1. run the existing projection exactly as today;
2. only when the operator requested actuals, append `--with-actuals`;
3. record exit 5 as `cost-reconciliation: not-verified`;
4. continue to Invoke/Evals/Red-team/Govern;
5. do not poll or sleep for Cost Management ingestion.

- [ ] **Step 4: Update production-ready guidance**

State:

- projection is always preserved;
- actuals are opt-in until mature-pilot validation is proven;
- SPEC section 14 enforcement is itself opt-in via `--require-value-model`, so
  legacy pilots keep passing until they migrate;
- exit 5 is advisory and always follows a written artifact, so the evidence
  needed to fix the run is on disk even when the verdict is `not-verified`;
- `COST-102/103` assess artifacts;
- `COST-101` remains a live budget check.

Bump `threadlight-production-ready` metadata and
`production_ready.py::VERSION` together from `0.10.0` to `0.11.0`. Bump
`threadlight-auto` metadata from `1.1.0` to `1.2.0`.

- [ ] **Step 5: Run targeted suites**

```bash
python -m pytest skills/threadlight-auto/tests -q
python -m pytest skills/threadlight-production-ready/tests -q
python scripts/ci/check-skill-description-length.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add \
  skills/threadlight-auto/tests/test_cost_actuals_guidance.py \
  skills/threadlight-auto/SKILL.md \
  skills/threadlight-production-ready/scripts/production_ready.py \
  skills/threadlight-production-ready/SKILL.md \
  CHANGELOG.md
git commit -m "feat(auto): carry advisory cost reconciliation"
```

### Task 16: Run regression gates and close PR 5

**Files:** no new files.

- [ ] **Step 1: Run every pytest directory separately**

```bash
set -euo pipefail
for d in $(find skills -maxdepth 2 -type d -name tests) scripts/ci/tests; do
  python -m pytest "$d" -q
done
```

Expected: zero failures. Do not combine skill directories because duplicate
test basenames collide.

- [ ] **Step 2: Run standalone and contract guards**

```bash
python scripts/ci/run-standalone-tests.py
python scripts/ci/check-test-dirs-wired.py
python scripts/ci/check-skill-description-length.py
python scripts/ci/check_pilot_contract.py \
  examples/returns-triage-governed \
  --stage design --stage deploy \
  --profile governed \
  --require-value-model \
  --expect-deployment-target customer-pilot
```

Expected: all pass. This strict `--require-value-model` invocation is the
same enforcement PR 2 (Task 2) already exercises; the legacy no-flag default
behavior — an absent section 14 still passing unless a caller opts in — is
already protected on its own by
`test_legacy_pilot_without_section_14_still_passes_by_default` in
`scripts/ci/tests/test_pilot_contract.py`, so this gate does not need to also
run the no-flag form to cover that case.

- [ ] **Step 3: Run `design-only` E2E**

Expected: design, Pattern 0, and section 14 contract pass; no resources.

- [ ] **Step 4: Run full E2E**

The new actuals path is not enabled by default. Expected:

- base design-to-deploy path remains unchanged;
- deploy/invoke/Phase 5 pass;
- no wait for Cost Management ingestion;
- teardown completes.

- [ ] **Step 5: Squash-merge and verify closure**

```bash
PR_NUMBER=$(gh pr list --head "$(git branch --show-current)" \
  --state merged --json number --jq '.[0].number')
gh pr view "$PR_NUMBER" --json state,mergedAt,mergeCommit \
  --jq '{state, mergedAt, mergeCommit: .mergeCommit.oid}'
gh pr list --state open --json number,title
```

Expected: PR state `MERGED`; no leftover implementation PR.

---

## Final acceptance checklist

- [ ] `specs/cost-manifest.json` golden output did not change.
- [ ] Total projected Azure monthly cost remains in every relevant report.
- [ ] Cost Management `Usage` with the selected cost column (`PreTaxCost`
      primary) is the sole observed total.
- [ ] Token repricing is never added to observed cost.
- [ ] Cost per successful interaction includes full workload Azure cost.
- [ ] Missing policy, permission, freshness, interaction evidence, or
      projection-attribution coverage never passes.
- [ ] SPEC section 14 contains no Threadlight-owned numeric defaults.
- [ ] Section 14 enforcement is opt-in: legacy pilots without the section still
      pass by default; a present-but-malformed section always fails.
- [ ] History snapshots are immutable; canonical files point to latest.
- [ ] `COST-101` remains live; `COST-102/103` consume evidence artifacts.
- [ ] Default Consumption IQ and auto behavior is unchanged.
- [ ] Full design-to-deploy E2E remains green.
- [ ] Interaction evidence is queried only against the Log Analytics workspace
      surface (`AppTraces`/`TimeGenerated`/`Message`/`Properties`); no
      `traces`/`customDimensions` App Insights identifier appears in any query.
- [ ] The workspace `customerId` is resolved from the ARM resource ID inside
      the skill; no interface accepts a raw GUID.
- [ ] The two coverage measures stay distinct: `cost.resource_id_coverage_pct`
      describes source quality and never gates; only
      `coverage.projection_attribution_coverage_pct` is compared to
      `min_projection_attribution_coverage_pct`.
- [ ] Optional evidence never lowers the top-level actuals `status`; it is
      reported through `usage.interaction_status` /
      `usage.model_attribution_status` instead.
- [ ] Every non-zero exit happens **after** the corresponding artifact is
      written; no incomplete-policy early exit remains.
- [ ] No code path parses `Retry-After`; retry is a bounded 2/4/8 backoff.
- [ ] Price basis is compared using `actual_billing_price_basis`, never the
      `usage-pretax` metric name.
- [ ] The daily window is validated end-exclusive and out-of-window rows are
      errors, not silent drops.
