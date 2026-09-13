"""Read audit is durable disclosure accounting, not an ACS binding."""
import asyncio
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def backend():
    path = Path(__file__).resolve().parents[1] / "references/governance/returns_mcp_backend.py"
    spec = importlib.util.spec_from_file_location("returns_read_audit_backend", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_audit_records_authenticated_identity_without_payload_or_policy_claim():
    module = backend()
    case = {"id": "S3-CASE", "_etag": '"revision"', "status": "in_triage",
            "amount": 100, "eligible": True, "high_risk": False, "secret": "never-copy"}
    identity = SimpleNamespace(subject="reader", client="client")
    record = module.read_audit_record(case, identity, tenant="tenant", deployment={"agent_version": "5"})
    assert record["id"].startswith("read-")
    assert record["scope"] == "tenant:reader"
    assert record["body"]["subject"] == "reader"
    assert record["body"]["client"] == "client"
    assert record["body"]["action_id"] == "returns_get_case"
    assert record["body"]["policy_binding"] == "none"
    assert record["body"]["result_digest"] == module.digest(
        {key: case[key] for key in module.CASE_FIELDS})
    assert "never-copy" not in str(record)
    assert "amount" not in record["body"]


def test_native_read_audit_is_wired_to_pinned_deployment_runner():
    root = Path(__file__).resolve().parents[3]
    runner = (root / "scripts/ci/run-governance-pin-tests.py").read_text()
    assert '"skills/threadlight-deploy/tests/test_returns_read_audit.py"' in runner
    assert '"test_real_cosmos_guard_has_a_separate_create_only_audit_protocol"' in runner


@pytest.mark.parametrize("fail", [False, True])
def test_read_audit_requires_real_ack_and_rechecks_authorization(fail):
    module = backend()
    calls = []
    document = {"id": "read-id", "scope": "tenant:reader", "body": {"action_id": "returns_get_case"}}

    async def authorize():
        calls.append("authorize")

    class Container:
        async def execute_item_batch(self, *, batch_operations, partition_key):
            assert calls[-1] == "authorize"
            assert partition_key == document["scope"]
            assert batch_operations == [("create", (document,), {})]
            calls.append("write")
            if fail:
                raise RuntimeError("audit_ack_unavailable")

    class Transport:
        @contextmanager
        def append_audit(self, callback, container, partition, record):
            assert callback is authorize
            assert partition == document["scope"]
            assert record == document
            calls.append("guarded")
            yield

    async def run():
        if fail:
            with pytest.raises(RuntimeError, match="audit_ack_unavailable"):
                await module.append_read_audit(Transport(), Container(), document, authorize)
        else:
            assert await module.append_read_audit(
                Transport(), Container(), document, authorize) == "read-id"
        assert calls == ["guarded", "authorize", "write"]
    asyncio.run(run())


@pytest.mark.governance_runtime
def test_real_cosmos_guard_has_a_separate_create_only_audit_protocol():
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "skills/threadlight-govern/references"))
    path = root / "examples/returns-triage-governed/src/agent/cosmos_effect.py"
    spec = importlib.util.spec_from_file_location("real_read_audit_transport", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from runtime import GovernedToolUnavailable
    transport = module.CosmosEffectTransport(endpoint="https://fixture.documents.azure.com")
    container = SimpleNamespace(container_link="dbs/governance/colls/audit")
    transport._containers.append(container)
    record = {"id": "read-1", "scope": "tenant:reader",
              "body": {"kind": "case-read", "action_id": "returns_get_case", "policy_binding": "none"}}
    with transport.append_audit(lambda: None, container, record["scope"], record):
        state = transport._batch.get()
        body = [{"operationType": "Create", "resourceBody": record}]
        headers = {
            "x-ms-cosmos-is-batch-request": "True", "x-ms-cosmos-batch-atomic": "True",
            "x-ms-cosmos-batch-continue-on-error": "False",
            "x-ms-documentdb-partitionkey": json.dumps([record["scope"]]),
        }
        transport._wire(state, "POST", "https://fixture.documents.azure.com/dbs/governance/colls/audit/docs",
                        headers, json.dumps(body))
        body[0]["operationType"] = "Replace"
        with pytest.raises(GovernedToolUnavailable):
            transport._wire(state, "POST", "https://fixture.documents.azure.com/dbs/governance/colls/audit/docs",
                            headers, json.dumps(body))
    with pytest.raises(GovernedToolUnavailable):
        with transport.append_audit(lambda: None, container, record["scope"], {
                **record, "body": {"kind": "case", "action_id": "returns_apply_decision"}}):
            pass
