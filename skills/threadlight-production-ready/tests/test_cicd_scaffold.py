"""Tests for Phase D: CI/CD scaffold (D3-D6).

Covers: _render_template, _cicd_context_from_framing, _scaffold_cicd,
        _detect_repo_full_name, --scaffold-cicd CLI flag, and the
        deferred-to-pipeline hint emitted by main().
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Python 3.14 dataclass + importlib workaround: register in sys.modules BEFORE exec_module.
_spec = importlib.util.spec_from_file_location(
    "production_ready", ROOT / "scripts" / "production_ready.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = mod
_spec.loader.exec_module(mod)


# --- D3 ---------------------------------------------------------------

def test_render_replaces_all_tokens():
    with tempfile.NamedTemporaryFile("w", suffix=".tmpl", delete=False) as f:
        f.write("hello {{NAME}} from {{PLACE}}")
        p = pathlib.Path(f.name)
    out = mod._render_template(p, {"NAME": "Threadlight", "PLACE": "Azure"})
    assert out == "hello Threadlight from Azure"


def test_render_leaves_unknown_token_visible():
    with tempfile.NamedTemporaryFile("w", suffix=".tmpl", delete=False) as f:
        f.write("hi {{NAME}} {{UNKNOWN}}")
        p = pathlib.Path(f.name)
    out = mod._render_template(p, {"NAME": "x"})
    # leaves it visible so the operator can spot gaps
    assert "{{UNKNOWN}}" in out


def test_render_builds_context_from_framing():
    framing = {
        "target_subscription_id": "sub",
        "target_resource_group": "rg",
        "target_posture": "agt",
        "central_platform_team": "platform-prod",
    }
    ctx = mod._cicd_context_from_framing(framing, repo_full_name="aiappsgbb/threadlight-skills")
    assert ctx["TARGET_SUBSCRIPTION_ID"] == "sub"
    assert ctx["REPO_FULL_NAME"] == "aiappsgbb/threadlight-skills"
    assert ctx["REPO_SLUG"] == "aiappsgbb-threadlight-skills"
    assert ctx["UAMI_NAME"].startswith("uami-")


# --- D4 ---------------------------------------------------------------

def test_scaffold_writes_both_files():
    tmp = pathlib.Path(tempfile.mkdtemp())
    framing = {
        "target_subscription_id": "sub",
        "target_resource_group": "rg",
        "target_posture": "agt",
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
        '"cicd_target": "github-actions"'
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
        '"cicd_target": "github-actions"'
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
