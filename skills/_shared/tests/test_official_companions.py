"""New official companions from azure-skills v1.2.113 are linked where Threadlight uses them.

Offline documentation contract: each link is pinned at the locked tag and listed in
fixtures/official-paths.json (checked by test_threadlight_owned_ports). It does not
prove the official skills run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TAG = json.loads((ROOT / "skills/_shared/official-skills-lock.json").read_text())["tag"]
BASE = f"https://github.com/microsoft/azure-skills/blob/{TAG}/.github/plugins"


@pytest.mark.parametrize("surface,link", [
    ("skills/threadlight-deploy/SKILL.md", "azure-skills/skills/azure-quotas/SKILL.md"),
    ("skills/threadlight-production-ready/SKILL.md", "azure-advisor/skills/azure-advisor/SKILL.md"),
    ("skills/threadlight-consumption-iq/SKILL.md", "azure-cost/skills/cost-optimization/SKILL.md"),
])
def test_new_official_companion_is_linked_at_the_locked_tag(surface, link):
    assert f"{BASE}/{link}" in (ROOT / surface).read_text(encoding="utf-8")


def test_readme_names_the_new_companions_and_the_telemetry_opt_out():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    block = readme[readme.index("### Companion skills: official first"):readme.index("## Live experience")]
    for name in ("azure-quotas", "azure-advisor@azure-skills", "cost-optimization"):
        assert name in block, name
    assert "copilot plugin install azure-advisor@azure-skills" in block
    assert "AZURE_MCP_COLLECT_TELEMETRY=false" in block
    assert "SessionStart" in block and "PostToolUse" in block
