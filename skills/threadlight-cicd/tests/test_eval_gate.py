"""Both generated workflows consume the same portable strict evidence gate."""
import copy
import importlib.util
from pathlib import Path

import pytest

from test_release_acceptance import document, NOW, START
from test_verified_pipeline import generator, framing


@pytest.mark.parametrize("platform", generator.SUPPORTED_PLATFORMS)
@pytest.mark.parametrize("domain", ["evals", "redteam"])
@pytest.mark.parametrize("case", ["complete", "empty", "partial", "contradictory", "missing_capability"])
def test_generated_canonical_consumer_enforces_actual_evidence(tmp_path, platform, domain, case):
    generator.generate(framing(platform), tmp_path)
    path = tmp_path / ".threadlight/skills/threadlight-production-ready/scripts/evidence_gate.py"
    spec = importlib.util.spec_from_file_location("copied_release_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = copy.deepcopy(document(domain))
    if case == "empty":
        data = {}
    elif case == "partial":
        data["verdict"] = "partial"
    elif case == "contradictory":
        data["must_fix"] = ["required"]
    elif case == "missing_capability":
        data["capabilities"].pop(next(iter(data["capabilities"])))
    if case == "complete":
        assert module.validate_release_manifest(domain, data, now=NOW, not_before=START)["status"] == "pass"
    else:
        with pytest.raises(ValueError):
            module.validate_release_manifest(domain, data, now=NOW, not_before=START)


@pytest.mark.parametrize("platform", generator.SUPPORTED_PLATFORMS)
def test_required_quality_checks_are_not_continue_on_error(tmp_path, platform):
    generator.generate(framing(platform), tmp_path)
    path = ".github/workflows/azd-deploy-prod.yml" if platform == "github-actions" else "azure-pipelines.yml"
    text = (tmp_path / path).read_text()
    assert "continue-on-error" not in text and "continueOnError" not in text
    assert "release_runner.py validate" in text
    assert "promotion" in text
    assert "echo \"Run threadlight" not in text
