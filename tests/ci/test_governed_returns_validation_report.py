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
