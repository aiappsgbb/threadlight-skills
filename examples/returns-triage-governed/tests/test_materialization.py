"""Offline packaging must not invent production configuration or live proof."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

EXAMPLE = Path(__file__).resolve().parents[1]
ROOT = EXAMPLE.parents[1]


@pytest.mark.governance_runtime
@pytest.mark.parametrize("probe_enabled", [False, True])
def test_materialized_actual_module_closure_and_fail_closed_startup(tmp_path, probe_enabled):
    script = EXAMPLE / "scripts/materialize.py"
    assert script.is_file(), "reproducible canonical source materializer missing"
    output = tmp_path / "portable"
    result = subprocess.run([sys.executable, str(script), "--output", str(output),
                             *(["--probe"] if probe_enabled else [])],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    agent = output / "src/agent"
    assert (agent / "runtime/trusted_context.py").read_bytes() == (
        ROOT / "skills/threadlight-govern/references/runtime/trusted_context.py").read_bytes()
    assert (agent / "governance_host.py").read_bytes() == (
        ROOT / "skills/threadlight-deploy/references/governance/maf-container.py").read_bytes()
    assert (agent / "sample-data/orders.json").read_bytes() == (EXAMPLE / "specs/sample-data/orders.json").read_bytes()
    report = json.loads((output / "source-package.json").read_text())
    assert report["binding_status"] == "unverified"
    assert report["signature"] == "not-configured"
    assert not (agent / "governance-config.json").exists()
    assert not (agent / "policy-envelope.json").exists()
    assert (agent / "vendor/gateway/probe_runtime.py").is_file() == probe_enabled
    contract = json.loads((output / "contract.json").read_text())
    assert any(t["id"] == "governance_probe_noop" for t in contract["tools"]) == probe_enabled
    assert not (output / "src/govern-probe-fixture").exists()
    assert not (output / "src/govern-gateway").exists()
    # -I drops catalog PYTHONPATH/cwd. Only the generated application is added.
    probe = subprocess.run([sys.executable, "-I", "-c",
        "import sys; from pathlib import Path; sys.path.insert(0, '.'); "
        "import governance_host, runtime, governance_application, govern_control_plane, skills._shared.governance; "
        "assert all(Path(m.__file__).resolve().is_relative_to(Path.cwd()) for m in "
        "(governance_host, runtime, governance_application, govern_control_plane, skills._shared.governance)); "
        "import asyncio; asyncio.run(governance_host.main())"],
        cwd=agent, capture_output=True, text=True)
    assert probe.returncode != 0
    assert "governance-config.json" in probe.stderr
    assert "diagnostic" not in (agent / "container.py").read_text()


def test_application_startup_requires_typed_existing_environment():
    path = EXAMPLE / "src/agent/deployment_config.py"
    assert path.exists(), "typed required deployment configuration missing"
    spec = importlib.util.spec_from_file_location("returns_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError):
        module.DeploymentConfiguration.model_validate({})
    assert module.DeploymentConfiguration.model_json_schema()["required"]


def test_all_declared_business_tools_have_real_source_implementations(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "skills/threadlight-governed-actions/scripts"))
    import inventory
    assert "returns_apply_decision" in inventory.discover_python_tools(EXAMPLE)


def test_current_manifest_is_offline_and_legacy_receipts_are_archived():
    from skills._shared.governance import validate_governance_manifest
    path = EXAMPLE / "specs/governance-manifest.json"
    assert path.exists(), "canonical offline manifest missing"
    manifest = validate_governance_manifest(json.loads(path.read_text()))
    assert manifest["live_probes"] == []
    assert manifest["agent"]["version"] is None and manifest["agent"]["image_digest"] is None
    assert manifest["policy_bundle"]["signature_verified"] is False
    bindings = {binding["tool_id"]: binding for binding in manifest["bindings"]}
    assert bindings["returns_apply_decision"]["status"] == "unverified"
    assert {b["status"] for key, b in bindings.items() if key != "returns_apply_decision"} == {"unbound"}
    assert not (EXAMPLE / "specs/govern-manifest.json").exists()
    assert not (EXAMPLE / "tests/postdeploy-manifest.json").exists()
    assert not (EXAMPLE / "policy.yaml").exists()
    assert (EXAMPLE / "archive/legacy/specs/govern-manifest.json").exists()
    import yaml
    definition = yaml.safe_load((EXAMPLE / "agent.yaml").read_text())
    assert json.loads((EXAMPLE / "specs/governance-contract.json").read_text()) == {
        key: definition[key] for key in ("framework", "governance", "tools")}
    source = EXAMPLE / "src/agent/governance/policy"
    bundled = EXAMPLE / "src/agent/governance/bundle"
    for file in source.iterdir():
        assert file.read_bytes() == (bundled / file.name).read_bytes(), "committed bundle drift"
    metadata = json.loads((bundled / "bundle-metadata.json").read_text())
    assert metadata["bundle_digest"] == manifest["policy_bundle"]["digest"]


@pytest.mark.governance_runtime
@pytest.mark.parametrize("fault", ["valid", "direct-model", "wrong-uami", "wrong-partition"])
def test_production_initializer_uses_existing_cosmos_and_citadel(monkeypatch, fault):
    import asyncio
    from contextlib import AsyncExitStack
    import governance_application
    import azure.cosmos.aio
    assert callable(getattr(governance_application, "initialize", None)), "production initializer missing"
    config = {
        "environment": "preproduction", "tenant_id": "11111111-1111-1111-1111-111111111111",
        "agent_client_id": "22222222-2222-2222-2222-222222222222", "agent_id": "returns-triage",
        "control_plane_url": "https://control.example",
        "control_plane_scope": "api://33333333-3333-3333-3333-333333333333/.default",
        "key_id": "https://example.vault.azure.net/keys/policy/" + "a"*32,
        "approver_roles": ["returns-supervisor"], "cosmos_url": "https://example.documents.azure.com",
        "cosmos_database": "returns", "cosmos_container": "cases",
        "citadel_project_endpoint": "https://apim-citadel-hub.azure-api.net/api/projects/returns",
        "signed_envelope": "policy-envelope.json", "policy_id": "returns-write-v1", "policy_version": "1",
        "policy_digest": "sha256:" + "a"*64,
    }
    monkeypatch.setenv("AZURE_CLIENT_ID", config["agent_client_id"] if fault != "wrong-uami" else "wrong")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", config["citadel_project_endpoint"]
                       if fault != "direct-model" else "https://direct.services.ai.azure.com/api/projects/returns")
    monkeypatch.setattr(governance_application, "_application", None)
    seen = []
    credential = object()
    class Cosmos:
        def __init__(self, url, *, credential):
            seen.append((url, credential))
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def get_database_client(self, name):
            assert name == "returns"
            return self
        def get_container_client(self, name):
            assert name == "cases"
            return self
        async def read(self):
            return {"partitionKey": {"paths": ["/case_id" if fault != "wrong-partition" else "/id"]}}
    monkeypatch.setattr(azure.cosmos.aio, "CosmosClient", Cosmos)
    monkeypatch.setattr(governance_application, "SAMPLES", EXAMPLE / "specs/sample-data")
    async def scenario():
        async with AsyncExitStack() as stack:
            if fault != "valid":
                with pytest.raises(ValueError):
                    await governance_application.initialize(config, credential, stack)
            else:
                await governance_application.initialize(config, credential, stack)
                assert len(governance_application.tools) == 5
                assert callable(governance_application.trusted_context)
                assert governance_application.require_ready is True
                assert seen == [(config["cosmos_url"], credential)]
    asyncio.run(scenario())
    template = ROOT / "skills/threadlight-deploy/references/governance/maf-container.py"
    assert "await application.initialize(config, credential, stack)" in template.read_text()
    assert 'getattr(application, "require_ready", False)' in template.read_text()


@pytest.mark.governance_runtime
def test_only_the_configured_uami_can_request_business_and_audit_tokens(monkeypatch):
    import governance_application
    import azure.identity.aio
    assert callable(getattr(governance_application, "credential_factory", None)), "UAMI-only factory missing"
    observed = []
    monkeypatch.setenv("AZURE_CLIENT_ID", "22222222-2222-2222-2222-222222222222")
    monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", lambda **kw: observed.append(kw))
    governance_application.credential_factory()
    assert observed[0]["managed_identity_client_id"] == "22222222-2222-2222-2222-222222222222"
    for mechanism in ("environment", "workload_identity", "cli", "powershell", "developer_cli",
                      "interactive_browser", "shared_token_cache", "visual_studio_code", "broker"):
        assert observed[0][f"exclude_{mechanism}_credential"] is True
    source = (ROOT / "skills/threadlight-deploy/references/governance/maf-container.py").read_text()
    assert 'getattr(application, "credential_factory", DefaultAzureCredential)' in source
    assert 'credential_factory=getattr(application, "credential_factory", workload_credential)' in source
