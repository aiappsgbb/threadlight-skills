"""The default release path validates a distinct target before promotion."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verified_pipeline_generator", ROOT / "scripts/generate_pipeline.py")
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def framing(platform):
    return dict(platform=platform, repo_full_name="example/agent",
                target_subscription_id="subscription", target_resource_group="rg-prod",
                tenant_id="tenant", env_name="prod", azure_client_id="production-client",
                ado_service_connection="production-connection", validation_env_name="validation",
                validation_subscription_id="subscription", validation_resource_group="rg-validation",
                validation_client_id="validation-client", validation_service_connection="validation-connection")


@pytest.mark.parametrize("platform", generator.SUPPORTED_PLATFORMS)
def test_default_pipeline_has_a_real_preproduction_validation_boundary(tmp_path, platform):
    generator.generate(framing(platform), tmp_path)
    path = (".github/workflows/azd-deploy-prod.yml" if platform == "github-actions" else "azure-pipelines.yml")
    text = (tmp_path / path).read_text()
    parsed = yaml.safe_load(text)
    assert "release_runner.py" in text
    assert "preflight" in text
    assert "echo \"Run threadlight" not in text
    assert "--receipt-sha256" in text
    assert "azd deploy --no-prompt" not in text
    if platform == "github-actions":
        jobs = parsed["jobs"]
        assert jobs["validation"]["environment"] == "validation"
        assert jobs["promotion"]["environment"] == "prod"
        assert jobs["promotion"]["needs"] == "validation"
        assert "needs.validation.outputs.receipt_sha256" in text
    else:
        stages = {stage["stage"]: stage for stage in parsed["stages"]}
        assert stages["validation"]["jobs"][0]["environment"] == "validation"
        assert stages["promotion"]["jobs"][0]["environment"] == "prod"
        assert stages["promotion"]["dependsOn"] == "validation"
        assert "stageDependencies.validation" in text
    assert (tmp_path / ".threadlight/skills/threadlight-cicd/scripts/release_runner.py").is_file()
    assert (tmp_path / ".threadlight/skills/threadlight-production-ready/scripts/evidence_gate.py").is_file()
    assert (tmp_path / ".threadlight/skills/threadlight-production-ready/scripts/mcp_sbom.py").is_file()
    assert (tmp_path / "docs/threadlight-cicd/release-contract.md").is_file()


@pytest.mark.parametrize("field", ["eval_gate", "mcp_gate"])
@pytest.mark.parametrize("value", ["soft", "typo", None])
def test_verified_release_cannot_downgrade_required_domains(tmp_path, field, value):
    values = framing("github-actions")
    values[field] = value
    with pytest.raises(ValueError):
        generator.generate(values, tmp_path)
    assert not (tmp_path / ".github/workflows/azd-deploy-prod.yml").exists()


def test_target_scope_cannot_silently_reuse_production(tmp_path):
    values = framing("github-actions")
    values["validation_resource_group"] = "rg-prod"
    with pytest.raises(ValueError):
        generator.generate(values, tmp_path)


def test_incomplete_framing_produces_an_explicit_non_executable_policy_example(tmp_path):
    generator.generate(dict(platform="github-actions"), tmp_path)
    assert not (tmp_path / "specs/release-policy.json").exists()
    example = (tmp_path / "specs/release-policy.example.json").read_text()
    assert "REPLACE" in example
    assert "validation" in example and "production" in example
    data = json.loads(example)
    assert data["validation"]["environment"] == "validation"
    assert data["production"]["environment"] == "prod"


def test_production_federation_cannot_bypass_environment_approval(tmp_path):
    generator.generate(framing("github-actions"), tmp_path)
    script = (tmp_path / "docs/threadlight-cicd/env-setup/01-uami-federated-credentials.sh").read_text()
    assert "ref:refs/heads/main" not in script
    assert script.count("az identity federated-credential create") == 1


def test_validation_setup_has_its_own_identity_and_permission_scope(tmp_path):
    generator.generate(framing("github-actions"), tmp_path)
    directory = tmp_path / "docs/threadlight-cicd/validation-env-setup"
    identity = (directory / "01-uami-federated-credentials.sh").read_text()
    roles = (directory / "02-rbac-role-assignments.sh").read_text()
    assert "environment:validation" in identity
    assert "resourceGroups/rg-validation" in roles
    assert "resourceGroups/rg-prod" not in roles


def test_cli_preserves_reviewed_platform_and_environment_from_framing(tmp_path):
    path = tmp_path / "framing.json"
    path.write_text(json.dumps({"platform": "azure-devops", "env_name": "production"}))
    args = generator._parse_args(["--framing-file", str(path)])
    assert generator._framing_from_args(args)["platform"] == "azure-devops"
    assert generator._framing_from_args(args)["env_name"] == "production"
    override = generator._parse_args(["--framing-file", str(path), "--platform", "github-actions",
                                     "--env-name", "prod"])
    assert generator._framing_from_args(override)["platform"] == "github-actions"
    assert generator._framing_from_args(override)["env_name"] == "prod"


def test_markdown_prompt_changes_do_not_skip_release_validation(tmp_path):
    generator.generate(framing("github-actions"), tmp_path)
    text = (tmp_path / ".github/workflows/azd-deploy-prod.yml").read_text()
    assert "paths-ignore" not in text


def test_supplied_azd_setup_precedes_executable_preflight(tmp_path):
    generator.generate(framing("github-actions"), tmp_path)
    text = (tmp_path / ".github/workflows/azd-deploy-prod.yml").read_text()
    for job in ("validation", "promotion"):
        block = text.split(f"\n  {job}:\n")[1].split("\n  promotion:\n")[0]
        assert block.index("Azure/setup-azd") < block.index("release_runner.py preflight")


@pytest.mark.parametrize("platform", generator.SUPPORTED_PLATFORMS)
def test_generated_operator_handbook_has_no_dangling_local_links_without_native_opt_in(tmp_path, platform):
    import re
    generator.generate(framing(platform), tmp_path)
    contract = tmp_path / "docs/threadlight-cicd/release-contract.md"
    links = re.findall(r"\]\(([^)]+)\)", contract.read_text())
    assert links
    for link in links:
        if not link.startswith(("https://", "#")):
            assert (contract.parent / link.split("#")[0]).is_file(), link
    assert not (tmp_path / ".threadlight/skills/threadlight-cicd/scripts/agentops_runtime.py").exists()
