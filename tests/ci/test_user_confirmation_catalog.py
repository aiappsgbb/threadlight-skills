"""Catalog guidance must expose the actual separate-user flow and its evidence limits."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_confirmation_catalog_version_is_consistent():
    plugin = json.loads((ROOT / "plugin.json").read_text())
    marketplace = json.loads((ROOT / ".github/plugin/marketplace.json").read_text())
    assert plugin["version"] == marketplace["metadata"]["version"] == "2.10.0"
    assert marketplace["plugins"][0]["version"] == plugin["version"]


def test_generator_guide_covers_confirmation_configuration_and_notification():
    guide = (ROOT / "skills/threadlight-deploy/references/governance/README.md").read_text()
    for term in (
        "confirmation_container", "user-confirmation-notification.bicep",
        "confirmation_subjects", "Governance.Confirm", "governance_request_context",
        "governance_operation_id", "user_client_id", "not MFA",
    ):
        assert term in guide


def test_normal_user_confirmation_is_native_mail_not_a_recipient_command():
    guide = (ROOT / "skills/threadlight-deploy/references/governance/README.md").read_text()
    section = guide.split("### Requesting-user confirmation", 1)[1].split("### Explicit native Outlook", 1)[0]
    assert "user-confirmation-approval.bicep" in section
    assert "`outlook-native`" in section
    assert "Approve/Reject directly in the email" in section
    assert "diagnostic" in section
    assert "The user independently runs" not in section
    govern = (ROOT / "skills/threadlight-govern/SKILL.md").read_text()
    assert "Approve/Reject directly in the email" in govern


def test_governance_skills_distinguish_user_confirmation_from_review_and_noop_proof():
    govern = (ROOT / "skills/threadlight-govern/SKILL.md").read_text()
    assessor = (ROOT / "skills/threadlight-governed-actions/SKILL.md").read_text()
    assert "user-confirmation" in govern
    assert "threadlight-confirm-action" in govern
    assert "user-confirmation-proof-required" in assessor
    assert "requesting user" in assessor


def test_deployment_runner_includes_confirmation_protocol_and_generated_client():
    runner = (ROOT / "scripts/ci/run-governance-pin-tests.py").read_text()
    for test in (
        "skills/threadlight-govern/tests/test_confirmation_gateway.py",
        "skills/threadlight-govern/tests/test_user_confirmation.py",
        "skills/threadlight-govern/tests/test_confirmation_providers.py",
        "skills/threadlight-govern/tests/test_confirmation_integration_fixes.py",
        "skills/threadlight-govern/tests/test_native_user_confirmation.py",
        "skills/threadlight-deploy/tests/test_user_confirmation_client.py",
        "skills/threadlight-deploy/tests/test_user_confirmation_generation.py",
        "skills/threadlight-deploy/tests/test_confirmation_descriptor_selection.py",
        "skills/threadlight-deploy/tests/test_returns_source_closure.py",
        "skills/threadlight-deploy/tests/test_user_confirmation_approval.py",
    ):
        assert test in runner
