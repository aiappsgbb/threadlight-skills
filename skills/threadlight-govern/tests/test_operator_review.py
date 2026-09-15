"""Offline delegated review transport regressions, not human or hosted evidence."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from test_control_plane import APP, DIGEST, TENANT, UI, WORKLOAD, Harness, module


def pending_document(harness):
    canonical = module("models").canonical
    facts = {
        "tenant": TENANT, "subject": WORKLOAD, "client": APP,
        "action": "refund", "scope": "case-1", "policy": DIGEST,
    }
    arguments = {"amount": 5}
    intent = {
        **harness.wire_intent,
        "context_identity": "sha256:" + hashlib.sha256(canonical(facts)).hexdigest(),
        "action_hash": "sha256:" + hashlib.sha256(
            canonical({"facts": facts, "arguments": arguments})).hexdigest(),
    }
    return {
        "status": "pending_approval", "operation_id": "one",
        "approval_intent": intent, "review_context": facts,
        "proposed_arguments": arguments,
    }


@pytest.mark.parametrize("scope", ["api://governance/Governance.Approve", "api://governance/.default"])
@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("human", [True, False])
def test_review_exact_scope_preserves_real_protocol_authorization(scope, approved, human):
    async def scenario():
        h = await Harness().initialize()
        scopes = []

        async def get_token(requested):
            scopes.append(requested)
            return SimpleNamespace(token=h.token(human=human))

        try:
            pending = pending_document(h)
            assert (await h.post("request", intent=pending["approval_intent"])).status_code == 202
            review = module("review")
            arguments = dict(
                approved=approved, role="Approver", base_url="https://control.example",
                scope=scope, credential=SimpleNamespace(get_token=get_token), http=h.client,
            )
            if human:
                result = await review.decide(pending, **arguments)
                assert result == {"operation_id": "one", "approved": approved, "execution": "not-started"}
                with pytest.raises(module("client").ApprovalUnavailable):
                    await review.decide(pending, **arguments)
                assert scopes == [scope, scope]
            else:
                with pytest.raises(module("client").ApprovalUnavailable):
                    await review.decide(pending, **arguments)
                assert scopes == [scope]
            record, _ = next(iter(h.store.docs.values()))
            assert record["state"] == ("decided" if human else "pending")
        finally:
            await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("scope", ["api://governance/Governance.Read", "Governance.Approve", ""])
def test_review_rejects_unsupported_scopes_before_credentials(scope):
    async def scenario():
        h = await Harness().initialize()

        async def get_token(_):
            pytest.fail("Invalid review scope reached authentication")

        try:
            with pytest.raises(ValueError, match="invalid_client_configuration"):
                await module("review").decide(
                    pending_document(h), approved=True, role="Approver",
                    base_url="https://control.example", scope=scope,
                    credential=SimpleNamespace(get_token=get_token), http=h.client)
        finally:
            await h.close()

    asyncio.run(scenario())


def test_delegated_review_scope_does_not_relax_workload_transport():
    with pytest.raises(ValueError, match="invalid_client_configuration"):
        module("client").ServiceTransport.validate_configuration(
            "https://control.example", "api://governance/Governance.Approve", 5)


def test_cli_accepts_delegated_scope_but_stops_without_human_confirmation(tmp_path, monkeypatch, capsys):
    async def prepare():
        h = await Harness().initialize()
        try:
            return pending_document(h)
        finally:
            await h.close()

    path = tmp_path / "pending.json"
    path.write_text(json.dumps(asyncio.run(prepare())))
    review = module("review")
    prompts = []
    monkeypatch.setattr(review.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def decline(prompt):
        prompts.append(prompt)
        return "do not submit"

    monkeypatch.setattr("builtins.input", decline)
    assert review.main([
        "--pending", str(path), "--control-plane-url", "https://control.example",
        "--scope", "api://governance/Governance.Approve", "--tenant", TENANT,
        "--client-id", UI, "--role", "Approver", "--decision", "approve",
    ]) == 2
    assert len(prompts) == 1
    assert "Decision not submitted." in capsys.readouterr().err
