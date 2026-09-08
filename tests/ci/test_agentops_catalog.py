import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_agentops_catalog_versions_and_ownership_are_consistent():
    plugin = json.loads((ROOT / "plugin.json").read_text())
    marketplace = json.loads((ROOT / ".github/plugin/marketplace.json").read_text())
    assert plugin["version"] == marketplace["metadata"]["version"] == "2.1.0"
    assert marketplace["plugins"][0]["version"] == plugin["version"]
    assert "agentops" in plugin["keywords"]
    for filename in ("README.md", "THREADLIGHT.md", "docs/production-readiness.md"):
        text = (ROOT / filename).read_text()
        assert "threadlight-agentops" in text, filename
        assert "agentops.yaml" in text, filename
        assert "AOPS-001" in text, filename
    assert "24 total" in plugin["description"]


def test_auto_agentops_is_read_only_and_preserves_selected_binding_gates():
    skill = (ROOT / "skills/threadlight-auto/SKILL.md").read_text()
    state = (ROOT / "skills/threadlight-auto/references/state-schema.md").read_text()
    assert "threadlight-agentops" in skill
    assert "no native eval or Doctor" in skill
    assert "governed_actions_gate" in skill and "governance_probe" in skill
    assert "specs/agentops-manifest.json" in state
