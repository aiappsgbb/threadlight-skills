"""AgentOps composes into the existing pipeline, and never runs by accident."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("generate_pipeline_agentops", ROOT / "scripts/generate_pipeline.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def framing(platform, **extra):
    return dict(
        platform=platform, repo_full_name="example/pilot",
        central_env_required=False, target_subscription_id="subscription",
        target_resource_group="rg-app", tenant_id="tenant",
        ado_service_connection="approved-app-connection", **extra,
    )


def render(root, platform, **extra):
    paths = mod.generate(framing(platform, **extra), root)
    relative = ".github/workflows/azd-deploy-prod.yml" if platform == "github-actions" else "azure-pipelines.yml"
    return (root / relative).read_text(), paths


@pytest.mark.parametrize("platform", mod.SUPPORTED_PLATFORMS)
def test_no_opt_in_has_no_agentops_workflow_change(tmp_path, platform):
    automatic, paths = render(tmp_path, platform)
    disabled, off_paths = render(tmp_path, platform, agentops="off")
    # Generation timestamps are not behavior.
    assert automatic.splitlines()[1:] == disabled.splitlines()[1:]
    assert paths == off_paths
    assert "agentops" not in automatic.lower()


@pytest.mark.parametrize("platform", mod.SUPPORTED_PLATFORMS)
def test_opt_in_composes_existing_pipeline_without_paid_doctor(tmp_path, platform):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: support:1\n")
    text, paths = render(tmp_path, platform)
    assert "agentops_runtime.py" in text
    assert "--run-eval" in text
    assert "--refresh-doctor" not in text
    assert "unset GITHUB_STEP_SUMMARY" in text
    assert "evals_check.py" in text
    assert "agentops workflow" not in text
    assert "baseline promote" not in text
    assert "evaluate.py" not in text
    assert 'path: .agentops' not in text
    assert not any("agentops" in str(p.relative_to(tmp_path)) and p.suffix in {".yml", ".yaml"} for p in paths)


def test_cli_agentops_mode_is_explicit():
    assert mod._parse_args(["--agentops", "off"]).agentops == "off"
    assert mod._framing_from_args(mod._parse_args([])).get("agentops", "auto") == "auto"


def test_unknown_agentops_mode_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="agentops"):
        render(tmp_path, "github-actions", agentops="enabled")


def test_new_output_directory_keeps_legacy_no_opt_in_generation(tmp_path):
    text, paths = render(tmp_path / "new-output", "github-actions")
    assert paths
    assert "agentops" not in text.lower()


def test_agentops_documents_authoritative_discovery_dependency():
    assert "PyYAML" in (ROOT / "SKILL.md").read_text()
    assert "PyYAML" in (ROOT / "references/agentops-runtime.md").read_text()


@pytest.mark.parametrize("platform", mod.SUPPORTED_PLATFORMS)
def test_pr_evaluation_never_deploys_and_uses_existing_context(tmp_path, platform):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: support:1\n")
    text, _ = render(tmp_path, platform, private_network=True)
    if platform == "github-actions":
        assert "pull_request:" in text
        assert "github.event_name != 'pull_request'" in text
        assert "github.event.pull_request.head.repo.full_name == github.repository" in text
        assert "self-hosted, threadlight-prod" in text
        assert "environment: prod" in text
    else:
        assert "pr:" in text
        assert "ne(variables['Build.Reason'], 'PullRequest')" in text
        assert "name: threadlight-prod-pool" in text
        assert "azureSubscription: approved-app-connection" in text


def test_opt_out_does_not_generate_tooling_even_when_opted_in(tmp_path):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: support:1\n")
    text, paths = render(tmp_path, "github-actions", agentops="off")
    assert "agentops" not in text.lower()
    assert not (tmp_path / ".threadlight").exists()


def test_generated_runtime_is_portable_and_cli_is_real(tmp_path):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: support:1\n")
    render(tmp_path, "github-actions")
    tool = tmp_path / ".threadlight/skills/threadlight-cicd/scripts/agentops_runtime.py"
    result = subprocess.run([sys.executable, str(tool), "--help"], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--run-eval" in result.stdout
    assert "--refresh-doctor" in result.stdout
    assert (tmp_path / ".threadlight/skills/threadlight-agentops/scripts/native_observer.py").is_file()
    assert not (tmp_path / ".threadlight/skills/threadlight-cicd/scripts/native_observer.py").exists()


def test_schedule_requires_explicit_doctor_opt_in(tmp_path):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: support:1\n")
    with pytest.raises(ValueError, match="doctor"):
        render(tmp_path, "github-actions", agentops_doctor_schedule="0 6 * * *")


@pytest.mark.parametrize("platform", mod.SUPPORTED_PLATFORMS)
def test_approved_doctor_schedule_is_not_a_deployment_schedule(tmp_path, platform):
    (tmp_path / "agentops.yaml").write_text("version: 1\nagent: support:1\n")
    text, _ = render(tmp_path, platform, agentops_refresh_doctor=True,
                     agentops_doctor_schedule="0 6 * * *")
    assert "--refresh-doctor" in text
    assert "0 6 * * *" in text
    assert "doctor" in text.lower()
    if platform == "github-actions":
        assert "github.event_name != 'schedule'" in text
    else:
        assert "ne(variables['Build.Reason'], 'Schedule')" in text


@pytest.mark.parametrize("platform", mod.SUPPORTED_PLATFORMS)
def test_composed_yaml_keeps_the_canonical_eval_gate_and_approvals(platform):
    values = framing(platform)
    ctx = mod.build_context(values, mod.resolve_onboarding_path(values))
    template = ("github-actions/azd-deploy-prod.yml.tmpl" if platform == "github-actions"
                else "azure-devops/azure-pipelines.yml.tmpl")
    text = mod._compose_agentops(mod._render_file(mod.REF / template, ctx),
                                platform, ctx, True, "0 6 * * *")
    parsed = yaml.safe_load(text)
    if platform == "github-actions":
        assert set(parsed["jobs"]) == {"deploy", "eval-gate", "red-team-gate", "mcp-supply-chain-gate"}
        quality = parsed["jobs"]["eval-gate"]
        assert quality["environment"] == "prod"
        assert quality["needs"] == "deploy"
        assert any("--run-eval" in step.get("run", "") for step in quality["steps"])
    else:
        stages = {stage["stage"]: stage for stage in parsed["stages"]}
        assert set(stages) == {"deploy", "eval_gate", "red_team_gate", "mcp_supply_chain_gate"}
        quality = stages["eval_gate"]["jobs"][0]
        assert quality["deployment"] == "quality_evals"
        assert quality["environment"] == "prod"
        steps = quality["strategy"]["runOnce"]["deploy"]["steps"]
        assert any("--run-eval" in step.get("inputs", {}).get("inlineScript", "") for step in steps)


@pytest.mark.parametrize("eval_exit,doctor_exit,refresh,expected,consumed", [
    (2, 0, False, 0, True),
    (1, 0, False, 1, False),
    (0, 2, True, 2, True),
])
def test_verified_negative_capture_reaches_consumer_before_gate(
        tmp_path, eval_exit, doctor_exit, refresh, expected, consumed):
    native = tmp_path / ".threadlight/skills/threadlight-cicd/scripts/agentops_runtime.py"
    native.parent.mkdir(parents=True)
    native.write_text(
        "import sys\n"
        f"raise SystemExit({eval_exit} if '--run-eval' in sys.argv else {doctor_exit})\n"
    )
    canonical = tmp_path / ".threadlight/skills/threadlight-evals/scripts/evals_check.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("from pathlib import Path\nPath('canonical-consumed').write_text('yes')\n")
    result = subprocess.run(
        ["bash", "-c", mod._agentops_command("github-actions", refresh)],
        cwd=tmp_path, env=dict(os.environ, GITHUB_EVENT_NAME="push"),
        capture_output=True, text=True,
    )
    assert result.returncode == expected, result.stderr
    assert (tmp_path / "canonical-consumed").exists() is consumed
