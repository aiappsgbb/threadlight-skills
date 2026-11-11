"""The ``private`` deployment profile: the same Fast-PoC pilot, promoted without rewriting.

``generate_only.py --profile private`` re-packages a design workspace (or an
already generated Fast-PoC repository, with ``--force``) for a private network:
Foundry account with public network access disabled and hosted-agent VNet
injection, private endpoints and DNS, an internal Container Apps environment for
the MCP server, Application Insights through a private link scope, an optional
Azure API Management model gateway, and an in-VNet operator host. Design-owned
files and the agent runtime stay byte-identical between the two profiles.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_generate_only import GENERATOR, PILOT, _run
from test_live_deploy_fixes import CONTAINER, FIXTURES, _pytest

PRIVATE_INFRA = PILOT / "infra-private"
DESIGN_OWNED = ("AGENTS.md", "specs/SPEC.md", "specs/manifest.json", "src/mcp/server.py")


def _copy(tmp_path: Path, fixture: str = "design-owned-mcp") -> Path:
    project = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, project)
    return project


def _gen(project: Path, tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    return _run([str(GENERATOR), "--project", str(project), *extra], tmp_path)


def _hashes(project: Path) -> dict[str, bytes]:
    return {rel: (project / rel).read_bytes() for rel in DESIGN_OWNED if (project / rel).is_file()}


@pytest.fixture(scope="module")
def private(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("private")
    project = _copy(tmp)
    result = _gen(project, tmp, "--profile", "private", "--model-gateway", "apim")
    assert result.returncode == 0, result.stdout + result.stderr
    return project


def test_default_profile_is_unchanged_fast_poc(tmp_path):
    project = _copy(tmp_path)
    assert _gen(project, tmp_path).returncode == 0
    assert (project / "infra" / "main.bicep").read_bytes() == (PILOT / "infra" / "main.bicep").read_bytes()
    assert not (project / "scripts" / "private").exists()
    assert "networking: public" in (project / "specs" / "deployment-posture.md").read_text()


def test_private_profile_uses_the_private_infra(private):
    main = (private / "infra" / "main.bicep").read_text()
    assert main == (PRIVATE_INFRA / "main.bicep").read_text()
    for module in sorted((PRIVATE_INFRA / "modules").glob("*.bicep")):
        assert (private / "infra" / "modules" / module.name).read_text() == module.read_text()
    params = json.loads((private / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["modelGateway"]["value"] == "${THREADLIGHT_MODEL_GATEWAY=apim}"
    assert params["acrBuildAllowedPrefixes"]["value"] == "${THREADLIGHT_ACR_BUILD_PREFIXES=}"
    assert "modelCapacity" in params and "jumpboxSshPublicKey" in params


def test_private_infra_closes_every_public_data_plane(private):
    main = (private / "infra" / "main.bicep").read_text()
    network = (private / "infra" / "modules" / "network.bicep").read_text()
    # Foundry: public access off, hosted-agent VNet injection on first create (Learn: can't be added later).
    assert re.search(r"publicNetworkAccess: 'Disabled'", main)
    assert "networkInjections" in main and "scenario: 'agent'" in main and "useMicrosoftManagedNetwork: false" in main
    assert "Microsoft.App/environments" in network
    # Private endpoints with DNS zone groups for Foundry, ACR, AMPLS and the gateway.
    for zone in ("privatelink.services.ai.azure.com", "privatelink.openai.azure.com",
                 "privatelink.cognitiveservices.azure.com", "privatelink.azurecr.io",
                 "privatelink.monitor.azure.com", "privatelink.azure-api.net"):
        assert zone in main + network + (private / "infra" / "modules" / "apim-gateway.bicep").read_text(), zone
    # MCP server only inside the VNet; App Insights ingestion only through the private link scope.
    assert "internal: true" in main
    assert "publicNetworkAccessForIngestion: 'Disabled'" in main
    assert "ingestionAccessMode: 'PrivateOnly'" in main
    # Only the PROJECT capability host (VNet evaluations need it; the platform creates the account host).
    assert "projects/capabilityHosts" in main and "accounts/capabilityHosts" not in main
    # Network-injected evaluations need the project identity on the account (Learn).
    assert "resource projectFoundryUser" in main
    # Scale to zero stays the default.
    assert "param mcpMinReplicas int = 0" in main


def test_gateway_url_keeps_the_openai_v1_suffix(private):
    # github-copilot-sdk 1.0.1 (type="azure") keeps only the origin of a base URL that
    # does not end in /openai/v1: "/models" was dropped and APIM answered 404 live.
    apim = (private / "infra" / "modules" / "apim-gateway.bicep").read_text()
    assert re.search(r"output gatewayUrl string = '.*/models/openai/v1'", apim)
    assert "serviceUrl: projectEndpoint" in apim


def test_operator_host_may_grant_every_role_the_hooks_grant(private):
    jump = (private / "infra" / "modules" / "jumpbox.bicep").read_text()
    hook = (private / "hooks" / "postdeploy.sh").read_text()
    granted = set(re.findall(r"ROLE_[A-Z_]+=([0-9a-f-]{36})", hook))
    assert granted, "postdeploy.sh grants a role by GUID"
    allowed = re.search(r"var grantable = '([^']+)'", jump).group(1)
    names = dict(re.findall(r"var (role\w+) = '([0-9a-f-]{36})'", jump))
    for guid in granted:
        assert any(names.get(n) == guid for n in re.findall(r"\$\{(\w+)\}", allowed)), guid


def test_acr_public_endpoint_is_off_unless_build_prefixes_are_given(private):
    main = (private / "infra" / "main.bicep").read_text()
    assert "empty(acrBuildAllowedPrefixes) ? 'Disabled' : 'Enabled'" in main
    assert "defaultAction: 'Deny'" in main
    hook = (private / "hooks" / "preprovision.sh").read_text()
    assert "list-service-tags" in hook and "AzureContainerRegistry." in hook


def test_private_azure_yaml_keeps_the_agent_and_adds_the_gateway(private, tmp_path):
    text = (private / "azure.yaml").read_text()
    assert "remoteBuild: true" in text
    assert "MODEL_GATEWAY_URL" in text
    assert "preprovision:" in text
    fast = _copy(tmp_path)
    assert _gen(fast, tmp_path).returncode == 0
    import yaml
    priv, fast_doc = yaml.safe_load(text), yaml.safe_load((fast / "azure.yaml").read_text())
    # Same services and agent definition; the private profile only adds the gateway variable and hooks.
    assert priv["services"].keys() == fast_doc["services"].keys()
    agent = next(k for k, v in priv["services"].items() if v.get("host") == "azure.ai.agent")
    p_env = priv["services"][agent].pop("environmentVariables")
    f_env = fast_doc["services"][agent].pop("environmentVariables")
    assert priv["services"] == fast_doc["services"]
    assert [e for e in p_env if e["name"] != "MODEL_GATEWAY_URL"] == f_env
    assert set(priv["hooks"]) == set(fast_doc["hooks"]) | {"preprovision"}


def test_promotion_from_fast_poc_rewrites_no_design_or_runtime_file(tmp_path):
    project = _copy(tmp_path)
    assert _gen(project, tmp_path).returncode == 0
    before = _hashes(project)
    runtime = (project / "src" / "agent" / "container.py").read_bytes()
    refused = _gen(project, tmp_path, "--profile", "private")
    assert refused.returncode == 5, "promotion must not overwrite generated files silently"
    result = _gen(project, tmp_path, "--profile", "private", "--force")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _hashes(project) == before
    assert (project / "src" / "agent" / "container.py").read_bytes() == runtime
    posture = (project / "specs" / "deployment-posture.md").read_text()
    assert "networking: private" in posture and "profile: private" in posture


def test_private_generated_tests_pass(private, tmp_path):
    result = _pytest(private, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_private_deploy_doc_explains_laptop_and_in_network_steps(private):
    doc = (private / "DEPLOY.md").read_text()
    for term in ("azd provision", "scripts/private/vnet_run.py", "Bastion", "AzureContainerRegistry",
                 "publicNetworkAccess", "from inside the network"):
        assert term in doc, term
    deferred = (private / "specs" / "deferred-steps.md").read_text()
    assert "private" in deferred.lower()
    gaps = json.loads((private / "tests" / "postdeploy-manifest.json").read_text())["gaps"]
    assert any("private network" in g for g in gaps)


def test_bad_profile_or_gateway_without_private_is_refused(tmp_path):
    project = _copy(tmp_path)
    assert _gen(project, tmp_path, "--model-gateway", "apim").returncode == 2
    assert _gen(project, tmp_path, "--profile", "nope").returncode == 2


# --- operator transport: laptop -> in-VNet host via Run Command -----------------

def _vnet_run(private: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("vnet_run", private / "scripts" / "private" / "vnet_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vnet_run_chunks_the_repository_under_the_run_command_limit(private, tmp_path):
    vr = _vnet_run(private)
    payload = os.urandom(700_000)
    scripts = vr.upload_scripts(payload, "/opt/threadlight/repo.tgz")
    assert len(scripts) > 1
    assert all(len(s.encode()) <= vr.MAX_SCRIPT_BYTES for s in scripts)
    assert "sha256sum" in scripts[-1] and vr.hashlib.sha256(payload).hexdigest() in scripts[-1]


def test_vnet_run_sync_keeps_evidence_recorded_on_the_host(private):
    # 2026-11-11: a second sync overwrote the agent/trace evidence recorded in the VNet.
    assert "--keep-newer-files" in (private / "scripts" / "private" / "vnet_run.py").read_text()


def test_vnet_run_archive_excludes_local_state(private, tmp_path):
    vr = _vnet_run(private)
    (private / ".venv").mkdir(exist_ok=True)
    (private / ".venv" / "x").write_text("x")
    (private / ".azure" / "env1").mkdir(parents=True, exist_ok=True)
    (private / ".azure" / "env1" / ".env").write_text('AZURE_ENV_NAME="env1"\n')
    names = vr.archive_names(private)
    assert not any(n.startswith(".venv") for n in names)
    assert ".azure/env1/.env" in names
    assert "azure.yaml" in names


# --- the agent runtime is the same file in both profiles ------------------------

def _provider_functions(**env):
    tree = ast.parse(CONTAINER.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_model_base_url",)]
    assert nodes, "container.py must resolve the model base URL in _model_base_url()"
    ns = {"os": os}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(CONTAINER), "exec"), ns)
    return ns["_model_base_url"]


def test_model_gateway_url_overrides_the_project_endpoint(monkeypatch):
    base = _provider_functions()
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://acct.services.ai.azure.com/api/projects/p/")
    monkeypatch.delenv("MODEL_GATEWAY_URL", raising=False)
    assert base() == "https://acct.services.ai.azure.com/api/projects/p"
    monkeypatch.setenv("MODEL_GATEWAY_URL", " https://gw.azure-api.net/models/openai/v1/ ")
    assert base() == "https://gw.azure-api.net/models/openai/v1"
    monkeypatch.setenv("MODEL_GATEWAY_URL", "")
    assert base() == "https://acct.services.ai.azure.com/api/projects/p"


@pytest.mark.skipif(shutil.which("az") is None, reason="az bicep not installed")
def test_private_bicep_builds(private, tmp_path):
    out = tmp_path / "main.json"
    result = subprocess.run(["az", "bicep", "build", "--file", str(private / "infra" / "main.bicep"),
                             "--outfile", str(out)], capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text())["resources"]
