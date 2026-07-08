# Design — Support symbolic-name ARM (languageVersion 2.0) and harden the readiness scorecard against unresolvable ARM expressions

- **Skill:** `threadlight-production-ready`
- **Date:** 2026-07-07
- **Version:** 0.8.0 → 0.8.1 (patch — robustness fixes, no new pillar/finding/behaviour surface)
- **Scope:** the 13-pillar readiness scorecard (`scripts/production_ready.py`), specifically the compiled-ARM resource walker and the model-lifecycle static check.

## Problem

The scorecard compiles a repo's Bicep to ARM once (`az bicep build`) and reasons over the resulting resource graph (`BicepGraph`). Two real-world ARM shapes break it today:

1. **Symbolic-name ARM (`languageVersion: 2.0`).** Modern Bicep — the current azd/Bicep default — compiles to ARM where the top-level `resources` is a **map** `{symbolicName: resourceObject}`, not a **list**. `BicepGraph._walk` iterates `resources` assuming a list; on a map it iterates the string keys and calls `.get(...)` on a `str`, raising `AttributeError` **before any pillar runs**. The entire assessment dies on the most common modern infra shape.

2. **ARM parameter / copy-loop expressions in nested sub-objects.** When a model deployment's `model` (or its `version`) is supplied via a parameter or a copy loop, the compiled ARM carries the sub-object as an **expression string** (e.g. `"[parameters('deployments')[copyIndex()].model]"`) rather than an object. `_check_model_lifecycle_static` reads `properties.model` and calls `.get("version")` on it — crashing on the string. A naive "treat non-dict as unpinned" fix would instead raise a **false must-fix** (MDL-001) and trip the hard gate, even though the model may well be pinned via that parameter.

Both are correctness/robustness defects: the tool must run to completion on arbitrary, valid, modern customer infrastructure and report honestly.

## Goals

- **G1** — Compile and walk symbolic-name ARM (`languageVersion >= 2.0`) so the scorecard runs on the modern azd/Bicep default.
- **G2** — The model-lifecycle static check tolerates param/expression-driven model deployments: it neither crashes nor emits a false must-fix. A model, or its version, supplied via an ARM parameter/variable/copy expression degrades to **not-verified** ("verify at deploy", already covered by the live check MDL-101). A genuinely absent model stays **must-fix** (unambiguously unpinned).
- **G3** — A single pillar's static analyzer raising an unexpected exception must **not** abort the whole assessment, but must also **not** silently relax the hard go-live gate. That pillar **fails closed**: its tier-0 findings degrade to visible, gate-blocking `must-fix` findings carrying the error; every other pillar still scores and the run completes.

## Non-goals

- Resolving ARM parameter/variable/copy expressions to concrete values. Static analysis legitimately cannot; the live tier (MDL-101, `az ... deployment list`) already verifies pinned versions against the deployed resource.
- Any change to the pillar set, finding catalog, scoring weights, or CLI surface.
- Sweeping every nested `.get(...)` read in the file. The three fixes above cover the known crash class; the G3 resilience guard is the safety net for any unforeseen shape, so exhaustive hardening is unnecessary churn.

## Design

### A. `_as_dict` helper

```python
def _as_dict(v: Any) -> dict:
    """ARM sub-objects can arrive as expression STRINGS (parameter/copy
    expressions) instead of objects. Coerce anything non-dict to {} so
    downstream `.get(...)` reads never crash on a str."""
    return v if isinstance(v, dict) else {}
```

Used at the ARM-shape reads that fetch a sub-object and then index into it.

### B. `BicepGraph._walk` — accept map or list

Normalise both ARM shapes to an iterable of resource objects; skip any non-dict entry defensively; recurse into nested-template resources (which are themselves a map under `languageVersion 2.0`) and inline child resources (list **or** map).

```python
@staticmethod
def _walk(resources: Any) -> list[dict]:
    out: list[dict] = []
    if isinstance(resources, dict):        # symbolic-name ARM (languageVersion >= 2.0)
        items = list(resources.values())
    elif isinstance(resources, list):      # classic ARM
        items = resources
    else:
        return out
    for r in items:
        if not isinstance(r, dict):
            continue
        rtype = (r.get("type") or "").lower()
        if rtype == "microsoft.resources/deployments":
            nested = _as_dict(_as_dict(r.get("properties")).get("template")).get("resources") or []
            out.extend(BicepGraph._walk(nested))
        else:
            out.append(r)
            kids = r.get("resources")
            if isinstance(kids, (list, dict)):
                out.extend(BicepGraph._walk(kids))
    return out
```

`_by_type` construction (which reads `r.get("type")`) is then safe: every element `_walk` returns is a dict.

### C. `_check_model_lifecycle_static` — MDL-001 param-aware

Classify each declared model deployment:

- `properties` is a **non-dict** (whole block is an expression string) → *deferred* (nothing statically resolvable).
- `properties.model` is a **non-dict string** (expression) → *deferred* (model supplied via parameter/copy expression).
- `properties.model` is **absent / null** → *missing* (a deployment declaring no model at all is unambiguously unpinned).
- `properties.model` is a **dict** → read `version`: an expression string `[...]` → *deferred* (pin real but not statically resolvable); `latest` → *floating*; empty/missing → *missing*; else → *pinned*.

Verdict:

- any *floating* or *missing* → **must-fix** (a real, statically-provable gap).
- else if any *deferred* → **not-verified**, detail: "N model deployment(s) set model/version via ARM parameters or expressions — pin verified at deploy (see live check MDL-101)". Not-verified does **not** trip the hard gate (`_hard_gate_would_fail` counts only `status == "must-fix"`), so no false failure.
- else → **pass**.

The live read (MDL-101) is unchanged: `az ... deployment list` returns concrete JSON, so the deployed model is always a real object there.

### D. Per-pillar static-check resilience guard (`_run_pillar`)

Wrap only the **static** dispatch in a guard whose `except` is narrowed to the JSON-shape-mismatch family (`AttributeError`, `TypeError`, `KeyError`, `ValueError`, `IndexError`) — a genuinely unexpected error (e.g. `MemoryError`) still propagates. On a caught exception, **fail closed**: degrade that pillar's static (tier-0) catalog findings to gate-blocking `must-fix` — surfacing the exception type and message in the detail — and emit a `stderr` warning. The live dispatch and all other pillars proceed normally.

```python
try:
    <existing static if/elif dispatch>
except (AttributeError, TypeError, KeyError, ValueError, IndexError) as exc:
    existing = {f.id for f in findings}
    for fid, meta in FINDING_CATALOG.items():
        if meta["pillar"] == pillar and meta["tier"] == 0 and fid not in existing:
            findings.append(_mk_finding(
                fid, status="must-fix",
                detail=f"static analyzer could not verify this control "
                       f"({type(exc).__name__}: {exc}) — failing closed; "
                       f"resolve or re-run before go-live"))
    print(f"[warn] static checks for pillar '{pillar}' raised "
          f"{type(exc).__name__}: {exc} — failing closed (must-fix)", file=sys.stderr)
```

This is a deliberate, **visible, fail-closed** degradation, not exception-swallowing: the pillar shows `must-fix` with the error in the report, the run completes, and — critically — the hard go-live gate keeps blocking, because a static analyzer that could not complete has NOT verified that pillar's must-fix controls (degrading to a non-gating `not-verified` would flip the gate FAIL→PASS, i.e. fail open on a security gate). It uses real catalog IDs, so no invariant downstream breaks. Because A–C handle the known shapes properly, the guard is a last-resort net that should rarely fire.

## Data flow (unchanged except at the two ARM reads)

`az bicep build` → ARM JSON → `BicepGraph._walk` (now map/list-aware) → `_by_type` index → per-pillar static checks (model-lifecycle now param-aware; each wrapped by the resilience guard) → findings → scoring/manifest/report.

## Testing (pytest-collected, no `az` dependency)

All tests construct `BicepGraph(resources=[...])` / synthetic ARM directly or a minimal `RepoContext`, mirroring `tests/test_cost_006.py`. They run in CI without the Bicep CLI (the existing `az`-gated `test_bicep_graph.py` cases stay as-is).

New `tests/test_symbolic_name_arm.py`:

- **`_as_dict`** — dict passthrough; str/None/list → `{}`.
- **`_walk` (RED)** — a symbolic-name ARM `{ "resources": {sym1: {...vnet...}, sym2: {...law...}} }` yields both resource dicts; a `resources` list containing a bare string skips the string; a nested `Microsoft.Resources/deployments` whose `properties.template.resources` is a **map** flattens its children.
- **model-lifecycle (RED)** — a deployment whose `properties.model` (or whose `properties` block) is an expression string → MDL-001 `not-verified` (no crash, not must-fix); a model dict whose `version` is a parameter expression → `not-verified`; a literal pinned model → `pass`; a literal `latest` → `must-fix`; an absent model → `must-fix`; a mix of pinned + expression-string → `not-verified`; a mix with a real `latest` → `must-fix`.
- **resilience guard (RED)** — monkeypatch a pillar's static runner in `PILLAR_RUNNERS` to raise a shape-mismatch error; `_run_pillar(..., static_only=True, ...)` returns that pillar's tier-0 findings as gate-blocking `must-fix` (detail names the exception), `_hard_gate_would_fail(findings)` stays `True`, and it does **not** propagate — while a non-shape error (`MemoryError`) DOES propagate.

Full `threadlight-production-ready` suite must stay green (225 baseline + 16 new = 241 pass, plus the 2 pre-existing time-based stale-fixture `test_end_to_end` failures that are already `continue-on-error` in CI).

## Versioning & docs

- `VERSION` const `0.8.0` → `0.8.1`; `SKILL.md` frontmatter `version` `0.8.0` → `0.8.1`; `tests/test_version.py` expected value.
- `SKILL.md` changelog table: a `0.8.1` row ("support symbolic-name ARM `languageVersion 2.0`; param-aware model-lifecycle static check; per-pillar static-analysis resilience").
- Root `CHANGELOG.md` `[Unreleased] / ### Fixed`.
- No `plugin.json` / `marketplace.json` bump — patch bugfix, no new capability or keyword (matches the hygiene/credibility patch cadence).

## Rollout / risk

- Pure robustness: existing classic-ARM list inputs walk identically (the `isinstance(list)` branch is the old behaviour). Model deployments with literal dict models score exactly as before (regression-tested). The guard changes runtime behaviour only when a static check would otherwise crash — strictly better than aborting.
