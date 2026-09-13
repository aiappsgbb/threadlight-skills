"""Keep scenario evidence scopes explicit in the PR-facing validation record."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs/governed-returns-validation.md"


def test_report_separates_observed_scenarios_from_hosting_targets():
    text = " ".join(REPORT.read_text().split())
    for heading in (
        "## Scenario status",
        "## S1: VM-hosted native MAF and governed MCP",
        "### Deployed setup",
        "### Identity and authorization boundaries",
        "### Executed cases",
        "### Evidence chain",
        "### Failures encountered and corrections",
        "## S2: Foundry hosted agent",
        "## S3: Prompt-agent applicability",
    ):
        assert heading in text
    for marker in (
        "S1-MODEL-ALLOW", "S1-MCP-DENY", "S1-HUMAN-RESUME",
        "S1-COMPLETED-REPLAY", "S1-SERVED-READ",
        "not an atomicity or timing proof by themselves",
        "No hosted agent version was registered in S1",
        "not a drop-in replacement",
        "14 focused tests",
    ):
        assert marker in text


def test_report_preserves_evidence_references_without_private_targets():
    text = REPORT.read_text()
    for digest in (
        "29eb9710becbe74767d05d9bfee16d955da301e3101c42d610bd4ccfb5951291",
        "03ce6b83708193e8d15510098a39fd2b09cbc463cb3a34ff7431ded85943edba",
        "f3d6bf5c9c386f12ca7fd5ac779ebf80e9c220c64f42dc9994ac3306cf056ac4",
    ):
        assert digest in text
    assert "/Users/" not in text
    assert "ssh-ed25519 " not in text
    assert not re.search(r"https://[a-z0-9-]+\.(?:azurecr\.io|openai\.azure\.com)", text)
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
        if "://" not in target:
            assert (REPORT.parent / target).exists(), target


def test_hosted_registration_is_not_reported_as_business_execution():
    text = " ".join(REPORT.read_text().split())
    for marker in (
        "S2-REGISTERED-NOT-RUNNING",
        "ProvisioningError",
        "No S2 business invocation is credited",
        "container token use was not observed",
        "32d5c44d43d4bb1d89ec347aa56ac1e743ea4a65fcc33f67f02383a271b24994",
    ):
        assert marker in text


def test_canonical_private_control_does_not_credit_unobserved_platform_execution():
    text = " ".join(REPORT.read_text().split())
    for marker in (
        "### September 13: complete canonical private startup control",
        "2ef44f6b47803a0166956cc668e5f429c1c1f8cb",
        "53e02ab62b0be348717de32f4df755aef919f1eb90019374cbd1e23602a7cd5d",
        "no governance bootstrap and no remote/business tools",
        "networkAcls.bypass",
        "not sufficient to resolve provisioning",
        "agent_version_failed",
        "registry metrics do not identify the caller",
        "No hosted business result or native session-home success",
        "canonical-bypass-attempt-0913/direct-version-2.json",
        "public access enabled",
    ):
        assert marker in text


def test_instrumented_control_distinguishes_platform_acquisition_from_snapshot_success():
    text = " ".join(REPORT.read_text().split())
    for marker in (
        "### Instrumented retry: project-identity registry access observed",
        "Buildah/1.42.1",
        "MDCContainersSecurity/1.0",
        "aa794464706678e9b2d477a30886bedb",
        "not proof of successful layer unpacking",
        "per-caller blob completion",
        "canonical-instrumented-attempt-0913/registry-events-final.json",
        "missing organization-managed Log Analytics workspace",
    ):
        assert marker in text
