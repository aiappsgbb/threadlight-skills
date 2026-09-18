"""Authenticated operator transitions use the real gateway/ACS/receipt protocols."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from test_gateway import GatewayHarness, gateway, registry
from test_control_plane import HUMAN, UI, WORKLOAD, TENANT, module as cp


def operator_token(h, **changes):
    return "Bearer " + h.cp.token(human=True, changes={
        "roles": ["Governance.Operate"], "scp": "Governance.Operate", **changes})


async def harness(tmp_path):
    assert hasattr(gateway("dispatcher").GovernedDispatcher, "operate"), "operator recovery API missing"
    document = registry()
    document["actions"][0].update(
        recovery_contract="fenced-outcome/v1", recovery_endpoint="https://downstream.example/recover")
    h = await GatewayHarness().initialize(tmp_path, document=document)
    auth = cp("auth")
    h.cp.settings.operation_controllers.update({
        HUMAN: auth.ProbeController(client_id=UI, subjects=[WORKLOAD], actions=["refund"])})
    h.dispatcher.operations_required = True
    return h


async def operate(h, operation, **fields):
    return await h.dispatcher.operate(
        authorization=operator_token(h),
        body={"operation": operation, "action": "refund", "requester": WORKLOAD, **fields})


async def open_admission(h):
    result = await operate(h, "admission", expected_record_hash=None, state="open",
                           valid_until=(datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
                           reason_code="test_admission")
    assert result["state"] == "open", result
    return result


@pytest.mark.governance_runtime
def test_operator_stop_is_fail_closed_and_preserves_completed_replay(tmp_path):
    async def check():
        h = await harness(tmp_path)
        try:
            assert (await h.call())["reason_code"] == "admission_closed"
            opened = await open_admission(h)
            assert (await h.call())["status"] == "completed"
            stopped = await operate(h, "admission", expected_record_hash=opened["record_hash"],
                                    state="stopped", valid_until=None, reason_code="incident_stop")
            assert stopped["state"] == "stopped"
            assert (await h.call(key="new"))["reason_code"] == "admission_closed"
            assert (await h.call())["status"] == "completed"
            assert len(h.calls) == 1 and len(h.gets) == 1
        finally:
            await h.close()
    asyncio.run(check())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("change", [
    {"roles": ["Approver"]}, {"scp": "Governance.Read"}, {"oid": WORKLOAD},
    {"tid": "99999999-9999-4999-8999-999999999999"}, {"exp": 1},
])
def test_operator_authority_is_not_workload_or_pasted_claims(tmp_path, change):
    async def check():
        h = await harness(tmp_path)
        try:
            before = deepcopy(h.store.docs)
            response = await h.dispatcher.operate(authorization=operator_token(h, **change), body={
                "operation": "admission", "action": "refund", "requester": WORKLOAD,
                "expected_record_hash": None, "state": "stopped", "valid_until": None,
                "reason_code": "test"})
            assert response["status"] == "blocked"
            assert h.store.docs == before
        finally:
            await h.close()
    asyncio.run(check())


@pytest.mark.governance_runtime
def test_stop_after_credential_wait_prevents_effect(tmp_path):
    async def check():
        h = await harness(tmp_path)
        try:
            opened = await open_admission(h)
            async def stop():
                await operate(h, "admission", expected_record_hash=opened["record_hash"],
                              state="stopped", valid_until=None, reason_code="stop_during_wait")
            h.credential.hook = stop
            assert (await h.call())["reason_code"] == "admission_closed"
            assert not h.calls
        finally:
            await h.close()
    asyncio.run(check())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("outcome", ["completed", "not_executed", "unknown", "404", "wrong-scope"])
def test_unknown_recovery_is_independent_scoped_and_never_redispatches(tmp_path, outcome):
    async def check():
        h = await harness(tmp_path)
        try:
            await open_admission(h)
            h.downstream_status = 503
            assert (await h.call())["status"] == "unavailable"
            original_calls = len(h.calls)
            inspected = await operate(h, "inspect", operation_id="one", arguments={"amount": 5})
            assert inspected["state"] == "pending"
            record_before = deepcopy(h.store.docs)
            async def witness(request):
                proof = {
                    "state": outcome if outcome in {"completed", "not_executed"} else "unknown",
                    "receipt_id": "outcome-1" if outcome == "completed" else "fence-1",
                    "action_hash": request.headers["x-action-hash"],
                    "provenance": request.headers["x-governance-provenance"],
                    "operation_key": request.headers["idempotency-key"],
                    "deployment_hash": request.headers["x-deployment-hash"],
                }
                if outcome == "wrong-scope":
                    proof.update(state="completed", action_hash="sha256:" + "0" * 64)
                return httpx.Response(404 if outcome == "404" else 200, json=proof)
            await h.downstream.aclose()
            h.dispatcher.downstream = gateway("dispatcher").DownstreamClient(
                credential=h.credential, transport=httpx.MockTransport(witness))
            result = await operate(h, "reconcile", operation_id="one", arguments={"amount": 5},
                                   expected_record_hash=inspected["record_hash"])
            if outcome in {"completed", "not_executed"}:
                assert result["state"] == outcome, result
                assert any(r["reason_code"] == "recovery_" + outcome for r in h.receipt_bodies())
            else:
                assert result["reason_code"] == "outcome_unknown"
                assert h.store.docs == record_before
            assert len(h.calls) == original_calls
            assert (await h.call(key="one", arguments={"amount": 6}))["reason_code"] == "idempotency_conflict"
            await h.dispatcher.downstream.aclose()
        finally:
            await h.close()
    asyncio.run(check())


@pytest.mark.governance_runtime
def test_admission_race_and_lost_receipt_ack_never_open(tmp_path):
    async def check():
        h = await harness(tmp_path)
        try:
            opened = await open_admission(h)
            async def stop():
                return await operate(h, "admission", expected_record_hash=opened["record_hash"],
                                     state="stopped", valid_until=None, reason_code="stop")
            results = await asyncio.gather(stop(), stop())
            assert sum(r.get("state") == "stopped" for r in results) == 1
            stopped = next(r for r in results if r.get("state") == "stopped")
            h.cp.store.failed = True
            result = await operate(h, "admission", expected_record_hash=stopped["record_hash"],
                                   state="open", valid_until=(datetime.now(timezone.utc)
                                   + timedelta(seconds=120)).isoformat(), reason_code="reopen")
            assert result["status"] == "unavailable"
            assert (await h.call())["reason_code"] == "admission_closed"
        finally:
            await h.close()
    asyncio.run(check())


def test_operation_store_requires_strong_single_writer_consistency():
    async def check():
        class Container:
            async def read(self):
                return {"partitionKey": {"paths": ["/scope"]}}
        async def account():
            return SimpleNamespace(WritableLocations=[{}],
                                   ConsistencyPolicy={"defaultConsistencyLevel": "Session"})
        store = gateway("server").GatewayStore(
            None, Container(), account_reader=account, operations_required=True)
        with pytest.raises(RuntimeError, match="strong"):
            await store.health()
    asyncio.run(check())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("fault", ["key-revoked", "lease-expired", "store-unavailable"])
def test_operator_admission_rechecks_revocation_after_wait(tmp_path, fault):
    async def check():
        h = await harness(tmp_path)
        try:
            await open_admission(h)
            async def change():
                if fault == "key-revoked":
                    async def revoked():
                        raise RuntimeError("key disabled at authority")
                    h.policy.signer.health = revoked
                elif fault == "lease-expired":
                    for (scope, key), (body, etag) in h.store.docs.items():
                        if key == "admission":
                            body["valid_until"] = "2020-01-01T00:00:00+00:00"
                else:
                    h.store.failed = True
            h.credential.hook = change
            assert (await h.call())["reason_code"] == "admission_closed"
            assert not h.calls
        finally:
            await h.close()
    asyncio.run(check())


@pytest.mark.governance_runtime
def test_operator_http_and_client_are_separate_from_model_tools(tmp_path):
    async def check():
        h = await harness(tmp_path)
        try:
            app = gateway("server").create_app(h.dispatcher)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                            base_url="https://gateway.example") as client:
                    request = {"operation": "inspect", "action": "refund", "requester": WORKLOAD}
                    forbidden = await client.post("/governance/operations", json=request,
                                                  headers={"Authorization": "Bearer " + h.cp.token()})
                    assert forbidden.status_code == 403
                    from test_gateway import Credential
                    result = await cp("operator").submit(
                        request, gateway_url="https://gateway.example", scope="api://governance/.default",
                        credential=Credential(operator_token(h)[7:]), http=client)
                    assert result["state"] == "missing"
                    assert result["retry_authorized"] is False
                    assert (await client.get("/governance/operations")).status_code == 405
                    assert not h.calls and not h.store.docs
        finally:
            await h.close()
    asyncio.run(check())


@pytest.mark.governance_runtime
def test_admission_lease_cannot_outlive_operator_token(tmp_path):
    async def check():
        h = await harness(tmp_path)
        try:
            now = datetime.now(timezone.utc)
            result = await h.dispatcher.operate(
                authorization=operator_token(h, exp=int((now + timedelta(seconds=30)).timestamp())),
                body={"operation": "admission", "action": "refund", "requester": WORKLOAD,
                      "expected_record_hash": None, "state": "open",
                      "valid_until": (now + timedelta(seconds=120)).isoformat(), "reason_code": "test"})
            assert result["reason_code"] == "admission_lease_invalid"
            assert not h.store.docs and not h.calls
        finally:
            await h.close()
    asyncio.run(check())
