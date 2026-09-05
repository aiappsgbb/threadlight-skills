"""Business tools consumed by the shared Task10 native hosted-agent factory."""
import os
from pathlib import Path
from typing import Literal

from agent_framework import tool
from pydantic import BaseModel, ConfigDict, Field


class DecisionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rma_id: str = Field(pattern=r"^RMA-\d{4}-\d{6}$")
    decision: Literal["approve_refund", "deny_refund", "escalate_to_supervisor", "request_more_info"]
    disposition: Literal["restock_a", "restock_b", "liquidation", "return_to_customer", "destroy"] | None
    citations: list[str] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=2000)


class ReturnsApplication:
    def __init__(self, backend):
        self.backend = backend
        self.middleware = []
        self.trusted_context = backend.trusted_context

        @tool(approval_mode="never_require")
        async def returns_get_case(rma_id: str) -> dict:
            """Read a return case by RMA id."""
            return await backend.get_case(rma_id)

        @tool(approval_mode="never_require")
        def oms_get_order(order_id: str) -> dict:
            """Read a mock OMS order."""
            return backend.get_order(order_id)

        @tool(approval_mode="never_require")
        def customer_get_profile(customer_id: str) -> dict:
            """Read a mock customer profile."""
            return backend.get_customer(customer_id)

        @tool(approval_mode="never_require")
        async def returns_list_open(offset: int = 0, limit: int = 20) -> list[dict]:
            """List open returns; read a selected case before triaging it."""
            return await backend.list_open(offset, limit)

        @tool(name="returns_apply_decision", schema=DecisionArguments, approval_mode="never_require",
              description="Record a cited recommendation and audit, never settle payment.")
        async def returns_apply_decision(rma_id, decision, disposition, citations, rationale):
            return await backend.apply_decision(
                rma_id=rma_id, decision=decision, disposition=disposition,
                citations=citations, rationale=rationale)

        self.tools = [oms_get_order, returns_get_case, returns_list_open, customer_get_profile,
                      returns_apply_decision]

    @staticmethod
    def safe_evidence(identity):
        return {"source": "host-owned-backend-context"}


SAMPLES = Path(__file__).parent / "sample-data"
_application = None
require_ready = True
tools = []
middleware = []
safe_evidence = ReturnsApplication.safe_evidence


def credential_factory():
    from azure.identity.aio import DefaultAzureCredential
    return DefaultAzureCredential(
        managed_identity_client_id=os.environ["AZURE_CLIENT_ID"],
        exclude_environment_credential=True, exclude_workload_identity_credential=True,
        exclude_shared_token_cache_credential=True, exclude_visual_studio_code_credential=True,
        exclude_cli_credential=True, exclude_powershell_credential=True,
        exclude_developer_cli_credential=True, exclude_interactive_browser_credential=True,
        exclude_broker_credential=True)


async def trusted_context(identity, tool_call, reads):
    if _application is None:
        raise ValueError("returns_backend_not_initialized")
    return await _application.trusted_context(identity, tool_call, reads)


async def initialize(config, credential, stack):
    from azure.cosmos.aio import CosmosClient
    from deployment_config import DeploymentConfiguration
    from returns_backend import ReturnsBackend
    global _application, tools
    if _application is not None:
        raise ValueError("returns_backend_already_initialized")
    settings = DeploymentConfiguration.model_validate(
        {key: config[key] for key in DeploymentConfiguration.model_fields if key in config})
    if (os.environ.get("AZURE_CLIENT_ID") != settings.agent_client_id
            or os.environ.get("FOUNDRY_PROJECT_ENDPOINT") != settings.citadel_project_endpoint):
        raise ValueError("existing_uami_and_citadel_binding_required")
    client = await stack.enter_async_context(CosmosClient(settings.cosmos_url, credential=credential))
    container = client.get_database_client(settings.cosmos_database).get_container_client(settings.cosmos_container)
    properties = await container.read()
    if properties.get("partitionKey", {}).get("paths") != ["/case_id"]:
        raise ValueError("case_partition_transaction_required")
    _application = ReturnsApplication(ReturnsBackend(container=container, samples=SAMPLES))
    tools = _application.tools
