"""Tests for apply-plan schema + builder (v0.4.0 Phase A, Task A4).

Run with `python3 tests/test_apply_plan_schema.py`.
"""
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production_ready.py"
_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


def test_apply_plan_kinds_constant():
    assert mod.APPLY_PLAN_KINDS == {
        "repo-edit",
        "sibling-skill",
        "manual",
        "deferred-to-pipeline",
    }


def test_apply_plan_schema_version_is_one():
    assert mod.APPLY_PLAN_SCHEMA_VERSION == 1


def test_build_apply_plan_pins_manifest_sha():
    manifest = {"version": "0.4.0", "findings": []}
    plan = mod.build_apply_plan(manifest=manifest, recipes={}, framing={"target_posture": "citadel-spoke"})
    expected_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    assert plan["manifest_sha256"] == expected_sha
    assert plan["schema_version"] == 1
    assert plan["framing"]["target_posture"] == "citadel-spoke"
    assert plan["items"] == []


def test_build_apply_plan_rejects_unknown_kind():
    manifest = {"version": "0.4.0", "findings": [{"id": "AGT-001", "status": "fail"}]}
    recipes = {"AGT-001": {"kind": "bogus", "summary": "x"}}
    try:
        mod.build_apply_plan(manifest=manifest, recipes=recipes, framing={})
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert "bogus" in str(e)


def test_build_apply_plan_skips_pass_findings():
    manifest = {
        "version": "0.4.0",
        "findings": [
            {"id": "AGT-001", "status": "pass"},
            {"id": "NET-002", "status": "fail"},
            {"id": "SEC-001", "status": "warn"},
            {"id": "OBS-101", "status": "not-applicable"},
        ],
    }
    recipes = {
        "AGT-001": {"kind": "repo-edit", "summary": "x"},
        "NET-002": {"kind": "repo-edit", "summary": "fix pe"},
        "SEC-001": {"kind": "manual", "summary": "rotate"},
        "OBS-101": {"kind": "sibling-skill", "summary": "x"},
    }
    plan = mod.build_apply_plan(manifest=manifest, recipes=recipes, framing={})
    ids = [it["finding_id"] for it in plan["items"]]
    assert ids == ["NET-002", "SEC-001"], f"only fail/warn should be planned: {ids}"


def test_build_apply_plan_emits_manual_when_recipe_missing():
    manifest = {"version": "0.4.0", "findings": [{"id": "MDL-110", "status": "fail"}]}
    plan = mod.build_apply_plan(manifest=manifest, recipes={}, framing={})
    assert plan["items"][0]["kind"] == "manual"
    assert "MDL-110" in plan["items"][0]["summary"]


def test_build_apply_plan_records_framing_path():
    """G4 e2e test asserts plan['framing_path'] exists when framing came from a file."""
    manifest = {"version": "0.4.0", "findings": []}
    plan = mod.build_apply_plan(
        manifest=manifest, recipes={}, framing={"target_posture": "agt"},
        framing_path="/tmp/framing.json",
    )
    assert plan["framing_path"] == "/tmp/framing.json"


def test_write_apply_plan_round_trip():
    manifest = {"version": "0.4.0", "findings": [{"id": "NET-002", "status": "fail"}]}
    recipes = {"NET-002": {"kind": "repo-edit", "summary": "fix pe",
                           "target_file": "infra/main.bicep", "edit_type": "replace"}}
    plan = mod.build_apply_plan(manifest=manifest, recipes=recipes, framing={"target_posture": "agt"})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        out = pathlib.Path(f.name)
    mod.write_apply_plan(plan, out)
    round = json.loads(out.read_text())
    assert round["items"][0]["finding_id"] == "NET-002"
    assert round["items"][0]["target_file"] == "infra/main.bicep"


# ---------------------------------------------------------------------------
# A6: recipe catalog loader
# ---------------------------------------------------------------------------

def test_recipe_catalog_loader_parses_markdown():
    tmp = pathlib.Path(tempfile.mkdtemp())
    rdir = tmp / "remediation-recipes"
    rdir.mkdir()
    (rdir / "AGT-001.md").write_text("""---
kind: repo-edit
summary: Set defaultPolicyContentType to JSON
target_file: infra/foundry/agent.bicep
edit_type: replace
---

## Target file
`infra/foundry/agent.bicep`

## Edit type
`replace`

## Edit recipe
Replace `defaultPolicyContentType: 'XML'` with `defaultPolicyContentType: 'JSON'`.

## Verification
Re-run threadlight, AGT-001 status flips to `pass`.
""")
    (rdir / "_template.md").write_text("---\nkind: manual\n---\n")  # should be skipped
    recipes = mod.load_recipe_catalog(rdir)
    assert "AGT-001" in recipes
    assert "_template" not in recipes
    assert recipes["AGT-001"]["kind"] == "repo-edit"
    assert recipes["AGT-001"]["summary"].startswith("Set defaultPolicyContentType")
    assert recipes["AGT-001"]["target_file"] == "infra/foundry/agent.bicep"
    assert recipes["AGT-001"]["edit_type"] == "replace"


def test_recipe_catalog_rejects_unknown_kind():
    tmp = pathlib.Path(tempfile.mkdtemp())
    rdir = tmp / "remediation-recipes"
    rdir.mkdir()
    (rdir / "BAD-001.md").write_text("---\nkind: not-a-kind\n---\n\n## Target file\nx\n")
    try:
        mod.load_recipe_catalog(rdir)
        raise AssertionError("should have raised")
    except SystemExit as e:
        assert "not-a-kind" in str(e)


def test_recipe_catalog_dir_helper_resolves_to_references():
    p = mod._recipe_catalog_dir()
    assert p.name == "remediation-recipes"
    assert p.parent.name == "references"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
