# Cost Actuals Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Threadlight's complete Azure cost projection while adding
read-only post-deploy actuals, forecast reconciliation, and complete-Azure cost
per successful interaction.

**Architecture:** Keep `specs/cost-manifest.json` strict-v1 and unchanged.
`threadlight-consumption-iq` emits separate actuals and reconciliation
manifests from Cost Management, Azure Monitor, traces, and SPEC section 14
policy. `threadlight-production-ready` assesses those artifacts instead of
reimplementing live cost math. Every new path is opt-in and fail-closed.

**Tech Stack:** Python 3.13 stdlib, pytest, Azure CLI (`az rest`,
`az monitor metrics list`, `az monitor log-analytics query`), Cost Management
Query REST API `2025-03-01`, Markdown/JSON contracts.

**Approved design:** `docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`

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
  threshold values.
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
rg -n -i \
  'T[B]D|T[O]DO|FIX[M]E|X[X]X|NEEDS CLARIFICATION|<place''holder>|to be decided|open question' \
  docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md \
  docs/superpowers/plans/2026-08-18-cost-actuals-reconciliation.md
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

### Task 2: Make section 14 a generated artifact contract

**Files:**
- Modify: `scripts/ci/tests/test_pilot_contract.py`
- Modify: `scripts/ci/check_pilot_contract.py`
- Test: `scripts/ci/tests/test_pilot_contract.py`

- [ ] **Step 1: Write failing section 14 tests**

Add:

```python
VALUE_MODEL_MARKERS = (
    "value_model:",
    "maturity_policy:",
    "success_event:",
    "baseline:",
    "accounting:",
)


def test_missing_spec_section_14_is_rejected(pilot: Path) -> None:
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    spec.write_text(text.split("## 14.")[0], encoding="utf-8")
    assert "design.spec.no-section-14" in rules(check(pilot))


@pytest.mark.parametrize("marker", VALUE_MODEL_MARKERS)
def test_section_14_requires_value_model_shape(pilot: Path, marker: str) -> None:
    spec = pilot / "specs" / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    spec.write_text(text.replace(marker, f"# removed {marker}", 1), encoding="utf-8")
    assert "design.spec.value-model-shape" in rules(check(pilot))


def test_section_14_does_not_require_numeric_defaults(pilot: Path) -> None:
    failures = check(pilot)
    assert "design.spec.value-model-shape" not in rules(failures)
```

Update the `pilot` fixture to append:

````markdown
## 14. Value Model

```yaml
value_model:
  cost:
    maturity_policy:
      min_complete_days:
      min_successful_interactions:
      min_cost_settlement_age_hours:
      max_window_end_age_days:
      min_attribution_coverage_pct:
    success_event:
      name:
      trace_attribute:
      success_values: []
    baseline:
      target_cost_per_successful_interaction_usd:
      max_forecast_variance_pct:
    accounting:
      actual_cost_basis:
      forecast_price_basis:
      allow_basis_mismatch_for_verdict:
      scope_policy:
```
````

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest scripts/ci/tests/test_pilot_contract.py \
  -k 'section_14 or value_model' -v
```

Expected: failures because section 14 has no checker yet.

- [ ] **Step 3: Implement a generic top-level section extractor**

In `scripts/ci/check_pilot_contract.py`, replace section-specific slicing with:

```python
def extract_section(spec_text: str, number: int) -> str:
    start = re.search(
        rf"^##[ \t]+{number}\.[^\n]*$",
        spec_text,
        flags=re.MULTILINE,
    )
    if not start:
        return ""
    tail = spec_text[start.end():]
    next_h2 = re.search(
        rf"^##[ \t]+(?:{number + 1}|[1-9]\d+)\.[^\n]*$",
        tail,
        flags=re.MULTILINE,
    )
    return tail[:next_h2.start()] if next_h2 else tail


def extract_section_13(spec_text: str) -> str:
    return extract_section(spec_text, 13)
```

Add to `check_design`:

```python
section14 = extract_section(spec_text, 14)
if not section14:
    fail.add("design.spec.no-section-14", "SPEC.md section 14 Value Model is missing")
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
```

Do not validate numeric values here. Incomplete values are a valid design
state and become `not-verified` in Consumption IQ.

- [ ] **Step 4: Run contract tests**

Run:

```bash
python -m pytest scripts/ci/tests/test_pilot_contract.py -q
```

Expected: all tests pass, including existing section 13 boundary tests.

- [ ] **Step 5: Commit the checker change**

```bash
git add scripts/ci/check_pilot_contract.py scripts/ci/tests/test_pilot_contract.py
git commit -m "test(design): require the SPEC value-model shape"
```

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
      min_attribution_coverage_pct:      # float in (0, 1]; no default
    success_event:
      name:                              # restricted identifier
      trace_attribute:                   # restricted identifier
      success_values: []                 # >= 1 restricted identifier
    baseline:
      target_cost_per_successful_interaction_usd:  # float > 0
      max_forecast_variance_pct:         # float >= 0
    accounting:
      actual_cost_basis: usage-pretax    # v1 literal
      forecast_price_basis: retail       # retail | ea | mca
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group  # or tagged_allocation
```

State explicitly:

- comments are not values;
- generation must not invent numeric values;
- identifier grammar is `^[A-Za-z][A-Za-z0-9_.:-]{0,127}$`;
- incomplete policy is allowed but produces `not-verified`;
- `usage-pretax` names the Cost Management Query API's
  `Usage`/`PreTaxCost` contract and must not be described as an invoice.

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

### Task 4: Update the governed golden example and prove generation

**Files:**
- Modify: `examples/returns-triage-governed/specs/SPEC.md`
- Modify: `CHANGELOG.md`
- Test: `scripts/ci/tests/test_pilot_contract.py`

- [ ] **Step 1: Add explicit returns-triage policy**

Append section 14 with values labeled as decisions for this example, not
defaults:

```yaml
value_model:
  cost:
    maturity_policy:
      min_complete_days: 7
      min_successful_interactions: 100
      min_cost_settlement_age_hours: 48
      max_window_end_age_days: 14
      min_attribution_coverage_pct: 0.95
    success_event:
      name: return_decision_completed
      trace_attribute: decision.outcome
      success_values: [approved, denied, escalated]
    baseline:
      target_cost_per_successful_interaction_usd: 0.18
      max_forecast_variance_pct: 0.20
    accounting:
      actual_cost_basis: usage-pretax
      forecast_price_basis: retail
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group
```

- [ ] **Step 2: Run static contract checks**

```bash
python scripts/ci/check_pilot_contract.py \
  examples/returns-triage-governed \
  --stage design --stage deploy \
  --profile governed \
  --expect-deployment-target customer-pilot
python -m pytest scripts/ci/tests -q
```

Expected: contract OK; all CI-script tests pass.

- [ ] **Step 3: Update changelog and commit**

```bash
git add examples/returns-triage-governed/specs/SPEC.md CHANGELOG.md
git commit -m "docs(example): declare returns-triage value policy"
```

- [ ] **Step 4: Run a real design-only E2E**

Dispatch from the PR branch:

```bash
PR_BRANCH=feat/cost-value-model
gh workflow run threadlight-e2e-foundry.yml \
  --repo aiappsgbb/threadlight-skills \
  --ref "$PR_BRANCH" \
  -f mode=design-only \
  -f workload=returns-triage
```

Expected:

- Phase 1 and Phase 2 pass;
- contract checker passes section 14;
- Phase 3+ skip;
- no Azure resource group is created.

Only after this run and PR CI are green, squash-merge PR 2.

---

## PR 3: Add pure actuals and reconciliation core

### Task 5: Parse and validate section 14

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/value_model.py`
- Create: `skills/threadlight-consumption-iq/tests/test_value_model.py`

- [ ] **Step 1: Write failing parser tests**

Use this complete fixture and test matrix:

````python
import pytest

from value_model import ValueModelError, parse_value_model


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
      min_attribution_coverage_pct: 0.95
    success_event:
      name: return_decision_completed
      trace_attribute: decision.outcome
      success_values: [approved, denied, escalated]
    baseline:
      target_cost_per_successful_interaction_usd: 0.18
      max_forecast_variance_pct: 0.20
    accounting:
      actual_cost_basis: usage-pretax
      forecast_price_basis: retail
      allow_basis_mismatch_for_verdict: false
      scope_policy: dedicated_resource_group
```
"""


def test_complete_policy_parses() -> None:
    model = parse_value_model(COMPLETE)
    assert model["cost"]["maturity_policy"]["min_complete_days"] == 7
    assert model["cost"]["success_event"]["success_values"] == [
        "approved", "denied", "escalated"
    ]


def test_missing_field_reports_exact_dotted_path() -> None:
    partial = COMPLETE.replace("      max_window_end_age_days: 14\n", "")
    with pytest.raises(
        ValueModelError,
        match=r"cost\.maturity_policy\.max_window_end_age_days",
    ):
        parse_value_model(partial)


def test_numeric_comment_is_not_a_value() -> None:
    template = COMPLETE.replace(
        "      min_complete_days: 7",
        "      min_complete_days:  # int >= 1",
    )
    with pytest.raises(
        ValueModelError,
        match=r"cost\.maturity_policy\.min_complete_days",
    ):
        parse_value_model(template)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_zero_or_negative_threshold_is_rejected(value: str) -> None:
    invalid = COMPLETE.replace("min_complete_days: 7", f"min_complete_days: {value}")
    with pytest.raises(ValueModelError, match="min_complete_days"):
        parse_value_model(invalid)


def test_coverage_must_be_at_most_one() -> None:
    invalid = COMPLETE.replace(
        "min_attribution_coverage_pct: 0.95",
        "min_attribution_coverage_pct: 1.01",
    )
    with pytest.raises(ValueModelError, match="min_attribution_coverage_pct"):
        parse_value_model(invalid)


def test_identifier_rejects_kql_fragment() -> None:
    attack = 'approved") | union AppRequests | where ("x" == "x'
    invalid = COMPLETE.replace(
        "success_values: [approved, denied, escalated]",
        f"success_values: [{attack}]",
    )
    with pytest.raises(ValueModelError, match=r"success_values\[0\] invalid"):
        parse_value_model(invalid)


def test_success_values_must_be_nonempty() -> None:
    invalid = COMPLETE.replace(
        "success_values: [approved, denied, escalated]",
        "success_values: []",
    )
    with pytest.raises(ValueModelError, match="success_values"):
        parse_value_model(invalid)


def test_section_14_boundary_stops_at_section_15() -> None:
    invalid = COMPLETE.replace("      scope_policy: dedicated_resource_group\n", "")
    invalid += "\n## 15. Appendix\nscope_policy: dedicated_resource_group\n"
    with pytest.raises(ValueModelError, match="scope_policy"):
        parse_value_model(invalid)
````

The malicious identifier fixture must include:

```text
approved") | union AppRequests | where ("x" == "x
```

and expect `ValueModelError("success_event.success_values[0] invalid")`.

- [ ] **Step 2: Run and verify import failure**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_value_model.py -v
```

Expected: FAIL because `value_model.py` does not exist.

- [ ] **Step 3: Implement the parser**

Public interface:

```python
class ValueModelError(ValueError):
    pass


REQUIRED_PATHS = (
    "cost.maturity_policy.min_complete_days",
    "cost.maturity_policy.min_successful_interactions",
    "cost.maturity_policy.min_cost_settlement_age_hours",
    "cost.maturity_policy.max_window_end_age_days",
    "cost.maturity_policy.min_attribution_coverage_pct",
    "cost.success_event.name",
    "cost.success_event.trace_attribute",
    "cost.success_event.success_values",
    "cost.baseline.target_cost_per_successful_interaction_usd",
    "cost.baseline.max_forecast_variance_pct",
    "cost.accounting.actual_cost_basis",
    "cost.accounting.forecast_price_basis",
    "cost.accounting.allow_basis_mismatch_for_verdict",
    "cost.accounting.scope_policy",
)


def parse_value_model(spec_text: str) -> dict[str, object]:
    """Return validated `value_model`, or raise ValueModelError."""


def load_value_model(spec_path: Path) -> dict[str, object]:
    return parse_value_model(spec_path.read_text(encoding="utf-8"))
```

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

### Task 6: Parse Cost Management evidence without assuming column order

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/cost_actuals.py`
- Create: `skills/threadlight-consumption-iq/tests/test_cost_actuals.py`
- Create: `skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals/cost-query-page-1.json`
- Create: `skills/threadlight-consumption-iq/references/fixtures/sample-cost-actuals/cost-query-page-2.json`
- Create: `skills/threadlight-consumption-iq/references/cost-actuals-manifest-schema.md`

- [ ] **Step 1: Write failing response parser tests**

Use sanitized Query API responses shaped as:

```json
{
  "properties": {
    "columns": [
      {"name": "ResourceType", "type": "String"},
      {"name": "PreTaxCost", "type": "Number"},
      {"name": "Currency", "type": "String"},
      {"name": "ResourceId", "type": "String"},
      {"name": "ServiceName", "type": "String"}
    ],
    "rows": [
      ["microsoft.app/containerapps", 12.5, "USD",
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

import pytest

from cost_actuals import (
    ActualsEvidenceError,
    aggregate_cost_rows,
    rows_from_query_page,
)


COLUMNS = [
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


def page(rows, columns=None):
    return {
        "properties": {
            "columns": deepcopy(columns or COLUMNS),
            "rows": rows,
            "nextLink": None,
        }
    }


def test_columns_are_mapped_by_name_not_position() -> None:
    rows = rows_from_query_page(
        page([["microsoft.app/containerapps", 12.5, "USD", RID, "ACA"]])
    )
    assert rows[0]["pretaxcost"] == 12.5
    assert rows[0]["resourceid"] == RID


def test_rows_for_same_resource_are_summed() -> None:
    resources, total, currency, unattributed = aggregate_cost_rows([
        page([
            ["microsoft.app/containerapps", 12.5, "USD", RID, "ACA"],
            ["microsoft.app/containerapps", 2.5, "USD", RID, "ACA"],
        ])
    ])
    assert resources == [{
        "resource_id": RID,
        "resource_type": "microsoft.app/containerapps",
        "service_name": "ACA",
        "period_cost_usd": 15.0,
    }]
    assert (total, currency, unattributed) == (15.0, "USD", 0.0)


def test_blank_resource_id_remains_in_total_and_is_unattributed() -> None:
    resources, total, _, unattributed = aggregate_cost_rows([
        page([
            ["microsoft.app/containerapps", 12.5, "USD", RID, "ACA"],
            ["", 3.0, "USD", "", "Bandwidth"],
        ])
    ])
    assert sum(r["period_cost_usd"] for r in resources) == 12.5
    assert total == 15.5
    assert unattributed == 3.0


def test_mixed_currency_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="multiple currencies"):
        aggregate_cost_rows([
            page([
                ["x", 1.0, "USD", RID, "A"],
                ["x", 1.0, "EUR", RID, "A"],
            ])
        ])


def test_missing_pretax_cost_column_is_rejected() -> None:
    columns = [c for c in COLUMNS if c["name"] != "PreTaxCost"]
    with pytest.raises(ActualsEvidenceError, match="PreTaxCost column missing"):
        rows_from_query_page(page([["x", "USD", RID, "A"]], columns))


def test_non_numeric_cost_is_rejected() -> None:
    with pytest.raises(ActualsEvidenceError, match="PreTaxCost is not numeric"):
        aggregate_cost_rows([page([["x", "free", "USD", RID, "A"]])])


def test_malformed_row_is_rejected_not_dropped() -> None:
    with pytest.raises(ActualsEvidenceError, match="row does not match columns"):
        rows_from_query_page(page([["x", 1.0]]))


def test_negative_refund_is_retained() -> None:
    resources, total, _, _ = aggregate_cost_rows([
        page([
            ["x", 10.0, "USD", RID, "A"],
            ["x", -2.0, "USD", RID, "A"],
        ])
    ])
    assert total == 8.0
    assert resources[0]["period_cost_usd"] == 8.0


def test_total_equals_resources_plus_unattributed() -> None:
    resources, total, _, unattributed = aggregate_cost_rows([
        page([
            ["x", 10.0, "USD", RID, "A"],
            ["", 4.0, "USD", "", "Tax"],
        ])
    ])
    assert total == sum(r["period_cost_usd"] for r in resources) + unattributed
```

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
    if "pretaxcost" not in names:
        raise ActualsEvidenceError("PreTaxCost column missing")
    parsed = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            raise ActualsEvidenceError("Cost Management row does not match columns")
        parsed.append(
            {names[index]: value for index, value in enumerate(row)}
        )
    return parsed


def aggregate_cost_rows(
    pages: list[dict[str, object]],
) -> tuple[list[dict[str, object]], float, str, float]:
    """Return resources, total_usd, currency, unattributed_usd."""


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
manifest serialization. Compute `settlement_age_hours` and
`window_end_age_days` from `generated_at - end`; the source does not claim an
unobservable Cost Management refresh timestamp.

- [ ] **Step 4: Write the actuals schema**

Pin:

- schema name `threadlight-cost-actuals/v1`;
- `status: pass | not-verified`;
- `basis: usage-pretax`;
- start-inclusive/end-exclusive UTC window;
- `period_total_usd`, resources, unattributed, attribution coverage;
- optional model usage and interaction counts;
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

### Task 7: Add safe trace and token evidence parsers

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/token_evidence.py`
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

Keep `fetch_metrics` in router-bench. In `metrics.py`, import the sibling skill
module by resolving the repository-relative path once:

```python
CONSUMPTION_SCRIPTS = (
    Path(__file__).resolve().parents[2] /
    "threadlight-consumption-iq" / "scripts"
)
sys.path.insert(0, str(CONSUMPTION_SCRIPTS))
from token_evidence import parse_token_metrics  # noqa: E402


def parse_metrics(doc):
    return parse_token_metrics(doc)
```

Add a clear runtime error if the shared module is absent; do not silently fall
back to duplicate code. Missing cached-token metrics must remain `None` in
`parse_token_series`; never turn absence into a zero-percent cache rate.

- [ ] **Step 3: Add safe KQL construction tests**

In `test_cost_actuals.py`:

```python
def test_success_kql_has_fixed_shape() -> None:
    query = build_success_kql(
        "2026-08-01T00:00:00Z",
        "2026-08-08T00:00:00Z",
        "return_decision_completed",
        "decision.outcome",
        ["approved", "denied", "escalated"],
    )
    assert query.startswith("traces\n")
    assert 'message == "return_decision_completed"' in query
    assert 'customDimensions["decision.outcome"]' in query
    assert '"approved", "denied", "escalated"' in query
    assert "union" not in query.casefold()


def test_success_kql_rejects_arbitrary_fragment() -> None:
    with pytest.raises(ActualsEvidenceError, match="identifier"):
        build_success_kql(
            "2026-08-01T00:00:00Z",
            "2026-08-08T00:00:00Z",
            'event") | union AppRequests',
            "decision.outcome",
            ["approved"],
        )


def test_trace_result_counts_total_and_successful_interactions() -> None:
    rows = [{"total_interactions": "120", "successful_interactions": 113}]
    assert parse_interaction_counts(rows) == (120, 113)


def test_empty_trace_result_returns_zero_counts_not_a_pass() -> None:
    assert parse_interaction_counts([]) == (0, 0)
```

Implement:

```python
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


def build_success_kql(
    start_iso: str,
    end_iso: str,
    event_name: str,
    trace_attribute: str,
    success_values: list[str],
) -> str:
    """Build a fixed traces query; reject every non-identifier input."""


def parse_interaction_counts(rows: object) -> tuple[int, int]:
    """Return total and successful counts from `az monitor ... query` JSON."""
```

The KQL shape is fixed:

```kusto
traces
| where timestamp >= datetime(2026-08-01T00:00:00Z)
    and timestamp < datetime(2026-08-08T00:00:00Z)
| where message == "return_decision_completed"
| extend outcome = tostring(customDimensions["decision.outcome"])
| summarize total_interactions=count(),
            successful_interactions=countif(
                outcome in ("approved", "denied", "escalated")
            )
```

Only validated identifiers are interpolated. Dates come from parsed
`datetime` values and are reserialized as ISO UTC.

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

from reconcile import reconcile_costs, sha256_json


RID = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/containerApps/a"
GENERATED = "2026-08-10T00:00:00Z"


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
            "currency": "USD",
            "period_total_usd": total,
            "resources": [{
                "resource_id": RID,
                "resource_type": "Microsoft.App/containerApps",
                "service_name": "Azure Container Apps",
                "period_cost_usd": total,
            }],
            "unattributed_usd": 0.0,
            "attribution_coverage_pct": 1.0,
        },
        "usage": {
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
                "min_attribution_coverage_pct": 0.95,
            },
            "success_event": {
                "name": "return_decision_completed",
                "trace_attribute": "decision.outcome",
                "success_values": ["approved"],
            },
            "baseline": {
                "target_cost_per_successful_interaction_usd": 1.0,
                "max_forecast_variance_pct": 0.20,
            },
            "accounting": {
                "actual_cost_basis": "usage-pretax",
                "forecast_price_basis": "retail",
                "allow_basis_mismatch_for_verdict": False,
                "scope_policy": "dedicated_resource_group",
            },
        }
    }


def run(f=None, a=None, p=None):
    return reconcile_costs(
        f or forecast(),
        a or actuals(),
        p or policy(),
        generated_at=GENERATED,
        policy_spec_sha256="spec-hash",
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


def test_unmodeled_resource_remains_in_actual_total() -> None:
    a = actuals()
    a["cost"]["resources"][0]["resource_id"] = RID + "-other"
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["coverage"]["unmodeled_actual_usd"] == 70.0


def test_unattributed_cost_reduces_coverage_not_total() -> None:
    a = actuals()
    a["cost"]["unattributed_usd"] = 7.0
    a["cost"]["attribution_coverage_pct"] = 0.9
    result = run(a=a)
    assert result["totals"]["actual_window_usd"] == 70.0
    assert result["coverage"]["attribution_coverage_pct"] == 0.9


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


def test_incomplete_policy_yields_not_verified() -> None:
    p = policy()
    del p["cost"]["maturity_policy"]["min_complete_days"]
    assert run(p=p)["maturity"]["status"] == "not-verified"


def test_window_too_recent_to_settle_yields_not_verified() -> None:
    a = actuals()
    a["window"]["settlement_age_hours"] = 12
    assert run(a=a)["maturity"]["status"] == "not-verified"


def test_window_too_old_for_policy_yields_not_verified() -> None:
    a = actuals()
    a["window"]["window_end_age_days"] = 30
    assert run(a=a)["maturity"]["status"] == "not-verified"


def test_low_attribution_coverage_yields_not_verified() -> None:
    a = actuals()
    a["cost"]["attribution_coverage_pct"] = 0.80
    assert run(a=a)["maturity"]["status"] == "not-verified"


def test_price_basis_mismatch_reports_delta_without_verdict() -> None:
    f = forecast()
    f["price_basis"] = "ea"
    result = run(f=f)
    assert result["totals"]["variance_pct"] == 0.0
    assert result["variance_status"] == "not-verified"


def test_forecast_and_actual_hashes_are_recorded() -> None:
    f, a = forecast(), actuals()
    result = run(f=f, a=a)
    assert result["forecast_ref"]["sha256"] == sha256_json(f)
    assert result["actuals_ref"]["sha256"] == sha256_json(a)
    assert result["policy_ref"]["spec_sha256"] == "spec-hash"
    assert result["policy_snapshot"]["max_forecast_variance_pct"] == 0.20


def test_payg_ptu_driver_compares_observed_and_forecast_token_volume() -> None:
    f, a = forecast(), actuals()
    f["resources"] = [{
        "resource_id": RID,
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "monthly_units_consumed": {
            "input_tokens": 80000,
            "output_tokens": 10000,
        },
    }]
    f["recommendations"] = [{
        "resource_id": RID,
        "current_sku": {"tier": "PAYG"},
        "recommended_sku": {"tier": "PTU"},
    }]
    a["usage"]["models"] = [{
        "deployment": "chat",
        "model": "gpt-5.4",
        "input_tokens": 19000,
        "output_tokens": 2000,
    }]
    assert run(f=f, a=a)["drivers"]["payg_ptu"]["status"] == "pass"


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
    generated_at: str,
    policy_spec_sha256: str,
) -> dict[str, object]:
    """Return `threadlight-cost-reconciliation/v1`."""
```

Use `Decimal` for money. Do not accept a token-cost argument in
`reconcile_costs`; the type boundary itself prevents double counting.

Resource matching order:

1. case-insensitive normalized resource ID;
2. normalized ARM resource type;
3. otherwise unmodeled/unattributed.

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
inside SPEC's declared `max_forecast_variance_pct`. Outside the band, emit
`should-fix` with "rerun PAYG/PTU analysis at observed volume". Missing
recommendation, zero forecast tokens, or missing token metrics is
`not-verified`. This checks whether the recommendation's load assumption still
matches reality; it does not call the token reprice billed actual.

- [ ] **Step 4: Write the reconciliation schema**

Pin the shape approved in the RFC, including:

- references and SHA-256 hashes;
- maturity checks;
- forecast monthly/window totals;
- actual window/monthly run-rate totals;
- variance;
- cost per successful interaction;
- coverage and drivers;
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

import pytest

from actuals_sources import (
    ActualsSourceError,
    assert_azure_context,
    collect_sources,
    cost_query_body,
    fetch_cost_pages,
    fetch_trace_rows,
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


def test_cost_query_uses_custom_window_and_grouping() -> None:
    body = cost_query_body(date(2026, 8, 1), date(2026, 8, 8))
    assert body["timePeriod"] == {
        "from": "2026-08-01T00:00:00Z",
        "to": "2026-08-08T00:00:00Z",
    }
    assert [g["name"] for g in body["dataset"]["grouping"]] == [
        "ResourceId", "ResourceType", "ServiceName"
    ]


def test_cost_query_uses_rg_scope_and_explicit_subscription(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/tmp/isolated-az")
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


def test_cost_query_follows_next_link(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/tmp/isolated-az")
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


def test_429_uses_retry_after_and_bounded_retry(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/tmp/isolated-az")
    sleeps = []
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        result(stderr="HTTP 429\nRetry-After: 7", code=1),
        result(json.dumps({"properties": {
            "columns": [], "rows": [], "nextLink": None
        }})),
    ])
    fetch_cost_pages(
        "sub-1", "rg-pilot", date(2026, 8, 1), date(2026, 8, 8),
        runner=runner, sleep=sleeps.append,
    )
    assert sleeps == [7]


def test_other_az_failure_surfaces_stderr(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/tmp/isolated-az")
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


def test_active_subscription_must_match_requested_subscription(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/tmp/isolated-az")
    runner = FakeRunner([
        result(json.dumps({"id": "sub-other", "tenantId": "tenant-1"}))
    ])
    with pytest.raises(ActualsSourceError, match="subscription mismatch"):
        assert_azure_context("sub-1", runner=runner)


def test_trace_query_uses_workspace_customer_id() -> None:
    runner = FakeRunner([result('[{"n": 1}]')])
    assert fetch_trace_rows("workspace-customer-id", "traces | count", runner) == [
        {"n": 1}
    ]
    assert "workspace-customer-id" in runner.calls[0]


def test_monitoring_failure_does_not_erase_valid_cost_evidence(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/tmp/isolated-az")
    runner = FakeRunner([
        result(json.dumps({"id": "sub-1", "tenantId": "tenant-1"})),
        result(json.dumps({"properties": {
            "columns": [], "rows": [], "nextLink": None
        }})),
        result(stderr="metrics forbidden", code=1),
        result(stderr="logs forbidden", code=1),
    ])
    bundle = collect_sources(
        subscription_id="sub-1",
        resource_group="rg-pilot",
        start=date(2026, 8, 1),
        end=date(2026, 8, 8),
        monitor_resource_id="/resource/model",
        workspace_customer_id="workspace-customer-id",
        kql="traces | count",
        runner=runner,
        sleep=lambda _: None,
    )
    assert len(bundle["cost_pages"]) == 1
    assert bundle["token_doc"] is None
    assert bundle["trace_rows"] is None
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
            "granularity": "None",
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

Retry only transient return codes/messages (429/5xx). Honor Retry-After when
available; otherwise use bounded exponential delays `2, 4, 8` seconds. Inject
both `runner` and `sleep` so tests never wait.

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


def collect_sources(
    *,
    subscription_id: str,
    resource_group: str,
    start: date,
    end: date,
    monitor_resource_id: str | None,
    workspace_customer_id: str | None,
    kql: str | None,
    runner: Runner,
    sleep: Callable[[float], None],
) -> dict[str, object]:
    """Require cost pages; degrade token/traces to warnings."""
```

- [ ] **Step 4: Implement Monitor and trace adapters**

Keep the existing `az monitor metrics list` dimensions and call the shared
parser. For traces:

```python
[
    "az", "monitor", "log-analytics", "query",
    "--workspace", customer_id,
    "--analytics-query", safe_kql,
    "--output", "json",
]
```

Cost Management failure prevents a verified actual total. Monitor or trace
failure preserves valid cost evidence and adds warnings.

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
        "maturity": {"status": "pass", "checks": []},
        "totals": {
            "forecast_monthly_usd": 300.0,
            "actual_window_usd": 70.0,
            "actual_monthly_run_rate_usd": 300.0,
        },
        "unit_economics": {
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


def test_parser_accepts_actuals_window_and_scope() -> None:
    args = consumption_iq.build_parser().parse_args([
        "actuals",
        "--start", "2026-08-01",
        "--end", "2026-08-08",
        "--subscription", "sub-1",
        "--resource-group", "rg-pilot",
    ])
    assert args.phase == "actuals"
    assert args.start == date(2026, 8, 1)
    assert args.end == date(2026, 8, 8)
    assert args.subscription == "sub-1"
    assert args.resource_group == "rg-pilot"


def test_parser_accepts_reconcile_paths() -> None:
    args = consumption_iq.build_parser().parse_args([
        "reconcile",
        "--forecast", "forecast.json",
        "--actuals-manifest", "actuals.json",
        "--spec", "SPEC.md",
    ])
    assert str(args.forecast) == "forecast.json"
    assert str(args.actuals_manifest) == "actuals.json"
    assert str(args.spec) == "SPEC.md"


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


def test_run_all_with_actuals_calls_projection_then_actuals(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(consumption_iq, "_run_projection", lambda args: calls.append("projection"))
    monkeypatch.setattr(consumption_iq, "_phase_actuals", lambda args: calls.append("actuals"))
    monkeypatch.setattr(
        consumption_iq,
        "_phase_reconcile",
        lambda args: calls.append("reconcile") or {"status": "pass"},
    )
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


def test_trace_failure_returns_exit_5_not_exit_3(monkeypatch) -> None:
    monkeypatch.setattr(
        consumption_iq,
        "_phase_actuals",
        lambda args: {"status": "not-verified", "warnings": ["logs forbidden"]},
    )
    assert consumption_iq.main([
        "actuals",
        "--start", "2026-08-01", "--end", "2026-08-08",
        "--subscription", "sub-1", "--resource-group", "rg-pilot",
    ]) == 5
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

Commands:

```text
actuals --start YYYY-MM-DD --end YYYY-MM-DD
        --subscription ID --resource-group NAME
        --workspace-resource-id ID
reconcile --forecast PATH --actuals-manifest PATH --spec PATH
run --all --with-actuals [same live args]
```

`--with-actuals` is false by default. `--pre-deploy --with-actuals` is rejected
with exit 2.

Before adding branches, extract today's lines 363-376 into:

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

Pin that `run --all` calls only this helper unless `--with-actuals` is present.

- [ ] **Step 3: Add narrow exception mapping**

```python
except ValueModelError as exc:
    print(f"value model incomplete: {exc}", file=sys.stderr)
    return 5
except ActualsSourceError as exc:
    print(f"actuals source unavailable: {exc}", file=sys.stderr)
    return 3
```

Do not add a broad `except Exception`. Reconciliation itself returns a manifest
with `status`. Emit that evidence first, then return:

```python
return 0 if reconciliation.get("status") == "pass" else 5
```

- [ ] **Step 4: Update SKILL.md**

Document:

- projection remains always available and unchanged;
- actuals are opt-in;
- required RBAC: Cost Management Reader, Monitoring Reader, Log Analytics
  Reader at the narrowest practical scope;
- daily maximum query cadence and four-hour refresh guidance;
- `usage-pretax` terminology;
- exit 5 is advisory;
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

python skills/threadlight-consumption-iq/scripts/consumption_iq.py actuals \
  --start "$COST_WINDOW_START" \
  --end "$COST_WINDOW_END" \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$PILOT_RESOURCE_GROUP" \
  --workspace-resource-id "$LOG_ANALYTICS_RESOURCE_ID" \
  --spec "$PILOT_ROOT/specs/SPEC.md" \
  --actuals-manifest /tmp/threadlight-cost-actuals.json
```

Expected: read-only calls only; no Azure mutation.

- [ ] **Step 3: Sanitize before committing**

Replace subscription IDs, tenant IDs, resource group names, resource names,
and prices with deterministic synthetic values while preserving:

- column names/order;
- number/string/null types;
- pagination shape;
- blank ResourceId behavior;
- dimensions.

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
            "policy_snapshot": {"max_forecast_variance_pct": 0.20},
            "totals": {"variance_pct": variance_pct},
            "variance_status": variance_status,
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
    ctx = make_ctx(tmp_path, variance_status="pass", variance_pct=0.50)
    path = tmp_path / "specs" / "cost-reconciliation-manifest.json"
    data = json.loads(path.read_text())
    data["policy_snapshot"]["max_forecast_variance_pct"] = 0.60
    path.write_text(json.dumps(data))
    assert findings(ctx)["COST-102"].status == "pass"


def test_cost103_not_verified_without_driver(tmp_path) -> None:
    assert findings(make_ctx(tmp_path, payg_ptu="not-verified"))[
        "COST-103"
    ].status == "not-verified"


def test_cost103_passes_when_observed_usage_supports_recommendation(
    tmp_path,
) -> None:
    assert findings(make_ctx(tmp_path, payg_ptu="pass"))["COST-103"].status == "pass"


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


def test_live_probe_no_longer_emits_duplicate_stub_findings(
    tmp_path, monkeypatch
) -> None:
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(pr, "_az_json", lambda *args: [])
    live, _ = pr._check_cost_live(
        ctx, {3: True}, "sub-1", "rg-pilot"
    )
    assert "COST-102" not in {f.id for f in live}
    assert "COST-103" not in {f.id for f in live}
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
    if hashlib.sha256(current_spec.encode("utf-8")).hexdigest() != expected_spec_hash:
        return None
    return data
```

- [ ] **Step 3: Replace only the `COST-102/103` stubs**

Keep `COST-101` live budget behavior. Remove the loop that blindly emits
`not-verified` for both IDs and call:

```python
findings.extend(_check_cost_reconciliation_static(ctx))
```

`COST-102`:

- `not-verified` if manifest missing, stale, immature, or basis-mismatched;
- validate that `variance_pct` is numeric and the policy snapshot contains a
  numeric `max_forecast_variance_pct`;
- consume the reconciler's `variance_status` (`pass` or `should-fix`) rather
  than introducing a second threshold;
- `not-verified` for any unknown status or malformed value.

`COST-103`:

- read `drivers.payg_ptu` from reconciliation;
- `not-verified` when that driver is absent or not verified;
- `pass` when observed monthly token volume remains inside the SPEC-declared
  variance band used by the forecast recommendation;
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
        unit["cost_per_successful_interaction_usd"] = cpi
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
    value = (
        unit.get("cost_per_successful_interaction_usd")
        if isinstance(unit, dict)
        else None
    )
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
- exit 5 is advisory;
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
  --expect-deployment-target customer-pilot
```

Expected: all pass.

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
- [ ] Cost Management `Usage`/`PreTaxCost` is the sole observed total.
- [ ] Token repricing is never added to observed cost.
- [ ] Cost per successful interaction includes full workload Azure cost.
- [ ] Missing policy, permission, freshness, traces, or coverage never passes.
- [ ] SPEC section 14 contains no Threadlight-owned numeric defaults.
- [ ] History snapshots are immutable; canonical files point to latest.
- [ ] `COST-101` remains live; `COST-102/103` consume evidence artifacts.
- [ ] Default Consumption IQ and auto behavior is unchanged.
- [ ] Full design-to-deploy E2E remains green.
