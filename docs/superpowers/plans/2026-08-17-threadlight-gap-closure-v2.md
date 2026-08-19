# Threadlight Gap Closure v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one `threadlight-skills` pull request that closes the approved local deltas across qualification, cost coverage, mock-to-real integration, grounding, load validation, upgrade scanning, runtime policy, documentation, GitHub Pages, Cowork packaging, and Lifecycle Canvas.

**Architecture:** Five new advisory skills emit strict evidence manifests consumed by existing Threadlight surfaces. Qualification and post-deploy cost analysis share one importable cost API; production-ready adds checks to existing pillars; safe-check gains only the verified real-vs-mock gate; auto and Canvas recognize evidence without launching live or cost-bearing work.

**Tech Stack:** Python 3.13 stdlib, pytest, JSON/JSON Schema documents, Node.js built-in test runner, vanilla JavaScript/HTML/CSS, GitHub Actions, existing Playwright suite.

---

## File map

### Shared contracts

- Create `skills/_shared/manifest.py`: validated envelope and atomic JSON writer.
- Create `skills/_shared/tests/test_manifest.py`: envelope and atomic-write tests.
- Modify `skills/threadlight-design/references/runtime-policy.json`: local vNext lifecycle metadata.
- Modify `tests/blueprint/runtime-policy.test.js`: lifecycle, region, and lineage guards.

### Qualification and cost

- Create `skills/threadlight-consumption-iq/scripts/cost_api.py`: stable projection API.
- Create `skills/threadlight-consumption-iq/scripts/meter_demand.py`: normalized meter discovery.
- Create `skills/threadlight-consumption-iq/scripts/model_catalog.py`: dated catalog loader.
- Create `skills/threadlight-consumption-iq/references/model-catalog.json`: local model comparisons.
- Create `skills/threadlight-consumption-iq/references/cost-manifest.schema.json`: vNext schema.
- Create meter projector modules under `skills/threadlight-consumption-iq/scripts/projectors/`.
- Modify existing Consumption IQ CLI, pricing, projectors, emitter, schemas, fixtures, and tests.
- Create `skills/threadlight-qualify/`: Cowork-safe qualification skill, script, schemas, fixtures, tests.

### New evidence legs

- Create `skills/threadlight-connect/`: contract extraction, conformance plan/apply, manifest, tests.
- Create `skills/threadlight-ground/`: grounding evidence coordinator, schema, tests.
- Create `skills/threadlight-loadtest/`: guarded load-profile runner/adapters, schema, tests.
- Create `skills/threadlight-upgrade/`: compatibility matrix, scanner, migration plan, tests.

### Integration surfaces

- Modify `skills/threadlight-safe-check/scripts/safe_check.py` and its shipped example copy.
- Modify `skills/threadlight-production-ready/scripts/production_ready.py`, pillar references,
  remediation recipes, fixtures, and tests.
- Modify `skills/threadlight-auto/references/orchestrator.py`, `SKILL.md`, and tests.
- Modify Lifecycle Canvas registry, reader, projector, fixtures, and tests.

### Documentation and release

- Modify `README.md`, `THREADLIGHT.md`, `CHANGELOG.md`, `plugin.json`.
- Modify `scripts/build_process_library.py` and Blueprint tests/assets.
- Modify GitHub Pages funnel/production/lifecycle content and site tests.
- Modify `scripts/build-cowork-zips.sh`; add `docs/downloads/threadlight-qualify.zip`.
- Modify `.github/workflows/python-pytest.yml`.

## Execution constraints

- Do not modify functional files under `skills/threadlight-deploy/**`; PR #111 owns that area.
- Before the release/docs commit, require PR #111 to be merged and integrate `origin/main`.
- Read `examples/returns-triage-governed/AGENTS.md` before changing the example.
- Do not contact Azure or customer endpoints from tests.
- Do not add another Python or JavaScript test runner.
- Do not modify `aiappsgbb/agentic-loop`.

### Task 1: Strengthen runtime policy and add shared manifest contracts

**Files:**
- Create: `skills/_shared/manifest.py`
- Create: `skills/_shared/tests/test_manifest.py`
- Modify: `skills/threadlight-design/references/runtime-policy.json`
- Modify: `tests/blueprint/runtime-policy.test.js`

- [ ] **Step 1: Write failing manifest-envelope tests**

```python
# skills/_shared/tests/test_manifest.py
import json
from pathlib import Path

import pytest

from skills._shared.manifest import (
    ManifestValidationError,
    atomic_write_json,
    build_envelope,
    validate_envelope,
)


def test_build_envelope_declares_status_and_freshness():
    out = build_envelope(
        schema="threadlight.test/v1",
        tool_version="0.1.0",
        status="partial",
        generated_at="2026-08-17T10:00:00Z",
        valid_for_hours=24,
        source_oldest_at="2026-08-17T09:00:00Z",
        findings=[{"id": "TST-001", "status": "not-verified"}],
    )
    assert out["schema"] == "threadlight.test/v1"
    assert out["status"] == "partial"
    assert out["freshness"]["valid_for_hours"] == 24
    assert out["findings"][0]["id"] == "TST-001"


@pytest.mark.parametrize("status", ["complete", "partial", "aborted"])
def test_validate_envelope_accepts_declared_statuses(status):
    validate_envelope(build_envelope(
        schema="threadlight.test/v1",
        tool_version="0.1.0",
        status=status,
        generated_at="2026-08-17T10:00:00Z",
        valid_for_hours=24,
        source_oldest_at=None,
        findings=[],
    ))


def test_validate_envelope_rejects_success_shaped_unknown_status():
    with pytest.raises(ManifestValidationError, match="status"):
        validate_envelope({
            "schema": "threadlight.test/v1",
            "tool_version": "0.1.0",
            "generated_at": "2026-08-17T10:00:00Z",
            "freshness": {"valid_for_hours": 24, "source_oldest_at": None},
            "status": "passed",
            "findings": [],
        })


def test_atomic_write_preserves_previous_file_when_validation_fails(tmp_path):
    target = tmp_path / "manifest.json"
    target.write_text('{"stable": true}\n', encoding="utf-8")
    with pytest.raises(ManifestValidationError):
        atomic_write_json(target, {"status": "complete"})
    assert json.loads(target.read_text()) == {"stable": True}
```

- [ ] **Step 2: Run the shared tests and verify the missing module failure**

Run:

```bash
python -m pytest skills/_shared/tests/test_manifest.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'skills._shared.manifest'`.

- [ ] **Step 3: Implement the envelope and atomic writer**

```python
# skills/_shared/manifest.py
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

VALID_STATUSES = frozenset({"complete", "partial", "aborted"})
REQUIRED_KEYS = frozenset({
    "schema", "tool_version", "generated_at", "freshness", "status", "findings",
})


class ManifestValidationError(ValueError):
    pass


def build_envelope(
    *,
    schema: str,
    tool_version: str,
    status: str,
    generated_at: str,
    valid_for_hours: int,
    source_oldest_at: str | None,
    findings: list[dict[str, Any]],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "schema": schema,
        "tool_version": tool_version,
        "generated_at": generated_at,
        "freshness": {
            "valid_for_hours": valid_for_hours,
            "source_oldest_at": source_oldest_at,
        },
        "status": status,
        "findings": findings,
    }
    out.update(payload or {})
    validate_envelope(out)
    return out


def validate_envelope(value: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_KEYS - set(value))
    if missing:
        raise ManifestValidationError(f"manifest missing required keys: {missing}")
    if value["status"] not in VALID_STATUSES:
        raise ManifestValidationError(
            f"manifest status must be one of {sorted(VALID_STATUSES)}"
        )
    if not isinstance(value["findings"], list):
        raise ManifestValidationError("manifest findings must be a list")
    freshness = value["freshness"]
    if not isinstance(freshness, dict) or not isinstance(
        freshness.get("valid_for_hours"), int
    ):
        raise ManifestValidationError(
            "manifest freshness.valid_for_hours must be an int"
        )


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    validate_envelope(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
```

- [ ] **Step 4: Run the shared tests**

Run:

```bash
python -m pytest skills/_shared/tests/test_manifest.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Write failing runtime-policy lifecycle tests**

Append tests that require:

```javascript
test('runtime policy carries decision lifecycle and regional policy', () => {
  const policy = loadPolicy();
  assert.strictEqual(policy.contract_version, '2.0.0');
  assert.match(policy.last_reviewed, /^\d{4}-\d{2}-\d{2}$/);
  assert.deepStrictEqual(policy.region_policy.eu_residency, ['swedencentral']);
  for (const route of policy.routes) {
    assert.ok(route.decision_date);
    assert.ok(route.permanent === true || route.review_by || route.expiry_condition);
  }
});

test('runtime policy lineage is local and does not claim cross-repo consumption', () => {
  const policy = loadPolicy();
  assert.strictEqual(policy.authority.repository, 'aiappsgbb/threadlight-skills');
  assert.strictEqual(
    policy.authority.path,
    'skills/threadlight-design/references/runtime-policy.json',
  );
  assert.strictEqual(policy.authority.cross_repository_consumers, false);
});
```

- [ ] **Step 6: Run the policy test and verify it fails**

Run:

```bash
node --test tests/blueprint/runtime-policy.test.js
```

Expected: failure on missing `contract_version`, `region_policy`, or route lifecycle fields.

- [ ] **Step 7: Add lifecycle metadata without changing selector behavior**

Extend `runtime-policy.json` with:

```json
{
  "schema": "threadlight.runtime-policy/v1",
  "version": 2,
  "contract_version": "2.0.0",
  "last_reviewed": "2026-08-17",
  "authority": {
    "repository": "aiappsgbb/threadlight-skills",
    "path": "skills/threadlight-design/references/runtime-policy.json",
    "cross_repository_consumers": false
  },
  "region_policy": {
    "default": "eastus2",
    "eu_residency": ["swedencentral"],
    "selection_rule": "Use an EU region only when the complete required resource set is available there."
  }
}
```

Preserve existing selectors, compatible combinations, route priorities, and tuples.
Add `decision_date` plus either `review_by`, `expiry_condition`, or
`permanent: true` to every route. The `default-agent` route uses:

```json
{
  "decision_date": "2026-08-05",
  "expiry_condition": "Responses works end to end for the generated hosted runtime and every documented channel."
}
```

- [ ] **Step 8: Run policy and shared tests**

Run:

```bash
node --test tests/blueprint/runtime-policy.test.js
python -m pytest skills/_shared/tests/test_manifest.py -v
```

Expected: both commands pass.

- [ ] **Step 9: Commit the shared contracts**

```bash
git add skills/_shared tests/blueprint/runtime-policy.test.js \
  skills/threadlight-design/references/runtime-policy.json
git commit -m "feat(policy): add lifecycle-aware local contracts"
```

### Task 2: Complete the cost engine and add qualification

**Files:**
- Create: `skills/threadlight-consumption-iq/scripts/cost_api.py`
- Create: `skills/threadlight-consumption-iq/scripts/meter_demand.py`
- Create: `skills/threadlight-consumption-iq/scripts/model_catalog.py`
- Create: `skills/threadlight-consumption-iq/references/model-catalog.json`
- Create: `skills/threadlight-consumption-iq/references/cost-manifest.schema.json`
- Create: `skills/threadlight-consumption-iq/scripts/projectors/{content_understanding,content_contextualization,document_intelligence,speech,embeddings,search_agentic,search_semantic,web_grounding}.py`
- Modify: `skills/threadlight-consumption-iq/scripts/{consumption_iq,estimate,emitter,pricing_client,recommender}.py`
- Modify: `skills/threadlight-consumption-iq/scripts/projectors/*.py`
- Modify: `skills/threadlight-consumption-iq/references/*.md`
- Modify: `skills/threadlight-consumption-iq/tests/`
- Create: `skills/threadlight-qualify/SKILL.md`
- Create: `skills/threadlight-qualify/scripts/qualify.py`
- Create: `skills/threadlight-qualify/references/sizing-manifest.schema.json`
- Create: `skills/threadlight-qualify/references/citadel-sizing.json`
- Create: `skills/threadlight-qualify/references/fixtures/sample-qualification/`
- Create: `skills/threadlight-qualify/tests/test_qualify.py`
- Modify: `skills/threadlight-design/SKILL.md`
- Modify: `skills/threadlight-design/references/speckit-template.md`
- Modify: `scripts/build-cowork-zips.sh`

- [ ] **Step 1: Write failing normalized-meter tests**

```python
# skills/threadlight-consumption-iq/tests/test_meter_demand.py
from meter_demand import discover_meter_demands


def test_document_pipeline_emits_extraction_and_embedding_demands():
    profile = {
        "pages_per_month": 100_000,
        "embedding_tokens_per_month": 3_000_000,
        "document_origin": "scanned",
    }
    selectors = {
        "content_understanding": {"enabled": True, "tier": "standard"},
        "embeddings": {"enabled": True, "model": "text-embedding"},
    }
    demands = discover_meter_demands([], profile, selectors)
    assert [d["meter_kind"] for d in demands] == [
        "content-understanding-extraction",
        "embeddings",
    ]
    assert demands[0]["volume_driver"] == {
        "unit": "pages",
        "monthly_quantity": 100_000,
    }


def test_search_features_become_distinct_meters():
    profile = {
        "semantic_ranker_requests_per_month": 20_000,
        "agentic_retrievals_per_month": 10_000,
        "agentic_subquery_fanout": 4,
    }
    demands = discover_meter_demands([], profile, {
        "search_semantic_ranker": True,
        "search_agentic_retrieval": True,
    })
    assert {d["meter_kind"] for d in demands} == {
        "search-semantic-ranker",
        "search-agentic-retrieval",
    }
```

- [ ] **Step 2: Run meter tests and verify failure**

Run:

```bash
python -m pytest skills/threadlight-consumption-iq/tests/test_meter_demand.py -v
```

Expected: import fails because `meter_demand.py` does not exist.

- [ ] **Step 3: Implement deterministic meter discovery**

Implement:

```python
# meter_demand.py
from __future__ import annotations

from typing import Any


def _demand(kind: str, source: str, unit: str, quantity: float) -> dict[str, Any]:
    return {
        "meter_kind": kind,
        "source": source,
        "volume_driver": {"unit": unit, "monthly_quantity": quantity},
    }


def discover_meter_demands(
    resources: list[dict[str, Any]],
    profile: dict[str, Any],
    selectors: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if selectors.get("content_understanding", {}).get("enabled"):
        out.append(_demand(
            "content-understanding-extraction",
            "spec.selector.content_understanding",
            "pages",
            profile["pages_per_month"],
        ))
    if selectors.get("content_contextualization"):
        out.append(_demand(
            "content-understanding-contextualization",
            "spec.selector.content_contextualization",
            "pages-or-images",
            profile["contextualization_items_per_month"],
        ))
    if selectors.get("document_intelligence"):
        out.append(_demand(
            "document-intelligence",
            "spec.selector.document_intelligence",
            "pages",
            profile["pages_per_month"],
        ))
    if selectors.get("speech"):
        out.append(_demand(
            "speech",
            "spec.selector.speech",
            "hours",
            profile["media_hours_per_month"],
        ))
    if selectors.get("embeddings", {}).get("enabled"):
        out.append(_demand(
            "embeddings",
            "spec.selector.embeddings",
            "tokens",
            profile["embedding_tokens_per_month"],
        ))
    if selectors.get("search_agentic_retrieval"):
        out.append(_demand(
            "search-agentic-retrieval",
            "spec.selector.search_agentic_retrieval",
            "retrieval-subqueries",
            profile["agentic_retrievals_per_month"]
            * profile["agentic_subquery_fanout"],
        ))
    if selectors.get("search_semantic_ranker"):
        out.append(_demand(
            "search-semantic-ranker",
            "spec.selector.search_semantic_ranker",
            "requests",
            profile["semantic_ranker_requests_per_month"],
        ))
    if selectors.get("web_grounding"):
        out.append(_demand(
            "web-grounding",
            "spec.selector.web_grounding",
            "transactions",
            profile["web_grounding_transactions_per_month"],
        ))
    return sorted(out, key=lambda item: item["meter_kind"])
```

- [ ] **Step 4: Run meter tests**

Run:

```bash
python -m pytest skills/threadlight-consumption-iq/tests/test_meter_demand.py -v
```

Expected: pass.

- [ ] **Step 5: Write failing catalog and projector tests**

Create parameterized tests that require all meter modules and reject stale catalogs:

```python
class FakePricing:
    def __init__(self, *, unit_price_usd, error):
        self.unit_price_usd = unit_price_usd
        self.error = error

    def get_meter_price(self, meter_kind, selector):
        return {
            "unit_price_usd": self.unit_price_usd,
            "price_source": "test-fixture",
            "error": self.error,
        }


@pytest.mark.parametrize("meter_kind,module_name", [
    ("content-understanding-extraction", "content_understanding"),
    ("content-understanding-contextualization", "content_contextualization"),
    ("document-intelligence", "document_intelligence"),
    ("speech", "speech"),
    ("embeddings", "embeddings"),
    ("search-agentic-retrieval", "search_agentic"),
    ("search-semantic-ranker", "search_semantic"),
    ("web-grounding", "web_grounding"),
])
def test_every_meter_has_a_projector(meter_kind, module_name):
    assert METER_PROJECTOR_REGISTRY[meter_kind].__name__.endswith(module_name)


def test_model_catalog_warns_after_90_days(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "schema": "threadlight.model-catalog/v1",
        "checked_at": "2026-01-01",
        "models": [],
    }))
    loaded = load_model_catalog(catalog, today=date(2026, 5, 1))
    assert loaded["stale"] is True


def test_search_serverless_without_retail_rate_is_not_priceable():
    result = project_search_resource(
        resource={"sku": "serverless"},
        profile={"monthly_queries": 50_000},
        pricing=FakePricing(unit_price_usd=None, error="retail rate unavailable"),
    )
    assert result["pricing_status"] == "not-priceable"
    assert result["monthly_cost_usd"] is None


def test_unregistered_meter_is_emitted_not_dropped():
    result = project_meter_demand(
        {
            "meter_kind": "future-meter",
            "source": "spec.selector.future",
            "volume_driver": {"unit": "requests", "monthly_quantity": 10},
        },
        load_profile={},
        pricing=FakePricing(unit_price_usd=None, error="no projector"),
        selectors={},
    )
    assert result["meter_kind"] == "future-meter"
    assert result["pricing_status"] == "not-priceable"
    assert result["reason"] == "no projector registered"
```

- [ ] **Step 6: Run the catalog/projector tests and verify failure**

Run:

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_model_catalog.py \
  skills/threadlight-consumption-iq/tests/test_meter_projectors.py -v
```

Expected: missing modules, registry, and catalog loader.

- [ ] **Step 7: Implement the dated catalog and meter projector registry**

Use this catalog shape:

```json
{
  "schema": "threadlight.model-catalog/v1",
  "checked_at": "2026-08-17",
  "source": "versioned-fixture",
  "models": [
    {
      "id": "gpt-5.4-mini",
      "family": "current-mini",
      "comparison_group": "mini",
      "input_per_million_usd": null,
      "output_per_million_usd": null,
      "cached_input_per_million_usd": null,
      "batch_discount": null,
      "throughput": {"tokens_per_second": null},
      "price_source": "live-required"
    }
  ]
}
```

`null` means the live API or dated pricing fixture must resolve the field; it is
not converted to zero. Implement `load_model_catalog(path, today)` to parse
`checked_at`, set `stale`, and expose only comparisons in the same
`comparison_group`.

Register all eight meter modules in `projectors/__init__.py`. Each module calls a
shared helper:

```python
def project_usage_meter(*, meter_kind, demand, pricing_client, selector):
    price = pricing_client.get_meter_price(meter_kind, selector)
    quantity = demand["volume_driver"]["monthly_quantity"]
    if price["unit_price_usd"] is None:
        return {
            "meter_kind": meter_kind,
            "pricing_status": "not-priceable",
            "monthly_cost_usd": None,
            "monthly_units_consumed": demand["volume_driver"],
            "price_source": price["price_source"],
            "reason": price["error"],
            "alternatives": [],
        }
    return {
        "meter_kind": meter_kind,
        "pricing_status": "priced",
        "monthly_cost_usd": round(quantity * price["unit_price_usd"], 4),
        "monthly_units_consumed": demand["volume_driver"],
        "price_source": price["price_source"],
        "alternatives": [],
    }
```

Each wrapper declares its exact `METER_KIND` and delegates to this helper. Keep
extraction tier, contextualization tier, DocIntel model type, and Search fan-out
inside the selector passed to pricing.

- [ ] **Step 8: Move fallback rates out of formulas**

Refactor existing AOAI, AI Search, Cosmos, Storage, ACA, APIM, hosted-agent, and
observability projectors so formulas request prices from `PricingClient`. Keep
all fallback values in dated fixture JSON files. Remove:

```python
MODEL_SWAPS = {
    "gpt-4o": "gpt-4o-mini",
    "gpt-4o-mini": "gpt-4o",
}
```

Model alternatives come only from `model_catalog.comparisons_for(model_name)`.
If no comparison exists, emit no model-swap recommendation.

- [ ] **Step 9: Run all projector and pricing tests**

Run:

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/test_projector_*.py \
  skills/threadlight-consumption-iq/tests/test_pricing_client.py \
  skills/threadlight-consumption-iq/tests/test_model_catalog.py \
  skills/threadlight-consumption-iq/tests/test_meter_projectors.py -v
```

Expected: pass; no test performs a network call.

- [ ] **Step 10: Write failing vNext emitter and `--from-profile` tests**

Require:

```python
def test_incomplete_meter_marks_total_incomplete():
    manifest = build_cost_manifest(
        projections=[{
            "meter_kind": "content-understanding-extraction",
            "pricing_status": "not-priceable",
            "monthly_cost_usd": None,
            "reason": "retail rate unavailable",
        }],
        transaction_unit="claim",
        monthly_transactions=1000,
        generated_at="2026-08-17T10:00:00Z",
    )
    assert manifest["status"] == "partial"
    assert manifest["totals"]["complete"] is False
    assert manifest["totals"]["cost_per_transaction_usd"] is None


def test_from_profile_skips_discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(
        consumption_iq,
        "discover_resources",
        lambda **_: pytest.fail("discovery must not run"),
    )
    assert consumption_iq.main([
        "estimate", "--from-profile", str(PROFILE), "--cache", str(tmp_path / "c.json"),
    ]) == 0
```

- [ ] **Step 11: Implement the stable cost API and vNext output**

`cost_api.py` exposes:

```python
def project_profile(
    *,
    load_profile: dict[str, Any],
    resources: list[dict[str, Any]],
    selectors: dict[str, Any],
    pricing: PricingClient,
    transaction_unit: str,
    monthly_transactions: float,
    generated_at: str | None = None,
) -> dict[str, Any]:
    demands = discover_meter_demands(resources, load_profile, selectors)
    resource_lines = [
        project_resource(resource, load_profile, pricing)
        for resource in resources
    ]
    meter_lines = [
        project_meter_demand(demand, load_profile, pricing, selectors)
        for demand in demands
    ]
    return build_cost_manifest(
        projections=[*resource_lines, *meter_lines],
        transaction_unit=transaction_unit,
        monthly_transactions=monthly_transactions,
        generated_at=generated_at,
    )
```

Add `--from-profile`; when supplied, load resources, selectors, and load profile
from that file and never call discovery. Add PTU hourly, one-month, and one-year
commitment lines plus an explicit break-even inequality string. Mark totals
incomplete whenever any line is `not-priceable`.

- [ ] **Step 12: Add `COST-007` tests and implementation**

Extend `test_cost_006.py` with:

```python
def test_cost_007_must_fix_when_meter_is_not_priceable(tmp_path):
    ctx = _make_ctx(recommendations=[])
    manifest_path = ctx.root / "specs" / "cost-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "2.0",
        "resources": [{
            "meter_kind": "content-understanding-extraction",
            "pricing_status": "not-priceable",
            "reason": "retail rate unavailable",
        }],
        "recommendations": [],
    }), encoding="utf-8")
    finding = _findings_by_id(ctx)["COST-007"]
    assert finding.status == "must-fix"


def test_cost_007_not_verified_when_meter_discovery_is_incomplete(tmp_path):
    ctx = _make_ctx(recommendations=[])
    manifest_path = ctx.root / "specs" / "cost-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "2.0",
        "meter_coverage": {"status": "not-verified"},
        "resources": [],
        "recommendations": [],
    }), encoding="utf-8")
    assert _findings_by_id(ctx)["COST-007"].status == "not-verified"


def test_cost_007_passes_when_every_detected_meter_is_priced(tmp_path):
    ctx = _make_ctx(recommendations=[])
    manifest_path = ctx.root / "specs" / "cost-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "2.0",
        "meter_coverage": {"status": "complete"},
        "resources": [{
            "meter_kind": "embeddings",
            "pricing_status": "priced",
            "monthly_cost_usd": 12.5,
        }],
        "recommendations": [],
    }), encoding="utf-8")
    assert _findings_by_id(ctx)["COST-007"].status == "pass"
```

Register `COST-007` under the existing cost pillar and add a remediation recipe.
Do not alter COST-005/006 thresholds.

- [ ] **Step 13: Run Consumption IQ and production-ready cost tests**

Run:

```bash
python -m pytest skills/threadlight-consumption-iq/tests/ -v
python -m pytest \
  skills/threadlight-production-ready/tests/test_cost_006.py \
  skills/threadlight-production-ready/tests/test_recipe_catalog.py -v
```

Expected: pass.

- [ ] **Step 14: Write failing qualification tests**

```python
# skills/threadlight-qualify/tests/test_qualify.py
PINNED = "2026-08-17T10:00:00Z"


def complete_profile():
    return {
        "customer_brief": "Triage incoming claims and ground decisions in policy.",
        "workload_class": "document-batch",
        "annual_transaction_volume": 120_000,
        "transaction_unit": "claim",
        "pages_per_transaction": 4,
        "document_origin": "mixed",
        "turns_per_conversation": 1,
        "tokens_per_turn_estimate": 1500,
        "peak_concurrency": 20,
        "business_hours_only": True,
        "sites_or_entities": 2,
        "data_residency": "EU",
        "pinned_region": "swedencentral",
    }


def test_missing_required_volume_fails_without_outputs(tmp_path):
    profile = complete_profile()
    del profile["annual_transaction_volume"]
    with pytest.raises(QualificationError, match="annual_transaction_volume"):
        run_qualification(profile, output_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_qualification_is_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    run_qualification(complete_profile(), output_dir=first, generated_at=PINNED)
    run_qualification(complete_profile(), output_dir=second, generated_at=PINNED)
    assert (first / "qualification" / "sizing-manifest.json").read_bytes() == (
        second / "qualification" / "sizing-manifest.json"
    ).read_bytes()


def test_roi_is_only_emitted_with_current_cost_inputs(tmp_path):
    run_qualification(complete_profile(), output_dir=tmp_path, generated_at=PINNED)
    assert not (tmp_path / "qualification" / "roi.md").exists()


def test_roi_is_emitted_when_current_cost_inputs_are_complete(tmp_path):
    profile = complete_profile()
    profile.update({
        "current_annual_cost_usd": 480_000,
        "current_handling_minutes_per_transaction": 12,
    })
    run_qualification(profile, output_dir=tmp_path, generated_at=PINNED)
    assert (tmp_path / "qualification" / "roi.md").is_file()


def test_assumptions_have_provenance_and_hub_sizing_is_separate(tmp_path):
    run_qualification(complete_profile(), output_dir=tmp_path, generated_at=PINNED)
    manifest = json.loads(
        (tmp_path / "qualification" / "sizing-manifest.json").read_text()
    )
    assert all(
        assumption["provenance"] in {"user-supplied", "derived", "fixture", "live"}
        for assumption in manifest["assumptions"]
    )
    assert manifest["hub_sizing"]["kind"] == "citadel-hub"
    assert manifest["application_sizing"]["kind"] == "threadlight-application"
```

- [ ] **Step 15: Run qualification tests and verify failure**

Run:

```bash
python -m pytest skills/threadlight-qualify/tests/test_qualify.py -v
```

Expected: missing skill module.

- [ ] **Step 16: Implement the thin qualification entry point**

`qualify.py` validates the required interview fields, derives monthly volumes,
builds MVP and production profiles, calls `cost_api.project_profile`, writes the
three mandatory artifacts through the shared atomic writer, and emits the
optional ROI artifact only when both current-cost fields are supplied.

Use this public API:

```python
def run_qualification(
    profile: dict[str, Any],
    *,
    output_dir: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    validated = validate_qualification_profile(profile)
    sizing = project_qualification(validated, generated_at=generated_at)
    write_qualification_outputs(sizing, output_dir / "qualification")
    return sizing
```

Add a concise `SKILL.md` with `metadata.version: "0.1.0"` and explicit
`USE FOR`/`DO NOT USE FOR`. Add the sizing handoff to threadlight-design:
when `qualification/sizing-manifest.json` exists, seed SPEC section 12 from its
normalized profile instead of re-interviewing.

- [ ] **Step 17: Add Cowork packaging and verify limits**

Add `threadlight-qualify` to `COWORK_SAFE_SKILLS`, but do not archive its tests or
golden fixtures. Build its outer archive from exactly:

```text
SKILL.md
scripts/qualify.py
references/sizing-manifest.schema.json
references/citadel-sizing.json
vendor/model-catalog.json
vendor/cost-runtime.zip
```

Build `vendor/cost-runtime.zip` from the importable `cost_api.py`,
`meter_demand.py`, `model_catalog.py`, `emitter.py`, `pricing_client.py`, and the
complete `projectors/` package. Update `qualify.py` so repo execution imports the
normal Consumption IQ module tree, while Cowork execution adds
`vendor/cost-runtime.zip` to `sys.path` and supplies
`vendor/model-catalog.json` to the same `project_profile` API. This keeps one
projector implementation while counting the runtime bundle as one Cowork
companion.

Use a temporary staging directory created by `mktemp -d`, remove it through a
shell `trap`, and preserve the existing flat-archive checks. Run:

```bash
bash scripts/build-cowork-zips.sh
unzip -l docs/downloads/threadlight-qualify.zip
python -c 'import io,zipfile; outer=zipfile.ZipFile("docs/downloads/threadlight-qualify.zip"); inner=zipfile.ZipFile(io.BytesIO(outer.read("vendor/cost-runtime.zip"))); print("\n".join(inner.namelist()))'
```

Expected: `SKILL.md` is at the outer archive root; `cost_api.py` and
`projectors/__init__.py` are in the inner archive; there are five outer
companion files; no runtime dependency on `az`, `azd`, Bicep, Docker, or
customer credentials.

- [ ] **Step 18: Run qualification and cost golden tests**

Run:

```bash
python -m pytest \
  skills/threadlight-qualify/tests/ \
  skills/threadlight-consumption-iq/tests/test_e2e.py \
  skills/threadlight-consumption-iq/tests/test_e2e_presales.py \
  skills/threadlight-consumption-iq/tests/test_e2e_presales_topology.py -v
```

Expected: pass with updated reviewed goldens.

- [ ] **Step 19: Commit qualification and cost**

```bash
git add skills/threadlight-consumption-iq skills/threadlight-qualify \
  skills/threadlight-design scripts/build-cowork-zips.sh \
  docs/downloads/threadlight-qualify.zip \
  skills/threadlight-production-ready
git commit -m "feat(cost): close qualification and meter coverage gaps"
```

### Task 3: Add the mock-to-real connect leg

**Files:**
- Create: `skills/threadlight-connect/SKILL.md`
- Create: `skills/threadlight-connect/scripts/connect.py`
- Create: `skills/threadlight-connect/references/connect-manifest.schema.json`
- Create: `skills/threadlight-connect/references/data-contract.schema.json`
- Create: `skills/threadlight-connect/tests/test_connect.py`

- [ ] **Step 1: Write failing contract/state tests**

```python
def test_contract_extraction_records_only_fields_the_tool_reads():
    contract = extract_contract(
        tool_source='return {"id": row["id"], "status": row.get("status")}',
        sample={"id": "A-1", "status": "open", "internal": "secret"},
    )
    assert [field["name"] for field in contract["fields"]] == ["id", "status"]
    assert contract["fields"][0]["required"] is True
    assert contract["fields"][1]["required"] is False


def test_failed_conformance_never_produces_real_verified():
    result = transition_integration(
        current="mock",
        conformance={"status": "failed", "differences": ["status missing"]},
        identity={"status": "verified"},
        apply=True,
    )
    assert result["integration_state"] == "mock"
    assert result["target_state"] == "real-drift"
    assert result["edits"] == []


def test_apply_is_required_for_runtime_edits():
    result = transition_integration(
        current="mock",
        conformance={"status": "passed", "differences": []},
        identity={"status": "verified"},
        apply=False,
    )
    assert result["integration_state"] == "mock"
    assert result["target_state"] == "real-verified"
    assert result["edits"] == []
    assert result["apply_plan"]


def test_missing_obo_evidence_cannot_become_real_verified():
    result = transition_integration(
        current="mock",
        conformance={"status": "passed", "differences": []},
        identity={"status": "not-verified", "obo_user_scope": False},
        apply=True,
    )
    assert result["integration_state"] == "mock"
    assert result["target_state"] == "real-unverified"
    assert result["edits"] == []


def test_apply_after_all_evidence_records_every_changed_path():
    result = transition_integration(
        current="mock",
        conformance={"status": "passed", "differences": []},
        identity={
            "status": "verified",
            "obo_user_scope": True,
            "required_roles_revalidated": True,
        },
        apply=True,
    )
    assert result["integration_state"] == "real-verified"
    assert result["target_state"] == "real-verified"
    assert result["edits"] == ["specs/SPEC.md", "infra/mcp-config.json"]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest skills/threadlight-connect/tests/test_connect.py -v
```

Expected: import or missing-function failure.

- [ ] **Step 3: Implement inspect/contract/verify/plan/apply/emit**

Implement an explicit state enum:

```python
INTEGRATION_STATES = frozenset({
    "mock", "real-unverified", "real-verified", "real-drift",
})
```

`transition_integration` computes `target_state` as `real-drift` on field-level
conformance differences, `real-unverified` when OBO/RBAC evidence is missing,
and `real-verified` only when conformance, OBO user scoping, and required-role
revalidation are verified. `integration_state` remains the persisted current
state until a successful `apply=True`. `apply=False` always emits an apply plan
and performs no writes. `apply=True` uses atomic writes and records every
changed path in `connect-manifest.json`.

The generated conformance test must report:

```json
{
  "field": "status",
  "expected": "string|required",
  "actual": "missing",
  "path": "$.items[0].status"
}
```

- [ ] **Step 4: Add the skill contract and schemas**

Create a `SKILL.md` under the 1024-character description cap. State:

- manual handoff; `threadlight-auto` does not run it;
- customer-specific mapping is excluded;
- OBO scaffolding composes with the catalog skill that owns OBO;
- no endpoint call is represented as verified without evidence.

- [ ] **Step 5: Run connect tests**

Run:

```bash
python -m pytest skills/threadlight-connect/tests/ -v
```

Expected: pass.

- [ ] **Step 6: Commit connect**

```bash
git add skills/threadlight-connect
git commit -m "feat(connect): add evidence-based mock to real swaps"
```

### Task 4: Add the grounding evidence leg

**Files:**
- Create: `skills/threadlight-ground/SKILL.md`
- Create: `skills/threadlight-ground/scripts/ground.py`
- Create: `skills/threadlight-ground/references/ground-manifest.schema.json`
- Create: `skills/threadlight-ground/tests/test_ground.py`
- Modify: `skills/threadlight-design/references/speckit-template.md`

- [ ] **Step 1: Write failing evidence-mapping tests**

```python
PINNED = "2026-08-17T10:00:00Z"


def source(permission_model="acl"):
    return {
        "id": "policy-library",
        "type": "documents",
        "permission_model": permission_model,
        "refresh_cadence": "daily",
        "citation_required": True,
        "refuse_when_unsupported": True,
    }


def by_id(manifest):
    return {item["id"]: item for item in manifest["findings"]}


def test_missing_principals_is_not_verified():
    manifest = assess_grounding(
        sources=[source()],
        acl_runs=[],
        citation_runs=[],
        refusal_runs=[],
        generated_at=PINNED,
    )
    finding = by_id(manifest)["GRD-001"]
    assert finding["status"] == "not-verified"


def test_identical_protected_results_for_incompatible_principals_is_must_fix():
    runs = [
        {"principal": "entitled", "document_ids": ["A", "B"]},
        {"principal": "unentitled", "document_ids": ["A", "B"]},
    ]
    assert by_id(assess_grounding(
        sources=[source(permission_model="acl")],
        acl_runs=runs,
        citation_runs=[],
        refusal_runs=[],
        generated_at=PINNED,
    ))["GRD-001"]["status"] == "must-fix"


def test_citation_must_exist_in_retrieved_set():
    result = validate_citations(
        citations=["doc-9"],
        retrieved_ids=["doc-1", "doc-2"],
    )
    assert result == {
        "status": "must-fix",
        "missing_from_retrieval": ["doc-9"],
    }
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest skills/threadlight-ground/tests/test_ground.py -v
```

Expected: missing module/functions.

- [ ] **Step 3: Implement the coordinator**

`ground.py` accepts already-produced retrieval and evaluation results. It does
not implement Foundry IQ ingestion or an evaluator. Implement:

```python
def assess_grounding(
    *,
    sources: list[dict[str, Any]],
    acl_runs: list[dict[str, Any]],
    citation_runs: list[dict[str, Any]],
    refusal_runs: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    findings = [
        assess_acl(sources, acl_runs),
        assess_citations(citation_runs),
        assess_refusal(refusal_runs),
    ]
    status = "partial" if any(
        finding["status"] == "not-verified" for finding in findings
    ) else "complete"
    return build_envelope(
        schema="threadlight.ground/v1",
        tool_version=VERSION,
        status=status,
        generated_at=generated_at,
        valid_for_hours=24,
        source_oldest_at=oldest_timestamp(
            [*acl_runs, *citation_runs, *refusal_runs]
        ),
        findings=findings,
        payload={
            "sources": sources,
            "telemetry": aggregate_telemetry(
                [*acl_runs, *citation_runs, *refusal_runs]
            ),
        },
    )


def oldest_timestamp(runs: list[dict[str, Any]]) -> str | None:
    timestamps = sorted(
        run["captured_at"] for run in runs if run.get("captured_at")
    )
    return timestamps[0] if timestamps else None


def aggregate_telemetry(runs: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "retrieval_count": float(len(runs)),
        "subqueries": float(sum(run.get("subqueries", 0) for run in runs)),
        "tokens": float(sum(run.get("tokens", 0) for run in runs)),
    }
```

Persist only document IDs, metrics, source metadata, and aggregate token/fan-out
values. Do not persist retrieved content.

- [ ] **Step 4: Add knowledge-source SPEC contract**

Add a section to the design template with exact fields:

```yaml
knowledge_sources:
  - id: policy-library
    type: documents
    permission_model: acl
    refresh_cadence: daily
    citation_required: true
    refuse_when_unsupported: true
    acl_probe_principals:
      - entitled
      - unentitled
```

- [ ] **Step 5: Add skill contract and run tests**

Run:

```bash
python -m pytest skills/threadlight-ground/tests/ -v
```

Expected: pass.

- [ ] **Step 6: Commit grounding**

```bash
git add skills/threadlight-ground \
  skills/threadlight-design/references/speckit-template.md
git commit -m "feat(ground): add ACL-aware grounding evidence"
```

### Task 5: Add guarded load validation

**Files:**
- Create: `skills/threadlight-loadtest/SKILL.md`
- Create: `skills/threadlight-loadtest/scripts/loadtest.py`
- Create: `skills/threadlight-loadtest/scripts/adapters.py`
- Create: `skills/threadlight-loadtest/references/load-manifest.schema.json`
- Create: `skills/threadlight-loadtest/tests/test_loadtest.py`

- [ ] **Step 1: Write failing safety and measurement tests**

```python
PINNED = "2026-08-17T10:00:00Z"


class FakeAdapter:
    name = "fake"

    def __init__(self):
        self.calls = []

    def run(self, load_profile):
        self.calls.append(load_profile)
        return {
            "status": "complete",
            "samples": [
                {"latency_ms": 100, "tokens": 100, "success": True},
                {"latency_ms": 200, "tokens": 120, "success": True},
            ],
        }


class PartialAdapter:
    name = "partial"

    def run(self, load_profile):
        return {
            "status": "partial",
            "samples": [{"latency_ms": 100, "tokens": 100, "success": True}],
            "error": "load generator stopped before hold duration",
        }


def profile(projected_token_cost_usd):
    return {
        "peak_requests_per_second": 2,
        "hold_seconds": 10,
        "projected_token_cost_usd": projected_token_cost_usd,
    }


def sample(latency_ms):
    return {"latency_ms": latency_ms, "tokens": 100, "success": True}


def by_id(manifest):
    return {item["id"]: item for item in manifest["findings"]}


def test_budget_guard_aborts_before_adapter_runs():
    adapter = FakeAdapter()
    result = run_loadtest(
        profile=profile(projected_token_cost_usd=50),
        budget_ceiling_usd=10,
        endpoint_class="non-production",
        allow_production=False,
        adapter=adapter,
        generated_at=PINNED,
    )
    assert result["status"] == "aborted"
    assert adapter.calls == []


def test_production_requires_explicit_confirmation():
    result = run_loadtest(
        profile=profile(projected_token_cost_usd=1),
        budget_ceiling_usd=10,
        endpoint_class="production",
        allow_production=False,
        adapter=FakeAdapter(),
        generated_at=PINNED,
    )
    assert result["status"] == "aborted"
    assert by_id(result)["LOAD-001"]["status"] == "must-fix"


def test_percentiles_are_computed_from_observed_samples():
    metrics = summarize_samples([
        sample(100), sample(200), sample(300), sample(400), sample(500),
    ])
    assert metrics["p50_latency_ms"] == 300
    assert metrics["p95_latency_ms"] == 500
    assert metrics["tokens_per_request"] > 0


def test_partial_run_is_diagnostic_and_never_updates_spec():
    result = run_loadtest(
        profile=profile(projected_token_cost_usd=1),
        budget_ceiling_usd=10,
        endpoint_class="non-production",
        allow_production=False,
        adapter=PartialAdapter(),
        generated_at=PINNED,
    )
    assert result["status"] == "partial"
    assert by_id(result)["LOAD-002"]["status"] == "not-verified"
    assert "spec_update_plan" not in result
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest skills/threadlight-loadtest/tests/test_loadtest.py -v
```

Expected: missing module/functions.

- [ ] **Step 3: Implement adapter selection and guards**

Define:

```python
class LoadAdapter(Protocol):
    name: str
    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


def select_adapter(available_commands: set[str]) -> str | None:
    for candidate in ("k6", "locust"):
        if candidate in available_commands:
            return candidate
    return None
```

Do not install a missing dependency. No adapter produces a `partial` manifest
with `LOAD-002: not-verified`. Budget and production guards return `aborted`
before calling the adapter.

- [ ] **Step 4: Implement measurements and SPEC update plan**

Compute achieved RPS, p50/p95/p99, error rate, cold-start latency, time-to-scale,
and observed tokens. Emit a patch plan that adds:

```yaml
observed_load:
  captured_at: 2026-08-17T10:00:00Z
  achieved_requests_per_second: 12.1
  p95_latency_ms: 1840
  tokens_per_request: 1320
```

Do not overwrite declared values. Consumption IQ reads `observed_load` only
when the manifest is `complete` and fresh.

- [ ] **Step 5: Run load tests**

Run:

```bash
python -m pytest skills/threadlight-loadtest/tests/ -v
```

Expected: pass without k6, locust, Azure, or network.

- [ ] **Step 6: Commit load validation**

```bash
git add skills/threadlight-loadtest
git commit -m "feat(loadtest): add budget-capped load evidence"
```

### Task 6: Add compatibility and preview-drift scanning

**Files:**
- Create: `skills/threadlight-upgrade/SKILL.md`
- Create: `skills/threadlight-upgrade/scripts/upgrade.py`
- Create: `skills/threadlight-upgrade/references/compatibility-matrix.json`
- Create: `skills/threadlight-upgrade/references/upgrade-manifest.schema.json`
- Create: `skills/threadlight-upgrade/tests/test_upgrade.py`

- [ ] **Step 1: Write failing matrix and migration-plan tests**

```python
def project(*, dependencies=None, runtime_policy=None):
    return {
        "dependencies": dependencies or {},
        "runtime_policy": runtime_policy or {},
    }


def matrix(
    *,
    last_reviewed="2026-08-17",
    review_window_days=90,
    stable=None,
    triggered_expiry_conditions=None,
):
    return {
        "last_reviewed": last_reviewed,
        "review_window_days": review_window_days,
        "stable": stable or {},
        "triggered_expiry_conditions": triggered_expiry_conditions or [],
    }


def by_id(manifest):
    return {item["id"]: item for item in manifest["findings"]}


def test_stale_matrix_is_reported_without_guessing_latest():
    result = scan_project(
        project=project(),
        matrix=matrix(last_reviewed="2026-01-01", review_window_days=90),
        today=date(2026, 8, 17),
    )
    assert by_id(result)["UPG-001"]["status"] == "should-fix"
    assert "latest_version" not in result


def test_prerelease_pin_generates_ordered_plan():
    result = scan_project(
        project=project(dependencies={"agent-framework": "2.0.0b1"}),
        matrix=matrix(stable={"agent-framework": "2.0.0"}),
        today=date(2026, 8, 17),
    )
    assert result["migration_plan"]["items"] == [{
        "order": 1,
        "path": "pyproject.toml",
        "reason": "agent-framework is pinned to prerelease 2.0.0b1",
        "from": "2.0.0b1",
        "to": "2.0.0",
    }]


def test_expired_runtime_policy_decision_becomes_review_item():
    result = scan_project(
        project=project(runtime_policy={"default-agent": "invocations"}),
        matrix=matrix(triggered_expiry_conditions=["responses-end-to-end"]),
        today=date(2026, 8, 17),
    )
    assert by_id(result)["UPG-002"]["status"] == "should-fix"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest skills/threadlight-upgrade/tests/test_upgrade.py -v
```

Expected: missing module/functions.

- [ ] **Step 3: Implement matrix parsing and plan-only scanning**

The matrix records `surface`, `target`, `state`, `source`, `last_reviewed`, and
`review_window_days`. `scan_project` compares local pins and preview flags and
returns ordered plan items. External-source failure produces:

```json
{
  "id": "UPG-003",
  "status": "not-verified",
  "detail": "Official source unavailable; no latest version was inferred."
}
```

Do not implement an `--apply` flag.

- [ ] **Step 4: Add skill contract and run tests**

Run:

```bash
python -m pytest skills/threadlight-upgrade/tests/ -v
```

Expected: pass.

- [ ] **Step 5: Commit upgrade scanning**

```bash
git add skills/threadlight-upgrade
git commit -m "feat(upgrade): add compatibility drift plans"
```

### Task 7: Integrate safe-check, production-ready, auto, and Canvas

**Files:**
- Modify: `skills/threadlight-safe-check/scripts/safe_check.py`
- Modify: `skills/threadlight-safe-check/SKILL.md`
- Modify: `examples/returns-triage-governed/tests/safe_check.py`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py`
- Modify: `skills/threadlight-production-ready/tests/test_leg_manifests.py`
- Create: `skills/threadlight-production-ready/references/remediation-recipes/{INT-001,INT-002,INT-003,INT-004,GRD-001,GRD-002,GRD-003,GRD-004,LOAD-001,LOAD-002,LOAD-003,UPG-001,UPG-002,UPG-003}.md`
- Modify: relevant existing production-ready pillar references
- Modify: `skills/threadlight-auto/references/orchestrator.py`
- Modify: `skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
- Modify: `.github/extensions/threadlight-lifecycle/lib/{artifact-reader,lifecycle-registry,projector}.mjs`
- Modify: `tests/canvas/{artifact-reader,lifecycle-registry,projector}.test.mjs`
- Modify: `tests/canvas/fixtures.mjs`

- [ ] **Step 1: Read the example-specific instructions**

Run:

```bash
sed -n '1,240p' examples/returns-triage-governed/AGENTS.md
```

Expected: implementation agent records and follows every rule before touching
the example copy.

- [ ] **Step 2: Write failing real-vs-mock safe-check tests**

Add tests around a pure helper:

```python
def test_real_spec_with_mock_runtime_is_gap():
    gaps = integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"url": "https://mock.example/mcp"}}},
    )
    assert gaps == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]


def test_mock_spec_with_mock_runtime_is_allowed():
    assert integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "mock"}],
        mcp_config={"servers": {"erp": {"url": "https://mock.example/mcp"}}},
    ) == []
```

- [ ] **Step 3: Implement and synchronize the safe-check helper**

Parse integrations from the deployment manifest snapshot and effective
`mcp-config.json`. Add gaps only for a certain contradiction. Missing config
metadata must not be invented as a mock endpoint.

Keep canonical and example copies synchronized; add a test that compares the
helper source or complete script bytes after approved example-specific changes.

- [ ] **Step 4: Write failing production-ready manifest tests**

```python
def gap_manifest(name, **body):
    generated_at = body.pop("generated_at", _iso(datetime.now(timezone.utc)))
    return {
        "schema": f"threadlight.{name}/v1",
        "tool_version": "0.1.0",
        "generated_at": generated_at,
        "freshness": {"valid_for_hours": 24, "source_oldest_at": None},
        **body,
    }


@pytest.mark.parametrize("name,finding_id", [
    ("connect", "INT-001"),
    ("ground", "GRD-001"),
    ("load", "LOAD-001"),
    ("upgrade", "UPG-001"),
])
def test_missing_new_leg_manifest_is_not_verified(name, finding_id):
    findings = pr._check_gap_leg_manifests(_make_ctx())
    assert _by_id(findings)[finding_id].status == "not-verified"


def test_executed_acl_failure_is_must_fix():
    ctx = _make_ctx(manifests={
        "ground-manifest.json": gap_manifest(
            "ground",
            status="complete",
            findings=[{"id": "GRD-001", "status": "must-fix"}],
        ),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["GRD-001"].status == "must-fix"


def test_aborted_load_manifest_never_counts_as_pass():
    ctx = _make_ctx(manifests={
        "load-manifest.json": gap_manifest(
            "load",
            status="aborted",
            findings=[{"id": "LOAD-001", "status": "must-fix"}],
        ),
    })
    assert _by_id(pr._check_gap_leg_manifests(ctx))["LOAD-001"].status == "must-fix"


def test_partial_or_stale_pass_evidence_is_not_verified():
    stale = _iso(datetime.now(timezone.utc) - timedelta(days=2))
    ctx = _make_ctx(manifests={
        "connect-manifest.json": gap_manifest(
            "connect",
            status="partial",
            findings=[{"id": "INT-001", "status": "pass"}],
        ),
        "upgrade-manifest.json": gap_manifest(
            "upgrade",
            generated_at=stale,
            status="complete",
            findings=[{"id": "UPG-001", "status": "pass"}],
        ),
    })
    findings = _by_id(pr._check_gap_leg_manifests(ctx))
    assert findings["INT-001"].status == "not-verified"
    assert findings["UPG-001"].status == "not-verified"
```

- [ ] **Step 5: Implement manifest readers in existing pillars**

Add IDs to existing pillars:

- `INT-001..004`: supply-chain and reliability;
- `GRD-001..004`: responsible-ai and identity-access;
- `LOAD-001..003`: reliability and cost;
- `UPG-001..003`: model-lifecycle and supply-chain.

Use a single helper:

```python
def _leg_finding(
    ctx: RepoContext,
    *,
    filename: str,
    source_id: str,
    target_id: str,
) -> Finding:
    data = _load_leg_manifest(ctx, filename)
    if not isinstance(data, dict):
        return _mk_finding(target_id, status="not-verified",
                           detail=f"Run the producing skill to create specs/{filename}.")
    source = next(
        (item for item in data.get("findings", []) if item.get("id") == source_id),
        None,
    )
    if isinstance(source, dict) and source.get("status") == "must-fix":
        return _mk_finding(target_id, status="must-fix",
                           detail=source.get("detail", "Executed evidence failed."))
    if data.get("status") == "aborted":
        return _mk_finding(target_id, status="must-fix",
                           detail=f"specs/{filename} records an aborted run.")
    if data.get("status") != "complete" or not data.get("_fresh"):
        return _mk_finding(target_id, status="not-verified",
                           detail=f"specs/{filename} is partial, stale, or invalid.")
    if source is None:
        return _mk_finding(target_id, status="not-verified",
                           detail=f"specs/{filename} has no {source_id} evidence.")
    return _mk_finding(target_id, status=source["status"],
                       detail=source.get("detail", ""))
```

Extend `_load_leg_manifest` without breaking existing govern/evals/red-team
manifests: for the new envelope, compute `_fresh` from `generated_at` plus
`freshness.valid_for_hours`; otherwise retain the existing `captured_at` and
90-day behavior. Add remediation recipes and pillar docs for every registered
ID.

- [ ] **Step 6: Run safe-check and production-ready tests**

Run:

```bash
python -m pytest \
  skills/threadlight-safe-check/tests/ \
  skills/threadlight-production-ready/tests/test_leg_manifests.py \
  skills/threadlight-production-ready/tests/test_recipe_catalog.py \
  skills/threadlight-production-ready/tests/test_end_to_end.py -v
```

Expected: pass. If safe-check has no pytest directory, run its shipped standalone
test command documented in `SKILL.md` plus the example test.

- [ ] **Step 7: Write failing auto handoff tests**

```python
def test_new_live_legs_are_manual_handoffs_not_auto_stages(tmp_path):
    decision = decide(tmp_path)
    assert decision["manual_handoffs"] == [
        "threadlight-connect",
        "threadlight-ground",
        "threadlight-loadtest",
        "threadlight-upgrade",
    ]
    assert not {
        "connect", "ground", "loadtest", "upgrade",
    }.intersection(decision["stages"])
```

- [ ] **Step 8: Implement manual handoff projection**

Keep the existing automatic `STAGES` list. Add:

```python
MANUAL_HANDOFFS = {
    "threadlight-connect": "specs/connect-manifest.json",
    "threadlight-ground": "specs/ground-manifest.json",
    "threadlight-loadtest": "specs/load-manifest.json",
    "threadlight-upgrade": "specs/upgrade-manifest.json",
}
```

Return each handoff as `ready`, `complete`, `partial`, or `aborted` based on its
manifest. Never dispatch it from the stage runner.

- [ ] **Step 9: Run auto tests**

Run:

```bash
python -m pytest skills/threadlight-auto/tests/ -v
```

Expected: pass.

- [ ] **Step 10: Write failing Canvas registry and evidence tests**

Require five new registry entries, artifact allowlisting, and envelope status
mapping:

```javascript
test('registry exposes all 22 skills', () => {
  assert.equal(SKILL_REGISTRY.length, 22);
});

test('partial and aborted envelopes do not render complete', async () => {
  const partial = await projectFixture({ path: 'specs/ground-manifest.json', status: 'partial' });
  const aborted = await projectFixture({ path: 'specs/load-manifest.json', status: 'aborted' });
  assert.equal(partial.status, 'running');
  assert.equal(aborted.status, 'failed');
});
```

- [ ] **Step 11: Implement Canvas registry, reader, and projector updates**

Add allowlisted paths:

```javascript
"qualification/sizing-manifest.json",
"specs/connect-manifest.json",
"specs/ground-manifest.json",
"specs/load-manifest.json",
"specs/upgrade-manifest.json",
```

Add `threadlight-qualify` to Design and the four live legs as manual/advisory
skills in the appropriate phases. Map envelope `partial` to running and
`aborted` to failed. Add next intents that ask chat to invoke the named skill;
do not create an automatic live action.

- [ ] **Step 12: Run Canvas tests**

Run:

```bash
node --test tests/canvas/*.test.mjs
```

Expected: pass.

- [ ] **Step 13: Commit integration surfaces**

```bash
git add skills/threadlight-safe-check examples/returns-triage-governed \
  skills/threadlight-production-ready skills/threadlight-auto \
  .github/extensions/threadlight-lifecycle tests/canvas
git commit -m "feat(lifecycle): integrate gap evidence across gates and canvas"
```

### Task 8: Update docs, GitHub Pages, CI, and release metadata

**Files:**
- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `CHANGELOG.md`
- Modify: `plugin.json`
- Modify: `scripts/build_process_library.py`
- Modify: `docs/assets/process-library.json`
- Modify: `tests/blueprint/*.test.js`
- Modify: `docs/funnel.html`
- Modify: `docs/production.html`
- Modify: `docs/self-improving.html`
- Modify: `docs/index.html`
- Modify: relevant `docs/assets/*.js`
- Modify: `tests/playwright/tests/*.spec.mjs`
- Modify: `.github/workflows/python-pytest.yml`

- [ ] **Step 1: Confirm PR #111 is merged before touching shared release text**

Run:

```bash
gh pr view 111 --repo aiappsgbb/threadlight-skills \
  --json state,mergedAt --jq '[.state,.mergedAt] | @tsv'
```

Expected: `MERGED` with a non-null timestamp. If it is still open, stop this task
without editing `CHANGELOG.md`; all prior tasks remain valid.

- [ ] **Step 2: Integrate the current main branch**

Run:

```bash
git fetch origin
git rebase origin/main
git --no-pager status --short
```

Expected: clean worktree after resolving only reviewed conflicts. Do not discard
either side of `CHANGELOG.md`.

- [ ] **Step 3: Write failing 22-skill and process-library tests**

Extend tests with:

```javascript
test('published surfaces enumerate the 22-skill pack', () => {
  assert.match(read('plugin.json'), /22 total/);
  assert.match(read('README.md'), /threadlight-qualify/);
  assert.match(read('THREADLIGHT.md'), /threadlight-upgrade/);
});
```

Update process-library expectations so:

- document/knowledge workloads include `threadlight-ground`;
- external integrations include `threadlight-connect`;
- high-volume workloads can include `threadlight-loadtest`;
- every entry can point to `threadlight-qualify` as a no-repo entry, but it is
  not injected into the deployed runtime skill list;
- artifact mappings include all new manifests.

- [ ] **Step 4: Run Blueprint tests and verify failure**

Run:

```bash
node --test tests/blueprint/*.test.js
```

Expected: failures on count, missing skills, or artifact mappings.

- [ ] **Step 5: Update generated process metadata**

Extend `CANON`, `derive_build_skills`, and `SKILL_ARTIFACTS` with the new skills.
Regenerate `docs/assets/process-library.json` from its documented source. Do not
hand-edit the generated asset.

- [ ] **Step 6: Update README, THREADLIGHT, changelog, and plugin metadata**

Set plugin version to `1.12.0` and description to `21 pipeline skills +
threadlight-auto orchestrator (22 total)`. Add all five new skills to the table,
chain, entry picker, USE FOR keywords, and manual-handoff guidance. Record local
runtime-policy vNext without claiming cross-repo authority.

- [ ] **Step 7: Update GitHub Pages content**

Preserve layout and styles. Add:

- `threadlight-qualify` to the top of the funnel as the no-repo entry;
- connect, ground, and load evidence to production progression;
- upgrade scanning to lifecycle/self-improvement;
- accurate 22-skill count wherever 17 is currently rendered.

Do not add a new page unless the existing funnel/production/self-improving pages
cannot express the content.

- [ ] **Step 8: Write/adjust site tests**

Add assertions for the five skill names, links to their repository folders,
22-skill count, and no broken navigation. Run:

```bash
node --test tests/blueprint/*.test.js
cd tests/playwright && npm ci --ignore-scripts && npx playwright test tests/site.spec.mjs tests/how-it-works.spec.mjs
```

Expected: all selected tests pass.

- [ ] **Step 9: Add new pytest suites to the existing workflow**

Add hard-fail steps for:

```yaml
- name: Run threadlight shared-contract tests
  run: python -m pytest skills/_shared/tests/ -v
- name: Run threadlight-qualify tests
  run: python -m pytest skills/threadlight-qualify/tests/ -v
- name: Run threadlight-connect tests
  run: python -m pytest skills/threadlight-connect/tests/ -v
- name: Run threadlight-ground tests
  run: python -m pytest skills/threadlight-ground/tests/ -v
- name: Run threadlight-loadtest tests
  run: python -m pytest skills/threadlight-loadtest/tests/ -v
- name: Run threadlight-upgrade tests
  run: python -m pytest skills/threadlight-upgrade/tests/ -v
```

- [ ] **Step 10: Run repository validation**

Run:

```bash
python scripts/ci/check-skill-description-length.py
python scripts/ci/run-standalone-tests.py
python -m pytest \
  skills/_shared/tests/ \
  skills/threadlight-qualify/tests/ \
  skills/threadlight-consumption-iq/tests/ \
  skills/threadlight-connect/tests/ \
  skills/threadlight-ground/tests/ \
  skills/threadlight-loadtest/tests/ \
  skills/threadlight-upgrade/tests/ \
  skills/threadlight-production-ready/tests/ \
  skills/threadlight-auto/tests/ -q
node --test tests/blueprint/*.test.js tests/canvas/*.test.mjs
bash scripts/build-cowork-zips.sh
git --no-pager diff --check
```

Expected: every command passes; Cowork zips remain within documented limits.

- [ ] **Step 11: Check acceptance criteria against artifacts**

Run:

```bash
find skills -mindepth 1 -maxdepth 1 -type d -name 'threadlight-*' | wc -l
rg -n "17 skills|17 total|16 pipeline" README.md THREADLIGHT.md plugin.json docs
rg -n "gpt-4o.*gpt-4o-mini|gpt-4o-mini.*gpt-4o" skills/threadlight-consumption-iq
rg -n "agentic-loop" skills/threadlight-design/references/runtime-policy.json README.md THREADLIGHT.md
```

Expected:

- first command prints `22`;
- stale-count search returns no active product claims;
- static model-swap search returns no implementation mapping;
- runtime-policy surfaces do not claim `agentic-loop` consumption.

- [ ] **Step 12: Commit docs and release integration**

```bash
git add README.md THREADLIGHT.md CHANGELOG.md plugin.json \
  scripts/build_process_library.py docs tests/blueprint tests/playwright \
  .github/workflows/python-pytest.yml
git commit -m "docs: publish the 22-skill gap closure flow"
```

- [ ] **Step 13: Inspect final PR scope**

Run:

```bash
git --no-pager log --oneline origin/main..HEAD
git --no-pager diff --stat origin/main...HEAD
git --no-pager diff --name-only origin/main...HEAD | \
  rg '^skills/threadlight-deploy/' && exit 1 || true
```

Expected: one design commit, one plan commit, and eight implementation commits;
no functional path under `skills/threadlight-deploy/`.
