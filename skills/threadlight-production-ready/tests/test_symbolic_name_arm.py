"""Tests for symbolic-name ARM (languageVersion 2.0) support and scorecard
robustness against unresolvable ARM expression strings (v0.8.1).

Three defects pinned here:

  1. ``BicepGraph._walk`` crashed on ``languageVersion: 2.0`` ARM, where the
     top-level ``resources`` is a MAP ``{symbolicName: obj}`` rather than a
     list — the modern azd/Bicep default. It must accept both shapes.
  2. ``_check_model_lifecycle_static`` (MDL-001) crashed when a deployment's
     ``properties.model`` arrived as an ARM expression STRING (parameter /
     copy-loop). It must degrade to ``not-verified`` (verify at deploy), never
     crash and never raise a false must-fix — and it must NOT silently pass a
     model whose ``version`` is an expression, nor mask an absent model.
  3. A single pillar's static analyzer raising must not abort the whole
     assessment: ``_run_pillar`` degrades that pillar's tier-0 findings to a
     VISIBLE, gate-blocking ``must-fix`` (failing CLOSED — an unverified
     security pillar must not let the hard gate pass) and keeps going.

pytest-collected; no ``az`` dependency (synthetic ARM / directly-built
``BicepGraph`` and ``RepoContext``, mirroring test_cost_006.py).
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"

_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx_with_resources(resources: list[dict]) -> "mod.RepoContext":  # type: ignore[name-defined]
    """Minimal RepoContext backed by a directly-built BicepGraph."""
    tmpdir = Path(tempfile.mkdtemp())
    bg = mod.BicepGraph(resources=resources, source_files=[])
    return mod.RepoContext(
        root=tmpdir,
        bicep_files=[],
        src_files=[],
        test_files=[],
        spec_text="",
        spec_12={},
        spec_11b={},
        azure_yaml_text="",
        docs_text="",
        azd_env={},
        manifest={},
        bicep_text="",
        src_text="",
        bicep_graph=bg,
    )


def _mdl001(ctx) -> "mod.Finding":  # type: ignore[name-defined]
    return {f.id: f for f in mod._check_model_lifecycle_static(ctx)}["MDL-001"]


def _deployment(name: str, model) -> dict:
    return {
        "type": "Microsoft.CognitiveServices/accounts/deployments",
        "name": name,
        "properties": {"model": model},
    }


# ---------------------------------------------------------------------------
# A. _as_dict helper
# ---------------------------------------------------------------------------


def test_as_dict_passthrough_and_coerce():
    assert mod._as_dict({"a": 1}) == {"a": 1}
    assert mod._as_dict("[parameters('x')]") == {}
    assert mod._as_dict(None) == {}
    assert mod._as_dict(["a", "b"]) == {}


# ---------------------------------------------------------------------------
# B. _walk accepts symbolic-name MAP and classic LIST
# ---------------------------------------------------------------------------


def test_walk_accepts_symbolic_name_map():
    """languageVersion 2.0: resources is a {symbolicName: obj} MAP."""
    resources = {
        "vnet": {"type": "Microsoft.Network/virtualNetworks", "name": "v"},
        "law": {"type": "Microsoft.OperationalInsights/workspaces", "name": "l"},
    }
    walked = mod.BicepGraph._walk(resources)
    types = sorted((r.get("type") for r in walked))
    assert types == [
        "Microsoft.Network/virtualNetworks",
        "Microsoft.OperationalInsights/workspaces",
    ], f"expected both resources flattened, got {types}"


def test_walk_still_accepts_classic_list():
    resources = [
        {"type": "Microsoft.Network/virtualNetworks", "name": "v"},
    ]
    walked = mod.BicepGraph._walk(resources)
    assert [r.get("type") for r in walked] == ["Microsoft.Network/virtualNetworks"]


def test_walk_skips_non_dict_entries():
    """A stray string in a resources list must be skipped, not crash."""
    resources = ["[parameters('junk')]", {"type": "Microsoft.Foo/bar", "name": "x"}]
    walked = mod.BicepGraph._walk(resources)
    assert [r.get("type") for r in walked] == ["Microsoft.Foo/bar"]


def test_walk_flattens_nested_template_map():
    """A nested Microsoft.Resources/deployments whose template.resources is a
    MAP must have its children flattened; the wrapper itself is not counted."""
    resources = {
        "wrapper": {
            "type": "Microsoft.Resources/deployments",
            "name": "nested",
            "properties": {
                "template": {
                    "resources": {
                        "child": {
                            "type": "Microsoft.OperationalInsights/workspaces",
                            "name": "law",
                        }
                    }
                }
            },
        }
    }
    walked = mod.BicepGraph._walk(resources)
    types = [r.get("type") for r in walked]
    assert types == ["Microsoft.OperationalInsights/workspaces"], types


# ---------------------------------------------------------------------------
# C. MDL-001 param-aware model-lifecycle static check
# ---------------------------------------------------------------------------


def test_mdl001_expression_string_model_is_not_verified():
    """Model supplied via ARM parameter/copy expression -> not-verified, not
    a crash and not a false must-fix."""
    ctx = _ctx_with_resources(
        [_deployment("gpt", "[parameters('deployments')[copyIndex()].model]")]
    )
    f = _mdl001(ctx)  # must not raise
    assert f.status == "not-verified", f"expected not-verified, got {f.status!r}: {f.detail}"


def test_mdl001_literal_pinned_model_passes():
    ctx = _ctx_with_resources(
        [_deployment("gpt", {"name": "gpt-5.1", "version": "2026-01-01"})]
    )
    assert _mdl001(ctx).status == "pass"


def test_mdl001_literal_latest_is_must_fix():
    ctx = _ctx_with_resources(
        [_deployment("gpt", {"name": "gpt-5.1", "version": "latest"})]
    )
    assert _mdl001(ctx).status == "must-fix"


def test_mdl001_missing_version_is_must_fix():
    ctx = _ctx_with_resources([_deployment("gpt", {"name": "gpt-5.1"})])
    assert _mdl001(ctx).status == "must-fix"


def test_mdl001_mix_pinned_and_expression_is_not_verified():
    """Some pinned, some param-driven, none floating -> not-verified (cannot
    statically confirm ALL are pinned)."""
    ctx = _ctx_with_resources([
        _deployment("a", {"name": "gpt-5.1", "version": "2026-01-01"}),
        _deployment("b", "[parameters('deployments')[copyIndex()].model]"),
    ])
    assert _mdl001(ctx).status == "not-verified"


def test_mdl001_floating_wins_over_expression():
    """A real floating `latest` is a provable must-fix even when another
    deployment is param-driven."""
    ctx = _ctx_with_resources([
        _deployment("a", {"name": "gpt-5.1", "version": "latest"}),
        _deployment("b", "[parameters('deployments')[copyIndex()].model]"),
    ])
    assert _mdl001(ctx).status == "must-fix"


def test_mdl001_expression_version_is_not_verified():
    """model is an object but its `version` is an ARM expression string
    (e.g. a `modelVersion` parameter). The pin is real but not statically
    resolvable -> not-verified, NOT a false `pass`."""
    ctx = _ctx_with_resources(
        [_deployment("gpt", {"name": "gpt-5.1", "version": "[parameters('modelVersion')]"})]
    )
    f = _mdl001(ctx)
    assert f.status == "not-verified", f"expected not-verified, got {f.status!r}: {f.detail}"


def test_mdl001_absent_model_is_must_fix():
    """A deployment that declares NO model at all is unambiguously unpinned —
    it must stay must-fix, not be lumped in with deferred expressions."""
    absent = {
        "type": "Microsoft.CognitiveServices/accounts/deployments",
        "name": "gpt",
        "properties": {},
    }
    ctx = _ctx_with_resources([absent])
    assert _mdl001(ctx).status == "must-fix"


def test_mdl001_properties_expression_string_is_not_verified():
    """The whole `properties` block is an ARM expression string -> nothing is
    statically resolvable -> not-verified (defer to live), not a crash."""
    prop_expr = {
        "type": "Microsoft.CognitiveServices/accounts/deployments",
        "name": "gpt",
        "properties": "[parameters('deploymentProps')]",
    }
    ctx = _ctx_with_resources([prop_expr])
    assert _mdl001(ctx).status == "not-verified"


# ---------------------------------------------------------------------------
# D. Per-pillar static-analysis resilience guard
# ---------------------------------------------------------------------------


def test_run_pillar_fails_closed_on_static_crash(monkeypatch):
    """If a pillar's static runner raises, _run_pillar must degrade that
    pillar's tier-0 findings to a VISIBLE, gate-blocking must-fix (failing
    CLOSED) — surfacing the error — rather than propagate the exception OR
    silently relax the hard gate to a non-gating not-verified."""

    def _boom(_ctx):
        raise AttributeError("'str' object has no attribute 'get'")

    original = mod.PILLAR_RUNNERS["cost"]
    monkeypatch.setitem(mod.PILLAR_RUNNERS, "cost", (_boom, original[1]))

    ctx = _ctx_with_resources([])
    findings, _evidence = mod._run_pillar(
        "cost", ctx, static_only=True, tiers={0: True},
        sub=None, rg=None, resolved_posture="", agt_profile="none", quick=False,
    )

    cost_tier0 = [
        f for f in findings
        if f.pillar == "cost" and f.tier == 0
    ]
    assert cost_tier0, "expected cost tier-0 findings to be present"
    assert all(f.status == "must-fix" for f in cost_tier0), (
        "all cost tier-0 findings must fail closed to must-fix: "
        + repr([(f.id, f.status) for f in cost_tier0])
    )
    assert any("AttributeError" in f.detail for f in cost_tier0), (
        "the surfaced error must name the exception type"
    )
    # The security-critical invariant: a crashed static pillar must NOT let the
    # hard go-live gate pass. Fail-open here was the original defect.
    assert mod._hard_gate_would_fail(findings) is True, (
        "a crash-degraded pillar must keep the hard gate failing (fail-closed)"
    )


def test_run_pillar_reraises_unexpected_exception(monkeypatch):
    """The guard is narrowed to the JSON-shape-mismatch family; a genuinely
    unexpected error (not that family) must still surface by propagating, not
    be swallowed into a degraded pillar."""

    def _boom(_ctx):
        raise MemoryError("not a shape mismatch")

    original = mod.PILLAR_RUNNERS["cost"]
    monkeypatch.setitem(mod.PILLAR_RUNNERS, "cost", (_boom, original[1]))

    ctx = _ctx_with_resources([])
    raised = False
    try:
        mod._run_pillar(
            "cost", ctx, static_only=True, tiers={0: True},
            sub=None, rg=None, resolved_posture="", agt_profile="none", quick=False,
        )
    except MemoryError:
        raised = True
    assert raised, "unexpected (non-shape) exceptions must propagate, not be masked"


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for fn in fns:
        try:
            # crude monkeypatch shim for direct-run (pytest supplies the real one)
            if "monkeypatch" in fn.__code__.co_varnames:
                class _MP:
                    _saved: list = []
                    def setitem(self, d, k, v):
                        self._saved.append((d, k, d[k]))
                        d[k] = v
                    def undo(self):
                        for d, k, v in reversed(self._saved):
                            d[k] = v
                mp = _MP()
                try:
                    fn(mp)
                finally:
                    mp.undo()
            else:
                fn()
            print(f"  [PASS] {fn.__name__}")
        except Exception:
            fails += 1
            print(f"  [FAIL] {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
