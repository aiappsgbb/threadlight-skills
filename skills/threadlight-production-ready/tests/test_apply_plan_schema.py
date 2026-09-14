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


def _native_handoff_manifest():
    rows = [
        mod._mk_finding("NET-001", "must-fix"),
        mod._mk_finding("SEC-001", "should-fix"),
        mod._mk_finding("OBS-101", "not-verified"),
        mod._mk_finding("EVAL-001", "pass"),
        mod._mk_finding("HITL-001", "not-applicable"),
        mod._mk_finding("SUP-001", "waived"),
    ]
    grouped = {}
    for finding in rows:
        grouped.setdefault(finding.pillar, []).append(finding)
    return mod._build_manifest(
        posture={
            "declared": "standard-ai-gateway",
            "detected": None,
            "resolved": "standard-ai-gateway",
        },
        pillar_results_raw=grouped,
        pillar_results_waived=grouped,
        evidence=[],
        not_verified=[row for row in rows if row.status == "not-verified"],
        waivers={},
        tiers={0: True},
        warnings=[],
        agt_profile="none",
        safe_check_ref={},
        quick=False,
        static_only=True,
    )


def test_apply_plan_includes_current_statuses_and_legacy_aliases():
    manifest = {
        "findings": [
            {"id": "NET-001", "status": "must-fix"},
            {"id": "SEC-001", "status": "should-fix"},
            {"id": "OBS-101", "status": "not-verified"},
            {"id": "IAM-101", "status": "fail"},
            {"id": "EVAL-003", "status": "warn"},
            {"id": "EVAL-001", "status": "pass"},
            {"id": "HITL-001", "status": "not-applicable"},
            {"id": "SUP-001", "status": "waived"},
        ]
    }
    plan = mod.build_apply_plan(manifest=manifest, recipes={}, framing={})
    assert [item["finding_id"] for item in plan["items"]] == [
        "NET-001", "SEC-001", "OBS-101", "IAM-101", "EVAL-003"
    ]
    assert all(item["kind"] == "manual" for item in plan["items"])
    assert plan["schema_version"] == 1


def test_apply_plan_preserves_real_emitter_findings():
    manifest = _native_handoff_manifest()
    before = json.dumps(manifest, sort_keys=True)
    plan = mod.build_apply_plan(manifest=manifest, recipes={}, framing={})
    assert [item["finding_id"] for item in plan["items"]] == [
        "NET-001", "SEC-001", "OBS-101"
    ]
    assert plan["manifest_sha256"] == hashlib.sha256(before.encode()).hexdigest()
    assert json.dumps(manifest, sort_keys=True) == before


def test_native_plan_roundtrip_preserves_restricted_handoff_and_source_hash():
    manifest = _native_handoff_manifest()
    recipes = {
        "NET-001": {"kind": "repo-edit", "summary": "Configure the selected network"},
        "SEC-001": {"kind": "manual", "summary": "Review the selected secret control"},
        "OBS-101": {"kind": "sibling-skill", "summary": "Observe the selected workload"},
    }
    recipe_before = json.dumps(recipes, sort_keys=True)
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        source = root / "production-readiness-manifest.json"
        source.write_text(json.dumps(manifest), encoding="utf-8")
        saved_manifest = json.loads(source.read_text(encoding="utf-8"))
        plan = mod.build_apply_plan(
            manifest=saved_manifest,
            recipes=recipes,
            framing={"restricted_environment": True},
        )
        target = root / "apply-plan.json"
        mod.write_apply_plan(plan, target)
        saved_plan = json.loads(target.read_text(encoding="utf-8"))
        assert [
            (item["finding_id"], item["kind"]) for item in saved_plan["items"]
        ] == [
            ("NET-001", "manual"),
            ("SEC-001", "manual"),
            ("OBS-101", "sibling-skill"),
        ]
        assert saved_plan["manifest_sha256"] == hashlib.sha256(
            json.dumps(saved_manifest, sort_keys=True).encode()
        ).hexdigest()
        assert "demoted from repo-edit" in saved_plan["items"][0]["summary"]
        assert json.loads(source.read_text(encoding="utf-8")) == saved_manifest
    assert json.dumps(recipes, sort_keys=True) == recipe_before


def test_current_must_fix_still_rejects_unknown_recipe_kind():
    manifest = {"findings": [{"id": "NET-001", "status": "must-fix"}]}
    try:
        mod.build_apply_plan(
            manifest=manifest,
            recipes={"NET-001": {"kind": "invalid", "summary": "invalid recipe"}},
            framing={},
        )
    except SystemExit as error:
        assert "unknown kind" in str(error)
    else:
        raise AssertionError("Current must-fix must validate its selected recipe")


def test_apply_plan_documentation_preserves_advisory_scope():
    skill = SCRIPT.parent.parent / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "Current `must-fix`, `should-fix`, and `not-verified` findings" in text
    assert "Legacy `fail` and `warn` remain supported." in text
    assert "A plan is a proposal, not approval to edit, provision or deploy." in text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
