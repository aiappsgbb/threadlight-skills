from __future__ import annotations

import re
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
SKILL = TEST_DIR.parent / "SKILL.md"
TEMPLATE = TEST_DIR.parent / "references" / "speckit-template.md"


def _extract_between(text: str, start_heading: str, end_heading: str) -> str:
    pattern = re.compile(
        rf"^{re.escape(start_heading)}\n(?P<body>.*?)(?=^{re.escape(end_heading)}\n)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"could not extract section between {start_heading!r} and {end_heading!r}"
    return match.group("body")


def _manifest_example(skill_text: str) -> str:
    match = re.search(
        r"#### 5\. `specs/manifest\.json`\n.*?```json\n(?P<json>.*?)\n```",
        skill_text,
        re.DOTALL,
    )
    assert match, "manifest example JSON block not found"
    return match.group("json")


def test_speckit_template_documents_opt_in_tool_governance_contract():
    template = TEMPLATE.read_text(encoding="utf-8")
    section = _extract_between(template, "## 6. Tool Contracts", "## 7. Knowledge Sources")

    assert "Enabled only by `tool_governance.enabled: true`" in section
    assert "Use exactly one canonical tool per `###` heading." in section
    assert "Do not group multiple tools under one heading." in section
    assert "Do not invent a global allow/deny baseline." in section

    required_markers = (
        "- **Action class**:",
        "- **Decision**:",
        "- **HITL gate ID**:",
        "- **Enforcement point**:",
        "- **Policy ID**:",
        "- **Required audit fields**:",
    )
    for marker in required_markers:
        assert marker in section, f"section 6 must contain marker {marker!r}"

    assert (
        "- **Action class**: `read` | `reversible-write` | "
        "`irreversible-write` | `external-side-effect`"
    ) in section
    assert "- **Decision**: `allow` | `deny` | `conditional`" in section
    assert (
        "- **HITL gate ID**: `GATE-NNN` (required exactly when "
        "**Decision** is `conditional`; omit otherwise)"
    ) in section
    assert (
        "- **Enforcement point**: `agent-middleware` | `mcp-server` | `gateway`"
    ) in section
    assert "- **Policy ID**: stable non-empty identifier" in section
    assert (
        "- **Required audit fields**: `event_id`, `event_type`, `timestamp`, "
        "`correlation_id`, `contract_sha256`, `policy_id`, `tool_name`, "
        "`action_class`, `decision`, `enforcement_point`, `adapter_id`, "
        "`actor_id`; add `gate_id`, `approval_id` for `conditional`"
    ) in section


def test_speckit_template_section_8_requires_stable_gate_and_approval_propagation():
    template = TEMPLATE.read_text(encoding="utf-8")
    section = _extract_between(
        template,
        "## 8. Human Interaction Points",
        "## 8b. Human Interaction (Workspace UX)",
    )

    assert "- **Gate ID**: `GATE-NNN`" in section
    assert "return `approval_id` together with the original `correlation_id`" in section
    assert "correlation_id" in section
    assert "approval_id" in section


def test_design_skill_documents_tool_governance_opt_in_and_projection_rules():
    skill = SKILL.read_text(encoding="utf-8")

    assert "tool_governance.enabled: true" in skill
    assert "absence or `enabled: false` preserves all legacy behavior" in skill
    assert "every exact canonical tool appears exactly once" in skill
    assert "every tool has one explicit decision" in skill
    assert "unclassified tools are gaps, never implicit allows" in skill
    assert (
        "SPEC sections 6 and 8 are the source of truth; the manifest is a "
        "generated projection."
    ) in skill


def test_design_skill_manifest_example_includes_tool_governance_machine_contract():
    skill = SKILL.read_text(encoding="utf-8")
    example = _manifest_example(skill)

    assert '"tool_governance": {' in example
    assert example.index('"tool_governance": {') < example.index('"deployment_manifest": {')
    assert '"enabled": true' in example
    assert '"contract_version": "1.0"' in example
    assert '"source": {' in example
    assert '"tool_contracts": "specs/SPEC.md#6-tool-contracts"' in example
    assert '"action_gates": "specs/SPEC.md#8-human-interaction-points"' in example
    assert '"name": "returns_apply_decision"' in example
    assert '"decision": "conditional"' in example
    assert '"gate_id": "GATE-001"' in example
    assert '"required_audit_fields": [' in example

    for field in (
        "event_id",
        "event_type",
        "timestamp",
        "correlation_id",
        "contract_sha256",
        "policy_id",
        "tool_name",
        "action_class",
        "decision",
        "enforcement_point",
        "adapter_id",
        "actor_id",
        "gate_id",
        "approval_id",
    ):
        assert f'"{field}"' in example
