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
    priv["services"]["mcp"].pop("resourceName")
    agent = next(k for k, v in priv["services"].items() if v.get("host") == "azure.ai.agent")
    p_env = priv["services"][agent].pop("environmentVariables")
    f_env = fast_doc["services"][agent].pop("environmentVariables")
    assert priv["services"] == fast_doc["services"]
    private_only = {"MODEL_GATEWAY_URL", "APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL"}
    assert [e for e in p_env if e["name"] not in private_only] == f_env
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


def test_vnet_run_traces_the_command_without_expanding_it(private, tmp_path):
    # Review finding: echo "+ <cmd>" ran $(...) twice and aborted on unset variables.
    vr = _vnet_run(private)
    command = 'azd env set X "$(date +%s)" && echo $UNSET_VAR \\'
    script = vr.build_run_script("/opt/threadlight/p", command, ".threadlight/run.log")
    trace = next(line for line in script.splitlines() if "+ azd env set X" in line)
    assert trace.strip().startswith("printf") and "'+ azd env set X" in trace
    assert "set -u" not in script
    probe = tmp_path / "trace.sh"
    probe.write_text(trace.strip().removesuffix("&&").rstrip() + "\n")
    out = subprocess.run(["sh", str(probe)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "+ " + command


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

def _provider_functions():
    tree = ast.parse(CONTAINER.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_model_base_url"]
    assert nodes, "container.py must resolve the model base URL in _model_base_url()"
    ns = {"os": os}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(CONTAINER), "exec"), ns)
    return ns["_model_base_url"]


def test_model_gateway_url_overrides_the_project_endpoint(monkeypatch):
    base = _provider_functions()
    project = "https://acct.services.ai.azure.com/api/projects/p"
    monkeypatch.delenv("MODEL_GATEWAY_URL", raising=False)
    assert base(project) == project
    monkeypatch.setenv("MODEL_GATEWAY_URL", " https://gw.azure-api.net/models/openai/v1/ ")
    assert base(project) == "https://gw.azure-api.net/models/openai/v1"
    monkeypatch.setenv("MODEL_GATEWAY_URL", "")
    assert base(project) == project


def _function_source(name: str) -> str:
    tree = ast.parse(CONTAINER.read_text(encoding="utf-8"))
    return ast.unparse(next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and n.name == name))


def test_gateway_routes_only_model_calls_not_project_calls():
    # Review finding: the toolbox MCP URL must stay on the project endpoint.
    assert "_MODEL_BASE_URL" in _function_source("_get_provider")
    assert "_MODEL_BASE_URL" not in _function_source("_toolbox_tools")
    assert "_BYOK_ENDPOINT" in _function_source("_toolbox_tools")
    assert "MODEL_GATEWAY_URL" not in _function_source("_init_byok").replace("_model_base_url", "")


@pytest.mark.skipif(shutil.which("az") is None, reason="az bicep not installed")
def test_private_bicep_builds(private, tmp_path):
    out = tmp_path / "main.json"
    result = subprocess.run(["az", "bicep", "build", "--file", str(private / "infra" / "main.bicep"),
                             "--outfile", str(out)], capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text())["resources"]


# --- 2026-11-12 follow-up: telemetry, data services, external gateway, landing zone ----------

def test_private_profile_disables_sdk_statsbeat(private):
    # Live 2026-11-11: the exporter's statsbeat (Microsoft usage data, westus-0 ingestion) can't
    # resolve under a private-only link scope and retried on every start; Learn: usage data
    # collection doesn't support Private Link.
    import yaml
    doc = yaml.safe_load((private / "azure.yaml").read_text())
    agent = next(v for v in doc["services"].values() if v.get("host") == "azure.ai.agent")
    env = {e["name"]: e["value"] for e in agent["environmentVariables"]}
    assert env.get("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL") == "true"


def _select(project: Path, *modules: str) -> None:
    spec = project / "specs" / "SPEC.md"
    text = spec.read_text()
    for module in modules:
        row = re.search(rf"^\| `{module}` \| (yes|no) \|.*$", text, re.M)
        if row:
            text = text.replace(row.group(0), row.group(0).replace(f"| {row.group(1)} |", "| yes |", 1))
        else:
            text = text.replace("| `app-insights` |", f"| `{module}` | yes | test |\n| `app-insights` |", 1)
    spec.write_text(text)


def test_private_profile_provisions_selected_data_services_privately(tmp_path):
    project = _copy(tmp_path)
    _select(project, "ai-search", "storage-blob", "key-vault")
    assert _gen(project, tmp_path, "--profile", "private").returncode == 0
    params = json.loads((project / "infra" / "main.parameters.json").read_text())["parameters"]
    for flag in ("enableAiSearch", "enableStorage", "enableKeyVault"):
        assert params[flag]["value"] is True, flag
    data = (project / "infra" / "modules" / "data-services.bicep").read_text()
    for group, zone in (("searchService", "privatelink.search.windows.net"), ("blob", "privatelink.blob."),
                        ("vault", "privatelink.vaultcore.azure.net")):
        assert f"groupId: '{group}'" in data and zone in data
    assert data.count("publicNetworkAccess: 'Disabled'") + data.count("publicNetworkAccess: 'disabled'") == 3
    deferred = (project / "specs" / "deferred-steps.md").read_text()
    unprovisioned = re.search(r"^\| SPEC § 11c modules (.*?) \| not provisioned", deferred, re.M)
    for module in ("ai-search", "storage-blob", "key-vault"):
        assert not unprovisioned or module not in unprovisioned.group(1), module
    assert "provisioned privately" in deferred


def test_fast_poc_still_defers_data_services(tmp_path):
    project = _copy(tmp_path)
    _select(project, "ai-search", "storage-blob")
    assert _gen(project, tmp_path).returncode == 0
    assert "not provisioned" in (project / "specs" / "deferred-steps.md").read_text()
    assert not (project / "infra" / "modules").exists()


def test_external_gateway_takes_the_customer_url(tmp_path):
    project = _copy(tmp_path)
    assert _gen(project, tmp_path, "--model-gateway", "external").returncode == 2
    assert _gen(project, tmp_path, "--profile", "private", "--model-gateway", "external").returncode == 0
    params = json.loads((project / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["modelGateway"]["value"] == "${THREADLIGHT_MODEL_GATEWAY=external}"
    assert params["modelGatewayUrl"]["value"] == "${THREADLIGHT_MODEL_GATEWAY_URL=}"
    main = (project / "infra" / "main.bicep").read_text()
    assert "modelGateway == 'external' ? modelGatewayUrl" in main
    assert "THREADLIGHT_MODEL_GATEWAY_URL" in (project / "DEPLOY.md").read_text()


def test_landing_zone_mode_reuses_the_customer_vnet_and_central_dns(private):
    params = json.loads((private / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["existingVnetId"]["value"] == "${THREADLIGHT_EXISTING_VNET_ID=}"
    assert params["dnsZonesResourceGroup"]["value"] == "${THREADLIGHT_DNS_ZONES_RG=}"
    assert params["dnsZoneLinkVnetId"]["value"] == "${THREADLIGHT_DNS_LINK_VNET_ID=}"
    main = (private / "infra" / "main.bicep").read_text()
    assert "module network 'modules/network.bicep' = if (empty(existingVnetId))" in main
    assert "landingZone" in main


def test_landing_zone_contract_is_documented():
    doc = (PILOT.parents[3] / "private-profile.md").read_text()
    for term in ("THREADLIGHT_EXISTING_VNET_ID", "snet-agent", "Microsoft.App/environments",
                 "THREADLIGHT_DNS_ZONES_RG", "privatelink.search.windows.net"):
        assert term in doc, term


def test_selectors_are_read_from_a_level_3_section_11c(tmp_path):
    # threadlight-design writes "### 11c." under "## 11."; generate_only only read "## 11c."
    # and silently ignored the selected AI Search and Storage (2026-11-12, returns-triage).
    project = _copy(tmp_path)
    spec = project / "specs" / "SPEC.md"
    spec.write_text(spec.read_text().replace("## 11c. Tech Stack", "### 11c. Tech Stack"))
    assert _gen(project, tmp_path, "--profile", "private").returncode == 0
    params = json.loads((project / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["enableAiSearch"]["value"] is True and params["enableStorage"]["value"] is True
    fast = _copy(tmp_path / "fast")
    (fast / "specs" / "SPEC.md").write_text((fast / "specs" / "SPEC.md").read_text().replace(
        "## 11c. Tech Stack", "### 11c. Tech Stack"))
    assert _gen(fast, tmp_path).returncode == 0
    assert "`ai-search`" in (fast / "specs" / "deferred-steps.md").read_text()


def test_mcp_service_targets_its_own_container_app(private):
    # Live 2026-11-12: a second private pilot in the same resource group made `azd deploy`
    # fail ("found '2' host resources" tagged azd-service-name: mcp).
    import yaml
    doc = yaml.safe_load((private / "azure.yaml").read_text())
    assert doc["services"]["mcp"]["resourceName"] == "${MCP_CONTAINER_APP_NAME}"
    assert "output MCP_CONTAINER_APP_NAME string = mcp.name" in (private / "infra" / "main.bicep").read_text()
