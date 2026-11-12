"""The kit folds its business API and publisher into the generated pilot azd project."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

EXAMPLE = Path(__file__).resolve().parents[1]
CATALOG = EXAMPLE.parents[1]
PILOT = CATALOG / "skills/threadlight-deploy/references/hosted-agent/ghcp/references/pilot"
GENERATE = CATALOG / "skills/threadlight-deploy/references/governance/generate.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bicep_build(source, output):
    compiler = shutil.which("bicep")
    command = [compiler, "build"] if compiler else ["az", "bicep", "build", "--file"]
    result = subprocess.run([*command, str(source), "--outfile", str(output)],
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text())


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "pilot"
    shutil.copytree(PILOT, root)
    load(GENERATE, "kit_generate").compose_infra(root)  # what `generate.py foundation` adds
    return root


def test_compose_folds_business_and_publisher_into_one_provision(project, tmp_path):
    load(EXAMPLE / "scripts/compose_project.py", "kit_compose").compose(project)
    azure = yaml.safe_load((project / "azure.yaml").read_text())
    for name, context in (("returns-business", "src/returns-business"),
                          ("govern-publisher", "src/govern-publisher")):
        service = azure["services"][name]
        assert service["project"] == context and (project / context / "Dockerfile").is_file()
        assert service["docker"]["remoteBuild"] is True
        assert service["docker"]["buildArgs"][0].startswith("PYTHON_IMAGE=mcr.microsoft.com/")
        assert "@sha256:" in service["docker"]["buildArgs"][0]
        # Images are published (azd publish) and bound by digest; never azd-deployed.
        assert "exit 1" in service["hooks"]["predeploy"]["run"]
    assert azure["services"]["mcp"]["docker"]["remoteBuild"] is True
    assert (project / "src/mcp/server.py").read_bytes() == (EXAMPLE / "src/mcp/server.py").read_bytes()
    assert (project / "src/govern-publisher/publish_policy.py").is_file()
    assert (project / "src/govern-publisher/vendor/control-plane/pyproject.toml").is_file()
    assert (project / "src/returns-business/source-package.json").is_file()
    parameters = json.loads((project / "infra/main.parameters.json").read_text())["parameters"]
    assert "returnsCosmosUrl" not in parameters
    assert parameters["businessImage"] == {"value": "${RETURNS_BUSINESS_IMAGE=}"}
    assert parameters["publisherImage"] == {"value": "${GOVERN_PUBLISHER_IMAGE=}"}
    assert json.loads((project / "infra/business-config.json").read_text()) == {}
    template = bicep_build(project / "infra/main.bicep", tmp_path / "main.json")
    resources = template["resources"]
    resources = resources.values() if isinstance(resources, dict) else resources
    business = next(r for r in resources if r.get("name") == "returns-business")
    assert business["type"] == "Microsoft.Resources/deployments"
    assert "governance" in json.dumps(business.get("dependsOn", []))
    mcp = next(r for r in resources if r.get("type") == "Microsoft.App/containerApps")
    assert "returns-business" in json.dumps(mcp["properties"]["template"]["containers"])
    assert {"RETURNS_COSMOS_URL", "RETURNS_BUSINESS_URL"} <= set(template["outputs"])


def test_compose_requires_the_foundation_and_runs_once(tmp_path, project):
    compose = load(EXAMPLE / "scripts/compose_project.py", "kit_compose").compose
    bare = tmp_path / "bare"
    shutil.copytree(PILOT, bare)
    with pytest.raises(ValueError, match="run_generate_foundation_first"):
        compose(bare)
    compose(project)
    with pytest.raises(ValueError, match="already_composed"):
        compose(project)
