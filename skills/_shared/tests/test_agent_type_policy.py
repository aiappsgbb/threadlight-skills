"""SPEC agent_type resolution against runtime-policy.json (harness opt-in and disqualifiers).

The Foundry Copilot harness is preview: it resolves to ``harness`` only on the user's
explicit, preview-acknowledged opt-in with a complete ``harness_fit`` record and no
disqualifier. Everything else resolves to the hosted default.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from skills._shared import agent_type_policy as atp  # noqa: E402

FIT = """harness_fit:
  citadel_model_path_required: false
  acs_in_process_pep_required: false
  foundry_memory_required: false
  voice_frontend_required: false
  region: swedencentral
  consequential_write_seam: none
"""
OPT_IN = ("agent_type: harness\nharness_opt_in_source: user\n"
          "harness_opt_in_evidence: \"User: use the Copilot harness\"\npreview_acknowledged: true\n" + FIT)


def _spec(block: str) -> str:
    return f"# SPEC\n\n## 11e. Workflow Model\n\n```yaml\n{block}workflow_model: agent\n```\n"


def test_clean_opt_in_resolves_to_harness():
    result = atp.resolve(_spec(OPT_IN))
    assert result == {"requested": "harness", "agent_type": "harness", "reasons": [], "disqualifiers": []}


@pytest.mark.parametrize("value", ["none", "function-tool", "governed-mcp"])
def test_supported_write_seams_do_not_disqualify(value):
    result = atp.resolve(_spec(OPT_IN.replace("consequential_write_seam: none", f"consequential_write_seam: {value}")))
    assert result["agent_type"] == "harness", result


def test_policy_disqualifiers_are_the_only_ones_evaluated():
    ids = [d["id"] for d in atp.load_policy()["agent_hosting"]["harness_disqualifiers"]]
    result = atp.resolve(_spec("agent_type: harness\n"))
    assert set(result["disqualifiers"]) == set(ids)


def test_hosted_prompt_and_voice_pass_through():
    assert atp.resolve(_spec("agent_type: hosted\n"))["agent_type"] == "hosted"
    assert atp.resolve(_spec(""))["agent_type"] == "hosted"
    assert atp.resolve(_spec("agent_type: prompt\n"))["requested"] == "prompt"
    assert atp.resolve(_spec("agent_type: voice\n"))["requested"] == "voice"


def test_region_match_is_case_and_space_insensitive():
    result = atp.resolve(_spec(OPT_IN.replace("region: swedencentral", "region: Sweden Central")))
    assert result["agent_type"] == "harness", result


def test_fit_verdict_is_independent_of_the_opt_in_and_lists_reasons():
    assert atp.fit_verdict(None)["verdict"] == "not-assessed"
    fit = {"read_mostly": False, "citadel_model_path_required": False, "acs_in_process_pep_required": False,
           "foundry_memory_required": False, "voice_frontend_required": False, "region": "westeurope",
           "consequential_write_seam": "governed-mcp"}
    clean = atp.fit_verdict(fit)
    assert clean["verdict"] == "fit" and clean["reasons"] == [] and len(clean["advisories"]) == 1
    hit = atp.fit_verdict({**fit, "region": "eastus2"})
    assert hit["verdict"] == "not-fit" and hit["disqualifiers"] == ["region-not-supported"]
    md = atp.verdict_markdown(hit)
    assert md.startswith("**Harness fit verdict: not-fit**")
    assert "region-not-supported" in md and "| disqualifier |" in md and "| advisory |" in md
