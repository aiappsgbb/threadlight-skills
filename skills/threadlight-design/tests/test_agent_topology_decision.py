"""One agent with skills vs many agents is a recorded design decision.

Customers moving a pilot towards production ask whether the process should stay one
agent with skills or be split into several agents. The design step must ask it,
recommend single-agent unless a hard split trigger holds, and record the answer and
its criteria in SPEC § 11e so the choice is reviewable. Text contract only: it does
not prove that any multi-agent runtime was generated or deployed.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGN = REPO_ROOT / "skills" / "threadlight-design"
SKILL = DESIGN / "SKILL.md"
TEMPLATE = DESIGN / "references" / "speckit-template.md"
GUIDE = DESIGN / "references" / "process-traits.md"

CRITERIA = (
    "latency_hops", "sessions", "quota", "failure_points", "cost",
    "ownership_boundary", "governance_boundary", "eval_complexity",
)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _section_11e() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index("## 11e. Workflow Model")
    end = text.index("## 11f.", start)
    return text[start:end]


def _topology_block() -> dict:
    section = _section_11e()
    blocks = re.findall(r"```yaml\n(.*?)```", section, re.S)
    for block in blocks:
        data = yaml.safe_load(block)
        if isinstance(data, dict) and "agent_topology" in data:
            return data["agent_topology"]
    raise AssertionError("SPEC § 11e has no agent_topology YAML block")


def test_spec_template_records_topology_decision_and_criteria():
    topo = _topology_block()
    assert topo["decision"] == "single-agent-with-skills"
    assert topo["split_triggers"] == []
    assert topo["inter_agent_protocol"] == "none"
    assert set(CRITERIA) <= set(topo["criteria"]), sorted(set(CRITERIA) - set(topo["criteria"]))
    for key in ("rationale", "source", "agents"):
        assert key in topo, key
    section = _flat(_section_11e())
    for marker in (
        "single-agent-with-skills | multi-agent", "a2a | maf-workflow-in-hosted-agent",
        "references/process-traits.md", "security-compliance-boundary",
        "separate-owning-team", "independent-release-cadence",
    ):
        assert marker in section, marker


def test_design_step_asks_and_recommends_single_agent_by_default():
    text = _flat(SKILL.read_text(encoding="utf-8"))
    start = text.index("One agent with skills or several agents?")
    question = text[start:start + 3000]
    for marker in (
        "agent_topology", "§ 11e", "references/process-traits.md",
        "Recommend `single-agent-with-skills`", "split trigger",
        "separate SPEC", "open item",
    ):
        assert marker in question, marker


def test_guide_cites_current_foundry_multi_agent_support():
    text = _flat(GUIDE.read_text(encoding="utf-8"))
    for url in (
        "https://learn.microsoft.com/azure/cloud-adoption-framework/ai-agents/single-agent-multiple-agents",
        "https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent",
        "https://learn.microsoft.com/azure/foundry/agents/how-to/enable-agent-to-agent-endpoint",
        "https://learn.microsoft.com/azure/foundry/agents/concepts/workflow",
        "https://learn.microsoft.com/azure/foundry/openai/how-to/quota",
    ):
        assert url in text, url
    for fact in (
        "Connected Agents", "December 1, 2026", "A2A protocol 1.0",
        "Hosted agents aren't supported in the workflow designer",
        "not documented",
    ):
        assert fact in text, fact
    for criterion in CRITERIA:
        assert f"`{criterion}`" in text, criterion
    # Threadlight generates one hosted agent per SPEC; it must not claim to generate
    # the inter-agent wiring.
    assert "does not generate" in text
