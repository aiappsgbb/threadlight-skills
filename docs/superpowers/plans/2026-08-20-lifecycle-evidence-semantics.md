# Lifecycle Evidence Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workflow success, cost scope, Auto resumption, and Lifecycle Canvas evidence states communicate exactly what was proved.

**Architecture:** Add a small stdlib-only evidence policy module that the live workflow calls after existing producers run. Keep smoke and readiness semantics separate, bind cost evidence to the target deployment, tighten planner artifact checks, and expose later-pilot cost state in Canvas without changing lifecycle completion rules.

**Tech Stack:** GitHub Actions YAML, Python 3 stdlib and pytest, JSON manifests, Node `node:test`, vanilla JavaScript.

---

## File map

**Workflow semantics**

- Modify: `.github/workflows/threadlight-e2e-foundry.yml`
- Create: `skills/threadlight-production-ready/scripts/evidence_gate.py`
- Create: `skills/threadlight-production-ready/tests/test_evidence_gate.py`

**Cost scope binding**

- Modify: `skills/threadlight-consumption-iq/scripts/consumption_iq.py`
- Modify: `skills/threadlight-consumption-iq/scripts/reconcile.py`
- Modify: `skills/threadlight-consumption-iq/tests/test_reconcile.py`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py`
- Modify: `skills/threadlight-production-ready/tests/test_cost_reconciliation.py`

**Auto planner truth**

- Modify: `skills/threadlight-auto/references/orchestrator.py`
- Modify: `skills/threadlight-auto/references/state-schema.md`
- Modify: `skills/threadlight-auto/SKILL.md`
- Modify: `skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
- Modify: `skills/threadlight-auto/tests/fixtures/all-complete/tests/postdeploy-manifest.json`

**Lifecycle Canvas**

- Modify: `.github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs`
- Modify: `.github/extensions/threadlight-lifecycle/lib/projector.mjs`
- Modify: `.github/extensions/threadlight-lifecycle/web/app.js`
- Modify: `tests/canvas/artifact-reader.test.mjs`
- Modify: `tests/canvas/fixtures.mjs`
- Modify: `tests/canvas/projector.test.mjs`

**Narrow public wording**

- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `docs/self-improving.html`

### Task 1: Build a testable evidence policy

**Files:**
- Create: `skills/threadlight-production-ready/tests/test_evidence_gate.py`
- Create: `skills/threadlight-production-ready/scripts/evidence_gate.py`

- [ ] **Step 1: Write failing tests for smoke and readiness semantics**

Create `skills/threadlight-production-ready/tests/test_evidence_gate.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from evidence_gate import EvidenceGateError, evaluate_evidence  # noqa: E402


def write_json(root: Path, relative: str, payload: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def assurance(root: Path, *, govern="governed", evals="comprehensive", redteam="hardened") -> None:
    write_json(root, "specs/govern-manifest.json", {
        "schema": "threadlight-govern-manifest/v2",
        "tool_version": "0.1.0",
        "captured_at": "2026-08-20T08:00:00Z",
        "verdict": govern,
        "capabilities": {},
        "must_fix": [],
    })
    write_json(root, "specs/evals-manifest.json", {
        "schema": "threadlight-evals-manifest/v1",
        "tool_version": "0.1.0",
        "captured_at": "2026-08-20T08:00:00Z",
        "verdict": evals,
        "capabilities": {},
        "must_fix": [],
    })
    write_json(root, "specs/redteam-manifest.json", {
        "schema": "threadlight-redteam-manifest/v1",
        "tool_version": "0.1.0",
        "captured_at": "2026-08-20T08:00:00Z",
        "verdict": redteam,
        "capabilities": {},
        "must_fix": [],
        "should_fix": [],
        "not_verified": [],
        "asr": {},
        "thresholds": {"max_asr": 0.1, "freshness_days": 30, "min_attacks": 1},
    })


def readiness(root: Path) -> None:
    write_json(root, "tests/postdeploy-manifest.json", {"phase": "post-deploy", "gaps": []})
    write_json(root, "tests/production-readiness-manifest.json", {
        "go_live_recommendation": "ready",
        "would_fail_hard_gate": False,
        "kpi_scorecard": {
            "latency_declared": True,
            "cost_per_interaction_declared": True,
            "success_rate_declared": True,
            "deviation_alert_present": True,
            "traces_emit": True,
            "eval_pass_rate": 0.95,
            "cost_per_interaction_usd": 0.11,
        },
    })


def test_live_smoke_accepts_valid_non_passing_verdicts(tmp_path: Path) -> None:
    assurance(tmp_path, govern="ungoverned", evals="none", redteam="vulnerable")
    result = evaluate_evidence(tmp_path, "live-smoke")
    assert result["status"] == "pass"
    assert result["readiness_asserted"] is False
    assert result["verdicts"] == {
        "govern": "ungoverned",
        "evals": "none",
        "redteam": "vulnerable",
    }


def test_readiness_proof_requires_passing_verdicts(tmp_path: Path) -> None:
    assurance(tmp_path, govern="ungoverned")
    readiness(tmp_path)
    with pytest.raises(EvidenceGateError, match="govern expected governed"):
        evaluate_evidence(tmp_path, "readiness-proof")


def test_readiness_proof_requires_safe_check_and_scorecard(tmp_path: Path) -> None:
    assurance(tmp_path)
    with pytest.raises(EvidenceGateError, match="postdeploy-manifest"):
        evaluate_evidence(tmp_path, "readiness-proof")


def test_readiness_proof_passes_complete_semantic_evidence(tmp_path: Path) -> None:
    assurance(tmp_path)
    readiness(tmp_path)
    result = evaluate_evidence(tmp_path, "readiness-proof")
    assert result["status"] == "pass"
    assert result["readiness_asserted"] is True
```

- [ ] **Step 2: Run the test and verify the module is missing**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_evidence_gate.py -q
```

Expected: collection ERROR because `evidence_gate` does not exist.

- [ ] **Step 3: Implement the stdlib-only policy module**

Create `skills/threadlight-production-ready/scripts/evidence_gate.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class EvidenceGateError(ValueError):
    pass


CONTRACTS = {
    "govern": {
        "path": "specs/govern-manifest.json",
        "schema": "threadlight-govern-manifest/v2",
        "required": {"schema", "tool_version", "captured_at", "verdict", "capabilities"},
        "verdicts": {"governed", "partial", "ungoverned"},
        "passing": "governed",
    },
    "evals": {
        "path": "specs/evals-manifest.json",
        "schema": "threadlight-evals-manifest/v1",
        "required": {"schema", "tool_version", "captured_at", "verdict", "capabilities"},
        "verdicts": {"comprehensive", "partial", "offline-only", "none"},
        "passing": "comprehensive",
    },
    "redteam": {
        "path": "specs/redteam-manifest.json",
        "schema": "threadlight-redteam-manifest/v1",
        "required": {
            "schema", "tool_version", "captured_at", "verdict", "must_fix",
            "should_fix", "not_verified", "capabilities", "asr", "thresholds",
        },
        "verdicts": {"hardened", "partial", "vulnerable"},
        "passing": "hardened",
    },
}

KPI_BOOLEAN_FIELDS = {
    "latency_declared",
    "cost_per_interaction_declared",
    "success_rate_declared",
    "deviation_alert_present",
    "traces_emit",
}
KPI_NUMBER_FIELDS = {"eval_pass_rate", "cost_per_interaction_usd"}


def _read_object(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvidenceGateError(f"{relative} is missing") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceGateError(f"{relative} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise EvidenceGateError(f"{relative} must contain a JSON object")
    return value


def _assurance(root: Path) -> dict[str, str]:
    verdicts: dict[str, str] = {}
    for name, contract in CONTRACTS.items():
        document = _read_object(root, contract["path"])
        missing = contract["required"].difference(document)
        if missing:
            raise EvidenceGateError(
                f"{contract['path']} missing required fields: {', '.join(sorted(missing))}"
            )
        if document.get("schema") != contract["schema"]:
            raise EvidenceGateError(
                f"{contract['path']} schema expected {contract['schema']}"
            )
        if not isinstance(document.get("tool_version"), str):
            raise EvidenceGateError(f"{contract['path']} tool_version is missing")
        if not isinstance(document.get("captured_at"), str):
            raise EvidenceGateError(f"{contract['path']} captured_at is missing")
        if not isinstance(document.get("capabilities"), dict):
            raise EvidenceGateError(f"{contract['path']} capabilities must be an object")
        verdict = document.get("verdict")
        if verdict not in contract["verdicts"]:
            raise EvidenceGateError(f"{contract['path']} verdict is invalid")
        verdicts[name] = verdict
    return verdicts


def _readiness(root: Path) -> None:
    safe_check = _read_object(root, "tests/postdeploy-manifest.json")
    if safe_check.get("phase") != "post-deploy" or safe_check.get("gaps") != []:
        raise EvidenceGateError("postdeploy-manifest does not prove a green safe-check")

    manifest = _read_object(root, "tests/production-readiness-manifest.json")
    if manifest.get("would_fail_hard_gate") is not False:
        raise EvidenceGateError("production readiness would fail the hard gate")
    if manifest.get("go_live_recommendation") != "ready":
        raise EvidenceGateError("production readiness recommendation is not ready")

    scorecard = manifest.get("kpi_scorecard")
    if not isinstance(scorecard, dict):
        raise EvidenceGateError("production readiness kpi_scorecard is missing")
    for field in KPI_BOOLEAN_FIELDS:
        if scorecard.get(field) is not True:
            raise EvidenceGateError(f"kpi_scorecard.{field} must be true")
    for field in KPI_NUMBER_FIELDS:
        value = scorecard.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise EvidenceGateError(f"kpi_scorecard.{field} must be measured")


def evaluate_evidence(root: Path, mode: str) -> dict[str, Any]:
    if mode not in {"live-smoke", "readiness-proof"}:
        raise EvidenceGateError(f"unsupported evidence mode: {mode}")
    verdicts = _assurance(root)
    if mode == "readiness-proof":
        for name, contract in CONTRACTS.items():
            if verdicts[name] != contract["passing"]:
                raise EvidenceGateError(
                    f"{name} expected {contract['passing']}, got {verdicts[name]}"
                )
        _readiness(root)
    return {
        "status": "pass",
        "mode": mode,
        "readiness_asserted": mode == "readiness-proof",
        "verdicts": verdicts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=["live-smoke", "readiness-proof"], required=True)
    args = parser.parse_args()
    try:
        result = evaluate_evidence(args.root.resolve(), args.mode)
    except EvidenceGateError as exc:
        print(json.dumps({"status": "fail", "mode": args.mode, "error": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the focused tests**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_evidence_gate.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit the policy module**

```bash
git add skills/threadlight-production-ready/scripts/evidence_gate.py skills/threadlight-production-ready/tests/test_evidence_gate.py
git commit -m "feat: add lifecycle evidence policy"
```

### Task 2: Separate live smoke from readiness proof in the workflow

**Files:**
- Modify: `.github/workflows/threadlight-e2e-foundry.yml:40-55`
- Modify: `.github/workflows/threadlight-e2e-foundry.yml:151-159`
- Modify: `.github/workflows/threadlight-e2e-foundry.yml:743-763`
- Modify: `.github/workflows/threadlight-e2e-foundry.yml:1010-1085`

- [ ] **Step 1: Add the new workflow choices and compatibility alias**

Use:

```yaml
      mode:
        description: "live-smoke deploys and invokes but does not assert readiness; readiness-proof additionally requires green safe-check, passing assurance verdicts, a ready production scorecard, and measured outcome KPIs; full is a deprecated alias for live-smoke; design-only and smoke-only keep their existing behavior"
        required: false
        default: "live-smoke"
        type: choice
        options:
          - live-smoke
          - readiness-proof
          - full
          - design-only
          - smoke-only
```

Update top-of-file comments so `live-smoke` is the paid deployment/invocation
path and `readiness-proof` is the strict semantic path.

- [ ] **Step 2: Add a mode-normalization step**

Insert after checkout:

```yaml
      - name: Normalize evidence mode
        id: evidence-mode
        shell: bash
        run: |
          set -euo pipefail
          case "${{ inputs.mode }}" in
            full)
              echo "mode=live-smoke" >> "$GITHUB_OUTPUT"
              echo "::warning::mode=full is deprecated; use mode=live-smoke"
              ;;
            live-smoke|readiness-proof)
              echo "mode=${{ inputs.mode }}" >> "$GITHUB_OUTPUT"
              ;;
            *)
              echo "mode=none" >> "$GITHUB_OUTPUT"
              ;;
          esac
```

Do not rewrite existing `smoke-only` and `design-only` conditions; the two new
paid modes and the compatibility alias already satisfy the existing
`!= smoke-only && != design-only` expressions.

- [ ] **Step 3: Run safe-check only for strict readiness**

Insert immediately before the assurance-leg step:

```yaml
      - name: "[readiness-proof] Run post-deploy safe-check"
        if: success() && steps.evidence-mode.outputs.mode == 'readiness-proof'
        working-directory: ${{ env.E2E_WORKSPACE }}/${{ env.PILOT_SUBDIR }}
        run: |
          set -euo pipefail
          RG=$(azd env get-value AZURE_RESOURCE_GROUP)
          python3 "$GITHUB_WORKSPACE/skills/threadlight-safe-check/scripts/safe_check.py" \
            --phase post-deploy \
            --out tests \
            --rg "$RG"
```

The existing teardown steps remain `if: always()` so a failed strict gate still
cleans up.

- [ ] **Step 4: Run the deterministic design-to-deploy contract in every build mode**

Rename the existing `[design-only gate] design→deploy contract check` step to
`[contract gate] design→deploy contract check` and change its condition to:

```yaml
if: inputs.mode != 'smoke-only'
```

This keeps the free registry smoke minimal while preventing `live-smoke`,
`readiness-proof`, and the compatibility alias from bypassing the same
design-to-deploy contract enforced by `design-only`.

- [ ] **Step 5: Align assurance execution order with the lifecycle planner**

Run the existing producer commands in this order:

```bash
python3 "$REPO/skills/threadlight-evals/scripts/evals_check.py" \
  --target "$PILOT_DIR" --emit 2>&1 | tee -a /tmp/phase-legs.log \
  || echo "::notice::evals leg reported gaps"
python3 "$REPO/skills/threadlight-redteam/scripts/redteam_check.py" \
  --target "$PILOT_DIR" --emit 2>&1 | tee -a /tmp/phase-legs.log \
  || echo "::notice::red-team leg reported gaps"
python3 "$REPO/skills/threadlight-govern/scripts/govern_check.py" \
  --target "$PILOT_DIR" --emit 2>&1 | tee -a /tmp/phase-legs.log \
  || echo "::notice::govern leg reported gaps"
```

This matches Auto's `evals → redteam → govern` order without changing what each
producer validates.

- [ ] **Step 6: Replace the manifest-existence assertion with the policy command**

Keep the producer commands in report mode, then replace the current Phase 5
assert body with:

```yaml
      - name: "[Phase 5/5 assert] Evaluate lifecycle evidence semantics"
        if: success() && inputs.mode != 'smoke-only' && inputs.mode != 'design-only'
        run: |
          set -euo pipefail
          PILOT_DIR="$E2E_WORKSPACE/${PILOT_SUBDIR}"
          python3 "$GITHUB_WORKSPACE/skills/threadlight-production-ready/scripts/evidence_gate.py" \
            --root "$PILOT_DIR" \
            --mode "${{ steps.evidence-mode.outputs.mode }}" \
            | tee /tmp/evidence-gate.json
          {
            echo "## Lifecycle evidence"
            echo ""
            echo '```json'
            cat /tmp/evidence-gate.json
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
```

Replace the single production-ready invocation with an explicit strict/soft
branch:

```bash
PRODUCTION_READY=(
  python3 "$REPO/skills/threadlight-production-ready/scripts/production_ready.py"
  --root "$PILOT_DIR" --target citadel-spoke --static --no-rights-probe --quiet
  --accept-stale-safe-check
  --out "$PILOT_DIR/tests/production-readiness-manifest.json"
  --report "$PILOT_DIR/docs/production-readiness-report.md"
)
if [ "${{ steps.evidence-mode.outputs.mode }}" = "readiness-proof" ]; then
  "${PRODUCTION_READY[@]}" 2>&1 | tee -a /tmp/phase-legs.log
else
  "${PRODUCTION_READY[@]}" 2>&1 | tee -a /tmp/phase-legs.log \
    || echo "::notice::scorecard reported gaps; readiness was not asserted"
fi
```

- [ ] **Step 7: Update the design-only and paid-run summaries**

Change the design-only final line to:

```bash
echo "Run \`mode: live-smoke\` for deployment and invocation, or \`mode: readiness-proof\` for strict lifecycle evidence."
```

Add a paid-run summary step that prints:

```bash
if [ "${{ steps.evidence-mode.outputs.mode }}" = "readiness-proof" ]; then
  echo "Readiness semantics were asserted."
else
  echo "Deployment and invocation were asserted; production readiness was not asserted."
fi
```

- [ ] **Step 8: Run the offline evidence and workflow-text tests**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_evidence_gate.py -q
rg -n "live-smoke|readiness-proof|deprecated alias" .github/workflows/threadlight-e2e-foundry.yml
```

Expected: tests PASS; the workflow contains all three phrases.

- [ ] **Step 9: Commit workflow semantics**

```bash
git add .github/workflows/threadlight-e2e-foundry.yml
git commit -m "ci: separate smoke from readiness proof"
```

### Task 3: Bind reconciled actuals to the target pilot

**Files:**
- Modify: `skills/threadlight-consumption-iq/tests/test_reconcile.py`
- Modify: `skills/threadlight-consumption-iq/scripts/reconcile.py:709-779`
- Modify: `skills/threadlight-consumption-iq/scripts/reconcile.py:1728-1796`
- Modify: `skills/threadlight-consumption-iq/scripts/consumption_iq.py:538-595`
- Modify: `skills/threadlight-consumption-iq/scripts/consumption_iq.py:821-834`
- Modify: `skills/threadlight-production-ready/tests/test_cost_reconciliation.py`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py:4528-4598`

- [ ] **Step 1: Add reconciliation scope-match tests**

First add the scope required by the shipped actuals schema to the `actuals()`
fixture:

```python
        "scope": {"subscription_id": "sub-a", "resource_group": "rg-a"},
```

Add to `test_reconcile.py`:

```python
def test_expected_scope_matching_actuals_passes() -> None:
    a = actuals()
    a["scope"] = {"subscription_id": "sub-a", "resource_group": "rg-a"}
    result = reconcile_costs(
        forecast(),
        a,
        policy(),
        policy_errors=[],
        generated_at=GENERATED,
        policy_spec_sha256=SPEC_SHA256,
        expected_subscription_id="sub-a",
        expected_resource_group="rg-a",
    )
    assert result["status"] == "pass"


@pytest.mark.parametrize(
    ("subscription", "resource_group", "message"),
    [
        ("sub-b", "rg-a", "different subscription"),
        ("sub-a", "rg-b", "different resource group"),
    ],
)
def test_expected_scope_mismatch_is_rejected(
    subscription: str, resource_group: str, message: str
) -> None:
    a = actuals()
    a["scope"] = {"subscription_id": "sub-a", "resource_group": "rg-a"}
    with pytest.raises(ReconciliationInputError, match=message):
        reconcile_costs(
            forecast(),
            a,
            policy(),
            policy_errors=[],
            generated_at=GENERATED,
            policy_spec_sha256=SPEC_SHA256,
            expected_subscription_id=subscription,
            expected_resource_group=resource_group,
        )
```

- [ ] **Step 2: Run the new reconciliation tests**

Run:

```bash
python -m pytest skills/threadlight-consumption-iq/tests/test_reconcile.py -q -k "expected_scope"
```

Expected: FAIL because the keyword arguments do not exist.

- [ ] **Step 3: Add scope parsing and fail-closed comparison**

In `_Actuals.__init__`, read the existing scope:

```python
        scope = _section(actuals, "scope", "actuals.scope")
        self.subscription_id = _optional_str(
            scope, "subscription_id", "actuals.scope.subscription_id"
        )
        self.resource_group = _optional_str(
            scope, "resource_group", "actuals.scope.resource_group"
        )
```

Add these keyword parameters to `reconcile_costs`:

```python
    expected_subscription_id: str | None = None,
    expected_resource_group: str | None = None,
```

Immediately after `observed = _Actuals(actuals)`, add:

```python
    if (
        expected_subscription_id is not None
        and observed.subscription_id != expected_subscription_id
    ):
        raise ReconciliationInputError(
            "actuals manifest was collected for a different subscription"
        )
    if (
        expected_resource_group is not None
        and observed.resource_group != expected_resource_group
    ):
        raise ReconciliationInputError(
            "actuals manifest was collected for a different resource group"
        )
```

- [ ] **Step 4: Add explicit standalone reconcile scope flags**

Add to the `reconcile` parser in `consumption_iq.py`:

```python
    reconcile_p.add_argument("--expect-subscription")
    reconcile_p.add_argument("--expect-resource-group")
```

Pass them from `_phase_reconcile`:

```python
        expected_subscription_id=getattr(args, "expect_subscription", None),
        expected_resource_group=getattr(args, "expect_resource_group", None),
```

Keep the existing `run --all --with-actuals` cross-check unchanged; it already
validates the collected scope against its requested subscription and resource
group.

- [ ] **Step 5: Add production-ready target-scope tests**

In `test_cost_reconciliation.py`, update `_make_ctx` to accept and publish a
matching deployment target by default:

```python
def _make_ctx(root: Path, *, subscription_id="sub-1", resource_group="rg-pilot"):
    return pr.RepoContext(
        root=root,
        bicep_files=[],
        src_files=[],
        test_files=[],
        spec_text=(root / "specs" / "SPEC.md").read_text(encoding="utf-8")
        if (root / "specs" / "SPEC.md").exists()
        else "",
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text="",
        azd_env={},
        manifest={
            "deployment_manifest": {
                "subscription_id": subscription_id,
                "resource_group": resource_group,
            }
        },
        bicep_text="",
        src_text="",
        bicep_graph=pr.BicepGraph(resources=[], source_files=[]),
    )
```

Add:

```python
def test_bundle_from_another_resource_group_is_not_verified(tmp_path, monkeypatch) -> None:
    _freeze(monkeypatch)
    ctx = _write_bundle(tmp_path)
    ctx.manifest["deployment_manifest"]["resource_group"] = "rg-other"
    _both_not_verified(ctx)
```

- [ ] **Step 6: Reject unknown or mismatched target scope in the bundle loader**

Add before returning the bundle in `_read_cost_reconciliation_bundle`:

```python
    deployment = ctx.manifest.get("deployment_manifest")
    expected_sub = deployment.get("subscription_id") if isinstance(deployment, dict) else None
    expected_rg = deployment.get("resource_group") if isinstance(deployment, dict) else None
    actual_scope = actuals.get("scope")
    actual_sub = actual_scope.get("subscription_id") if isinstance(actual_scope, dict) else None
    actual_rg = actual_scope.get("resource_group") if isinstance(actual_scope, dict) else None
    if (
        not expected_sub
        or not expected_rg
        or actual_sub != expected_sub
        or actual_rg != expected_rg
    ):
        return None
```

Returning `None` uses the existing fail-closed path for COST-102, COST-103, and
KPI-003, so no cost consumer can relay a bundle whose target scope is absent or
mismatched.

- [ ] **Step 7: Run both cost suites**

Run:

```bash
python -m pytest skills/threadlight-consumption-iq/tests/test_reconcile.py -q
python -m pytest skills/threadlight-production-ready/tests/test_cost_reconciliation.py -q
```

Expected: both suites PASS.

- [ ] **Step 8: Commit scope binding**

```bash
git add skills/threadlight-consumption-iq/scripts/reconcile.py skills/threadlight-consumption-iq/scripts/consumption_iq.py skills/threadlight-consumption-iq/tests/test_reconcile.py skills/threadlight-production-ready/scripts/production_ready.py skills/threadlight-production-ready/tests/test_cost_reconciliation.py
git commit -m "fix: bind cost evidence to deployment scope"
```

### Task 4: Make Auto resumption validate evidence, not file presence

**Files:**
- Modify: `skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
- Modify: `skills/threadlight-auto/references/orchestrator.py:312-334`
- Modify: `skills/threadlight-auto/references/orchestrator.py:411-545`
- Modify: `skills/threadlight-auto/references/state-schema.md:1-12`
- Modify: `skills/threadlight-auto/SKILL.md:206-221`
- Create: `skills/threadlight-auto/tests/fixtures/all-complete/tests/postdeploy-manifest.json`

- [ ] **Step 1: Add failing safe-check and assurance validation tests**

Add to `test_threadlight_auto_orchestrator.py`:

```python
def test_safe_check_requires_green_postdeploy_manifest(tmp_path):
    docs = tmp_path / "docs"
    tests = tmp_path / "tests"
    docs.mkdir()
    tests.mkdir()
    (docs / "safe-check-post.md").write_text("PASS\n", encoding="utf-8")
    (tests / "postdeploy-manifest.json").write_text(
        json.dumps({"phase": "post-deploy", "gaps": ["resource mismatch"]}),
        encoding="utf-8",
    )
    decision = orch._check_safe_check(tmp_path, {})
    assert decision.decision == "run"
    assert "gaps" in decision.reason


def test_leg_manifest_requires_schema_timestamp_and_known_verdict(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "govern-manifest.json").write_text(
        json.dumps({
            "schema": "wrong-schema",
            "captured_at": "2026-08-20T08:00:00Z",
            "verdict": "governed",
        }),
        encoding="utf-8",
    )
    decision = orch._check_govern(tmp_path, {})
    assert decision.decision == "run"
    assert "schema" in decision.reason


def test_leg_manifest_reports_non_passing_verdict_without_rerunning(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "evals-manifest.json").write_text(
        json.dumps({
            "schema": "threadlight-evals-manifest/v1",
            "captured_at": orch.datetime.now(orch.timezone.utc).isoformat(),
            "verdict": "partial",
        }),
        encoding="utf-8",
    )
    decision = orch._check_evals(tmp_path, {})
    assert decision.decision == "skip"
    assert "verdict=partial" in decision.reason
```

- [ ] **Step 2: Run the focused tests**

Run:

```bash
python -m pytest skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py -q -k "safe_check_requires|leg_manifest"
```

Expected: FAIL because safe-check ignores the JSON manifest and leg checks trust age.

- [ ] **Step 3: Tighten the safe-check probe**

Replace the final `skip` return in `_check_safe_check` with validation of
`tests/postdeploy-manifest.json`, followed by the original skip return:

```python
    postdeploy = workspace / "tests" / "postdeploy-manifest.json"
    try:
        payload = json.loads(postdeploy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return StageDecision(
            "safe_check",
            "run",
            "tests/postdeploy-manifest.json missing or malformed.",
            artifacts_missing=["tests/postdeploy-manifest.json"],
        )
    if payload.get("phase") != "post-deploy" or payload.get("gaps") != []:
        return StageDecision(
            "safe_check",
            "run",
            "post-deploy safe-check carries gaps; re-running.",
            artifacts_seen=["tests/postdeploy-manifest.json"],
        )
    return StageDecision(
        "safe_check",
        "skip",
        f"safe-check evidence is green and {int(age/60)} m old (< 24 h).",
        artifacts_seen=["docs/safe-check-post.md", "tests/postdeploy-manifest.json"],
    )
```

Add a green `tests/postdeploy-manifest.json` to the `all-complete` fixture:

```json
{
  "phase": "post-deploy",
  "gaps": []
}
```

- [ ] **Step 4: Replace age-only legacy leg checks**

Define:

```python
LEG_CONTRACTS = {
    "evals": (
        "specs/evals-manifest.json",
        "threadlight-evals-manifest/v1",
        {"comprehensive", "partial", "offline-only", "none"},
    ),
    "redteam": (
        "specs/redteam-manifest.json",
        "threadlight-redteam-manifest/v1",
        {"hardened", "partial", "vulnerable"},
    ),
    "govern": (
        "specs/govern-manifest.json",
        "threadlight-govern-manifest/v2",
        {"governed", "partial", "ungoverned"},
    ),
}
```

Change `_check_leg_manifest` to parse JSON, require the expected schema,
`captured_at`, and a known verdict, compute freshness from `captured_at`, and
include `verdict=<value>` in the skip reason. A malformed or unknown document
returns `run`; a fresh valid non-passing verdict returns `skip` because Auto is a
planner of executed legs, not the strict readiness policy.

- [ ] **Step 5: Validate cost manifest identity before reuse**

In `_check_cost_projection`, require:

```python
schema_version = data.get("schema_version")
if not isinstance(schema_version, str) or not schema_version.startswith("1."):
    manifest_generated_at = None
```

Do not invent a source hash the cost manifest does not publish.

- [ ] **Step 6: Correct the state ownership documentation**

Replace the opening of `state-schema.md` with:

```markdown
`threadlight-auto` guidance owns `.threadlight/auto-state.json`. The Python
planner reads this file; it does not write or migrate it. With `--commit`,
`orchestrator.py` writes `.threadlight/auto-next.json`, which records the next
decision for the coding agent.
```

Make the same planner/agent ownership explicit in `SKILL.md`'s resumption table.

- [ ] **Step 7: Run the Auto suite**

Run:

```bash
python -m pytest skills/threadlight-auto/tests/ -q
python skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py
```

Expected: both commands PASS.

- [ ] **Step 8: Commit planner evidence validation**

```bash
git add skills/threadlight-auto/references/orchestrator.py skills/threadlight-auto/references/state-schema.md skills/threadlight-auto/SKILL.md skills/threadlight-auto/tests
git commit -m "fix: validate Auto resume evidence"
```

### Task 5: Expose cost and readiness evidence state in Lifecycle Canvas

**Files:**
- Modify: `tests/canvas/artifact-reader.test.mjs`
- Modify: `tests/canvas/fixtures.mjs`
- Modify: `tests/canvas/projector.test.mjs`
- Modify: `.github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs:4-28`
- Modify: `.github/extensions/threadlight-lifecycle/lib/projector.mjs`
- Modify: `.github/extensions/threadlight-lifecycle/web/app.js:86-104`

- [ ] **Step 1: Add failing projection tests**

Add to `tests/canvas/projector.test.mjs`:

```javascript
test('projects forecast, actuals, reconciliation, and scope mismatch states', async () => {
  await withFixture('complete-pilot', async ({ workspace, writeJson }) => {
    let model = await projectWorkspace(workspace, { now: NOW });
    assert.equal(findSkill(model, 'threadlight-consumption-iq').evidenceState, 'forecast-only');

    await writeJson('specs/cost-actuals-manifest.json', {
      schema: 'threadlight-cost-actuals/v1',
      status: 'pass',
      scope: { subscription_id: 'sub-1', resource_group: 'rg-pilot' },
    });
    model = await projectWorkspace(workspace, { now: NOW });
    assert.equal(findSkill(model, 'threadlight-consumption-iq').evidenceState, 'actuals-collected');

    await writeJson('specs/cost-reconciliation-manifest.json', {
      schema: 'threadlight-cost-reconciliation/v1',
      status: 'pass',
    });
    model = await projectWorkspace(workspace, { now: NOW });
    assert.equal(findSkill(model, 'threadlight-consumption-iq').evidenceState, 'reconciled');

    await writeJson('specs/cost-actuals-manifest.json', {
      schema: 'threadlight-cost-actuals/v1',
      status: 'pass',
      scope: { subscription_id: 'sub-1', resource_group: 'rg-other' },
    });
    model = await projectWorkspace(workspace, { now: NOW });
    assert.equal(findSkill(model, 'threadlight-consumption-iq').evidenceState, 'scope-mismatch');
  });
});


test('readiness evidence state requires passing assurance and KPI scorecard', async () => {
  await withFixture('complete-pilot', async ({ workspace, writeJson }) => {
    await writeJson('specs/evals-manifest.json', {
      captured_at: '2026-08-06T08:00:00Z',
      verdict: 'partial',
      must_fix: [],
    });
    let model = await projectWorkspace(workspace, { now: NOW });
    assert.equal(
      findSkill(model, 'threadlight-production-ready').evidenceState,
      'readiness-incomplete',
    );

    await writeJson('specs/evals-manifest.json', {
      captured_at: '2026-08-06T08:00:00Z',
      verdict: 'comprehensive',
      must_fix: [],
    });
    await writeJson('tests/production-readiness-manifest.json', {
      checked_at: '2026-08-06T08:00:00Z',
      go_live_recommendation: 'ready',
      would_fail_hard_gate: false,
      kpi_scorecard: {
        latency_declared: true,
        cost_per_interaction_declared: true,
        success_rate_declared: true,
        deviation_alert_present: true,
        traces_emit: true,
        eval_pass_rate: 0.95,
        cost_per_interaction_usd: 0.11,
      },
    });
    model = await projectWorkspace(workspace, { now: NOW });
    assert.equal(
      findSkill(model, 'threadlight-production-ready').evidenceState,
      'readiness-proof',
    );
  });
});
```

- [ ] **Step 2: Run the Canvas tests and confirm `evidenceState` is absent**

Run:

```bash
node --test tests/canvas/projector.test.mjs
```

Expected: FAIL with `undefined` evidence states.

- [ ] **Step 3: Allow the two cost artifacts**

Add to `ALLOWED_FILES`:

```javascript
  "specs/cost-actuals-manifest.json",
  "specs/cost-reconciliation-manifest.json",
  "docs/cost-reconciliation-report.md",
```

Extend `artifact-reader.test.mjs` so those three paths are readable and an
unlisted sibling remains rejected.

Use these assertions:

```javascript
for (const relativePath of [
  "specs/cost-actuals-manifest.json",
  "specs/cost-reconciliation-manifest.json",
  "docs/cost-reconciliation-report.md",
]) {
  await fixture.writeString(relativePath, "{}");
  assert.equal(await reader.exists(relativePath), true);
}
await assert.rejects(
  () => reader.exists("specs/cost-private-export.json"),
  /not allowlisted/,
);
```

- [ ] **Step 4: Add non-gating evidence-state helpers**

In `projector.mjs`, add:

```javascript
async function costEvidenceState(reader, manifest) {
  if (!(await reader.exists("specs/cost-manifest.json"))) return null;
  if (!(await reader.exists("specs/cost-actuals-manifest.json"))) return "forecast-only";

  const actuals = await reader.readJson("specs/cost-actuals-manifest.json");
  if (actuals?.schema !== "threadlight-cost-actuals/v1" || actuals?.status !== "pass") {
    return "actuals-invalid";
  }
  if (!(await reader.exists("specs/cost-reconciliation-manifest.json"))) {
    return "actuals-collected";
  }
  const reconciliation = await reader.readJson("specs/cost-reconciliation-manifest.json");
  if (reconciliation?.schema !== "threadlight-cost-reconciliation/v1") {
    return "reconciliation-invalid";
  }
  const expected = manifest?.deployment_manifest;
  const scope = actuals?.scope;
  if (
    expected?.subscription_id !== scope?.subscription_id ||
    expected?.resource_group !== scope?.resource_group
  ) {
    return "scope-mismatch";
  }
  return reconciliation?.status === "pass" ? "reconciled" : "reconciliation-not-verified";
}

function readinessEvidenceState(assurance, readiness) {
  const requiredBooleans = [
    "latency_declared",
    "cost_per_interaction_declared",
    "success_rate_declared",
    "deviation_alert_present",
    "traces_emit",
  ];
  const strict =
    assurance.govern?.verdict === "governed" &&
    assurance.evals?.verdict === "comprehensive" &&
    assurance.redteam?.verdict === "hardened" &&
    readiness?.go_live_recommendation === "ready" &&
    readiness?.would_fail_hard_gate === false &&
    requiredBooleans.every((field) => readiness?.kpi_scorecard?.[field] === true) &&
    Number.isFinite(readiness?.kpi_scorecard?.eval_pass_rate) &&
    Number.isFinite(readiness?.kpi_scorecard?.cost_per_interaction_usd);
  return strict ? "readiness-proof" : "readiness-incomplete";
}
```

Before returning from `projectSkill`, attach the state:

```javascript
  let evidenceState = null;
  if (definition.id === "threadlight-consumption-iq") {
    evidenceState = await costEvidenceState(reader, manifest);
  }
  if (definition.id === "threadlight-production-ready") {
    const readOptional = async (relativePath) =>
      (await reader.exists(relativePath)) ? reader.readJson(relativePath) : null;
    evidenceState = readinessEvidenceState(
      {
        govern: await readOptional("specs/govern-manifest.json"),
        evals: await readOptional("specs/evals-manifest.json"),
        redteam: await readOptional("specs/redteam-manifest.json"),
      },
      await readOptional("tests/production-readiness-manifest.json"),
    );
  }

  return {
    definition,
    status,
    evidenceState,
    evidence,
    blockers: incompletePrerequisite
      ? [`${definition.label} is waiting for ${incompletePrerequisite.definition.label}`]
      : [],
  };
```

Do not alter phase completion or the existing `status` field; actuals remain
later-pilot evidence.

Update the `complete-pilot` fixture in `tests/canvas/fixtures.mjs` so its
deployment manifest includes:

```javascript
subscription_id: "sub-1",
resource_group: "rg-pilot",
```

and so the assurance verdicts use the shipped enums:

```javascript
// specs/redteam-manifest.json
verdict: "hardened",

// specs/govern-manifest.json
verdict: "governed",
```

- [ ] **Step 5: Render the evidence state in technical details**

Change `renderSkillDetails` to:

```javascript
      const evidenceState = skill?.evidenceState
        ? `<span class="muted"> · ${escapeHtml(skill.evidenceState)}</span>`
        : "";
      return `<li><code>${escapeHtml(id)}</code>${statusBadge(skill?.status)}${evidenceState}</li>`;
```

- [ ] **Step 6: Run Canvas contracts**

Run:

```bash
node --test tests/canvas/*.test.mjs
```

Expected: PASS.

- [ ] **Step 7: Commit Canvas evidence states**

```bash
git add .github/extensions/threadlight-lifecycle/lib/artifact-reader.mjs .github/extensions/threadlight-lifecycle/lib/projector.mjs .github/extensions/threadlight-lifecycle/web/app.js tests/canvas
git commit -m "feat: expose lifecycle evidence states"
```

### Task 6: Align the remaining public claim boundaries

**Files:**
- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `docs/self-improving.html`
- Modify: `tests/blueprint/published-surfaces.test.js`

- [ ] **Step 1: Add failing wording assertions**

Append:

```javascript
test('public surfaces distinguish planning, smoke evidence, and diagnostics', () => {
  const surfaces = [
    read('README.md'),
    read('THREADLIGHT.md'),
    read('docs/self-improving.html'),
  ].join('\n');

  assert.match(surfaces, /agent-guided lifecycle planner/i);
  assert.match(surfaces, /live smoke/i);
  assert.match(surfaces, /readiness proof/i);
  assert.match(surfaces, /diagnostics-to-backlog/i);
  assert.doesNotMatch(surfaces, /closed autonomous loop/i);
  assert.doesNotMatch(surfaces, /orchestrator\.py executes/i);
});
```

- [ ] **Step 2: Run the wording test**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js
```

Expected: FAIL until the workflow semantics are described publicly.

- [ ] **Step 3: Add the narrow public explanation**

Use this paragraph in README and THREADLIGHT:

```markdown
The paid live workflow has two evidence meanings. **Live smoke** proves the
design, deployment, invocation, and assurance producers executed; it does not
assert production readiness. **Readiness proof** additionally requires a green
post-deploy safe-check, governed/comprehensive/hardened assurance verdicts, a
ready production scorecard, and measured outcome KPIs.
```

Keep the self-improving page on:

```text
workflow run → deterministic diagnostics → ranked backlog
```

Do not add auto-apply, automatic rerun, or measured-improvement claims.

- [ ] **Step 4: Run the public-contract tests**

Run:

```bash
node --test tests/blueprint/*.test.js
```

Expected: PASS.

- [ ] **Step 5: Commit the final wording**

```bash
git add README.md THREADLIGHT.md docs/self-improving.html tests/blueprint/published-surfaces.test.js
git commit -m "docs: define lifecycle evidence semantics"
```

### Task 7: Run the complete PR 2 gate

**Files:**
- Verify only: all files changed by Tasks 1-6

- [ ] **Step 1: Run focused Python suites**

```bash
python -m pytest \
  skills/threadlight-production-ready/tests/test_evidence_gate.py \
  skills/threadlight-production-ready/tests/test_cost_reconciliation.py \
  skills/threadlight-consumption-iq/tests/test_reconcile.py \
  skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full touched-skill suites**

```bash
python -m pytest \
  skills/threadlight-consumption-iq/tests/ \
  skills/threadlight-production-ready/tests/ \
  skills/threadlight-auto/tests/ \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run Canvas and public-contract suites**

```bash
node --test tests/canvas/*.test.mjs
node --test tests/blueprint/*.test.js
```

Expected: PASS.

- [ ] **Step 4: Validate workflow and public-safe scope**

```bash
rg -n "live-smoke|readiness-proof|full is deprecated" .github/workflows/threadlight-e2e-foundry.yml
node --test tests/blueprint/published-surfaces.test.js
git diff --check
git diff --name-only
```

Expected: workflow terms are present; public-safety assertions pass; diff check
exits 0; the file list contains only intended PR 2 files.

- [ ] **Step 5: Run the live smoke after merge**

Dispatch `.github/workflows/threadlight-e2e-foundry.yml` with:

```text
mode=live-smoke
teardown=true
```

Expected: deployment and invocation assertions pass; the workflow summary states
that production readiness was not asserted; teardown completes.

Do not require `readiness-proof` to pass against the intentionally gap-oriented
workshop pilot. Its expected behavior there is a clear semantic failure.

## Scope cuts

If this plan exceeds two focused implementation sessions, cut in this order:

1. Canvas web-label rendering; keep the model-level `evidenceState`.
2. Compatibility-alias removal; keep `full` accepted and documented as deprecated.
3. Additional Auto artifact classes beyond safe-check, cost forecast, and the
   three legacy assurance manifests.

Do not cut the smoke/readiness separation, strict evidence policy, cost scope
binding, or correction of Auto state ownership.
