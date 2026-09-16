"""The reviewed delivery surfaces must exist and describe their real authority."""
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / "skills/threadlight-hitl-patterns/references/cards"
GATES = ("approve", "edit-and-approve", "reject", "escalate",
         "signoff", "audit-view", "request-info")


@pytest.mark.parametrize("gate", GATES)
def test_advertised_card_is_copyable_and_does_not_fabricate_authority(gate):
    card = json.loads((CARDS / f"{gate}.json").read_text())
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.5"
    assert isinstance(card["body"], list) and card["body"]
    actions = card.get("actions", [])
    assert bool(actions) == (gate != "audit-view")
    for action in actions:
        assert action["type"] == "Action.Submit"
        data = action["data"]
        assert data["gate"] == gate
        assert data["case_id"] == "${caseId}"
        assert data["review_id"] == "${reviewId}"
        assert not {"actor", "role", "token", "grant", "signature"} & data.keys()
        if data.get("decision") == "cancelled":
            assert action["associatedInputs"] == "none"


def test_cards_explain_edits_and_the_separate_authority_boundary():
    guide = (CARDS / "README.md").read_text()
    assert "not an approval grant" in guide
    assert "new intent" in guide
    assert "runtime-support.md" in guide
    assert "Placeholder" not in guide


def test_supported_resume_route_is_not_contradicted_by_legacy_blocker_text():
    guide = (ROOT / "docs/production-readiness.md").read_text()
    assert "### Supported protected deployment route" in guide
    assert "resume-signed-bootstrap/v1" in guide
    assert "runtime_readiness_remote.py" in guide
    assert "Legacy azd target staging" in guide
    assert "Target/application staging is also unresolved" not in guide
    assert "cannot be reached by a successful protected deployment until" not in guide


def test_support_matrix_distinguishes_ui_from_native_human_resume():
    guide = (ROOT / "docs/runtime-support.md").read_text()
    for required in ("GitHub Copilot SDK", "MAF", "Outlook", "Teams",
                     "not an approval grant", "ghcp_deferred_approval_resume_unsupported",
                     "3,600", "same native session"):
        assert required in guide
