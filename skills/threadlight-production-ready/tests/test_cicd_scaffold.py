"""Tests for Phase D: CI/CD scaffold (D3-D6).

Covers: authoritative _scaffold_cicd delegation,
        _detect_repo_full_name, --scaffold-cicd CLI flag, and the
        deferred-to-pipeline hint emitted by main().
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import json
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Python 3.14 dataclass + importlib workaround: register in sys.modules BEFORE exec_module.
_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


# --- D4 ---------------------------------------------------------------

def test_scaffold_writes_both_files():
    tmp = pathlib.Path(tempfile.mkdtemp())
    framing = {
        "target_subscription_id": "sub",
        "target_resource_group": "rg",
        "target_posture": "agt",
        "azure_tenant_id": "00000000-0000-0000-0000-000000000001",
    }
    written = mod._scaffold_cicd(framing, "aiappsgbb/threadlight-skills", out_root=tmp)
    paths = [str(p.relative_to(tmp)) for p in written]
    assert ".github/workflows/azd-deploy-prod.yml" in paths
    assert "docs/threadlight-cicd/central-team-uami-readme.md" in paths
    wf = (tmp / ".github/workflows/azd-deploy-prod.yml").read_text()
    # all rendered tokens substituted
    assert "{{TARGET_SUBSCRIPTION_ID}}" not in wf
    assert "sub" in wf
    readme = (tmp / "docs/threadlight-cicd/central-team-uami-readme.md").read_text()
    assert "{{REPO_FULL_NAME}}" not in readme
    assert "aiappsgbb/threadlight-skills" in readme


# --- D5 ---------------------------------------------------------------

def test_detect_repo_full_name_from_https_url():
    """Stub out subprocess to simulate `git remote get-url origin`."""
    import types
    saved = mod.subprocess.run
    try:
        def fake_run(cmd, **kw):
            return types.SimpleNamespace(
                returncode=0, stdout="https://github.com/aiappsgbb/threadlight-skills.git\n", stderr=""
            )
        mod.subprocess.run = fake_run
        assert mod._detect_repo_full_name("/anywhere") == "aiappsgbb/threadlight-skills"
    finally:
        mod.subprocess.run = saved


def test_detect_repo_full_name_from_ssh_url():
    import types
    saved = mod.subprocess.run
    try:
        def fake_run(cmd, **kw):
            return types.SimpleNamespace(
                returncode=0, stdout="git@github.com:aiappsgbb/threadlight-skills.git\n", stderr=""
            )
        mod.subprocess.run = fake_run
        assert mod._detect_repo_full_name("/anywhere") == "aiappsgbb/threadlight-skills"
    finally:
        mod.subprocess.run = saved


def test_detect_repo_full_name_returns_none_on_non_github():
    import types
    saved = mod.subprocess.run
    try:
        def fake_run(cmd, **kw):
            return types.SimpleNamespace(
                returncode=0, stdout="https://dev.azure.com/org/proj/_git/repo\n", stderr=""
            )
        mod.subprocess.run = fake_run
        assert mod._detect_repo_full_name("/anywhere") is None
    finally:
        mod.subprocess.run = saved


def test_scaffold_via_cli_flag_e2e():
    tmp = pathlib.Path(tempfile.mkdtemp())
    framing_file = tmp / "framing.json"
    framing_file.write_text(
        '{'
        '"target_subscription_id": "sub", "target_resource_group": "rg",'
        '"target_posture": "agt", "provisioning_rights": true,'
        '"central_platform_team": false, "restricted_environment": false,'
        '"cicd_target": "github-actions",'
        '"azure_tenant_id": "00000000-0000-0000-0000-000000000001"'
        '}'
    )
    script = ROOT / "scripts" / "production_ready.py"
    r = subprocess.run(
        ["python3", str(script),
         "--framing-file", str(framing_file),
         "--scaffold-cicd",
         "--repo-full-name", "aiappsgbb/threadlight-skills",
         "--no-rights-probe",
         "--apply-plan-out", str(tmp / "apply-plan.json")],
        capture_output=True, text=True, cwd=str(tmp),
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (tmp / ".github/workflows/azd-deploy-prod.yml").exists()
    assert (tmp / "docs/threadlight-cicd/central-team-uami-readme.md").exists()


# --- D6 ---------------------------------------------------------------

def test_scaffold_hint_when_pipeline_items_present_and_flag_absent():
    """When apply-plan contains kind=deferred-to-pipeline items and the
    operator did NOT pass --scaffold-cicd, stderr must include a hint."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    framing_file = tmp / "framing.json"
    framing_file.write_text(
        '{'
        '"target_subscription_id": "sub", "target_resource_group": "rg",'
        '"target_posture": "agt", "provisioning_rights": true,'
        '"central_platform_team": false, "restricted_environment": false,'
        '"cicd_target": "github-actions",'
        '"azure_tenant_id": "00000000-0000-0000-0000-000000000001"'
        '}'
    )
    # Force-inject a deferred-to-pipeline item by writing an apply-plan post-hoc?
    # Instead: trigger the dispatcher path that builds apply-plan from the
    # FINDING_CATALOG-filtered recipes (REL-102 is deferred-to-pipeline). We
    # rely on the dispatcher to emit the hint when ANY pipeline item exists.
    # If items=[] (Phase C integration gap), this test simulates by patching.
    plan_path = tmp / "apply-plan.json"
    plan_path.write_text(
        '{"schema_version":1,"framing_path":"x","items":['
        '{"id":"REL-102","kind":"deferred-to-pipeline","title":"x","prompt":"x"}'
        ']}'
    )
    # We invoke the hint logic directly by importing the helper from main.
    # The hint is emitted from main(); easiest to assert on _hint_pipeline_scaffold.
    captured = []
    saved = mod._eprint
    try:
        mod._eprint = lambda *a, **k: captured.append(" ".join(str(x) for x in a))
        mod._hint_pipeline_scaffold_if_needed(
            apply_plan={"items": [{"id": "REL-102", "kind": "deferred-to-pipeline"}]},
            scaffold_cicd_flag=False,
        )
    finally:
        mod._eprint = saved
    assert any("--scaffold-cicd" in line for line in captured), captured


def test_scaffold_hint_silent_when_flag_passed():
    captured = []
    saved = mod._eprint
    try:
        mod._eprint = lambda *a, **k: captured.append(" ".join(str(x) for x in a))
        mod._hint_pipeline_scaffold_if_needed(
            apply_plan={"items": [{"id": "REL-102", "kind": "deferred-to-pipeline"}]},
            scaffold_cicd_flag=True,
        )
    finally:
        mod._eprint = saved
    assert not any("--scaffold-cicd" in line for line in captured), captured


def test_scaffold_hint_silent_when_no_pipeline_items():
    captured = []
    saved = mod._eprint
    try:
        mod._eprint = lambda *a, **k: captured.append(" ".join(str(x) for x in a))
        mod._hint_pipeline_scaffold_if_needed(
            apply_plan={"items": [{"id": "NET-002", "kind": "repo-edit"}]},
            scaffold_cicd_flag=False,
        )
    finally:
        mod._eprint = saved
    assert not any("--scaffold-cicd" in line for line in captured), captured


# --- B5 (v0.5.0): runbook has no surviving placeholders -----------------

def test_runbook_has_no_unfilled_angle_bracket_placeholders():
    """Closes #33 defense-in-depth: with a full framing dict (including
    azure_tenant_id), the scaffolded runbook must have zero surviving '<...>'
    patterns — every angle-bracket placeholder must be substituted."""
    import re
    tmp = pathlib.Path(tempfile.mkdtemp())
    framing = {
        "target_subscription_id": "00000000-0000-0000-0000-000000000000",
        "target_resource_group": "rg-test",
        "target_posture": "agt",
        "provisioning_rights": True,
        "central_platform_team": False,
        "restricted_environment": False,
        "cicd_target": "github-actions",
        "azure_tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    written = mod._scaffold_cicd(framing, "aiappsgbb/threadlight-skills", out_root=tmp)
    for path in written:
        if not str(path).endswith("central-team-uami-readme.md"):
            continue
        text = path.read_text(encoding="utf-8")
        matches = re.findall(r"<[a-z-]+>", text)
        # Allow `<tenant-id>` ONLY if it's inside a code-fence comment or quoted example;
        # the strict assertion is "the substitution token didn't leak through".
        assert matches == [], f"Runbook has unfilled placeholders: {matches}. See #33."


@pytest.mark.parametrize("platform", ["github-actions", "azure-devops"])
def test_secondary_scaffold_uses_the_authoritative_verified_release(tmp_path, platform):
    framing = dict(cicd_target=platform, azure_tenant_id="tenant", target_subscription_id="sub",
                   target_resource_group="prod-rg", central_env_required=True, central_env_exists=True,
                   validation_resource_group="validation-rg", validation_subscription_id="sub")
    paths = mod._scaffold_cicd(framing, "example/app", tmp_path)
    pipeline = tmp_path / (".github/workflows/azd-deploy-prod.yml" if platform == "github-actions"
                           else "azure-pipelines.yml")
    assert pipeline in paths
    text = pipeline.read_text()
    assert "release_runner.py" in text and "--receipt-sha256" in text
    assert "continue-on-error" not in text
    assert "azd deploy --no-prompt" not in text
    assert (tmp_path / ".threadlight/skills/threadlight-cicd/scripts/release_runner.py").is_file()
    assert not (tmp_path / "specs/release-policy.json").exists()
    policy = json.loads((tmp_path / "specs/release-policy.example.json").read_text())
    assert policy["production"]["tenant_id"] == "tenant"
    assert policy["validation"]["resource_group"] == "validation-rg"
    assert json.loads((tmp_path / "docs/threadlight-cicd/onboarding-path.json").read_text())["path"] == "spoke-onboard"


def test_framing_wizard_exposes_both_supported_release_platforms():
    question = next(item for item in mod.FRAMING_QUESTIONS if item["id"] == "cicd_target")
    assert question["choices"] == ["github-actions", "azure-devops"]


if __name__ == "__main__":
    failures = []
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures.append((name, repr(e)))
                print(f"FAIL {name}: {e!r}")
            except Exception as e:
                failures.append((name, repr(e)))
                print(f"ERROR {name}: {e!r}")
    if failures:
        print(f"\n{len(failures)} test(s) failed")
        sys.exit(1)
    print("OK")
