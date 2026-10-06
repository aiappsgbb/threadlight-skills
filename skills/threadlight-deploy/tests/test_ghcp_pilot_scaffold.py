"""Generated-artifact defects found by the live2 E2E (official skills + Threadlight).

D1 GHCP Dockerfile must COPY/CMD the real entrypoint (container.py) from an MCR base.
D2 Generated MCP server: no ``parents[2]`` path walk, ``getenv() or default``, ``mcp<2``.
D3 Pilot Bicep must create Foundry account+project with identity, model, tags and
   role assignments by GUID (role *names* such as "Azure AI User" do not resolve in az 2.86).
D4 azure.yaml must use remote ACR builds; a .dockerignore must ship with the build context.
D5 Platform steps (model deploy, hosted-agent create/run, eval) delegate to microsoft-foundry.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REFS = ROOT / "skills/threadlight-deploy/references/hosted-agent/ghcp/references"
PILOT = REFS / "pilot"
DEPLOY_SKILL = ROOT / "skills/threadlight-deploy/SKILL.md"
MCP_SKILL = ROOT / "skills/threadlight-mcp-aca/SKILL.md"
AUTO_SKILL = ROOT / "skills/threadlight-auto/SKILL.md"
MAF_DOCKERFILE = ROOT / "skills/threadlight-deploy/references/hosted-agent/maf/references/docker/Dockerfile"
FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"


def read(path):
    return path.read_text(encoding="utf-8")


def from_lines(text):
    return [line.split()[1] for line in text.splitlines() if line.startswith("FROM ")]


def test_ghcp_dockerfile_uses_mcr_and_existing_entrypoint():
    text = read(REFS / "Dockerfile")
    assert all(img.startswith("mcr.microsoft.com/") for img in from_lines(text)), from_lines(text)
    assert 'CMD ["python", "container.py"]' in text
    assert (REFS / "container.py").exists()
    # skills/ and copilot-instructions.md are optional for container.py, so the
    # build must not COPY them by name; the entrypoint must still fail fast.
    assert not re.search(r"^COPY .*(skills/|copilot-instructions\.md)", text, re.M)
    assert re.search(r"^RUN test -f container\.py", text, re.M)


def test_dockerignore_ships_with_ghcp_and_pilot():
    ghcp = read(REFS / "dockerignore")
    assert ".env" in ghcp and "__pycache__" in ghcp
    pilot = read(PILOT / "dockerignore").splitlines()
    assert "*" in pilot, "repo-root build context must be an allowlist (ACR tar limit)"
    assert any(l.startswith("!src/mcp") for l in pilot)


def test_maf_and_mcp_aca_dockerfiles_use_mcr_base():
    assert all(i.startswith("mcr.microsoft.com/") for i in from_lines(read(MAF_DOCKERFILE)))
    mcp = read(MCP_SKILL)
    assert "FROM python:" not in mcp
    assert "FROM mcr.microsoft.com/oryx/python:3.12" in mcp


def test_pilot_mcp_server_has_no_path_walk_and_lazy_defaults():
    server = read(PILOT / "mcp/server.py")
    code = "\n".join(l for l in server.splitlines() if not l.lstrip().startswith("#"))
    assert "parents[" not in code
    assert not re.search(r"getenv\([^)]*,", code), "use getenv(X) or default, never eager getenv(X, default)"
    assert "SAMPLE_DATA_DIR" in code
    assert "tool=" in code, "server-side tool log lines prove the business case"


def test_mcp_dependency_is_upper_bounded_everywhere():
    pattern = re.compile(r"mcp>=1\.10(?![\d.]*,<2)")
    for path in (PILOT / "mcp/Dockerfile", DEPLOY_SKILL, MCP_SKILL):
        text = read(path)
        assert not pattern.search(text), f"{path}: pin mcp<2 (mcp 2.x removes mcp.server.fastmcp)"
    assert "mcp>=1.10,<2" in read(PILOT / "mcp/Dockerfile")


def test_pilot_bicep_is_complete_and_uses_role_guids():
    bicep = read(PILOT / "infra/main.bicep")
    for needle in (
        "Microsoft.CognitiveServices/accounts@",
        "Microsoft.CognitiveServices/accounts/projects@",
        "Microsoft.CognitiveServices/accounts/deployments@",
        "SystemAssigned",
        "param tags object",
        FOUNDRY_USER,
    ):
        assert needle in bicep, needle
    code = "\n".join(l.split("//")[0] for l in bicep.splitlines())
    assert "Azure AI User" not in code
    # deployer Foundry User at project scope
    block = re.search(r"scope: project\s*\n.*?roleFoundryUser", bicep, re.S)
    assert block, "deployer needs Foundry User at project scope"
    assert "ricchi" not in bicep
    params = json.loads(read(PILOT / "infra/main.parameters.json"))
    tags = params["parameters"]["tags"]["value"]
    assert set(tags) >= {"owner", "purpose", "disposable"}
    assert all(v.startswith("${") for v in tags.values()), "tags come from azd env, never hard-coded"


def test_pilot_azure_yaml_remote_builds_and_postdeploy_hook():
    yaml_text = read(PILOT / "azure.yaml")
    assert len(re.findall(r"^\s+remoteBuild: true", yaml_text, re.M)) == 2
    assert "host: azure.ai.agent" in yaml_text and "host: containerapp" in yaml_text
    assert "hooks/postdeploy.sh" in yaml_text
    hook = read(PILOT / "hooks/postdeploy.sh")
    assert FOUNDRY_USER in hook and "instance_identity" in hook
    assert "--role \"Azure AI User\"" not in hook and "--role 'Azure AI User'" not in hook


def test_deploy_skill_never_assigns_foundry_roles_by_name():
    text = read(DEPLOY_SKILL)
    offenders = [
        line for line in text.splitlines()
        if "role assignment create" in line and re.search(r"--role ['\"]Azure AI (User|Developer)", line)
    ]
    assert not offenders, offenders
    assert FOUNDRY_USER in text
    assert "references/hosted-agent/ghcp/references/pilot" in text


def test_auto_skill_names_the_real_entrypoint():
    text = read(AUTO_SKILL)
    assert "src/agent/main.py` runs" not in text
    assert "{main.py,container.py" not in text


def test_deploy_skill_delegates_platform_steps_to_microsoft_foundry():
    text = read(DEPLOY_SKILL)
    section = re.search(r"## Platform steps owned by `microsoft-foundry`\n(.*?)\n## ", text, re.S)
    assert section, "explicit delegation section required"
    body = section.group(1)
    for step in ("model deployment", "hosted-agent", "evaluation", "azd ai agent"):
        assert step in body, step
    assert "threadlight-safe-check" in body


def test_pilot_deployer_principal_type_follows_azd_login():
    params = read(PILOT / "infra" / "main.parameters.json")
    assert '"principalType": { "value": "${AZURE_PRINCIPAL_TYPE=User}" }' in params


def test_pilot_postdeploy_publishes_agent_fqdn_for_the_auto_deploy_probe():
    hook = read(PILOT / "hooks" / "postdeploy.sh")
    assert "azd env set AGENT_FQDN" in hook
    assert hook.index('[ -z "$pid" ]') < hook.index("azd env set AGENT_FQDN")


def test_pilot_parameters_map_mcp_image_and_model_capacity():
    """Live P3 kyc C-03/C-07: unmapped params made `azd provision` revert the MCP
    app to the quickstart image and pinned capacity to a 429-prone value."""
    params = json.loads(read(PILOT / "infra/main.parameters.json"))["parameters"]
    assert params["mcpImage"]["value"].startswith("${SERVICE_MCP_IMAGE_NAME=mcr.microsoft.com/")
    capacity = params["modelCapacity"]["value"]
    assert re.fullmatch(r"\$\{AZURE_AI_MODEL_CAPACITY=(\d+)\}", capacity), capacity
    assert int(re.search(r"=(\d+)", capacity).group(1)) >= 300
    bicep = read(PILOT / "infra/main.bicep")
    assert re.search(r"^param modelCapacity int = 300$", bicep, re.M)


def test_pilot_mcp_server_never_writes_logs_to_stdout():
    """Live P3 kyc C-06: print() to stdout corrupts the stdio JSON-RPC stream."""
    text = read(PILOT / "mcp/server.py")
    for call in re.findall(r"print\((.*)\)", text):
        assert "file=sys.stderr" in call, call
