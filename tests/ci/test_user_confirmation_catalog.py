"""Catalog guidance must expose the actual separate-user flow and its evidence limits."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_confirmation_catalog_version_is_consistent():
    plugin = json.loads((ROOT / "plugin.json").read_text())
    marketplace = json.loads((ROOT / ".github/plugin/marketplace.json").read_text())
    assert plugin["version"] == marketplace["metadata"]["version"] == "2.8.0"
    assert marketplace["plugins"][0]["version"] == plugin["version"]


def test_generator_guide_covers_confirmation_configuration_and_notification():
    guide = (ROOT / "skills/threadlight-deploy/references/governance/README.md").read_text()
    for term in (
        "confirmation_container", "user-confirmation-notification.bicep",
        "confirmation_subjects", "Governance.Confirm", "governance_request_context",
        "governance_operation_id", "user_client_id", "not MFA",
    ):
        assert term in guide


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
        "skills/threadlight-deploy/tests/test_user_confirmation_client.py",
        "skills/threadlight-deploy/tests/test_user_confirmation_generation.py",
    ):
        assert test in runner
