"""Explicit local business-source fixtures; no live purchase verification."""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
from test_evidence_attestations import fixture
from test_returns_mcp_backend import case, arguments


def backend():
    from test_control_plane import module as control
    control("attestations")
    spec = importlib.util.spec_from_file_location(
        "returns_evidence_backend", ROOT / "skills/threadlight-deploy/references/governance/returns_mcp_backend.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def purchase_case():
    return {**case(), "customer_id": "customer-1", "currency": "EUR", "defect_declared": True,
            "purchase": {
                "reference": "fixture-order-1", "revision": "v3",
                "customer_id": "customer-1", "amount": 40, "currency": "EUR",
            }}


def test_returns_profile_only_attests_authoritative_purchase_not_declared_defect():
    module = backend()
    result = module.purchase_evidence(purchase_case(), arguments())
    assert result.claims == {"purchase_verified": True, "amount": 40, "defect_declared": True}
    assert "defect_verified" not in result.claims
    assert result.revision == arguments()["expected_etag"]
    for change in ({"purchase": None}, {"purchase": {"ocr_confidence": 1.0, "amount": 40}},
                   {"amount": 41}, {"customer_id": "someone-else"}):
        assert module.purchase_evidence({**purchase_case(), **change}, arguments()) is None


def test_same_provider_contract_and_backend_cas_bind_the_exact_corroboration():
    async def run():
        module = backend()
        e, signer, req, identity, _, _, _, _, facts = fixture()
        source = purchase_case()
        req = req.model_copy(update={"subjects": {source["id"]: source["customer_id"]}})
        class ContainerFixture:
            async def read_item(self, item, partition_key):
                assert item == partition_key == source["id"]
                return deepcopy(source)
        provider = e.EvidenceProvider(
            requirement=req, action="refund", tenant=identity.tenant, signer=signer,
            key_id=req.keys[0].kid, adapter=module.PurchaseAdapter(ContainerFixture()),
            grants=[e.EvidenceGrant(principal=identity.subject, client=identity.client,
                                    case_id=source["id"], subject=source["customer_id"])])
        issued = await provider.issue(identity, arguments())
        verified = e.verify_attestation(issued["attestation"], req, facts, arguments())
        module.verify_purchase_binding(source, arguments(), requirement=req, facts=facts,
                                       expected_fingerprint=verified.fingerprint)
        changed = deepcopy(source)
        changed["purchase"]["revision"] = "corrected-v4"
        with pytest.raises(module.BusinessConflict):
            module.verify_purchase_binding(changed, arguments(), requirement=req, facts=facts,
                                           expected_fingerprint=verified.fingerprint)
        operations, _ = module.decision_batch(
            source, arguments(), operation_id="op", provenance={"evidence_fingerprint": verified.fingerprint})
        assert operations[0][2] == {"if_match_etag": arguments()["expected_etag"]}
        assert operations[1][1][0]["provenance"]["evidence_fingerprint"] == verified.fingerprint
    asyncio.run(run())


def test_evidence_contract_is_gateway_only_and_read_stays_unbound():
    from skills._shared.governance import GovernanceContractError, validate_governance_contract
    path = ROOT / "skills/threadlight-deploy/references/governance/returns_mcp_agent.py"
    spec = importlib.util.spec_from_file_location("evidence_returns_agent", path)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    document = agent.contract(evidence=True)
    normalized = validate_governance_contract(document, deployment_target="customer-pilot")
    assert normalized["tools"][0]["requires"][-1] == "signed-evidence"
    assert normalized["tools"][-1]["id"] == "returns_verify_purchase"
    assert normalized["tools"][-1]["policy_binding"] is None
    document["tools"][0]["enforcement_path"] = "local-agent-hooks"
    with pytest.raises(GovernanceContractError, match="signed.evidence"):
        validate_governance_contract(document, deployment_target="customer-pilot")


def test_business_evidence_retention_is_an_explicit_opt_in():
    module = backend()
    e, _, req, _, _, _, _, _, _ = fixture()
    from test_control_plane import Harness, KEY, WORKLOAD, OTHER
    import asyncio
    h = Harness()
    try:
        config = {
            **h.settings.model_dump(), "cosmos_url": "https://fixture.documents.azure.com:443/",
            "cosmos_database": "business", "cosmos_container": "returns",
            "service_client_id": OTHER, "writer_subject": OTHER, "agent_subject": WORKLOAD,
            "cases": ["RMA-1"], "policy_digest": "sha256:" + "a" * 64, "deployment": {},
            "evidence_requirement": req.model_copy(update={"profile": "returns-purchase-v1"}).model_dump(),
        }
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="returns_evidence_configuration_required"):
            module.Configuration.model_validate(config)
        config.update(evidence_container="business-verifications", evidence_retention_seconds=600,
                      business_retention_policy="fixture-only-retention-not-legal-guidance")
        assert module.Configuration.model_validate(config).key_id == KEY
        config["evidence_requirement"] = None
        with pytest.raises(ValidationError, match="evidence_configuration_required"):
            module.Configuration.model_validate(config)
        for key in ("evidence_requirement", "evidence_container",
                    "evidence_retention_seconds", "business_retention_policy"):
            config.pop(key)
        plain = module.Configuration.model_validate(config)
        assert module.Configuration.model_validate(plain.model_dump()) == plain
    finally:
        asyncio.run(h.close())


@pytest.mark.governance_runtime
def test_authenticated_provider_route_retains_result_and_backend_rejects_correction(tmp_path, monkeypatch):
    import json
    import httpx
    from azure.cosmos.exceptions import CosmosResourceNotFoundError
    from test_control_plane import Harness, WORKLOAD, OTHER
    from test_control_plane import module as control
    async def run():
        e, signer, req, _, _, _, _, _, _ = fixture()
        h = Harness()
        module = backend()
        source = purchase_case()
        req = req.model_copy(update={"profile": "returns-purchase-v1",
                                    "subjects": {source["id"]: source["customer_id"]}})
        config = {
            **h.settings.model_dump(), "cosmos_url": "https://fixture.documents.azure.com:443/",
            "cosmos_database": "business", "cosmos_container": "returns",
            "service_client_id": OTHER, "writer_subject": OTHER, "agent_subject": WORKLOAD,
            "cases": [source["id"]], "policy_digest": "sha256:" + "a" * 64, "deployment": {},
            "evidence_requirement": req.model_dump(), "evidence_container": "verifications",
            "evidence_retention_seconds": 600, "business_retention_policy": "fixture-retention",
        }
        file = tmp_path / "config.json"
        file.write_text(json.dumps(config))
        monkeypatch.setenv("RETURNS_CONFIG_FILE", str(file))
        monkeypatch.syspath_prepend(str(ROOT / "skills/threadlight-govern/references"))
        monkeypatch.syspath_prepend(str(ROOT / "examples/returns-triage-governed/src/agent"))
        from cosmos_effect import CosmosEffectTransport
        records, effects = [], []

        class ExternalClientFixture:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class ContainerFixture:
            def __init__(self, name):
                self.name = name
                self.container_link = "dbs/business/colls/" + name
            async def read(self):
                return ({"partitionKey": {"paths": ["/scope"]}, "defaultTtl": 600}
                        if self.name == "verifications" else {"partitionKey": {"paths": ["/case_id"]}})
            async def read_item(self, item, partition_key):
                if item == source["id"]:
                    return deepcopy(source)
                raise CosmosResourceNotFoundError()
            async def execute_item_batch(self, *, batch_operations, partition_key):
                if self.name == "verifications":
                    records.extend(operation[1][0] for operation in batch_operations)
                else:
                    effects.append(batch_operations)
        async def connect(transport, *, stack, credential, database, container, **kwargs):
            result = ContainerFixture(container)
            transport._containers.append(result)
            return result
        monkeypatch.setattr(CosmosEffectTransport, "connect", connect)
        monkeypatch.setattr("azure.identity.aio.ManagedIdentityCredential", ExternalClientFixture)
        monkeypatch.setattr("azure.keyvault.keys.aio.KeyClient", ExternalClientFixture)
        monkeypatch.setattr("azure.keyvault.keys.crypto.aio.CryptographyClient", ExternalClientFixture)
        monkeypatch.setattr(control("storage"), "KeyVaultSigner", lambda *a, **k: signer)
        monkeypatch.setattr(module, "EntraAuth", lambda *a, **k: h.auth)
        app = module.create_app()
        try:
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="https://business.example") as client:
                assert (await client.post("/evidence/purchase", json=arguments())).status_code == 401
                token_response = await client.post(
                    "/evidence/purchase", json=arguments(),
                    headers={"Authorization": "Bearer " + h.token()})
                assert token_response.status_code == 200, token_response.text
                token = token_response.json()["attestation"]
                assert len(records) == 1 and token not in json.dumps(records)
                facts = {"tenant": config["tenant_id"], "subject": WORKLOAD, "client": h.settings.workloads[WORKLOAD].client_id,
                         "action": "returns_apply_decision", "scope": "returns",
                         "policy": config["policy_digest"], "deployment": {}}
                verified = e.verify_attestation(token, req, facts, arguments())
                facts["evidence_fingerprint"] = verified.fingerprint
                headers = {
                    "Authorization": "Bearer " + h.token(changes={"oid": OTHER}),
                    "X-Tenant-ID": config["tenant_id"], "X-Requester-ID": WORKLOAD,
                    "X-Action-ID": facts["action"], "X-Policy-Digest": facts["policy"],
                    "X-Deployment-Hash": module.digest({}), "Idempotency-Key": "operation-1",
                    "X-Action-Hash": module.digest({"facts": facts, "arguments": arguments()}),
                    "X-Governance-Provenance": "receipt-1", "X-Evidence-Fingerprint": verified.fingerprint}
                # A correction made during review changes the admitted source; approval
                # cannot turn the old token into corroboration of that new source.
                source["purchase"]["amount"] = 41
                source["_etag"] = '"revision-2"'
                denied = await client.post("/decisions", headers=headers, json=arguments())
                assert denied.status_code == 409 and not effects
                source.update(purchase=purchase_case()["purchase"], _etag=arguments()["expected_etag"])
                allowed = await client.post("/decisions", headers=headers, json=arguments())
                assert allowed.status_code == 200 and len(effects) == 1
                assert effects[0][0][2] == {"if_match_etag": source["_etag"]}
                source["purchase"] = {"ocr_confidence": 1.0}
                insufficient = await client.post(
                    "/evidence/purchase", json=arguments(),
                    headers={"Authorization": "Bearer " + h.token()})
                assert insufficient.json()["status"] == "insufficient_evidence"
                assert "attestation" not in insufficient.json()
                assert len(records) == 2
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("profile,verified,amount,decision,expected", [
    ("returns-purchase-v1", True, 40, "approve_refund", "allow"),
    ("returns-purchase-v1", False, 40, "approve_refund", "deny"),
    ("untrusted", True, 40, "approve_refund", "deny"),
    ("returns-purchase-v1", True, 1500, "approve_refund", "deny"),
    ("returns-purchase-v1", True, 1500, "escalate_to_supervisor", "escalate"),
])
def test_shipped_returns_evidence_policy_uses_native_acs(
        tmp_path, profile, verified, amount, decision, expected):
    import yaml
    from test_policy_bundle import bundle_module
    from agent_control_specification import AgentControl
    source = tmp_path / "source"
    source.mkdir()
    rego = ROOT / "skills/threadlight-deploy/references/governance/returns-evidence.rego"
    (source / "returns.rego").write_bytes(rego.read_bytes())
    (source / "manifest.yaml").write_text(yaml.safe_dump({
        "agent_control_specification_version": "0.3.1-beta",
        "policies": {"safe": {"type": "rego", "data": ["returns.rego"],
                              "query": "data.returns_evidence.pre_tool_call"}},
        "intervention_points": {
            "pre_tool_call": {"policy_target": "$.tool_call.args", "policy": {"id": "safe"}}},
    }))
    built = bundle_module().build_bundle(source=source, destination=tmp_path / "bundle",
                                         policy_id="safe", version="1")
    async def run():
        engine = AgentControl.from_path(str(built.manifest_path))
        result = await engine.evaluate_intervention_point("pre_tool_call", {
            "tool_call": {"name": "returns_apply_decision", "args": {"decision": decision}},
            "safe": {"evidence": {"profile": profile, "claims": {
                "purchase_verified": verified, "amount": amount}}},
        }, mode="enforce")
        assert result.verdict.decision.value == expected
    asyncio.run(run())
