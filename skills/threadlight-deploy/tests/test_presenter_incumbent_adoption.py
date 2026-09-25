"""Consume the existing presenter fixtures/validator; no replacement runtime."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from skills._shared import presenter
from skills._shared.tests.test_presenter import (
    evidence, orchestrator, pilot, skip_legacy_probes, write,
)


ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "skills/threadlight-deploy/references/presenter-adoption.md"


def test_adoption_release_versions_and_limits():
    for skill, version in {
        "threadlight-deploy": "1.9.0",
        "threadlight-workspace-ui": "1.1.1",
        "threadlight-demo-data-factory": "1.0.1",
    }.items():
        text = (ROOT / "skills" / skill / "SKILL.md").read_text()
        assert yaml.safe_load(text.split("---", 2)[1])["metadata"]["version"] == version
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert "### Incumbent presenter adoption and web artifact preflight" in changelog
    section = changelog.split("### Incumbent presenter adoption and web artifact preflight", 1)[1].split("\n### ", 1)[0]
    for marker in ("2.11.0", "1.9.0", "1.1.1", "1.0.1",
                   "10-second", "not image-runtime or hosted proof"):
        assert marker in section


def test_adoption_guidance_preserves_authorities_and_incumbent():
    text = GUIDE.read_text()
    for required in (
        "specs/presenter-contract.json", "presenter-deployment-pin.json",
        "explicit opt-in", "Do not regenerate", "UNKNOWN",
        "day-after", "image-runtime", "recorded-not-independently-attested",
        "two compatible processes", "expiry", "frozen",
    ):
        assert required in text
    for consumer in ("threadlight-deploy", "threadlight-workspace-ui",
                     "threadlight-demo-data-factory"):
        assert "presenter-adoption.md" in (
            ROOT / "skills" / consumer / "SKILL.md").read_text()


def test_workspace_requires_artifact_not_development_server_proof():
    text = (ROOT / "skills/threadlight-workspace-ui/SKILL.md").read_text()
    for required in ("web_artifact_smoke.py", ".mjs", "non-root",
                     "actual image", "real server origin"):
        assert required in text


@pytest.mark.parametrize("process", ["returns", "maintenance"])
def test_day_after_does_not_dispatch_or_mutate_shell_history(tmp_path, monkeypatch, process):
    contract = pilot(tmp_path, process)
    contract["availability"]["expires_at"] = "2026-09-25T00:00:00Z"
    contract["evidence_max_age_hours"] = 72
    write(tmp_path, presenter.CONTRACT, contract)
    evidence(tmp_path, contract)
    before = {p.relative_to(tmp_path): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    real_assess = presenter.assess
    monkeypatch.setattr(presenter, "assess", lambda root, **_: real_assess(
        root, now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc)))
    report = presenter.assess(tmp_path)
    assert report["states"]["source-ready"] == "verified"
    assert report["checks"]["backend"]["status"] == "verified"
    assert not report["source_usable"] and not report["ready"]
    # Missing current backend evidence cannot dispatch a new interaction on expiry.
    evidence(tmp_path, contract, checks=["package", "deployment"])
    orch = orchestrator()
    skip_legacy_probes(monkeypatch, orch)
    dispatched = []
    result = orch.execute(tmp_path, lambda stage: dispatched.append(stage) or 0)
    assert result["status"] == "blocked" and result["stage"] == "invoke"
    assert dispatched == []
    for path, data in before.items():
        if path != Path(presenter.EVIDENCE):
            assert (tmp_path / path).read_bytes() == data
    assert (tmp_path / "src/workspace/index.html").read_bytes()
    assert (tmp_path / "evidence/backend.json").read_bytes()


@pytest.mark.parametrize("selection", [None, "default"])
def test_incumbent_without_opt_in_stays_unchanged(tmp_path, selection):
    write(tmp_path, "specs/manifest.json",
          {} if selection is None else {"delivery_profile": selection})
    assert presenter.assess(tmp_path) == {"enabled": False}


def test_malformed_source_is_not_valid_expiry(tmp_path):
    contract = pilot(tmp_path)
    contract["availability"]["expires_at"] = "not-a-date"
    write(tmp_path, presenter.CONTRACT, contract)
    assert presenter.assess(tmp_path)["states"]["source-ready"] == "blocked"
