"""Task14 quality regressions. All inputs and effects are local, not Azure proof."""
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.ci import runtime_readiness


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "skills/threadlight-deploy/references/governance"


def generator():
    return importlib.import_module("skills.threadlight-deploy.references.governance.generate")


def snapshot(project):
    return {p.relative_to(project).as_posix(): (
        p.stat().st_mode, p.read_bytes() if p.is_file() else None)
        for p in project.rglob("*")}


def export_project(project):
    (project / ".github/workflows").mkdir(parents=True)
    (project / ".github/workflows/operator.yml").write_text("name: preserve operator CI\n")
    (project / ".gitignore").write_text("operator-private/\n")
    (project / "governance").mkdir()
    return project


def test_local_validation_export_preserves_unrelated_workflow(tmp_path):
    project = export_project(tmp_path / "pilot")
    operator = project / ".github/workflows/operator.yml"
    before = operator.read_bytes()
    generator().export_local_validation(project)
    assert operator.read_bytes() == before
    assert (project / ".github/workflows/native-local.yml").read_bytes() == (
        REFERENCE / "native-local.yml").read_bytes()
    assert (project / ".governance-tools/scripts/ci/run-governance-pin-tests.py").is_file()


@pytest.mark.parametrize("relative,original", [
    (".github/workflows/native-local.yml", REFERENCE / "native-local.yml"),
    (".github/CODEOWNERS", ROOT / ".github/CODEOWNERS"),
])
def test_local_validation_export_accepts_identical_owned_file(relative, original, tmp_path):
    project = export_project(tmp_path / "pilot")
    destination = project / relative
    shutil.copyfile(original, destination)
    generator().export_local_validation(project)
    assert destination.read_bytes() == original.read_bytes()


@pytest.mark.parametrize("relative", [".github/workflows/native-local.yml", ".github/CODEOWNERS"])
def test_local_validation_export_collision_is_descriptive_and_transactional(relative, tmp_path):
    project = export_project(tmp_path / "pilot")
    (project / relative).write_text("operator-owned content: never overwrite\n")
    before = snapshot(project)
    with pytest.raises(ValueError, match="local_validation_export_conflict:" + relative):
        generator().export_local_validation(project)
    assert snapshot(project) == before


def test_local_validation_export_preparation_failure_leaves_no_partial_tree(tmp_path, monkeypatch):
    project = export_project(tmp_path / "pilot")
    before = snapshot(project)
    gen = generator()
    copyfile = gen.shutil.copyfile

    def fail(source, destination, *args, **kwargs):
        if str(destination).endswith("scripts/ci/governance_ctk.py"):
            raise OSError("local injected copy failure")
        return copyfile(source, destination, *args, **kwargs)

    monkeypatch.setattr(gen.shutil, "copyfile", fail)
    with pytest.raises(OSError, match="local injected copy failure"):
        gen.export_local_validation(project)
    assert snapshot(project) == before


def test_local_validation_export_publish_failure_rolls_back(tmp_path, monkeypatch):
    project = export_project(tmp_path / "pilot")
    before = snapshot(project)
    replace = Path.replace

    def fail(path, target):
        if Path(target) == project / ".governance-tools/source-manifest.json":
            raise OSError("local injected publication failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="local injected publication failure"):
        generator().export_local_validation(project)
    assert snapshot(project) == before


def test_prepare_export_collision_does_not_publish_a_partial_project(tmp_path):
    source = export_project(tmp_path / "source")
    (source / ".github/workflows/native-local.yml").write_text("operator-owned workflow\n")
    policy = tmp_path / "policy"
    policy.mkdir()
    before = snapshot(source)
    project = tmp_path / "prepared"
    config = {"source_project": source, "policy_source": policy, "probe_files": {},
              "source_digests": {"source_project": generator().tree_digest(source),
                                 "policy_source": generator().tree_digest(policy)}}
    with pytest.raises(ValueError, match="local_validation_export_conflict"):
        runtime_readiness.prepare(project, config)
    assert snapshot(source) == before
    assert not project.exists()


@pytest.mark.parametrize("framework", ["microsoft-agent-framework", "github-copilot-sdk"])
def test_unsupported_lifecycle_refuses_before_cloud_or_project_mutation(framework, tmp_path, monkeypatch):
    project = export_project(tmp_path / "pilot")
    before = snapshot(project)
    calls = []

    def unexpected(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unsupported lifecycle reached a command")

    monkeypatch.setattr(runtime_readiness, "account_matches", unexpected)
    monkeypatch.setattr(runtime_readiness, "run", unexpected)
    monkeypatch.setattr(runtime_readiness, "generate", unexpected)
    config = {"expected_target": {"tenant": "test", "subscription": "test", "resource_group": "test"},
              "deployment": {"infrastructure": {"runtime": framework}}}
    with pytest.raises(ValueError, match="NEEDS_CONTEXT.*immutable.*v1"):
        runtime_readiness.deploy(project, config)
    assert calls == []
    assert snapshot(project) == before


def test_workflow_validation_refuses_unsupported_lifecycle_before_login(tmp_path):
    from test_runtime_readiness import input_fixture
    import hashlib

    path, _ = input_fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/runtime_readiness.py"), "validate-inputs",
         "--configuration", str(path), "--project", str(tmp_path / "prepared")],
        capture_output=True, text=True,
        env={**os.environ, "GOVERNANCE_SAFE_PROBE": "true",
             "GOVERNANCE_CI_CONFIG_SHA256": hashlib.sha256(path.read_bytes()).hexdigest()})
    assert result.returncode == 1
    assert "NEEDS_CONTEXT" in result.stderr
    assert "completed" not in result.stdout
    assert not (tmp_path / "prepared").exists()


def test_readiness_documents_actual_api_blocker_instead_of_version_chasing():
    text = (ROOT / "docs/production-readiness.md").read_text()
    section = text[text.index("### Protected readiness-proof CI inputs"):text.index("Artifacts are")]
    assert "NEEDS_CONTEXT" in section
    assert "no separate start" in section
    assert "draft" in section and "new version" in section
    assert "ContainerConfiguration" in section and "mount" in section
    assert "AZURE_AI_PROJECT_ID" in section and "FOUNDRY_PROJECT_ENDPOINT" in section
    assert "A changed agent version during deployment requires new" not in section


def test_native_ci_executes_installed_sdk_characterization():
    from test_runtime_readiness import workflow

    step = next(s for s in workflow()["jobs"]["local-native-contract"]["steps"]
                if s.get("id") == "prepared")
    assert step["env"].get("THREADLIGHT_READINESS_SDK") == "1"
    assert "tests/ci/test_readiness_foundry_contract.py" in step["run"]
    assert "pinned_" in step["run"]
