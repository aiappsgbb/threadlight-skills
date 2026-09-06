"""Task14: execute workflow script strings and real fail-closed input validation.

Command recording proves orchestration/order only, never deployed enforcement.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/threadlight-e2e-foundry.yml"
SCRIPT = ROOT / "scripts/ci/runtime_readiness.py"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def helper():
    assert SCRIPT.exists(), "Missing executable readiness workflow producer"
    spec = importlib.util.spec_from_file_location("runtime_readiness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def prepared_project(tmp_path_factory):
    """LOCAL ONLY: syntax-valid placeholder inputs, never a deployment credential.

    Execute production preparation unchanged. Only native model/transport/storage
    seams in the existing child are fixtures; no Azure commands are executed.
    """
    if os.environ.get("THREADLIGHT_READINESS_NATIVE") != "1":
        pytest.skip("Run the explicit local-native-contract preparation job; not a docs/native proof")
    directory = tmp_path_factory.mktemp("actual-readiness")
    mod = helper()
    generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
    bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
    example = ROOT / "examples/returns-triage-governed"
    source = directory / "source"
    approved = subprocess.check_output(["git", "-C", str(example), "ls-files", "-z"], text=True)
    paths = [p for p in approved.split("\0") if p and not p.startswith("infra/")]
    for relative in paths:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example / relative, target)
    # The protected pipeline requires a reviewed resource-group ejection, not the
    # example's subscription bootstrap. This fixture intentionally has no infra.
    env = {**os.environ, "GIT_AUTHOR_NAME": "Local fixture", "GIT_COMMITTER_NAME": "Local fixture",
           "GIT_AUTHOR_EMAIL": "fixture@localhost", "GIT_COMMITTER_EMAIL": "fixture@localhost"}
    def source_git(*args):
        subprocess.run(["git", "-C", str(source), *args], check=True, env=env, capture_output=True)
    source_git("init", "-q")
    source_git("remote", "add", "origin", "file:///local-fixtures/readiness-input.git")
    source_git("add", "--", *paths)
    source_git("-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
               "commit", "-qm", "Reviewed local-only input")
    (source / ".gitignore").write_text("keep-local-only/\n")
    (source / ".env").write_text("LOCAL_ONLY_TOKEN=never-commit\n")
    with (source / "README.md").open("a") as readme:
        readme.write("\nLocal fixture: reviewed working-tree changes are digest-pinned.\n")
    policy = source / "src/agent/governance/policy"
    bundle = bundle_api.build_bundle(source=policy, destination=directory / "bundle",
                                     policy_id="returns-write-v1", version="1")
    tenant = "11111111-1111-1111-1111-111111111111"
    package = {
        "agent_service": "returns-triage", "agent_id": "returns-triage",
        "environment": "preproduction", "policy_id": "returns-write-v1", "policy_version": "1",
        "policy_digest": bundle.bundle_digest, "signed_envelope": str(directory / "envelope.json"),
        "tenant_id": tenant, "key_id": "https://local-only.vault.azure.net/keys/policy/" + "a" * 32,
        "control_plane_scope": "api://22222222-2222-2222-2222-222222222222/.default",
        "gateway_scope": "api://33333333-3333-3333-3333-333333333333/.default",
        "control_plane_url": "https://control.example", "gateway_url": "https://gateway.example/mcp",
        "approver_roles": ["Approver"],
        "network": {"posture": "public-pilot", "allowed_ips": ["192.0.2.10/32"],
                    "environment_id": f"/subscriptions/{tenant}/resourceGroups/local-only/providers/Microsoft.App/managedEnvironments/test"},
    }
    mod.write(package["signed_envelope"], {
        "envelope": {"policy_id": "returns-write-v1", "version": "1",
                     "content_digest": bundle.bundle_digest, "tenant_id": tenant,
                     "key_id": package["key_id"],
                     "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
        "signature": "bG9jYWwtb25seS1ub3QtYS1wcm9kdWN0aW9uLXNpZ25hdHVyZQ==",
    })
    deployment = {"infrastructure": {
        "prefix": "fixture", "storage_name": "fixturestorage", "cosmos_name": "fixturecosmos",
        "vault_name": "local-only", "acr_id": f"/subscriptions/{tenant}/resourceGroups/local-only/providers/Microsoft.ContainerRegistry/registries/fixture",
        "acr_authorization": "rbac", "tenant_id": tenant,
        "control_plane_app_id": package["control_plane_scope"][6:-9],
        "gateway_app_id": package["gateway_scope"][6:-9],
        "network": package["network"], "agent_id": package["agent_id"],
        "environment": package["environment"], "runtime": "microsoft-agent-framework",
        "enable_gateway": False, "human_clients": [tenant], "approver_subjects": [tenant],
        "auditor_subjects": [], "approver_roles": ["Approver"],
    }}
    mod.write(directory / "protected-token.json", {"token": "LOCAL-ONLY-DO-NOT-COMMIT"})
    mod.write(directory / "operator-private.json", {"operator_private": "PRIVATE-ARTIFACT-CANARY"})
    config = {
        "source_project": source, "policy_source": policy, "package": package,
        "deployment": deployment, "input_base": directory, "input_digest": "local-only",
        "probe_input": {"local_only": True},
        "probe_files": {
            "fixtures/protected-token.json": {"path": "protected-token.json"},
            "tests/operator-private-manifest.json": {"path": "operator-private.json"},
        },
        "source_digests": {"source_project": generator.tree_digest(source),
                           "policy_source": generator.tree_digest(policy)},
    }
    project = directory / "prepared"
    mod.prepare(project, config)
    tools = project / ".governance-tools"
    cache = tools / ".governance-validation"
    cache.mkdir()
    # Reuse qualified installed bytes, not old proof. Child observe() verifies
    # every wheel/installed module and the unchanged OPA pin inside Linux.
    for name in ("linux-venv", "wheels"):
        shutil.copytree(ROOT / ".governance-validation" / name, cache / name,
                        ignore=shutil.ignore_patterns("lib64"))
    shutil.copyfile(ROOT / ".governance-validation/opa-linux-amd64", cache / "opa-linux-amd64")
    return project, config


def test_actual_prepare_has_traceable_standalone_snapshot_and_runs_gate(prepared_project, monkeypatch):
    project, config = prepared_project
    from scripts.ci import readiness_evidence as evidence
    monkeypatch.setenv("RUNNER_TEMP", str(project.parent))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    journal = evidence.initialize(project)
    evidence.begin_stage(journal, project, "predeploy")
    script = project / ".governance-tools/skills/threadlight-governed-actions/scripts/governed_actions.py"
    result = subprocess.run([sys.executable, str(script), "--target", str(project),
                             "--phase", "pre-deploy", "--emit", "--gate"],
                            cwd=project, text=True, capture_output=True, timeout=180,
                            env={**os.environ, "GIT_CEILING_DIRECTORIES": str(project.parent)})
    (project.parent / "gate.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence.finish_stage(journal, project, "predeploy", True)
    assert evidence.export(journal, project)
    exported = json.loads((journal.parent / "upload/governed-actions-summary.json").read_text())
    assert exported["evidence"]["scope"] == "local-assessment-not-deployment"
    def git(*args):
        return subprocess.check_output(["git", "-C", str(project), *args], text=True).strip()
    assert Path(git("rev-parse", "--show-toplevel")) == project
    provenance = json.loads((project / "governance/source-provenance.json").read_text())
    expected = subprocess.check_output(["git", "-C", str(config["source_project"]),
                                       "rev-parse", "HEAD"], text=True).strip()
    assert provenance["source_commit"] == expected
    assert provenance["source_project"] == "."
    assert provenance["source_dirty"] is True
    assert provenance["scope"] == "local-generated-snapshot-not-deployment"
    assert git("rev-parse", "HEAD") != expected
    tracked = set(git("ls-files").splitlines())
    assert {"src/agent/container.py", "src/agent/governance_host.py",
            "src/agent/runtime/governance_provider.py",
            ".github/workflows/native-local.yml"} <= tracked
    protected = {"fixtures/protected-token.json", "src/agent/governance-config.json",
                 "src/agent/policy-envelope.json", "infra/main.parameters.json"}
    assert not tracked & protected
    assert not any(p.startswith(".threadlight/") for p in tracked)
    assert ".env" not in tracked
    assert "keep-local-only/" in (project / ".gitignore").read_text()
    for path in protected:
        assert git("check-ignore", path) == path
    assert git("status", "--porcelain", "--untracked-files=no") == ""
    manifest = json.loads((project / "tests/governed-actions-manifest.json").read_text())
    assert manifest["source"]["commit"] == git("rev-parse", "HEAD")
    assert manifest["source"]["dirty"] is False
    assert manifest["summary"]["verdict"] == "partial"
    agent = project / "src/agent/container.py"
    original = agent.read_bytes()
    try:
        agent.write_bytes(original + b"\n# observed local modification\n")
        assert "src/agent/container.py" in git("diff", "--name-only")
        checked = subprocess.run([sys.executable, "-c",
            "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "import governed_actions; assert governed_actions.resolve_source(Path(sys.argv[2])).dirty",
            str(script.parent), str(project)], cwd=project, capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
    finally:
        agent.write_bytes(original)


def test_actual_prepare_served_factory_executes_existing_native_local14(prepared_project):
    project, _ = prepared_project
    scripts = project / ".governance-tools/skills/threadlight-governed-actions/scripts"
    code = (
        "import sys,json; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
        "import native_local,inventory; project=Path(sys.argv[2]); "
        "declaration=native_local.contract(project); events=native_local.execute(project,declaration); "
        "paths,results,pins=native_local.evaluate(events,declaration,inventory.build_action_inventory(project).actions); "
        "assert len([r for r in results if r.status=='pass']) == 14; "
        "assert events[0]['observation']['deployed_image']=='not-verified'; "
        "print(json.dumps({'native_passes':14,'paths':len(paths),"
        "'terminals':len([e for e in events if e['event']=='terminal'])}))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(scripts), str(project)],
                            cwd=project, text=True, capture_output=True, timeout=180)
    (project.parent / "native.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["native_passes"] == 14
    assert (project / "src/agent/container.py").read_bytes() == (
        ROOT / "examples/returns-triage-governed/src/agent/container.py").read_bytes()
    assert (project / "src/agent/governance_host.py").read_bytes() == (
        ROOT / "skills/threadlight-deploy/references/governance/maf-container.py").read_bytes()
    # Imports come from the generated deployment tree, never the catalog or the
    # runner's installed control-plane package; the child tests this same factory.
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, '.'); "
        "import container,governance_host,governance_application,runtime,govern_control_plane; "
        "assert container.build_host is governance_host.build_host; "
        "assert all(Path(m.__file__).resolve().is_relative_to(Path.cwd()) for m in "
        "(container,governance_host,governance_application,runtime,govern_control_plane))"
    )
    if sys.platform == "linux":
        command = [sys.executable, "-I", "-c", code]
    else:
        command = ["docker", "run", "--rm", "--pull", "never", "--network", "none",
                   "--platform", "linux/amd64", "-v", f"{project}:/work:ro",
                   "-w", "/work/src/agent", "python:3.12-slim",
                   "/work/.governance-tools/.governance-validation/linux-venv/bin/python", "-I", "-c", code]
    imports = subprocess.run(command, cwd=project / "src/agent", text=True, capture_output=True)
    assert imports.returncode == 0, imports.stderr


def test_actual_prepare_gitignored_private_manifest_is_not_an_upload(prepared_project, monkeypatch):
    project, _ = prepared_project
    private = project / "tests/operator-private-manifest.json"
    assert "PRIVATE-ARTIFACT-CANARY" in private.read_text()
    assert subprocess.check_output(
        ["git", "-C", str(project), "check-ignore", "tests/operator-private-manifest.json"],
        text=True).strip() == "tests/operator-private-manifest.json"
    assert "tests/operator-private-manifest.json" not in subprocess.check_output(
        ["git", "-C", str(project), "ls-files"], text=True).splitlines()
    # Git ignore is irrelevant to upload-artifact's filesystem glob matching.
    assert private in list((project / "tests").glob("*manifest.json"))
    step = next(s for s in workflow()["jobs"]["readiness-proof"]["steps"]
                if s.get("uses", "").startswith("actions/upload-artifact"))
    for pattern in step["with"]["path"].splitlines():
        if pattern.startswith("${{ env.GOV_PROJECT }}/"):
            relative = pattern.removeprefix("${{ env.GOV_PROJECT }}/")
            assert private not in project.glob(relative), "protected Git-ignored file selected for upload"
    from scripts.ci import readiness_evidence as evidence
    monkeypatch.setenv("RUNNER_TEMP", str(project.parent))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    journal = evidence.initialize(project)
    evidence.begin_stage(journal, project, "postdeploy")
    helper().write(project / "tests/runtime-readiness.json", {
        "status": "not-verified", "live": False, "reason": "PRIVATE-ARTIFACT-CANARY"})
    evidence.finish_stage(journal, project, "postdeploy", False)
    assert evidence.export(journal, project)
    exported = list((journal.parent / "upload").iterdir())
    assert {p.name for p in exported} == {"export-status.json", "runtime-readiness.json"}
    assert all("PRIVATE-ARTIFACT-CANARY" not in p.read_text() for p in exported)


def test_readiness_upload_only_uses_automatic_export_tree():
    steps = workflow()["jobs"]["readiness-proof"]["steps"]
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
    assert upload["with"]["path"] == "${{ steps.evidence-export.outputs.upload }}"
    export = next(s for s in steps if s.get("id") == "evidence-export")
    assert export["if"] == "always()" and upload["if"] == "always()"
    assert not export.get("continue-on-error")


@pytest.mark.parametrize("destination", [
    "tests/postdeploy-manifest.json", "tests/runtime-readiness.json",
    "tests/governed-actions-manifest.json", "tests/production-readiness-manifest.json",
    "specs/governance-manifest.json", "specs/manifest.json", "specs/governance-contract.json",
    "specs/governance-acceptances.json", "governance/probe-contract.json",
    "governance/installed-packages.json", "specs/SPEC.md", "AGENTS.md",
    ".threadlight/readiness-attempt.json", ".threadlight/governance-probe.json",
    ".threadlight/ci-input.json", ".threadlight/governance-package.json",
    "governance/source-provenance.json", ".git/config", ".gitignore",
    ".governance-tools/source-manifest.json", "tests/runtime-readiness.json/child",
    "../outside", "/absolute", "tests/../fixtures/alias.json", "./fixtures/alias.json",
    "fixtures//alias.json", "fixtures\\alias.json",
    "tests/Runtime-readiness.json",
])
def test_supplemental_paths_cannot_supply_evidence_or_trust_metadata(destination, tmp_path):
    path, config = input_fixture(tmp_path)
    config["probe_files"] = {destination: next(iter(config["probe_files"].values()))}
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="protected configuration"):
        helper().load_inputs(path)


@pytest.mark.parametrize("destination", [
    "tests/operator-private-manifest.json", ".threadlight/fixture/config.json",
    "config/probe-fixture.json", "mounts/governance-probe/config.json",
])
def test_private_config_and_read_only_mount_mappings_remain_accepted(destination, tmp_path):
    path, config = input_fixture(tmp_path)
    config["probe_files"] = {destination: next(iter(config["probe_files"].values()))}
    path.write_text(json.dumps(config))
    assert helper().load_inputs(path)["probe_files"] == config["probe_files"]


def test_supplemental_input_internal_symlink_parent_is_rejected(tmp_path):
    path, config = input_fixture(tmp_path)
    (tmp_path / "alias").symlink_to(tmp_path / "policy", target_is_directory=True)
    (tmp_path / "policy/input.json").write_text("{}")
    config["probe_files"]["fixtures/config.json"]["path"] = "alias/input.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="protected configuration"):
        helper().load_inputs(path)


def test_supplemental_destinations_cannot_overlap_each_other(tmp_path):
    path, config = input_fixture(tmp_path)
    item = next(iter(config["probe_files"].values()))
    config["probe_files"] = {"fixtures/config.json": item, "fixtures/config.json/child": item}
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="protected configuration"):
        helper().load_inputs(path)


def test_documented_artifacts_are_projections_not_parent_snapshots():
    text = (ROOT / "docs/production-readiness.md").read_text()
    section = text[text.index("Artifacts are"):text.index("> **What's new")]
    for term in ("RUNNER_TEMP", "projection", "raw", "journal", "export-status.json"):
        assert term in section
    assert "preserves all\nresource gaps" not in section


def test_every_workflow_script_still_parses():
    import re
    snippets = [s["run"] for job in workflow()["jobs"].values()
                for s in job["steps"] if "run" in s]
    assert len(snippets) >= 44
    for snippet in snippets:
        result = subprocess.run(["bash", "-n"], input=re.sub(r"\$\{\{.*?\}\}", "test", snippet),
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


def test_root_readiness_guidance_requires_current_binding_evidence_not_verdict_labels():
    text = (ROOT / "THREADLIGHT.md").read_text()
    workflow_guidance = text[text.index("The paid live workflow"):text.index("> **Runtime-policy authority.")]
    assert "governed/comprehensive/hardened" not in workflow_guidance
    for term in ("per-binding", "exact", "current", "local", "partial", "business", "noop"):
        assert term in workflow_guidance.lower()


def test_native_workflow_runs_actual_preparation_tests_separately_from_fixed_pin_suite():
    steps = workflow()["jobs"]["local-native-contract"]["steps"]
    step = next((s for s in steps if s.get("name") == "Actual prepared-project local gate"), None)
    assert step is not None, "Prepared-project regressions must execute on the native CI path"
    assert step["env"]["THREADLIGHT_READINESS_NATIVE"] == "1"
    for term in ("runner-venv", "deployment_runtime", "tests/ci/test_runtime_readiness.py",
                 "actual_prepare", "--prepare-local"):
        assert term in step["run"]


def test_snapshot_refuses_operator_ignores_that_hide_served_source(tmp_path):
    project = tmp_path / "generated"
    (project / "src/agent").mkdir(parents=True)
    (project / "src/agent/container.py").write_text("# approved served source\n")
    (project / ".governance-tools").mkdir()
    (project / ".governance-tools/source-manifest.json").write_text('{"files":{}}')
    (project / "azure.yaml").write_text("services:\n  agent:\n    project: src/agent\n")
    (project / ".gitignore").write_text("src/agent/container.py\n")
    with pytest.raises(ValueError, match="served source is ignored"):
        helper().snapshot_generated_sources(
            project, ROOT, {"source_commit": "a" * 40}, {"src/agent/container.py"},
            {"package": {"agent_service": "agent"}, "probe_files": {}})


def test_checked_out_subproject_preserves_input_commit_and_boundary():
    source = ROOT / "examples/returns-triage-governed"
    root, identity, approved = helper().source_identity(source, "approved-input-digest")
    assert root == ROOT
    assert identity["source_project"] == "examples/returns-triage-governed"
    assert identity["source_commit"] == subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    assert identity["source_digest"] == "approved-input-digest"
    assert "src/agent/container.py" in approved
    assert not any(p.startswith("../") for p in approved)


def test_separate_local_and_live_jobs_do_not_use_legacy_smoke_path():
    jobs = workflow()["jobs"]
    assert "local-native-contract" in jobs, "No independent native/CTK contract job"
    assert "readiness-proof" in jobs, "No protected real runtime readiness job"
    assert "inputs.mode != 'readiness-proof'" in jobs["e2e"]["if"]
    local = "\n".join(s.get("run", "") for s in jobs["local-native-contract"]["steps"])
    assert "run-governance-pin-tests.py" in local and "--deployment" in local
    live = jobs["readiness-proof"]
    assert live["environment"] == "governance-preproduction"
    assert live["needs"] == "local-native-contract"
    assert all(not s.get("continue-on-error") for s in live["steps"])
    scripts = "\n".join(s.get("run", "") for s in live["steps"])
    for stage in ("validate-inputs", "prepare", "predeploy", "deploy", "postdeploy"):
        assert f"runtime_readiness.py {stage}" in scripts
    assert scripts.index(" prepare ") < scripts.index(" predeploy ") < scripts.index(" deploy ") < scripts.index(" postdeploy ")
    for skip in ("hashFiles(", "continue-on-error", "|| true", "--accept-stale-safe-check"):
        assert skip not in scripts


def test_workflow_required_inputs_script_really_refuses_no_probe_opt_in(tmp_path):
    jobs = workflow()["jobs"]
    assert "readiness-proof" in jobs, "Missing live job"
    step = next(s for s in jobs["readiness-proof"]["steps"] if s.get("id") == "governance-inputs")
    (tmp_path / "python").symlink_to(sys.executable)
    env = {**os.environ, "GOVERNANCE_SAFE_PROBE": "false",
           "GOVERNANCE_CI_CONFIG": "", "GITHUB_WORKSPACE": str(ROOT),
           "GOV_PROJECT": str(tmp_path / "pilot"),
           "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(["bash", "-eu", "-c", step["run"]], cwd=ROOT,
                            env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "explicit preproduction safe-probe opt-in required" in result.stderr


@pytest.mark.parametrize("value", ["", "{}", '{"environment":"production"}'])
def test_inputs_fail_descriptively_without_protected_configuration(value, tmp_path):
    mod = helper()
    config = tmp_path / "config.json"
    config.write_text(value)
    with pytest.raises(ValueError, match="protected configuration"):
        mod.load_inputs(config)


def test_source_configuration_requires_concrete_files_not_an_acceptance_boolean(tmp_path):
    mod = helper()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"safe": True, "approved": True, "environment": "preproduction"}))
    with pytest.raises(ValueError, match="protected configuration"):
        mod.load_inputs(config)


def test_postdeploy_uses_current_manifest_and_refuses_legacy_green(tmp_path):
    mod = helper()
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/governance-manifest.json").write_text(json.dumps({
        "schema": "threadlight-govern-manifest/v2", "verdict": "governed", "gaps": [],
    }))
    with pytest.raises(ValueError):
        mod.validate_collected(tmp_path, {
            "tenant": "tenant", "subscription": "subscription", "resource_group": "rg",
        }, "2026-09-06T00:00:00Z")


def test_generated_commands_use_actual_shared_apis():
    mod = helper()
    text = SCRIPT.read_text()
    for term in ("build_bundle(", "verify_bundle(", "validate_native_manifest(",
                 '"generate"', '"agent-image"', '"bind"',
                 '"--phase", "pre-deploy", "--emit", "--gate"',
                 '"azd", "provision"', '"azd", "deploy"',
                 '"--phase", "post-deploy"', '"--subscription"',
                 "validate_governance_manifest(", "assess(",
                 "started_at", "governance-live.json"):
        assert term in text, term
    assert callable(mod.validate_collected)


def test_missing_current_deployment_attempt_is_not_reusable(tmp_path):
    mod = helper()
    with pytest.raises(ValueError, match="deployment attempt"):
        mod.attempt_start(tmp_path)


def test_safe_check_result_is_published_without_a_second_host_invocation(tmp_path):
    mod = helper()
    assert hasattr(mod, "publish_postdeploy"), "safe-check returns collection inside postdeploy-manifest.json"
    from skills._shared.tests.governance_consumer_fixtures import live_fixture
    manifest, *_ = live_fixture()
    # Local protocol fixture only: validation/publication is not hosted proof.
    report = {"governance_manifest": manifest, "governance_gaps": [], "gaps": ["existing resource gap"]}
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/postdeploy-manifest.json").write_text(json.dumps(report))
    mod.publish_postdeploy(tmp_path)
    assert json.loads((tmp_path / ".threadlight/governance-live.json").read_text()) == report
    assert json.loads((tmp_path / "specs/governance-manifest.json").read_text()) == manifest


def test_repeated_collection_cannot_borrow_an_old_report(tmp_path):
    mod = helper()
    assert hasattr(mod, "publish_postdeploy")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/postdeploy-manifest.json").write_text('{"gaps":[]}')
    with pytest.raises(ValueError, match="collector"):
        mod.publish_postdeploy(tmp_path)


def test_predeploy_is_an_executed_gated_command_not_report_only(tmp_path, monkeypatch):
    mod = helper()
    commands = []
    monkeypatch.setattr(mod, "run", lambda args, **kwargs: commands.append(list(map(str, args))))
    mod.predeploy(tmp_path, {})
    assert commands[-1][-6:] == ["--target", str(tmp_path), "--phase", "pre-deploy", "--emit", "--gate"]
    assert any("run-governance-pin-tests.py" in c[1] for c in commands)
    assert any("--prepare-local" in c for c in commands)


def test_parent_scope_mismatch_prevents_provisioning(tmp_path, monkeypatch):
    mod = helper()
    calls = []
    def wrong_parent(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '{"id":"wrong","tenantId":"wrong"}')
    monkeypatch.setattr(mod.subprocess, "run", wrong_parent)
    with pytest.raises(ValueError, match="observed Azure parent"):
        mod.deploy(tmp_path, {"expected_target": {"subscription": "expected", "tenant": "expected"}})
    assert len(calls) == 1


def test_each_workflow_rerun_requires_its_own_completed_attempt(tmp_path, monkeypatch):
    mod = helper()
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    mod.write(tmp_path / mod.ATTEMPT, {
        "run_id": "100", "run_attempt": "1", "completed_at": "2026-09-06T00:00:00Z",
    })
    with pytest.raises(ValueError, match="deployment attempt"):
        mod.attempt_start(tmp_path)


def test_official_probe_opt_in_is_not_replaced_by_an_invented_shape():
    text = SCRIPT.read_text()
    assert '"/mnt/governance-probe/config.json"' in text


def input_fixture(tmp_path):
    """Test-only configuration: never presented as signed/live deployment evidence."""
    from skills._shared.tests.governance_consumer_fixtures import contract
    source = tmp_path / "source"
    (source / "specs").mkdir(parents=True)
    (source / "specs/governance-contract.json").write_text(json.dumps(contract(selected=True)))
    (tmp_path / "policy").mkdir()
    (tmp_path / "envelope.json").write_text("{}")
    (tmp_path / "fixture.json").write_text("{}")
    target = {"tenant": "11111111-1111-4111-8111-111111111111",
              "subscription": "22222222-2222-4222-8222-222222222222", "resource_group": "rg-test"}
    import hashlib
    config = {
        "schema": "threadlight-readiness-input/v1", "environment": "preproduction",
        "expected_target": target, "source_project": "source", "policy_source": "policy",
        "package": {"tenant_id": target["tenant"], "environment": "preproduction",
                    "signed_envelope": "envelope.json", "probe_observability": {
                        "enabled": True, "configuration_file": "/mnt/governance-probe/config.json"}},
        "agent_image": {"agent_image": "registry.azurecr.io/agent@sha256:" + "a" * 64},
        "deployment": {"infrastructure": {"environment": "preproduction"},
                       "images": {"agent": "test"}, "bindings": {"agent_version": "test"},
                       "observations": {"foundation": "test"}},
        "probe_input": {"selection": {"subscription": target["subscription"], "resource_group": target["resource_group"]}},
        "probe_files": {"fixtures/config.json": {"path": "fixture.json",
                         "sha256": hashlib.sha256(b"{}").hexdigest()}},
        "source_digests": {"source_project": "sha256:" + "a" * 64},
        "azd_environment": "test", "location": "test",
    }
    path = tmp_path / "protected.json"
    path.write_text(json.dumps(config))
    return path, config


@pytest.mark.parametrize("mutation", ["off", "bad-selected", "tenant", "subscription", "rg", "fixture", "escape"])
def test_selected_inputs_fail_closed_without_changing_scope(mutation, tmp_path):
    mod = helper()
    path, config = input_fixture(tmp_path)
    if mutation in {"off", "bad-selected"}:
        from skills._shared.tests.governance_consumer_fixtures import contract
        document = contract() if mutation == "off" else {"governance": {"mode": "selective"}}
        (tmp_path / "source/specs/governance-contract.json").write_text(json.dumps(document))
    elif mutation == "tenant":
        config["package"]["tenant_id"] = "33333333-3333-4333-8333-333333333333"
    elif mutation in {"subscription", "rg"}:
        config["probe_input"]["selection"]["resource_group" if mutation == "rg" else mutation] = "wrong"
    elif mutation == "fixture":
        (tmp_path / "fixture.json").write_text('{"changed":true}')
    else:
        config["probe_files"]["../escape.json"] = config["probe_files"].pop("fixtures/config.json")
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="protected configuration"):
        mod.load_inputs(path)


def test_complete_input_transport_is_accepted_but_is_not_an_enforcement_claim(tmp_path):
    mod = helper()
    path, config = input_fixture(tmp_path)
    loaded = mod.load_inputs(path)
    assert loaded["expected_target"] == config["expected_target"]
    assert "live" not in loaded and "signature_verified" not in loaded


def test_workflow_input_script_rejects_modified_protected_bytes(tmp_path):
    path, _ = input_fixture(tmp_path)
    step = next(s for s in workflow()["jobs"]["readiness-proof"]["steps"] if s.get("id") == "governance-inputs")
    (tmp_path / "python").symlink_to(sys.executable)
    env = {**os.environ, "GOVERNANCE_SAFE_PROBE": "true", "GOVERNANCE_CI_CONFIG": str(path),
           "GOVERNANCE_CI_CONFIG_SHA256": "0" * 64, "GOV_PROJECT": str(tmp_path / "pilot"),
           "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(["bash", "-eu", "-c", step["run"]], cwd=ROOT, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "SHA256 required/mismatched" in result.stderr


def test_deploy_uses_actual_generated_agent_path_and_completes_only_after_azd(tmp_path, monkeypatch):
    mod = helper()
    generator = importlib.import_module("skills.threadlight-deploy.references.governance.generate")
    agent = tmp_path / "src/custom-agent"
    for directory in (agent, tmp_path / "src/govern-control-plane", tmp_path / "src/govern-gateway"):
        directory.mkdir(parents=True)
        (directory / "entry.py").write_text("# reviewed source\n")
    monkeypatch.setattr(generator, "frozen_configuration", lambda *args: (agent, {}))
    mod.write(tmp_path / ".threadlight/governance-package.json", {"configuration": {}})
    config = {"expected_target": {"tenant": "test", "subscription": "test", "resource_group": "rg-test"},
              "agent_image": {}, "deployment": {}, "azd_environment": "test", "location": "test",
              "package": {"agent_service": "actual-agent"},
              "source_digests": {name: generator.tree_digest(directory) for name, directory in (
                  ("agent", agent), ("govern-control-plane", tmp_path / "src/govern-control-plane"),
                  ("govern-gateway", tmp_path / "src/govern-gateway"))}}
    commands = []
    monkeypatch.setattr(mod, "account_matches", lambda *args: None)
    monkeypatch.setattr(mod, "generate", lambda stage, *args: commands.append([stage]))
    monkeypatch.setattr(mod, "run", lambda args, **kwargs: commands.append(list(map(str, args))))
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    mod.deploy(tmp_path, config)
    assert commands[:2] == [["agent-image"], ["bind"]]
    assert commands[-2:] == [["azd", "provision", "--no-prompt"],
                            ["azd", "deploy", "actual-agent", "--no-prompt"]]
    assert mod.attempt_start(tmp_path)


@pytest.mark.parametrize("mutation", ["none", "business", "prior-deployment", "signed-envelope", "failed-parent"])
def test_current_shared_readiness_consumer_is_used_not_just_schema(mutation, tmp_path, monkeypatch):
    """Real v1 validator/consumer with local wire fixtures and no remote transports."""
    from copy import deepcopy
    from datetime import timedelta
    from skills._shared.tests.governance_consumer_fixtures import live_fixture
    import skills._shared.governance_readiness as readiness
    mod = helper()
    value, document, current, now = live_fixture()
    target = {k: current["expected_target"][k] for k in ("tenant", "subscription", "resource_group")}
    after = now - timedelta(seconds=5)
    if mutation == "business":
        document["tools"][0]["id"] = "returns_apply_decision"
    elif mutation == "prior-deployment":
        after = now + timedelta(seconds=5)
    elif mutation == "signed-envelope":
        current = deepcopy(current)
        current["policy_bindings"]["policy"]["signature"] = "changed"
    mod.write(tmp_path / "specs/governance-contract.json", document)
    mod.write(tmp_path / "specs/governance-manifest.json", value)
    mod.write(tmp_path / ".threadlight/governance-live.json", {
        "governance_manifest": value, "governance_gaps": ["failure"] if mutation == "failed-parent" else [],
    })
    monkeypatch.setattr(readiness, "current_context", lambda *args: current)
    if mutation == "none":
        mod.validate_collected(tmp_path, target, after.isoformat())
    else:
        with pytest.raises(ValueError):
            mod.validate_collected(tmp_path, target, after.isoformat())
