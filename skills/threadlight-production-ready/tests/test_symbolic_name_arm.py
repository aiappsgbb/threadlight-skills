"""Tests for symbolic-name ARM (languageVersion 2.0) support and scorecard
robustness against unresolvable ARM expression strings (v0.8.1).

Three defects pinned here:

  1. ``BicepGraph._walk`` crashed on ``languageVersion: 2.0`` ARM, where the
     top-level ``resources`` is a MAP ``{symbolicName: obj}`` rather than a
     list — the modern azd/Bicep default. It must accept both shapes.
  2. ``_check_model_lifecycle_static`` (MDL-001) crashed when a deployment's
     ``properties.model`` arrived as an ARM expression STRING (parameter /
     copy-loop). It must degrade to ``not-verified`` (verify at deploy), never
     crash and never raise a false must-fix.
  3. A single pillar's static analyzer raising must not abort the whole
     assessment: ``_run_pillar`` degrades that pillar's tier-0 findings to
     ``not-verified`` and keeps going.

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


# ---------------------------------------------------------------------------
# D. Per-pillar static-analysis resilience guard
# ---------------------------------------------------------------------------


def test_run_pillar_degrades_on_static_crash(monkeypatch):
    """If a pillar's static runner raises, _run_pillar must degrade that
    pillar's tier-0 findings to not-verified (surfacing the error) rather than
    propagate the exception."""

    def _boom(_ctx):
        raise RuntimeError("synthetic ARM shape the analyzer did not expect")

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
    assert all(f.status == "not-verified" for f in cost_tier0), (
        "all cost tier-0 findings must degrade to not-verified: "
        + repr([(f.id, f.status) for f in cost_tier0])
    )
    assert any("RuntimeError" in f.detail for f in cost_tier0), (
        "the surfaced error must name the exception type"
    )


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
