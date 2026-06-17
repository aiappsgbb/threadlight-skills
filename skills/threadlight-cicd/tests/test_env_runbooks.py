"""Tests for the env-setup runbooks + companion shell scripts."""
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


def test_env_setup_emits_runbooks_and_scripts():
    tmp = pathlib.Path(tempfile.mkdtemp())
    written = mod.generate(_framing(), out_root=tmp)
    rel = {str(p.relative_to(tmp)) for p in written}
    base = "docs/threadlight-cicd/env-setup/"
    for stem in ("01-uami-federated-credentials", "02-rbac-role-assignments", "03-runners-private-vnet"):
        assert base + stem + ".md" in rel, stem
        assert base + stem + ".sh" in rel, stem
    assert base + "README.md" in rel


def test_rbac_runbook_scopes_to_resource_group_least_privilege():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(), out_root=tmp)
    rbac = (tmp / "docs/threadlight-cicd/env-setup/02-rbac-role-assignments.md").read_text()
    # least privilege: role assignment scoped to the target RG, not the subscription
    assert "az role assignment create" in rbac
    assert "resourceGroups/rg-pilot-prod" in rbac
    assert "{{" not in rbac


def test_uami_runbook_creates_identity_and_federated_credential():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(), out_root=tmp)
    uami = (tmp / "docs/threadlight-cicd/env-setup/01-uami-federated-credentials.md").read_text()
    assert "az identity create" in uami
    assert "az identity federated-credential create" in uami


def test_runner_runbook_has_managed_and_self_hosted_options():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(private_network=True), out_root=tmp)
    runner = (tmp / "docs/threadlight-cicd/env-setup/03-runners-private-vnet.md").read_text()
    low = runner.lower()
    assert "self-hosted" in low
    # at least one managed private-runner option is documented
    assert ("managed devops pool" in low) or ("larger runner" in low) or ("private networking" in low)


def test_shell_scripts_have_safety_header():
    tmp = pathlib.Path(tempfile.mkdtemp())
    mod.generate(_framing(), out_root=tmp)
    sh = (tmp / "docs/threadlight-cicd/env-setup/01-uami-federated-credentials.sh").read_text()
    assert sh.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in sh
    assert "{{" not in sh
