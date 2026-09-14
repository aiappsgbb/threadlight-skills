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
        "## S3: Public authenticated Foundry hosted execution",
        "## S4: Prompt-agent applicability",
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


def test_public_hosted_record_preserves_exact_business_and_human_boundaries():
    text = " ".join(REPORT.read_text().split())
    for marker in (
        "S3-HOSTED-ALLOW", "S3-HOSTED-DENY", "S3-PENDING-APPROVAL",
        "S3-EXPIRED-RESUME", "S3-AUDITED-READ", "SecurityControl=Ignore",
        "3e46f91052d3f22bcaa1897c394075caed767f54585be57725fa93b13096c69d",
        "decision-24b13a1968a7cbc1a0d4105b30c968bb3a52b437d51f56411aba13c7b3ae1cda",
        "53c7b11c83a4436b94732ad1517d8ebe",
        "live-expired-resume-proof.json", "reconciliation.json",
        "backend-acknowledged-before-return", "post-run",
        "Human approve/reject and successful hosted resume remain unproved",
        "Office 365 consent is missing", "not financial settlement",
        "headers:baggage,traceparent", "imagePassthrough",
    ):
        assert marker in text


def test_reproduction_documents_operator_supplied_cohort_and_audit_limits():
    references = ROOT / "skills/threadlight-deploy/references/governance"
    text = " ".join((references / "returns-mcp-demo.md").read_text().split())
    for marker in (
        "hosted_cohort", "read_audit_container", "returns_reconcile.py",
        "review-notification.bicep", "imagePassthrough", "selected-tools",
        "1.34.0", "azure-tenant-isolation", "RETURNS_CONFIG_VERSION",
        "Office 365 consent", "not a grant",
        "Edit API connection", "APPROVE <nonce>", "Governance.Approve",
        "omit `governance_operation_id`", "new-pending.json",
    ):
        assert marker in text


def test_september14_private_retry_is_not_presented_as_transient_resolution():
    text = " ".join(REPORT.read_text().split())
    for marker in (
        "### September 14: one unchanged private transient-control retry",
        "08:12:19", "08:15:13", "08:15:38", "version 4",
        "3ccff821812154762ffa14ad7270f9a0",
        "six Login and six Pull", "private-morning-0914/registry-events-final.json",
        "no overnight recovery was observed", "does not prove a backend defect",
        "no new business invocation", "same raw manifest",
    ):
        assert marker in text


def test_readme_exposes_implementation_evidence_and_proposed_pages_separately():
    text = " ".join((ROOT / "README.md").read_text().split())
    for target in ("docs/agent-governance-deep-dive.md", "docs/governed-returns-validation.md",
                   "docs/production-readiness-pages-spec.md"):
        assert f"]({target})" in text
    assert "proposed, not a published site change" in text


def test_public_receipts_do_not_renew_distinct_bootstrap_and_policy_leases():
    text = " ".join(REPORT.read_text().split())
    for marker in (
        "2026-09-14T10:26:48.991423+00:00",
        "2026-09-14T12:03:41.859581+00:00",
        "Historical successful receipts are not current executable authorization",
        "the earlier bootstrap expiry",
    ):
        assert marker in text


def test_egress_inspection_does_not_invent_a_relaxation_or_hosted_connectivity_proof():
    text = " ".join(REPORT.read_text().split())
    for marker in (
        "### September 14: scoped egress inspection with no network change",
        "10:55", "version 5", "09:06:22", "09:09:10",
        "f7a05c43279c67934fc8216b8399e9d6",
        "no customer network change", "not an A/B relaxation test",
        "no NSG", "no UDR", "shared NAT", "Network Watcher",
        "no source VM", "not hosted Internet egress proof",
        "private-egress-0914/outcome.json",
        "eb211156d171e842c02af0abc34b60ce93b7acbb5ba04d0103e712d8faf9cd5c",
    ):
        assert marker in text


def test_private_basic_success_does_not_promote_the_private_governed_agent():
    text = " ".join(REPORT.read_text().split())
    for marker in (
        "### September 14: private BASIC model smoke after registry binding and image comparison",
        "S2-BASIC-MODEL-VERIFIED", "ContainerRegistry", "ManagedIdentity",
        "09:48:41", "09:51:31", "09:59:47", "09:59:58", "10:00:39", "10:00:42",
        "7ef4074b69a1a286fe81c30153401da2",
        "8ce8505c36b53fb193e2e22142d7220b7a2122ff0c98f1f81fcf5e285c3b95fa",
        "caresp_05dd439fbfad842700382LI6uzcCnASJc9xKQ6p1g0vLlt3wIP",
        "Billing Issue", "private governed returns remains unproved",
        "not a format-only causal proof", "mutable", "unused placeholder",
        "private-cross-image-0914/private-basic-proof.json",
        "305d28edd6ebe84be3e6dca73a69f5fdabf944109f834f3be53a184d486803e2",
    ):
        assert marker in text
    assert "private BASIC model smoke" in " ".join((ROOT / "README.md").read_text().split())


def test_runbook_distinguishes_registry_binding_from_pull_role_and_build_context():
    text = " ".join((ROOT / "skills/threadlight-deploy/references/governance/returns-mcp-demo.md").read_text().split())
    for marker in ("ContainerRegistry", "project-scoped", "ManagedIdentity", ".dockerignore",
                   "AcrPull alone", "source and destination", "not private governed returns"):
        assert marker in text
