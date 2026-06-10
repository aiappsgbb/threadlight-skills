"""End-to-end smoke: framing -> assess -> apply-plan -> CI/CD scaffold.

Runs the script as a subprocess, asserts on the artifacts it produces.
Uses the sample-pilot-restricted fixture so the test exercises the
central-team-handoff path (the harder code path).
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"
FIXT = ROOT / "references" / "fixtures" / "sample-pilot-restricted"


def _setup_workdir():
    tmp = pathlib.Path(tempfile.mkdtemp())
    for child in FIXT.iterdir():
        if child.is_file():
            shutil.copy(child, tmp / child.name)
        else:
            shutil.copytree(child, tmp / child.name)
    return tmp


def test_e2e_handoff_path_produces_scaffold_and_apply_plan():
    tmp = _setup_workdir()
    apply_plan = tmp / "apply-plan.json"
    r = subprocess.run([
        sys.executable, str(SCRIPT),
        "--framing-file", str(tmp / "framing.json"),
        "--apply-plan-out", str(apply_plan),
        "--scaffold-cicd",
        "--repo-full-name", "aiappsgbb/threadlight-skills",
        "--no-rights-probe",
        "--static",
        "--target", "citadel-spoke",
        "--quiet",
    ], capture_output=True, text=True, cwd=str(tmp), timeout=120)

    assert r.returncode == 0, (
        f"script failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )

    assert apply_plan.exists(), "apply-plan.json not written"
    plan = json.loads(apply_plan.read_text())
    assert "items" in plan
    assert "manifest_sha256" in plan
    assert "framing_path" in plan
    kinds = {i["kind"] for i in plan["items"]}
    assert "repo-edit" not in kinds, (
        f"unexpected repo-edit items in restricted plan: {kinds}"
    )

    wf = tmp / ".github" / "workflows" / "azd-deploy-prod.yml"
    rb = tmp / "docs" / "threadlight-cicd" / "central-team-uami-readme.md"
    assert wf.exists(), "workflow scaffold not written"
    assert rb.exists(), "UAMI readme not written"
    assert "{{TARGET_SUBSCRIPTION_ID}}" not in wf.read_text(), (
        "unresolved token in workflow"
    )
    assert "{{REPO_FULL_NAME}}" not in rb.read_text(), (
        "unresolved token in readme"
    )

    assert "central-team handoff" in r.stderr, (
        f"phase banner missing from stderr:\n{r.stderr}"
    )

    # Default --out is tests/production-readiness-manifest.json, so the
    # actual location relative to cwd is tmp/tests/.
    manifest = json.loads(
        (tmp / "tests" / "production-readiness-manifest.json").read_text()
    )
    assert manifest["version"] == "0.4.0"
    assert manifest["phase_decision"]["phase2_mode"] == "central-team handoff"


def test_e2e_apply_plan_matches_golden_kinds():
    """The set of `kind` values in apply-plan.json for the restricted fixture
    is stable across runs (deterministic ordering check)."""
    tmp = _setup_workdir()
    apply_plan = tmp / "apply-plan.json"
    subprocess.run([
        sys.executable, str(SCRIPT),
        "--framing-file", str(tmp / "framing.json"),
        "--apply-plan-out", str(apply_plan),
        "--no-rights-probe", "--static",
        "--target", "citadel-spoke", "--quiet",
    ], check=True, cwd=str(tmp), timeout=120)
    plan = json.loads(apply_plan.read_text())
    actual_kinds = sorted({i["kind"] for i in plan["items"]})
    expected_kinds = ["deferred-to-pipeline", "manual", "sibling-skill"]
    assert actual_kinds == expected_kinds, f"kinds drifted: {actual_kinds}"


if __name__ == "__main__":
    test_e2e_handoff_path_produces_scaffold_and_apply_plan()
    test_e2e_apply_plan_matches_golden_kinds()
    print("OK")
