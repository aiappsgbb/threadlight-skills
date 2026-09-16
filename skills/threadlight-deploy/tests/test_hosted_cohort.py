import hashlib
import json
import tomllib

import pytest

from test_governance_wiring import module, contract, REFERENCES


def inputs(tmp_path):
    pins = json.loads((REFERENCES.parents[2] / "_shared/governance-upstream-pin.json").read_text())
    dependencies = [name + "==" + version for name, version in pins["maf"].items()]
    dependencies += [
        "azure-ai-projects~=2.3.0", "azure-ai-agentserver-core==2.1.0b1",
        "azure-ai-agentserver-responses==2.1.0b1", "azure-ai-agentserver-invocations==1.1.0b1",
    ]
    path = tmp_path / "canonical.toml"
    path.write_text("[project]\ndependencies = " + json.dumps(dependencies))
    return {"environment": "preproduction", "hosted_cohort": {
        "file": str(path), "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}}


def test_explicit_hosted_cohort_is_hash_checked_and_contains_no_operator_path(tmp_path):
    config = inputs(tmp_path)
    result = module("generate").hosted_cohort(config, "microsoft-agent-framework")
    assert result["sha256"] == config["hosted_cohort"]["sha256"]
    assert result["pins"]["azure-ai-agentserver-core"] == "2.1.0b1"
    assert "file" not in result
    portable = module("generate").portable_configuration(
        config, contract("microsoft-agent-framework"), "microsoft-agent-framework")
    assert portable["hosted_cohort"] == result
    assert str(tmp_path) not in json.dumps(portable)


@pytest.mark.parametrize("change", ["digest", "production", "framework"])
def test_hosted_cohort_cannot_silently_replace_production_or_unverified_inputs(tmp_path, change):
    config = inputs(tmp_path)
    framework = "microsoft-agent-framework"
    if change == "digest":
        config["hosted_cohort"]["sha256"] = "sha256:" + "0" * 64
    elif change == "production":
        config["environment"] = "production"
    else:
        framework = "github-copilot-sdk"
    with pytest.raises(ValueError):
        module("generate").hosted_cohort(config, framework)


@pytest.mark.governance_runtime
def test_generation_emits_complete_frozen_cohort_without_changing_shared_pins(tmp_path):
    from test_maf_gateway_generation import gateway_inputs

    project, document, config, _, _ = gateway_inputs(tmp_path, environment="preproduction")
    config.update(inputs(tmp_path))
    shared = REFERENCES.parents[2] / "_shared/governance-upstream-pin.json"
    before = shared.read_bytes()
    module("generate").generate(project, document, configuration=config)
    agent = project / "src/agent"
    dependencies = tomllib.loads((agent / "pyproject.toml").read_text())["project"]["dependencies"]
    frozen = json.loads((agent / "governance-config.json").read_text())
    for name, version in frozen["hosted_cohort"]["pins"].items():
        assert [dep for dep in dependencies if dep.startswith(name + "==")] == [name + "==" + version]
    assert frozen["hosted_cohort"]["sha256"] == config["hosted_cohort"]["sha256"]
    assert str(tmp_path) not in json.dumps(frozen)
    assert shared.read_bytes() == before
