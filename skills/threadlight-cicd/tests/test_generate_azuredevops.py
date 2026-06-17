"""Tests for Azure DevOps artifact generation."""
import importlib.util
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


def _ado_framing(**over):
    f = {
        "platform": "azure-devops",
        "central_env_required": False,
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "target_location": "eastus2",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "ado_org": "contoso",
        "ado_project": "AI-Pilots",
        "ado_service_connection": "sc-contoso-pilot-prod",
        "env_name": "prod",
    }
    f.update(over)
    return f


def test_ado_emits_pipeline_file():
    tmp = pathlib.Path(tempfile.mkdtemp())
    written = mod.generate(_ado_framing(), out_root=tmp)
    rel = {str(p.relative_to(tmp)) for p in written}
    assert "azure-pipelines.yml" in rel
    # env setup + boundary always emitted regardless of platform
    assert "docs/threadlight-cicd/central-platform-boundary.md" in rel


def test_ado_pipeline_uses_wif_service_connection_no_secrets():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_ado_framing(), out_root=tmp)
    pipe = (tmp / "azure-pipelines.yml").read_text()
    # references the workload-identity-federation service connection by name
    assert "sc-contoso-pilot-prod" in pipe
    assert "AzureCLI@2" in pipe
    # no secrets / PAT in the pipeline
    assert "clientSecret" not in pipe
    assert "AZURE_CREDENTIALS" not in pipe
    assert "{{" not in pipe


def test_ado_uami_runbook_uses_service_connection_subject():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_ado_framing(), out_root=tmp)
    runbook = (tmp / "docs/threadlight-cicd/env-setup/01-uami-federated-credentials.md").read_text()
    # Azure DevOps WIF federated subject identifier format
    assert "sc://contoso/AI-Pilots/sc-contoso-pilot-prod" in runbook
    assert "{{" not in runbook


def test_ado_private_network_references_pool():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_ado_framing(private_network=True), out_root=tmp)
    pipe = (tmp / "azure-pipelines.yml").read_text()
    # private deployments use a named pool (Managed DevOps Pool / self-hosted),
    # not the hosted vmImage
    assert "pool:" in pipe
    assert "name:" in pipe
