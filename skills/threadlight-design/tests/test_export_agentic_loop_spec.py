"""Tests for export_agentic_loop_spec.py — Threadlight SPEC -> Agentic Loop docs/spec.md.

The exporter lets a Threadlight design drive an Agentic Loop / Spec2Cloud run
(plan -> implement -> verify -> deploy) without retyping the specification. The
target shape is the lean Spec2Cloud ``specify`` template that the Agentic Loop
policy layer post-processes. Every test writes only under ``tmp_path``.
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
GOLDEN = SKILL_DIR / "references" / "agentic-loop-export" / "returns-triage.spec.md"

# Section headings of the lean Spec2Cloud specify template
# (Azure-Samples/Spec2Cloud plugins/lean-spec2cloud/skills/specify/resources/
# spec-template.md at 6ea82279af854b7bb447f49376be0789f8ccedf3).
LEAN_HEADINGS = [
    "1. Summary",
    "2. Goals & Non-Goals",
    "3. Users & Scenarios",
    "4. Functional Requirements",
    "5. Non-Functional Requirements",
    "6. Architecture Overview",
    "7. Tech Stack",
    "8. Azure Services",
    "9. AI / Foundry",
    "10. Data Model",
    "11. Interfaces",
    "12. Open Questions",
]


def _load():
    spec = importlib.util.spec_from_file_location("export_agentic_loop_spec", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_agentic_loop_spec"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exporter():
    assert SCRIPT.is_file(), f"missing exporter {SCRIPT}"
    return _load()


@pytest.fixture(scope="module")
def returns_export(exporter):
    return exporter.export(RETURNS_SPEC.read_text(encoding="utf-8"))


def _h2(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, flags=re.M)


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    assert match, f"missing section {heading}"
    return match.group(1)


def test_title_and_date_come_from_the_spec(returns_export):
    first = returns_export.splitlines()[0]
    assert first == "# Returns Triage — Specification"
    assert "> **Last updated:** 2026-07-07" in returns_export


def test_lean_template_sections_in_order(returns_export):
    assert _h2(returns_export)[: len(LEAN_HEADINGS)] == LEAN_HEADINGS


def test_every_business_rule_becomes_a_traceable_requirement(returns_export):
    frs = _section(returns_export, "4. Functional Requirements")
    for br in ("BR-001", "BR-002", "BR-003", "BR-004", "BR-005"):
        assert br in frs, f"{br} not traceable in functional requirements"
    rows = re.findall(r"^\| FR-\d{3} \|", frs, flags=re.M)
    assert len(rows) >= 5
    assert "MUST" in frs


def test_human_gates_survive_the_export(returns_export):
    frs = _section(returns_export, "4. Functional Requirements")
    assert "Supervisor escalation review" in frs
    assert "human" in frs.lower()
    interfaces = _section(returns_export, "11. Interfaces")
    assert "returns_apply_decision" in interfaces
    assert "Side effects" in interfaces


def test_scenarios_and_data_model_are_carried(returns_export):
    users = _section(returns_export, "3. Users & Scenarios")
    assert "| Persona | Scenario | Success Criteria |" in users
    assert "S-004" in users and "escalate_to_supervisor" in users
    data = _section(returns_export, "10. Data Model")
    for entity in ("### orders", "### returns", "### customers"):
        assert entity in data


def test_ai_and_services_reflect_the_design(returns_export):
    ai = _section(returns_export, "9. AI / Foundry")
    assert "gpt-5.4" in ai
    assert "Foundry IQ" in ai or "foundry-iq" in ai
    services = _section(returns_export, "8. Azure Services")
    assert "Azure Cosmos DB" in services
    assert "Azure AI Search" in services
    assert "Doc" not in services  # doc-intel is not selected in § 11c


def test_non_functional_requirements_keep_security_and_residency(returns_export):
    nfr = _section(returns_export, "5. Non-Functional Requirements")
    assert "managed identity" in nfr.lower()
    assert "EU" in nfr
    assert "< 60s" in nfr or "60s" in nfr


def test_open_questions_and_assumptions_are_tracked(returns_export):
    questions = _section(returns_export, "12. Open Questions")
    assert "| # | Question | Owner | Status |" in questions
    assert "Real return-policy thresholds" in questions
    assert "open" in questions


def test_provenance_names_what_is_not_carried(returns_export):
    assert "## Appendix — Threadlight provenance" in returns_export
    appendix = returns_export.split("## Appendix — Threadlight provenance", 1)[1]
    assert "Value Model" in appendix
    assert "not carried" in appendix
    assert "source of truth" in appendix


def test_export_is_deterministic(exporter):
    source = RETURNS_SPEC.read_text(encoding="utf-8")
    assert exporter.export(source) == exporter.export(source)


def test_committed_example_matches_a_fresh_export(returns_export):
    assert GOLDEN.is_file(), "commit the generated returns-triage example"
    assert GOLDEN.read_text(encoding="utf-8") == returns_export


def test_minimal_spec_yields_placeholders_not_crashes(exporter):
    out = exporter.export("# SpecKit: Tiny\n\n## 1. Process Overview\n\n**Description**: A tiny process.\n")
    assert _h2(out)[: len(LEAN_HEADINGS)] == LEAN_HEADINGS
    assert "[NEEDS CLARIFICATION:" in out
    assert "A tiny process." in out


def test_cli_refuses_to_overwrite_without_force(tmp_path):
    out = tmp_path / "docs" / "spec.md"
    cmd = [sys.executable, str(SCRIPT), str(RETURNS_SPEC), "-o", str(out)]
    first = subprocess.run(cmd, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert out.read_text(encoding="utf-8").startswith("# Returns Triage")
    out.write_text("keep me\n", encoding="utf-8")
    second = subprocess.run(cmd, capture_output=True, text=True)
    assert second.returncode != 0
    assert out.read_text(encoding="utf-8") == "keep me\n"
    forced = subprocess.run(cmd + ["--force"], capture_output=True, text=True)
    assert forced.returncode == 0, forced.stderr
    assert out.read_text(encoding="utf-8").startswith("# Returns Triage")
