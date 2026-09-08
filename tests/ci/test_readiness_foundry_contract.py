"""Installed SDK characterization, not a simulated successful deployment.

Run with THREADLIGHT_READINESS_SDK=1 in the existing pinned Linux environment.
Only the HTTP adapter and credential are local fixtures; SDK models, request
serialization and response deserialization are the published 2.3.0 bytes.
"""
import inspect
from io import BytesIO
import json
import os
import time
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("THREADLIGHT_READINESS_SDK") != "1",
    reason="requires explicit installed-SDK contract job; not hosted proof",
)


def test_pinned_sdk_observes_server_version_and_identity_over_http():
    from importlib.metadata import version
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import ContainerConfiguration, HostedAgentDefinition, ProtocolVersionRecord
    from azure.core.credentials import AccessToken
    from azure.core.pipeline.transport import RequestsTransport
    from requests import Response, Session
    from requests.adapters import BaseAdapter
    from urllib3.response import HTTPResponse

    assert version("azure-ai-projects") == "2.3.0"
    endpoint = "https://local-fixture.services.ai.azure.com/api/projects/local-fixture"
    image = "fixture.azurecr.io/agent@sha256:" + "a" * 64
    identity = {"principal_id": str(uuid4()), "client_id": str(uuid4())}
    server_id = str(uuid4())
    calls = []
    observed = None

    class Credential:
        def get_token(self, *scopes, **kwargs):
            assert scopes == ("https://ai.azure.com/.default",)
            return AccessToken("local-only-not-a-real-token", int(time.time()) + 3600)

    class OfflineFoundry(BaseAdapter):
        def send(self, request, **kwargs):
            nonlocal observed
            body = json.loads(request.body) if request.body else None
            calls.append((request.method, request.url, body))
            assert request.url.startswith(endpoint + "/agents/fixture")
            response = Response()
            response.request = request
            response.headers["Content-Type"] = "application/json"
            response.status_code = 200
            if request.method == "POST":
                assert request.url == endpoint + "/agents/fixture/versions?api-version=v1"
                assert observed is None, "a second create must not masquerade as activation"
                assert set(body) == {"definition"}
                assert body["definition"]["container_configuration"] == {"image": image}
                observed = {
                    "id": server_id, "object": "agent.version", "name": "fixture",
                    "version": "17", "metadata": {}, "created_at": int(time.time()),
                    "definition": body["definition"], "status": "active",
                    "instance_identity": identity,
                }
            else:
                assert request.method == "GET"
                assert request.url == endpoint + "/agents/fixture/versions/17?api-version=v1"
            response._content = json.dumps(observed).encode()
            response.raw = HTTPResponse(
                body=BytesIO(response._content), headers=response.headers, status=200, preload_content=False)
            return response

        def close(self):
            pass

    with Session() as session:
        session.trust_env = False
        session.mount("https://", OfflineFoundry())
        with AIProjectClient(
            endpoint=endpoint, credential=Credential(), api_version="v1",
            transport=RequestsTransport(session=session, session_owner=False),
        ) as client:
            created = client.agents.create_version(
                agent_name="fixture",
                definition=HostedAgentDefinition(
                    cpu="1", memory="2Gi",
                    container_configuration=ContainerConfiguration(image=image),
                    protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
                    environment_variables={"TL_GOV_IMAGE_DIGEST": image.split("@")[1]},
                ),
            )
            reread = client.agents.get_version(agent_name=created.name, agent_version=created.version)
    assert created.version == reread.version == "17"
    assert created.id == reread.id == server_id
    assert reread.instance_identity.principal_id == identity["principal_id"]
    assert reread.instance_identity.client_id == identity["client_id"]
    assert reread.definition.container_configuration.image == image
    assert [(method, url.rsplit("?", 1)[-1]) for method, url, _ in calls] == [
        ("POST", "api-version=v1"), ("GET", "api-version=v1")]


def test_pinned_typed_schema_cannot_supply_requested_start_or_mount():
    from importlib.metadata import version
    from azure.ai.projects.models import ContainerConfiguration, HostedAgentDefinition
    from azure.ai.projects.operations import AgentsOperations

    assert version("azure-ai-projects") == "2.3.0"
    assert set(ContainerConfiguration.__annotations__) == {"image"}
    assert not {"mounts", "volumes", "volume_mounts", "start", "auto_start"} & set(
        HostedAgentDefinition.__annotations__)
    methods = {name for name in dir(AgentsOperations) if not name.startswith("_")}
    assert not {"start", "start_container", "start_agent", "start_version", "update_version", "promote_version"} & methods
    assert {"create_version", "get_version", "update_details", "enable", "disable"} <= methods
    assert "draft" in inspect.signature(AgentsOperations.create_version).parameters
    assert "agent_endpoint" in inspect.signature(AgentsOperations.update_details).parameters
    assert "definition" not in inspect.signature(AgentsOperations.update_details).parameters
    assert "agent_version" not in inspect.signature(AgentsOperations.enable).parameters
