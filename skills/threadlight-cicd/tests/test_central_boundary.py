"""Tests for the central-platform boundary artifact (the must-tell)."""
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


def _framing(**over):
    f = {
        "platform": "github-actions",
        "central_env_required": True,
        "central_env_exists": True,
        "target_subscription_id": "11111111-1111-1111-1111-111111111111",
        "target_resource_group": "rg-pilot-prod",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "repo_full_name": "aiappsgbb/contoso-pilot",
        "env_name": "prod",
    }
    f.update(over)
    return f


def test_boundary_doc_names_the_central_track_and_forbids_hub_writes():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(), out_root=tmp)
    doc = (tmp / "docs/threadlight-cicd/central-platform-boundary.md").read_text()
    low = doc.lower()
    # references the central-platform deployment track explicitly
    assert "citadel-hub-deploy" in low
    # states the pilot pipeline must not deploy/modify the hub
    assert "hub" in low
    assert "must not" in low or "never" in low
    # separate repo / separate pipeline language
    assert "separate" in low
    assert "{{" not in doc


def test_boundary_doc_emitted_even_for_standalone():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(central_env_required=False, central_env_exists=None), out_root=tmp)
    doc = tmp / "docs/threadlight-cicd/central-platform-boundary.md"
    assert doc.exists()
    assert "standalone" in doc.read_text().lower()


def test_boundary_doc_spoke_onboard_forbids_running_hub_deploy():
    # hub already exists -> onboard as a spoke; the doc must explicitly say
    # NOT to run citadel-hub-deploy (the apply-test caught this contradiction).
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(central_env_required=True, central_env_exists=True), out_root=tmp)
    low = (tmp / "docs/threadlight-cicd/central-platform-boundary.md").read_text().lower()
    assert "citadel-spoke-onboarding" in low
    assert "already exists" in low
    assert "do not run citadel-hub-deploy" in low


def test_boundary_doc_hub_missing_instructs_central_track():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(central_env_required=True, central_env_exists=False), out_root=tmp)
    low = (tmp / "docs/threadlight-cicd/central-platform-boundary.md").read_text().lower()
    assert "citadel-hub-deploy" in low
    assert "not yet" in low


def test_spoke_boundary_surfaces_hub_coordinates_and_access_contract():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(
        central_env_required=True, central_env_exists=True,
        hub_subscription_id="hub-sub-1234",
        hub_apim_resource_id="/subscriptions/h/resourceGroups/rg-hub/providers/Microsoft.ApiManagement/service/apim-hub",
        access_contract_product="unified-ai",
    ), out_root=tmp)
    doc = (tmp / "docs/threadlight-cicd/central-platform-boundary.md").read_text()
    assert "unified-ai" in doc
    assert "apim-hub" in doc
    assert "hub-sub-1234" in doc
