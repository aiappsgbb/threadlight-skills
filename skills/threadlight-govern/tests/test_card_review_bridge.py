"""Cards reach the real delegated protocol; no card field becomes authority."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_control_plane import Harness, module
from test_operator_review import pending_document


ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "skills/threadlight-hitl-patterns/references/handlers/control_plane_review.py"


def bridge():
    assert BRIDGE.is_file(), "copyable delegated review bridge is missing"
    spec = importlib.util.spec_from_file_location("card_review_bridge", BRIDGE)
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    return implementation


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("human", [True, False])
def test_card_decision_uses_real_authority_and_never_executes_business_work(approved, human):
    implementation = bridge()

    async def scenario():
        h = await Harness().initialize()
        try:
            pending = pending_document(h)
            assert (await h.post("request", intent=pending["approval_intent"])).status_code == 202

            async def load_review(review_id):
                assert review_id == pending["approval_intent"]["nonce"]
                return "case-1", pending

            async def get_token(scope):
                return SimpleNamespace(token=h.token(human=human))

            submission = {
                "gate": "approve", "decision": "approved" if approved else "declined",
                "case_id": "case-1", "review_id": pending["approval_intent"]["nonce"],
            }
            arguments = dict(
                load_review=load_review, role="Approver",
                base_url="https://control.example", scope="api://governance/Governance.Approve",
                credential=SimpleNamespace(get_token=get_token), http=h.client,
            )
            if human:
                result = await implementation.decide_submission(submission, **arguments)
                assert result == {"operation_id": "one", "approved": approved, "execution": "not-started"}
                with pytest.raises(module("client").ApprovalUnavailable):
                    await implementation.decide_submission(submission, **arguments)
            else:
                with pytest.raises(module("client").ApprovalUnavailable):
                    await implementation.decide_submission(submission, **arguments)
        finally:
            await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["case", "review", "role-field", "edits", "request-info"])
def test_card_mismatch_or_unimplemented_business_gate_never_requests_credentials(fault):
    implementation = bridge()

    async def scenario():
        h = await Harness().initialize()
        try:
            pending = pending_document(h)
            submission = {
                "gate": "approve", "decision": "approved", "case_id": "case-1",
                "review_id": pending["approval_intent"]["nonce"],
            }
            if fault in ("case", "review"):
                submission[f"{fault}_id"] = "different"
            elif fault == "role-field":
                submission["actor_role"] = "Approver"
            else:
                submission["gate"] = "edit-and-approve" if fault == "edits" else "request-info"

            async def load_review(_):
                return "case-1", pending

            async def get_token(_):
                pytest.fail("Invalid card submission reached credentials")

            with pytest.raises(ValueError):
                await implementation.decide_submission(
                    submission, load_review=load_review, role="Approver",
                    base_url="https://control.example", scope="api://governance/Governance.Approve",
                    credential=SimpleNamespace(get_token=get_token), http=h.client)
        finally:
            await h.close()

    asyncio.run(scenario())


def test_skill_routes_governed_handlers_to_the_real_bridge_not_direct_business_writes():
    skill = (ROOT / "skills/threadlight-hitl-patterns/SKILL.md").read_text()
    assert "control_plane_review.py" in skill
    assert "runtime-support.md" in skill
    step = skill.split("### Step 3:", 1)[1].split("### Step 4:", 1)[0]
    assert "NotImplementedError" not in step
    assert "never writes the business case" in step
