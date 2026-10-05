"""The presenter deployment pin follows the official azure-skills guide.

Frozen incumbent contracts (for example the library's returns-triage
presenter contract) keep validating against the exact legacy awesome-gbb pin.
"""
import copy
import json
from pathlib import Path

import pytest

from skills._shared import presenter
from skills._shared.tests.test_presenter import pilot, write


ROOT = Path(__file__).resolve().parents[3]
SHARED = ROOT / "skills/_shared"
LOCK = json.loads((SHARED / "official-skills-lock.json").read_text())
LEGACY = {
    "repository": "aiappsgbb/awesome-gbb",
    "commit": "2a29e084882a52e74b94def3ee9bb32b73592e60",
    "path": "skills/foundry-hosted-agents/SKILL.md",
}
DEPLOY_GUIDE = "microsoft-foundry/foundry-agent/deploy/deploy.md"


def _contract(tmp_path, guidance):
    contract = pilot(tmp_path)
    contract["deployment"]["guidance"] = guidance
    write(tmp_path, presenter.CONTRACT, contract)
    return contract


def test_pin_is_the_locked_official_foundry_deploy_guide():
    pin = json.loads((SHARED / "presenter-deployment-pin.json").read_text())
    plugin = LOCK["plugins"]["azure@azure-skills"]
    skill, _, rel = DEPLOY_GUIDE.partition("/")
    assert pin == presenter.DEPLOYMENT_PIN
    assert pin["repository"] == LOCK["repository"] == "microsoft/azure-skills"
    assert pin["tag"] == LOCK["tag"] and pin["commit"] == LOCK["commit"]
    assert pin["path"] == f"{plugin['root']}/skills/{DEPLOY_GUIDE}"
    assert pin["sha256"] == plugin["skills"][skill]["files"][rel]
    assert "awesome-gbb" not in json.dumps(pin)


def test_new_contracts_validate_with_the_official_pin(tmp_path):
    _contract(tmp_path, copy.deepcopy(presenter.DEPLOYMENT_PIN))
    presenter.load_contract(tmp_path)


def test_frozen_incumbent_legacy_pin_still_validates(tmp_path):
    _contract(tmp_path, dict(LEGACY))
    presenter.load_contract(tmp_path)
    assert LEGACY in presenter.LEGACY_DEPLOYMENT_PINS


@pytest.mark.parametrize("guidance", [
    {**LEGACY, "commit": "0" * 40},
    {**LEGACY, "path": "skills/foundry-hosted-agents/README.md"},
    {**LEGACY, "sha256": "0" * 64},
    {k: v for k, v in LEGACY.items() if k != "path"},
    {"repository": "microsoft/azure-skills", "commit": "74f27068b21b85807e35ae69ab3976756b060c03",
     "path": "skills/microsoft-foundry/SKILL.md"},
    None,
])
def test_any_other_guidance_is_rejected(tmp_path, guidance):
    official = copy.deepcopy(presenter.DEPLOYMENT_PIN)
    _contract(tmp_path, guidance)
    with pytest.raises(ValueError, match="deployment-guidance-pin-mismatch"):
        presenter.load_contract(tmp_path)
    if guidance is not None:
        _contract(tmp_path, {**official, "sha256": "0" * 64})
        with pytest.raises(ValueError, match="deployment-guidance-pin-mismatch"):
            presenter.load_contract(tmp_path)


def test_legacy_pins_are_read_only_acceptance_with_reasons():
    data = json.loads((SHARED / "presenter-deployment-pin-legacy.json").read_text())
    assert data["schema"] == "threadlight-presenter-deployment-pin-legacy/v1"
    assert [entry["guidance"] for entry in data["accepted"]] == [LEGACY]
    for entry in data["accepted"]:
        assert "new contracts" in entry["reason"]
    assert presenter.LEGACY_DEPLOYMENT_PINS == [LEGACY]


def test_docs_name_the_official_guide_and_the_native_sdk_gap():
    doc = (ROOT / "docs/presenter-ready.md").read_text()
    assert "microsoft/azure-skills" in doc and LOCK["commit"] in doc
    assert "awesome-gbb's hosted-agent guide at an immutable commit" not in doc
    assert "presenter-deployment-pin-legacy.json" in doc
    assert "references/hosted-agent/maf" in doc


def test_incumbent_adoption_guide_keeps_legacy_guidance_unrewritten():
    guide = (ROOT / "skills/threadlight-deploy/references/presenter-adoption.md").read_text()
    flat = " ".join(guide.split())
    assert "presenter-deployment-pin-legacy.json" in flat
    assert "keep `deployment.guidance` equal to that pin" not in flat
    assert "keeps its existing guidance object" in flat
    assert "Only new contracts copy" in flat
