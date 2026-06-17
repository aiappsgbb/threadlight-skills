"""Tests for GitHub Actions artifact generation."""
import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "generate_pipeline", ROOT / "scripts" / "generate_pipeline.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["generate_pipeline"] = mod
_spec.loader.exec_module(mod)


def _gh_framing(**over):
    f = {
        "platform": "github-actions",
        "central_env_required": False,
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "target_location": "eastus2",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "repo_full_name": "aiappsgbb/contoso-pilot",
        "env_name": "prod",
    }
    f.update(over)
    return f


def test_github_emits_expected_files():
    tmp = pathlib.Path(tempfile.mkdtemp())
    written = mod.generate(_gh_framing(), out_root=tmp)
    rel = {str(p.relative_to(tmp)) for p in written}
    assert ".github/workflows/azd-deploy-prod.yml" in rel
    assert "docs/threadlight-cicd/onboarding-path.json" in rel
    assert "docs/threadlight-cicd/central-platform-boundary.md" in rel
    assert any("env-setup/01-uami-federated-credentials.md" in p for p in rel)
    assert any("env-setup/02-rbac-role-assignments.md" in p for p in rel)
    assert any("env-setup/03-runners-private-vnet.md" in p for p in rel)


def test_github_workflow_is_oidc_and_has_no_secrets():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_gh_framing(), out_root=tmp)
    wf = (tmp / ".github/workflows/azd-deploy-prod.yml").read_text()
    assert "id-token: write" in wf
    assert "azure/login@" in wf
    assert "azd" in wf
    # OIDC only — never a long-lived secret
    assert "AZURE_CREDENTIALS" not in wf
    assert "client-secret" not in wf
    assert "clientSecret" not in wf
    # all tokens substituted
    assert "{{" not in wf


def test_github_uami_runbook_uses_environment_federated_subject():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_gh_framing(), out_root=tmp)
    runbook = (tmp / "docs/threadlight-cicd/env-setup/01-uami-federated-credentials.md").read_text()
    # GitHub OIDC subject for the production environment
    assert "repo:aiappsgbb/contoso-pilot:" in runbook
    assert "token.actions.githubusercontent.com" in runbook
    assert "{{" not in runbook


def test_github_public_runner_default_and_private_self_hosted():
    tmp_pub = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_gh_framing(private_network=False), out_root=tmp_pub)
    wf_pub = (tmp_pub / ".github/workflows/azd-deploy-prod.yml").read_text()
    assert "ubuntu-latest" in wf_pub

    tmp_priv = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_gh_framing(private_network=True), out_root=tmp_priv)
    wf_priv = (tmp_priv / ".github/workflows/azd-deploy-prod.yml").read_text()
    assert "self-hosted" in wf_priv


def test_onboarding_path_json_is_valid_and_records_decision():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_gh_framing(central_env_required=True, central_env_exists=True), out_root=tmp)
    data = json.loads((tmp / "docs/threadlight-cicd/onboarding-path.json").read_text())
    assert data["path"] == "spoke-onboard"
    assert data["posture"] == "citadel-spoke"
    assert data["rbac_scope"] == "spoke-rg"
    assert "generator_version" in data
