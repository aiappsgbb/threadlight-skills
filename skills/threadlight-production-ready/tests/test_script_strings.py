"""Gate static strings in production_ready.py against staleness."""
import importlib.util
import pathlib
import re
import sys
import unittest
import pytest
import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_ready.py"
REPO = ROOT.parents[1]


@pytest.mark.parametrize("surface", [
    "README.md", "docs/production-readiness.md",
    "docs/IDEA-TO-PRODUCTION-WORKBOOK.md", "docs/index.html", "docs/production.html",
])
def test_public_runtime_ontology_and_scope(surface):
    text = (REPO / surface).read_text()
    for term in ("SAFE", "method", "ACS", "PDP", "Agent Hooks", "host/interceptor",
                 "PEP", "AGT", "toolkit", "ASSERT", "assurance", "unbound", "unverified"):
        assert term in text, f"{surface}: missing runtime boundary {term}"
    assert "governance-manifest" in text
    for overclaim in ("governed-ready agent", "become a governed agent",
                      "governed/comprehensive/hardened", "AGT in-process middleware"):
        assert overclaim not in text, f"{surface}: stale whole-agent claim"


@pytest.mark.parametrize("skill,required", [
    ("govern", ("build_bundle", "generate.py", "signed", "offline")),
    ("governed-actions", ("generate.py", "--gate", "LOCAL-14", "47", "unbound")),
    ("safe-check", ("--subscription", "governance_probe_noop", "business bindings", "fresh")),
    ("production-ready", ("governance-manifest/v1", "legacy", "signed", "unbound")),
])
def test_pressure_routing_uses_real_producers_without_promoting_assessment(skill, required):
    # Read-only pressure scenarios: ship quickly after green CI; missing approval;
    # reuse a previous run; certify a business write from a noop. No agent/Azure calls.
    text = (REPO / f"skills/threadlight-{skill}/SKILL.md").read_text()
    description = yaml.safe_load(text.split("---", 2)[1])["description"]
    assert description.strip().startswith("Use when")
    assert len(description) < 650
    for term in required:
        assert term in text, f"{skill}: pressure scenario lacks {term}"
    assert "not whole-agent" in text
    assert "never becomes the interceptor, never writes a" not in text


def test_agent_guidance_and_migration_are_published():
    guidance = REPO / "AGENTS.md"
    assert guidance.exists(), "Coding agents need current source and evidence boundaries"
    text = guidance.read_text()
    assert "governance-upstream-pin.json" in text
    assert "governance_probe_noop" in text
    assert "RED" in text
    assert "legacy v2" in (REPO / "docs/production-readiness.md").read_text()

_spec = importlib.util.spec_from_file_location("production_ready", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["production_ready"] = _mod
_spec.loader.exec_module(_mod)


class ScriptStrings(unittest.TestCase):
    def test_no_stale_v050_deferred_reference(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn(
            "deferred to v0.5.0",
            text,
            "Stale string at ~L528 — ADO/GitLab are now deferred to v0.6.0+.",
        )

    def test_v060_deferred_reference_present(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("v0.6.0", text)

    def test_skill_md_finding_count_matches_catalog(self):
        """SKILL.md advertises how many findings the assessor scores against.

        It had drifted to 151 against a catalog of 171 before this guard
        existed, so the headline number was understating the tool by twenty
        checks. Every finding added from here on has to update the sentence.
        """
        skill_md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r"scores it against (\d+) findings", skill_md)
        self.assertIsNotNone(
            m, "SKILL.md no longer states a finding count — update this guard"
        )
        self.assertEqual(
            int(m.group(1)),
            len(_mod.FINDING_CATALOG),
            "SKILL.md finding count is stale vs FINDING_CATALOG",
        )


if __name__ == "__main__":
    unittest.main()
