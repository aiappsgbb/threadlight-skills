"""Tests for the SDD brief target of export_agentic_loop_spec.py.

The published Foundry Agentic Loop plugin (``foundry-agentic-loop@awesome-copilot``)
starts SDD from a self-contained ``brief`` and its Specify phase writes the EARS
``docs/spec.md`` and ``.azure/deployment-plan.md`` itself. The default export is
therefore a brief; the lean Spec2Cloud ``docs/spec.md`` stays available as the
``spec2cloud`` target for legacy five-stage workspaces. Every test writes only
under ``tmp_path``. These are offline shape checks, not an Agentic Loop run.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "export_agentic_loop_spec.py"
REPO_ROOT = TEST_DIR.parents[2]
RETURNS_SPEC = REPO_ROOT / "examples" / "returns-triage-governed" / "specs" / "SPEC.md"
BRIEF_GOLDEN = SKILL_DIR / "references" / "agentic-loop-export" / "returns-triage.brief.md"
LEGACY_GOLDEN = SKILL_DIR / "references" / "agentic-loop-export" / "returns-triage.spec.md"

BRIEF_HEADINGS = [
    "Solution",
    "Goals and non-goals",
    "Users and scenarios",
    "Functional requirements and human gates",
    "Non-functional requirements",
    "Process flow",
    "Azure services selected by the design",
    "Agents, models and grounding",
    "Data and seed facts",
    "Tools, integrations and triggers",
    "Decisions already taken",
    "Left to the Agentic Loop",
    "Assumptions and open questions",
    "Appendix — Threadlight provenance",
]


def _load():
    spec = importlib.util.spec_from_file_location("export_agentic_loop_spec_brief", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exporter():
    return _load()


@pytest.fixture(scope="module")
def brief(exporter):
    return exporter.export_brief(RETURNS_SPEC.read_text(encoding="utf-8"))


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    assert match, f"missing section {heading}"
    return match.group(1)


def test_brief_title_and_sections(brief):
    assert brief.splitlines()[0] == "# Returns Triage — Agentic Loop SDD brief"
    assert re.findall(r"^## (.+)$", brief, flags=re.M) == BRIEF_HEADINGS


def test_brief_says_how_to_start_the_published_plugin(brief):
    head = brief.split("\n## ", 1)[0]
    assert "copilot plugin install foundry-agentic-loop@awesome-copilot" in head
    assert "SDD" in head and "brief" in head
    assert "docs/spec.md" in head and ".azure/deployment-plan.md" in head
    assert "source of truth" in head


def test_rules_gates_and_scenarios_are_carried_for_ears(brief):
    frs = _section(brief, "Functional requirements and human gates")
    for br in ("BR-001", "BR-002", "BR-003", "BR-004", "BR-005"):
        assert br in frs
    assert "Supervisor escalation review" in frs
    assert "EARS" in frs
    users = _section(brief, "Users and scenarios")
    assert "S-004" in users and "escalate_to_supervisor" in users
    assert "evaluation" in users.lower()


def test_tools_data_and_nfrs_are_carried(brief):
    tools = _section(brief, "Tools, integrations and triggers")
    assert "returns_apply_decision" in tools and "Side effects" in tools
    data = _section(brief, "Data and seed facts")
    for entity in ("### orders", "### returns", "### customers"):
        assert entity in data
    nfr = _section(brief, "Non-functional requirements")
    assert "managed identity" in nfr.lower() and "EU" in nfr
    ai = _section(brief, "Agents, models and grounding")
    assert "gpt-5.4" in ai


def test_brief_has_no_legacy_loop_vocabulary(brief):
    assert "plan stage" not in brief
    assert "policy layer" not in brief
    assert "Spec2Cloud" not in brief
    assert "scenarios in § 3" not in brief and "human gates in § 4" not in brief


def test_decisions_and_handoff_are_explicit(brief):
    taken = _section(brief, "Decisions already taken")
    assert "Microsoft Foundry" in taken and "hosted agent" in taken
    left = _section(brief, "Left to the Agentic Loop")
    for item in ("framework", "EARS", "implementation contract", "workstreams", "RBAC", "placement"):
        assert item in left, item
    appendix = _section(brief, "Appendix — Threadlight provenance")
    assert "not carried" in appendix and "Value Model" in appendix
    assert "does not inherit" in appendix


def test_brief_is_deterministic_and_matches_committed_example(exporter, brief):
    source = RETURNS_SPEC.read_text(encoding="utf-8")
    assert exporter.export_brief(source) == brief
    assert BRIEF_GOLDEN.read_text(encoding="utf-8") == brief


def test_minimal_spec_brief_marks_gaps(exporter):
    out = exporter.export_brief("# SpecKit: Tiny\n\n## 1. Process Overview\n\n**Description**: A tiny process.\n")
    assert "A tiny process." in out and "[NEEDS CLARIFICATION:" in out


def test_legacy_spec2cloud_target_is_unchanged(exporter):
    assert exporter.export(RETURNS_SPEC.read_text(encoding="utf-8")) == LEGACY_GOLDEN.read_text(encoding="utf-8")


def test_cli_defaults_to_the_brief_and_keeps_the_legacy_target(tmp_path):
    default = subprocess.run([sys.executable, str(SCRIPT), str(RETURNS_SPEC)], capture_output=True, text=True)
    assert default.returncode == 0, default.stderr
    assert default.stdout == BRIEF_GOLDEN.read_text(encoding="utf-8")
    legacy = subprocess.run([sys.executable, str(SCRIPT), str(RETURNS_SPEC), "--target", "spec2cloud"],
                            capture_output=True, text=True)
    assert legacy.returncode == 0, legacy.stderr
    assert legacy.stdout == LEGACY_GOLDEN.read_text(encoding="utf-8")
    out = tmp_path / "docs" / "threadlight-brief.md"
    first = subprocess.run([sys.executable, str(SCRIPT), str(RETURNS_SPEC), "-o", str(out)], capture_output=True, text=True)
    assert first.returncode == 0 and out.read_text(encoding="utf-8").startswith("# Returns Triage — Agentic Loop SDD brief")
    again = subprocess.run([sys.executable, str(SCRIPT), str(RETURNS_SPEC), "-o", str(out)], capture_output=True, text=True)
    assert again.returncode != 0
