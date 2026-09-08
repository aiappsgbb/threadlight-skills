"""Required existing-environment inputs; no fallback tenant, signature, or image."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class DeploymentConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    environment: Literal["preproduction", "production"]
    tenant_id: str = Field(pattern=UUID)
    agent_client_id: str = Field(pattern=UUID)
    agent_id: Literal["returns-triage"]
    control_plane_url: str
    control_plane_scope: str = Field(pattern=r"^api://[0-9a-f-]{36}/\.default$")
    key_id: str = Field(pattern=r"^https://[^/]+\.vault\.azure\.net/keys/[^/]+/[0-9a-f]{32}$")
    approver_roles: list[str] = Field(min_length=1, max_length=16)
    cosmos_url: str = Field(pattern=r"^https://[^/]+\.documents\.azure\.com:443/?$|^https://[^/]+\.documents\.azure\.com/?$")
    cosmos_database: str = Field(min_length=1)
    cosmos_container: str = Field(min_length=1)
    citadel_project_endpoint: str
    signed_envelope: str = Field(min_length=1)
    policy_id: Literal["returns-write-v1"]
    policy_version: Literal["1"]
    policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    subscription: str | None = Field(default=None, pattern=UUID)
    resource_group: str | None = Field(default=None, min_length=1, max_length=90)

    @model_validator(mode="after")
    def existing_endpoints(self):
        control, citadel = urlsplit(self.control_plane_url), urlsplit(self.citadel_project_endpoint)
        if (control.scheme != "https" or not control.hostname or control.path not in ("", "/")
                or control.username or control.query or control.fragment):
            raise ValueError("authenticated_control_plane_endpoint_required")
        if (citadel.scheme != "https" or citadel.hostname != "apim-citadel-hub.azure-api.net"
                or not citadel.path.startswith("/api/projects/") or citadel.username
                or citadel.query or citadel.fragment):
            raise ValueError("existing_citadel_project_proxy_required")
        if any(not role.strip() for role in self.approver_roles):
            raise ValueError("explicit_supervisor_roles_required")
        return self
