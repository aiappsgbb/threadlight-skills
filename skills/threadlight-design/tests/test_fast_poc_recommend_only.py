"""Fast-PoC designs are recommend-only (user decision 2026-11-09).

The 1-hour workshop runs design -> Pattern 0 -> deploy without threadlight-govern,
so a Fast-PoC that selected governance for a write tool deployed that write
unenforced (safe-check: ``selected-governance-not-packaged``). A Fast-PoC now
exposes only read tools: the agent proposes the decision with citations and a
person records or applies it. Governance is recorded as not selected
(``mode: off``) with reads unbound; Full mode and the Production path keep the
governance interview.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGN = REPO_ROOT / "skills" / "threadlight-design"
SKILL = DESIGN / "SKILL.md"
TEMPLATE = DESIGN / "references" / "speckit-template.md"
VALIDATOR = DESIGN / "scripts" / "validate_governance_contract.py"
WORKSHOP = REPO_ROOT / "docs" / "WORKSHOP-1H-QUICKSTART.md"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _fast_poc_section() -> str:
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("#### Fast-PoC is recommend-only")
    end = text.index("\n### ", start)
    return _flat(text[start:end])


def test_skill_makes_fast_poc_recommend_only():
    section = _fast_poc_section()
    for marker in (
        '"action_mode": "recommend-only"', "mode: off", "consequence: read", "enforcement_path: none",
        "SPEC § 6", "SPEC § 8", "a person records", "no write tool", "Production path",
        "threadlight-govern", "Full mode", "src/mcp/server.py", "tests/killer-prompts.md",
        "tests/eval_dataset.jsonl", "validate_governance_contract.py",
    ):
        assert marker in section, marker
    # Never an unbound consequential write, never a selected-but-unpackaged binding.
    assert "never an unbound consequential" in section.lower()


def test_skill_keeps_governance_interview_for_full_mode():
    text = _flat(SKILL.read_text(encoding="utf-8"))
    assert "Governance interview/output contract (mandatory whenever tools exist)" in text
    assert re.search(r"Full mode .{0,200}governance interview", text), "Full mode keeps governance"


def _recommend_only_block() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index("#### Fast-PoC (recommend-only) variant")
    match = re.search(r"```yaml\n([\s\S]*?)\n```", text[start:])
    assert match, "recommend-only § 11a example block"
    return match.group(1)


def test_template_recommend_only_block_is_a_valid_off_contract(tmp_path):
    pytest.importorskip("yaml")
    sys.path.insert(0, str(REPO_ROOT))
    from skills._shared.governance import validate_governance_contract
    from skills._shared.governance_selection import spec_contract

    # Parsed as consumers parse a SPEC: YAML 1.1 `off` stays the string "off".
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text("```yaml\n" + _recommend_only_block() + "\n```\n")
    document = spec_contract(tmp_path)[0]
    normalized = validate_governance_contract(document, deployment_target="demo-sandbox")
    assert normalized["governance"]["mode"] == "off"
    assert normalized["tools"] and all(t["consequence"] == "read" and t["enforcement_path"] == "none"
                                       for t in normalized["tools"])


def _workspace(tmp_path: Path, *, write: bool) -> Path:
    tools = ["returns_get_case", "return_policy_search"] + (["returns_record_recommendation"] if write else [])
    section6 = "".join(f"### `{t}`\n- **Description**: tool.\n\n" for t in tools)
    entries = "".join(
        f"  - id: {t}\n    consequence: {'write' if t.endswith('recommendation') else 'read'}\n"
        "    policy_binding: none\n    enforcement_path: none\n    intervention_points: []\n"
        "    safe_principles: [least-privilege]\n    requires: []\n" for t in tools)
    spec = ("# SpecKit\n\n## 6. Tool Contracts\n\n" + section6 + "## 11. Security\n\n"
            "### 11a. Runtime Governance Contract\n\n```yaml\nframework: github-copilot-sdk\ngovernance:\n"
            "  mode: off\n  environment_modes: {development: evaluate_only, staging: evaluate_only, "
            "preproduction: enforce, production: enforce}\n  lifecycle_bindings: []\ntools:\n" + entries + "```\n")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text(spec)
    (tmp_path / "specs" / "manifest.json").write_text(json.dumps(
        {"name": "returns-triage", "mode": "fast-poc", "action_mode": "recommend-only"}))
    return tmp_path


def _validate(workspace: Path):
    return subprocess.run([sys.executable, str(VALIDATOR), str(workspace)], capture_output=True, text=True)


def test_validator_accepts_a_consistent_recommend_only_workspace(tmp_path):
    result = _validate(_workspace(tmp_path, write=False))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "recommend-only" in result.stdout


def test_validator_rejects_an_unbound_write_in_a_recommend_only_workspace(tmp_path):
    result = _validate(_workspace(tmp_path, write=True))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "returns_record_recommendation" in result.stdout and "recommend-only" in result.stdout


def test_workshop_expects_a_recommendation_not_a_recorded_write():
    text = _flat(WORKSHOP.read_text(encoding="utf-8"))
    assert "recommend-only" in text
    assert "approve or reject it in the page" not in text
    assert re.search(r"proposes? the decision", text)


def test_validator_rejects_a_plain_heading_write_in_a_recommend_only_workspace(tmp_path):
    workspace = _workspace(tmp_path, write=False)
    spec = workspace / "specs" / "SPEC.md"
    spec.write_text(spec.read_text().replace(
        "## 11. Security",
        "### returns_apply_decision\n- **Description**: persist the outcome.\n- **Side Effects**: writes the case.\n\n"
        "## 11. Security"))
    result = _validate(workspace)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "returns_apply_decision" in result.stdout
