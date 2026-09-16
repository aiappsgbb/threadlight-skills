"""Focused contract tests for the separately authenticated returns backend."""
import importlib.util
from pathlib import Path
import sys

import pytest


PATH = Path(__file__).resolve().parents[1] / "references/governance/returns_mcp_backend.py"


@pytest.fixture
def backend():
    spec = importlib.util.spec_from_file_location("returns_mcp_backend", PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def case():
    return {
        "id": "RMA-ALLOW", "case_id": "RMA-ALLOW", "kind": "case",
        "_etag": '"revision-1"', "status": "in_triage",
        "amount": 40, "eligible": True, "high_risk": False,
    }


def arguments():
    return {
        "case_id": "RMA-ALLOW", "expected_etag": '"revision-1"',
        "decision": "approve_refund", "reason": "Eligible return; recommendation only.",
    }


def test_atomic_case_and_audit_preserve_revision(backend):
    original = case()
    operations, result = backend.decision_batch(
        original, arguments(), operation_id="operation-1",
        provenance={"receipt_id": "receipt-1", "action_hash": "sha256:" + "a" * 64},
    )
    assert original == case()
    assert [op[0] for op in operations] == ["replace", "create"]
    assert operations[0][2] == {"if_match_etag": '"revision-1"'}
    assert operations[0][1][1]["status"] == "closed"
    assert operations[1][1][0]["case_id"] == "RMA-ALLOW"
    assert operations[1][1][0]["kind"] == "decision-audit"
    assert operations[1][1][0]["provenance"]["receipt_id"] == "receipt-1"
    assert result == {
        "case_id": "RMA-ALLOW", "decision": "approve_refund", "audit_id": "operation-1",
    }


@pytest.mark.parametrize("change", [
    {"_etag": '"changed"'}, {"id": "FOREIGN"}, {"case_id": "FOREIGN"},
    {"status": "closed"}, {"amount": 1200}, {"eligible": False}, {"high_risk": True},
])
def test_backend_rejects_changed_or_ineligible_case(backend, change):
    current = {**case(), **change}
    with pytest.raises(backend.BusinessConflict):
        backend.decision_batch(current, arguments(), operation_id="operation-1", provenance={})


def test_high_value_is_supervisor_handoff_not_refund(backend):
    operations, result = backend.decision_batch(
        {**case(), "amount": 1200},
        {**arguments(), "decision": "escalate_to_supervisor"},
        operation_id="operation-1", provenance={},
    )
    assert operations[0][1][1]["status"] == "escalated"
    assert result["decision"] == "escalate_to_supervisor"


def test_schema_rejects_settlement_or_model_authority(backend):
    from pydantic import ValidationError
    for changes in ({"decision": "settle_payment"}, {"approved": True}, {"amount": 1}):
        with pytest.raises(ValidationError):
            backend.Decision.model_validate({**arguments(), **changes})


def test_agent_uses_selected_gateway_and_unbound_case_read():
    path = PATH.with_name("returns_mcp_agent.py")
    spec = importlib.util.spec_from_file_location("returns_mcp_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = module.contract()
    from skills._shared.governance import validate_governance_contract
    validated = validate_governance_contract(
        document, deployment_target="customer-pilot", runtime="microsoft-agent-framework")
    tools = {item["id"]: item for item in validated["tools"]}
    assert set(tools) == {"returns_get_case", "returns_apply_decision"}
    assert tools["returns_get_case"]["policy_binding"] is None
    assert tools["returns_apply_decision"]["enforcement_path"] == "governed-tool-gateway"
    assert "settlement" in module.INSTRUCTIONS.lower()


def test_native_responses_client_uses_azure_v1_endpoint():
    import asyncio
    from azure.identity.aio import ManagedIdentityCredential
    path = PATH.with_name("returns_mcp_agent.py")
    spec = importlib.util.spec_from_file_location("returns_mcp_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def check():
        async with ManagedIdentityCredential() as credential:
            async with module.model_client(
                {"model_endpoint": "https://example.openai.azure.com"}, credential
            ) as client:
                assert str(client.base_url) == "https://example.openai.azure.com/openai/v1/"
                assert client.max_retries == 0
    asyncio.run(check())


@pytest.mark.governance_runtime
def test_native_server_uses_explicit_operator_state_directory(tmp_path, monkeypatch):
    import os
    from azure.ai.agentserver.core import resolve_state_subdir
    path = PATH.with_name("returns_mcp_agent.py")
    spec = importlib.util.spec_from_file_location("returns_mcp_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("AGENTSERVER_STATE_ROOT", raising=False)
    state = tmp_path / "responses-state"
    module.configure_state({"agentserver_state_root": str(state)})
    assert state.is_dir()
    assert os.environ["AGENTSERVER_STATE_ROOT"] == str(state)
    assert resolve_state_subdir("responses") == state / "responses"


def test_demo_package_copies_real_sources_and_refuses_overwrite(tmp_path):
    path = PATH.with_name("package_returns_mcp.py")
    spec = importlib.util.spec_from_file_location("package_returns_mcp", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "package"
    module.materialize(output)
    relative = "skills/threadlight-deploy/references/governance/returns_mcp_backend.py"
    assert (output / relative).read_bytes() == PATH.read_bytes()
    assert (output / "skills/threadlight-govern/references/gateway/dispatcher.py").is_file()
    assert (output / "examples/returns-triage-governed/src/agent/cosmos_effect.py").is_file()
    assert "<<'PY'" not in (output / "Dockerfile").read_text()
    assert (output / "source-package.json").is_file()
    with pytest.raises(FileExistsError):
        module.materialize(output)
    guide = PATH.with_name("returns-mcp-demo.md").read_text()
    assert "not Foundry hosted" in guide
    assert "Do not delete" in guide
    skill = PATH.parents[2] / "SKILL.md"
    assert "returns-mcp-demo.md" in skill.read_text()


@pytest.mark.governance_runtime
def test_unbound_read_uses_injected_host_credential_without_policy():
    import asyncio
    from contextlib import AsyncExitStack
    from azure.core.credentials import AccessToken
    import httpx
    path = PATH.with_name("returns_mcp_agent.py")
    spec = importlib.util.spec_from_file_location("returns_mcp_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scopes, requests = [], []

    class Credential:
        async def get_token(self, scope):
            scopes.append(scope)
            return AccessToken("test-only-token", 4102444800)

    def receive(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "RMA-READ", "_etag": '"revision-1"'})

    async def check():
        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(httpx.AsyncClient(transport=httpx.MockTransport(receive)))
            tools = await module.build_read_tools({
                "cases": ["RMA-READ"], "business_scope": "api://business/.default",
                "business_url": "https://business.example",
            }, Credential(), stack, http=http)
            assert [tool.name for tool in tools] == ["returns_get_case"]
            result = await tools[0].invoke(arguments={"case_id": "RMA-READ"}, skip_parsing=True)
            assert result == {"id": "RMA-READ", "_etag": '"revision-1"'}
    asyncio.run(check())
    assert scopes == ["api://business/.default"]
    assert len(requests) == 1 and str(requests[0].url) == "https://business.example/cases/RMA-READ"
    assert requests[0].headers["authorization"] == "Bearer test-only-token"
