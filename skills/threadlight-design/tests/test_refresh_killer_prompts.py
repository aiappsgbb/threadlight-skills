"""The killer-prompts keeper script targets agent.yaml and fails clearly before deploy.

threadlight-design emits references/templates/refresh_killer_prompts.py as
infra/scripts/refresh_killer_prompts.py. Its SKILL.md "Target file
clarification" says STARTER_* env vars live in agent.yaml, and the canonical
returns-triage example keeps its region markers there.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO = SKILL_DIR.parents[1]
TEMPLATE = SKILL_DIR / "references" / "templates" / "refresh_killer_prompts.py"
EXAMPLE = REPO / "examples" / "returns-triage-governed"


def _workspace(tmp_path: Path, with_agent_yaml: bool) -> Path:
    ws = tmp_path / "pilot"
    (ws / "infra" / "scripts").mkdir(parents=True)
    (ws / "tests").mkdir()
    shutil.copy(TEMPLATE, ws / "infra" / "scripts" / "refresh_killer_prompts.py")
    shutil.copy(EXAMPLE / "tests" / "killer-prompts.md", ws / "tests" / "killer-prompts.md")
    if with_agent_yaml:
        shutil.copy(EXAMPLE / "agent.yaml", ws / "agent.yaml")
    return ws


def _run(ws: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ws / "infra" / "scripts" / "refresh_killer_prompts.py"), *args],
                          capture_output=True, text=True, cwd=ws)


def test_template_targets_agent_yaml_like_the_skill_and_example():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert 'TARGET = REPO_ROOT / "agent.yaml"' in text
    assert 'REPO_ROOT / "azure.yaml"' not in text


def test_template_keeps_the_example_agent_yaml_in_sync(tmp_path):
    ws = _workspace(tmp_path, with_agent_yaml=True)
    before = (ws / "agent.yaml").read_text(encoding="utf-8")
    result = _run(ws, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "agent.yaml" in result.stdout
    assert (ws / "agent.yaml").read_text(encoding="utf-8") == before


def test_missing_agent_yaml_explains_the_next_step_without_a_traceback(tmp_path):
    ws = _workspace(tmp_path, with_agent_yaml=False)
    result = _run(ws)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "agent.yaml" in result.stderr and "threadlight-deploy" in result.stderr
