"""Documentation contracts distinguish attestation scope from runtime/live proof."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_signed_evidence_documents_transport_consent_sources_and_limits():
    doc = (ROOT / "docs/signed-evidence.md").read_text()
    for text in (
        "threadlight-evidence+jwt", "governance_evidence", "threadlight/evidence",
        "signed-evidence", "Evidence Provider", "sequenceDiagram", "insufficient_evidence",
        "CAS", "not OBO", "fixture", "retention", "outcome_unknown",
        "GHCP deferred", "native local", "immediate revocation",
    ):
        assert text in doc
    for relative in (
        "skills/threadlight-govern/references/gateway/README.md",
        "skills/threadlight-deploy/references/governance/README.md",
        "skills/threadlight-deploy/references/governance/returns-mcp-demo.md",
    ):
        assert "signed-evidence.md" in (ROOT / relative).read_text()


def test_pinned_deployment_gate_executes_evidence_regressions():
    runner = (ROOT / "scripts/ci/run-governance-pin-tests.py").read_text()
    for filename in (
        "test_evidence_attestations.py", "test_evidence_gateway.py",
        "test_returns_evidence.py", "test_evidence_generation.py",
        "test_adversarial_evidence.py",
    ):
        assert filename in runner


def test_existing_deep_dive_and_dated_record_explain_adversarial_evidence():
    for name in ("agent-governance-deep-dive.md", "governed-returns-validation.md"):
        text = (ROOT / "docs" / name).read_text()
        for term in ("GPT-5.4", "Evidence Provider", "synthetic", "adversarial", "JWT"):
            assert term in text, (name, term)
    text = (ROOT / "docs/agent-governance-deep-dive.md").read_text()
    assert "The conversation changes. Authorization does not." in text
    assert "not a security boundary" in text
    assert "alternative unmediated path" in text
