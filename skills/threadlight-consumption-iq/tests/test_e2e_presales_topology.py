"""
End-to-end golden test for the **repo-free, per-phase topology** path.

This is the scenario the pre-sales mode is sold for and that the original
`test_e2e_presales.py` could NOT exercise: a rollout profile that declares its
own resource topology *per phase*, so the estimate runs with **no Bicep, no
`azd` env, no discovery** at all — and the topology *evolves* across phases (the
land-and-expand SKU step: AI Search Basic in the POC → S1 in expansion → S2 2x2
once it's business-wide).

It drives the in-process orchestrator (`estimate.emit_presales`) with
`resources=None` — the orchestrator must source every phase's resources from the
rollout's own `resources[]` blocks. A deterministic fallback pricing client and a
pinned timestamp keep the goldens stable.

To refresh the golden files after an intentional change:

    CONSUMPTION_IQ_REGENERATE_GOLDEN=1 \
        python3 -m pytest skills/threadlight-consumption-iq/tests/test_e2e_presales_topology.py -v

Then `git diff` the fixture and commit if the new output is correct.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
FIXTURE_DIR = SKILL_ROOT / "references" / "fixtures" / "sample-presales-topology-rollout"
EXPECTED_DIR = FIXTURE_DIR / "expected"

sys.path.insert(0, str(SCRIPTS_DIR))

from estimate import emit_presales  # noqa: E402
from rollout import load_rollout_profile  # noqa: E402


PINNED_TIMESTAMP = "2026-06-22T12:00:00+00:00"
PINNED_DEPLOY_REF = "sample-presales-topology-rollout/estimate-test"
REGENERATE = os.environ.get("CONSUMPTION_IQ_REGENERATE_GOLDEN") == "1"


class DeterministicPricingClient:
    """Always returns (None, fallback) so projectors use hardcoded matrices."""

    def get_price(self, resource_kind, sku):
        return {"unit_price_usd": None, "unit": None, "price_source": "fallback",
                "fetched_at": None, "azure_meter_id": None, "raw": {}}

    def warm(self, resource):
        return None


def _run(tmp_path: Path) -> dict[str, Path]:
    rollout = load_rollout_profile(FIXTURE_DIR / "rollout.json")
    report = tmp_path / "cost-estimate.md"
    manifest = tmp_path / "cost-estimate-manifest.json"
    onepager = tmp_path / "estimate-onepager.html"
    # resources=None on purpose: the entire topology must come from the rollout.
    emit_presales(
        rollout,
        None,
        DeterministicPricingClient(),
        report_path=report,
        manifest_path=manifest,
        onepager_path=onepager,
        audience="internal",
        pdf=False,
        deploy_ref=PINNED_DEPLOY_REF,
        generated_at=PINNED_TIMESTAMP,
    )
    return {"manifest": manifest, "report": report, "onepager": onepager}


def _check(actual_text: str, golden_name: str):
    expected_path = EXPECTED_DIR / golden_name
    if REGENERATE:
        EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(actual_text)
        pytest.skip(f"regenerated {expected_path} (REGENERATE=1)")
    assert actual_text == expected_path.read_text(), (
        f"{golden_name} drifted from golden. If intentional, regenerate via: "
        "CONSUMPTION_IQ_REGENERATE_GOLDEN=1 python3 -m pytest "
        f"{Path(__file__).relative_to(SKILL_ROOT.parent.parent)}"
    )


def test_topology_manifest_matches_golden(tmp_path):
    paths = _run(tmp_path)
    _check(paths["manifest"].read_text(), "cost-estimate-manifest.json")


def test_topology_markdown_matches_golden(tmp_path):
    paths = _run(tmp_path)
    _check(paths["report"].read_text(), "cost-estimate.md")


def test_topology_onepager_matches_golden(tmp_path):
    paths = _run(tmp_path)
    _check(paths["onepager"].read_text(), "estimate-onepager.html")


def test_topology_evolves_across_phases(tmp_path):
    """The whole point of Fix A: each phase is projected with ITS OWN declared
    topology, so the AI Search tier steps Basic -> S1 -> S2 across the rollout."""
    data = json.loads(_run(tmp_path)["manifest"].read_text())

    def _search_tier(phase_id: str) -> str:
        phase = next(p for p in data["phases"] if p["id"] == phase_id)
        search = next(r for r in phase["resources"]
                      if r["resource_kind"] == "Microsoft.Search/searchServices")
        return search["current_sku"]["tier"]

    assert _search_tier("poc") == "basic"
    assert _search_tier("expansion") == "S1"
    assert _search_tier("business-wide") == "S2"

    # And the AI Search monthly cost is strictly increasing as the tier steps up.
    def _search_cost(phase_id: str) -> float:
        phase = next(p for p in data["phases"] if p["id"] == phase_id)
        search = next(r for r in phase["resources"]
                      if r["resource_kind"] == "Microsoft.Search/searchServices")
        return float(search["monthly_cost_usd"])

    assert _search_cost("poc") < _search_cost("expansion") < _search_cost("business-wide")


def test_topology_estimate_is_repo_free(tmp_path):
    """A sanity assertion that the fixture itself declares the topology — i.e.
    this golden genuinely exercises the no-discovery path."""
    rollout = json.loads((FIXTURE_DIR / "rollout.json").read_text())
    assert all("resources" in ph for ph in rollout["phases"])
