"""Governed-write action mode: one governed write shown live in a workshop.

``"action_mode": "governed-write"`` sits between recommend-only and the full
Production path: every tool is an unbound read except exactly one write bound
to the governed tool gateway on the default GHCP runtime. These are offline
design-consistency checks, not runtime or live evidence.
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _skill_section() -> str:
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("#### Governed-write pilot (workshop)")
    end = min(i for i in (text.find("\n#### ", start + 10), text.find("\n---", start)) if i > 0)
    return _flat(text[start:end])


def test_skill_documents_the_governed_write_mode():
    section = _skill_section()
    for marker in (
        '"action_mode": "governed-write"', "mode: selective", "exactly one", "governed-tool-gateway",
        "pre_tool_call", "authorization", "signed-policy-bundle", "decision-receipt",
        "idempotency-or-transaction", "approval_mode: deferred", "approval_requirement: policy",
        "threadlight-review-action", "Policy rules", "Downstream service", "POST", "GET",
        "Governance foundation", "threadlight-govern", "github-copilot-sdk", "unbound read",
        "validate_governance_contract.py",
    ):
        assert marker in section, marker
    assert "never requires `approval`" in section


def _template_variant() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index("#### Workshop governed-write variant")
    end = text.index("\n### ", start)
    return text[start:end]


def _workspace(tmp_path: Path, variant: str) -> Path:
    section6 = ("## 6. Tool Contracts\n\n### `returns_get_case`\n- **Side Effects**: none\n\n"
                "### `return_policy_search`\n- **Side Effects**: none\n\n"
                "### `returns_record_recommendation`\n- **Side Effects**: sets the recommendation on the case\n\n")
    body = variant.split("\n", 1)[1]
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text(
        "# SpecKit\n\n" + section6 + "## 11. Security\n\n### 11a. Runtime Governance Contract\n\n" + body)
    (tmp_path / "specs" / "manifest.json").write_text(json.dumps(
        {"name": "returns-triage", "mode": "fast-poc", "action_mode": "governed-write"}))
    return tmp_path


def test_template_governed_write_variant_is_consistent(tmp_path):
    pytest.importorskip("yaml")
    from skills._shared.action_mode import governed_write_gaps, governed_write_tool

    workspace = _workspace(tmp_path, _template_variant())
    assert governed_write_gaps(workspace) == []
    assert governed_write_tool(workspace) == "returns_record_recommendation"


def _validate(workspace: Path):
    return subprocess.run([sys.executable, str(VALIDATOR), str(workspace)], capture_output=True, text=True)


def test_validator_accepts_a_consistent_governed_write_workspace(tmp_path):
    result = _validate(_workspace(tmp_path, _template_variant()))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "action_mode=governed-write" in result.stdout


def test_validator_rejects_a_governed_write_without_signed_policy(tmp_path):
    variant = _template_variant().replace("signed-policy-bundle, ", "")
    result = _validate(_workspace(tmp_path, variant))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "governed-write" in result.stdout and "signed-policy-bundle" in result.stdout


@pytest.mark.parametrize("path", [
    "skills/threadlight-deploy/SKILL.md", "skills/threadlight-deploy/references/generate-only-mode.md",
    "skills/threadlight-safe-check/SKILL.md", "skills/threadlight-auto/SKILL.md",
    "skills/threadlight-local-test/SKILL.md",
])
def test_consumers_document_the_governed_write_mode(path):
    assert '"action_mode": "governed-write"' in _flat((REPO_ROOT / path).read_text(encoding="utf-8")), path
