"""#218: the PoC default is a router-ready, evidence-gated model configuration."""
from __future__ import annotations

import re
from pathlib import Path

REFS = Path(__file__).resolve().parents[1] / "references"
FINDING = "threadlight-router-bench/references/findings/2026-11-20-router-workload-post-ignite.md"


def _read(name: str) -> str:
    return (REFS / name).read_text(encoding="utf-8")


def test_foundation_template_defaults_to_router_subset_with_fixed_baseline():
    text = _read("foundation-template.md")
    block = re.search(r"```yaml\nmodel:\n(.*?)```", text, re.S).group(1)
    assert re.search(r"^  default: gpt-6-luna\b", block, re.M)
    assert re.search(r'^  version: "2026-09-22"', block, re.M)
    assert re.search(r"^  routing: router-subset\b", block, re.M)
    for name, version in (("gpt-6-luna", "2026-09-22"), ("gpt-6.1-terra", "2026-10-26"),
                          ("gpt-6.1-sol", "2026-09-29")):
        assert f'{{ name: {name}, version: "{version}" }}' in block
    assert "mode: balanced" in block
    assert "gpt-5.4" not in block


def test_model_selection_cites_evidence_and_decision_rule():
    text = _read("model-selection.md")
    assert FINDING in text
    for figure in ("0.054", "0.082", "0.147", "1.83"):
        assert figure in text, figure
    assert re.search(r"90 ?%", text) and "routing fee" in text
    assert "routing_config.mode" in text and "quality" in text
    assert re.search(r"[Nn]ever use Auto for EU", text)
    assert "x-ratelimit-key" in text and "session_id" in text
    assert "completions" in text  # GHCP BYOK wire
    for url in ("model-router-models", "model-router-configure", "model-router-agents",
                "model-router-quotas"):
        assert f"learn.microsoft.com/en-us/azure/foundry/openai/" in text and url in text, url
    assert "**Runtime starting default: `gpt-5.4`.**" not in text


def test_spec_7b_default_row_is_router_ready():
    text = _read("speckit-template.md")
    assert "| **Default for threadlight pilots" in text
    row = next(line for line in text.splitlines() if line.startswith("  | **Default for threadlight pilots"))
    assert "model-router" in row and "gpt-6-luna" in row and "gpt-5.4" not in row
