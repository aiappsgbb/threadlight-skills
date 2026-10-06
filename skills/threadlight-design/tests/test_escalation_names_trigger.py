"""Escalation outputs must name the specific entity that triggered them (kyc EDD residual)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "references" / "speckit-template.md").read_text(encoding="utf-8")
KYC = (ROOT / "references" / "domains" / "fsi-kyc-aml.md").read_text(encoding="utf-8")
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")


def test_speckit_business_rules_require_naming_the_matched_entity():
    rules = TEMPLATE.split("## 3. Business Rules", 1)[1].split("## 4. Data Models", 1)[0]
    assert "**Output**" in rules
    assert "name the matched" in rules.lower()


def test_kyc_edd_rule_names_the_matched_pep_or_related_party():
    row = next(line for line in KYC.splitlines() if line.startswith("| **EDD Trigger**"))
    assert "name the matched PEP or related party" in row
    assert "relationship" in row


def test_design_stage_carries_the_rule_into_agent_instructions():
    assert "name the matched entity" in SKILL
