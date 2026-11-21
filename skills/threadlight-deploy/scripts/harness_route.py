"""generate_only harness route: a container-less azd project for the Foundry Copilot harness (#229).

Used only when ``skills/_shared/agent_type_policy.py`` resolves SPEC § 11e to
``agent_type: harness`` (the user's preview opt-in, a complete ``harness_fit``
record and no disqualifier). The agent is a Foundry **prompt agent** with
``harness.type: microsoft_copilot_preview``: no agent container, no ACR build for
the agent, no BYOK. The project declares

* one ``azure.ai.skill`` service per ``src/agent/skills/<name>/SKILL.md``, attached
  to the agent so ``azd deploy --all`` pins each immutable skill version;
* one ``microsoft_copilot_toolset_preview`` block with every harness tool off by
  default and only ``filesystem_read`` (to read staged input files) on, unless
  SPEC ``harness_toolset`` names more (shell and web stay off otherwise);
* the SPEC tools as one MCP tool on the mock MCP server, reads ``never`` and
  every write ``always`` approved (``require_approval: {never: [...]}``);
* an RAI egress policy (Audit first) whose allow-list always carries the
  telemetry and Agent 365 hosts, attached as ``rai_config``;
* hooks that require ``AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true`` and grant
  Foundry User on the project to the agent instance identity.

Sources: https://learn.microsoft.com/azure/foundry/agents/how-to/use-tools-skills-copilot-harness,
https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
HARNESS_REF = HERE.parent / "references" / "harness"
HARNESS_TYPE = "microsoft_copilot_preview"
TOOLSET_TYPE = "microsoft_copilot_toolset_preview"
HARNESS_TOOLS = ("filesystem_read", "filesystem_write", "shell", "web", "subagents")
DEFAULT_TOOLSET = ("filesystem_read",)
TELEMETRY_HOSTS = (
    "*.in.applicationinsights.azure.com",
    "*.livediagnostics.monitor.azure.com",
    "settings.sdk.monitor.azure.com",
    "agent365.svc.cloud.microsoft",
)
EGRESS_MODES = ("Audit", "Enforced")
_SKILL_NAME = re.compile(r"^[a-z0-9-]{1,64}$")
_HOST = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def harness_settings(spec_scalar: Callable[[str, str], str | None], spec: str, error: type[Exception]) -> dict[str, Any]:
    """SPEC § 11e ``harness_toolset``, ``harness_egress_allow`` and ``harness_egress_mode``."""
    toolset = _csv(spec_scalar(spec, "harness_toolset")) or list(DEFAULT_TOOLSET)
    unknown = [t for t in toolset if t not in HARNESS_TOOLS]
    if unknown:
        raise error(f"SPEC section 11e harness_toolset must name harness tools from {list(HARNESS_TOOLS)}, "
                    f"got {unknown}")
    hosts = [h.lower() for h in _csv(spec_scalar(spec, "harness_egress_allow"))]
    bad = [h for h in hosts if not _HOST.match(h)]
    if bad:
        raise error(f"SPEC section 11e harness_egress_allow must list host names (optionally *.domain), got {bad}")
    mode = spec_scalar(spec, "harness_egress_mode") or "Audit"
    if mode not in EGRESS_MODES:
        raise error(f"SPEC section 11e harness_egress_mode must be Audit or Enforced, got {mode!r}")
    return {"toolset": list(dict.fromkeys(toolset)), "egress_allow": list(dict.fromkeys(hosts)), "egress_mode": mode}


def _yaml_str(value: str) -> str:
    return json.dumps(value)


def _block(text: str, indent: int) -> str:
    pad = " " * indent
    return "".join(f"{pad}{line}\n" if line.strip() else "\n" for line in text.rstrip("\n").splitlines())


def render_azure_yaml(project: str, agent: str, description: str, instructions: str, skills: list[str],
                      plan: list[dict[str, Any]], settings: dict[str, Any], marker: str,
                      error: type[Exception]) -> str:
    if "${" in instructions:
        raise error("AGENTS.md contains '${', which azd would substitute in azure.yaml; remove it for the harness route")
    for name in skills:
        if not _SKILL_NAME.match(name) or name in ("ai-project", "mcp", agent):
            raise error(f"skill directory {name!r} must be lowercase letters, digits and hyphens (max 64) and "
                        "must not collide with another azure.yaml service")
    reads = [t["name"] for t in plan if t.get("kind") != "write"]
    writes = [t["name"] for t in plan if t.get("kind") == "write"]
    lines = [
        "# yaml-language-server: $schema=https://raw.githubusercontent.com/Azure/azure-dev/main/schemas/v1.0/azure.yaml.json",
        "#",
        f"# {marker}: Foundry Copilot harness route (preview, user opt-in, foundry-only-agents.md item 2d).",
        "# The agent is a Foundry PROMPT agent on harness.type microsoft_copilot_preview: no agent container.",
        "# Export AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true before any azd command (shell and CI).",
        f"name: {project}",
        "",
        "requiredVersions:",
        "  azd: '>= 1.37.0'",
        "  extensions:",
        "    azure.ai.agents: '>=1.0.0-beta.22'",
        "",
        "services:",
        "  ai-project:",
        "    host: azure.ai.project",
        "    endpoint: ${FOUNDRY_PROJECT_ENDPOINT}",
        "",
        "  # Mock MCP tool server (auxiliary compute): the SPEC tools the agent calls.",
        "  mcp:",
        "    host: containerapp",
        "    project: .",
        "    language: docker",
        "    docker:",
        "      path: src/mcp/Dockerfile",
        "      context: .",
        "      remoteBuild: true",
        "",
    ]
    for name in skills:
        lines += [f"  {name}:", "    host: azure.ai.skill", "    uses:", "      - ai-project",
                  f"    archive: ./src/agent/skills/{name}", ""]
    lines += [
        f"  {agent}:",
        "    host: azure.ai.agent",
        "    # No code: project only anchors skills/ (azure.ai.agents resolves each local skill's",
        "    # published version from <project>/skills/<name> and SKILL_<NAME>_VERSION).",
        "    project: src/agent",
        "    uses:",
        "      - ai-project",
        *[f"      - {name}" for name in skills],
        "    kind: prompt",
        f"    name: {agent}",
        f"    description: {_yaml_str(description or f'{agent} Foundry Copilot harness agent (preview)')}",
        "    model: ${AZURE_AI_MODEL_DEPLOYMENT_NAME}",
        "    instructions: |",
        _block(instructions, 6).rstrip("\n"),
        "    harness:",
        f"      type: {HARNESS_TYPE}",
    ]
    if skills:
        lines += ["    skills:", *[f"      - {name}" for name in skills]]
    lines += [
        "    tools:",
        "      # Every harness tool off by default; SPEC 11e harness_toolset turns tools on.",
        "      # Shell is the broadest tool and is not a strict boundary (Learn).",
        f"      - type: {TOOLSET_TYPE}",
        "        defaultConfig:",
        "          enabled: false",
        "        configs:",
        *[f"          - name: {tool}\n            enabled: true" for tool in settings["toolset"]],
    ]
    if plan:
        lines += [
            "      - type: mcp",
            f"        server_label: {_yaml_str(project.replace('-', '_') + '_tools')}",
            "        server_url: https://${MCP_SERVER_FQDN}/mcp",
        ]
        if writes:
            # Learn shows {"never": [...]}; the service rejected it live (2026-11-21, "Expected object,
            # got Array") and accepted the Responses shape {"never": {"tool_names": [...]}}.
            lines += ["        # Writes are never pre-approved: the client answers mcp_approval_request.",
                      "        require_approval:",
                      "          never:", "            tool_names:", *[f"              - {name}" for name in reads]]
            if not reads:
                lines[-2:] = ["        require_approval: always"]
        else:
            lines.append("        require_approval: never")
    lines += [
        "    policies:",
        "      # RAI policy with the egress (Network) control, created by infra/harness-egress.bicep.",
        "      - type: rai_policy",
        "        raiPolicyName: ${RAI_POLICY_ID}",
        "",
        "hooks:",
        "  preprovision:",
        "    posix:",
        "      shell: sh",
        "      run: sh hooks/preflight.sh",
        "      continueOnError: false",
        "  postprovision:",
        "    posix:",
        "      shell: sh",
        "      run: sh hooks/preflight.sh && sh hooks/postprovision.sh",
        "      continueOnError: false",
        "  predeploy:",
        "    posix:",
        "      shell: sh",
        "      run: sh hooks/preflight.sh",
        "      continueOnError: false",
        "  # Foundry User on the project for the agent instance identity (file-ID inputs, returned files).",
        "  postdeploy:",
        "    posix:",
        "      shell: sh",
        "      run: sh hooks/postdeploy.sh",
        "      continueOnError: false",
        "",
        "infra:",
        "  provider: bicep",
        "  path: infra",
        "",
    ]
    return "\n".join(lines)


_EGRESS_MODULE = """
// Foundry Copilot harness egress policy (generate_only harness route).
@description('Copilot harness egress mode: Audit first, then Enforced.')
@allowed([ 'Audit', 'Enforced' ])
param harnessEgressMode string = 'Audit'

@description('Hosts the harness sandbox may reach directly, besides the telemetry hosts.')
param harnessEgressAllowHosts array = []

// Account child writes conflict (RequestConflict, live 2026-11-21): create the
// policy only after the project and the model deployment.
module harnessEgress 'harness-egress.bicep' = {
  name: 'harness-egress'
  dependsOn: [ project, modelDeployments, router ]
  params: {
    accountName: account.name
    mode: harnessEgressMode
    allowHosts: harnessEgressAllowHosts
  }
}

output RAI_POLICY_ID string = harnessEgress.outputs.id
output HARNESS_EGRESS_MODE string = harnessEgressMode
"""


def render_main_bicep(pilot_main: str) -> str:
    for needle in ("resource account 'Microsoft.CognitiveServices/accounts@", "resource project '",
                   "resource modelDeployments '", "resource router '"):
        if needle not in pilot_main:
            raise ValueError(f"pilot infra/main.bicep changed shape: no {needle!r}")
    return pilot_main.rstrip("\n") + "\n" + _EGRESS_MODULE


def parameters(params: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    # The pilot default is a model router subset (#218); model router support in
    # the Copilot harness (preview) is not documented, so the harness uses one
    # fixed model deployment (AZURE_AI_MODEL_NAME) unless the operator opts in.
    params["parameters"]["modelStrategy"] = {"value": "${AZURE_AI_MODEL_STRATEGY=fixed}"}
    params["parameters"]["harnessEgressMode"] = {"value": f"${{THREADLIGHT_HARNESS_EGRESS_MODE={settings['egress_mode']}}}"}
    params["parameters"]["harnessEgressAllowHosts"] = {"value": settings["egress_allow"]}
    return params


def render_agent_tests(agent: str, skills: list[str], settings: dict[str, Any], plan: list[dict[str, Any]]) -> str:
    """Offline tests shipped with the generated pilot (run by generate_only_selfcheck)."""
    writes = sorted(t["name"] for t in plan if t.get("kind") == "write")
    return f'''"""{agent}: Foundry Copilot harness definition (generated, offline)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DOC = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))
AGENT = DOC["services"]["{agent}"]


def test_prompt_agent_on_the_copilot_harness():
    assert AGENT["host"] == "azure.ai.agent" and AGENT["kind"] == "prompt"
    assert AGENT["harness"] == {{"type": "{HARNESS_TYPE}"}}
    assert AGENT["project"] == "src/agent"
    for field in ("docker", "language", "protocols", "container"):
        assert field not in AGENT


def test_toolset_is_off_by_default_with_only_the_spec_tools_on():
    toolsets = [t for t in AGENT["tools"] if t["type"] == "{TOOLSET_TYPE}"]
    assert len(toolsets) == 1 and toolsets[0]["defaultConfig"] == {{"enabled": False}}
    enabled = sorted(c["name"] for c in toolsets[0]["configs"] if c["enabled"])
    assert enabled == {sorted(settings["toolset"])!r}


def test_skills_are_services_pinned_into_the_agent():
    assert AGENT.get("skills", []) == {skills!r}
    for name in {skills!r}:
        assert DOC["services"][name]["host"] == "azure.ai.skill"
        assert (ROOT / DOC["services"][name]["archive"] / "SKILL.md").is_file()


def test_writes_always_need_approval_and_egress_policy_is_attached():
    mcp = [t for t in AGENT["tools"] if t["type"] == "mcp"]
    never = []
    if mcp:
        approval = mcp[0]["require_approval"]
        never = (approval.get("never") or {{}}).get("tool_names", []) if isinstance(approval, dict) \
            else ([] if approval == "always" else ["*"])
    assert not set(never) & set({writes!r})
    assert AGENT["policies"] == [{{"type": "rai_policy", "raiPolicyName": "${{RAI_POLICY_ID}}"}}]


def test_telemetry_hosts_are_always_in_the_egress_allow_list():
    egress = (ROOT / "infra" / "harness-egress.bicep").read_text(encoding="utf-8")
    for host in {list(TELEMETRY_HOSTS)!r}:
        assert f"'{{host}}'" in egress
'''


def render_deploy_md(project: str, agent: str, skills: list[str], settings: dict[str, Any]) -> str:
    on = ", ".join(f"`{t}`" for t in settings["toolset"])
    extra = ", ".join(f"`{h}`" for h in settings["egress_allow"]) or "none"
    return f"""# Deploy {project}: Foundry Copilot harness agent (preview)

<!-- {project}: generated by threadlight-deploy generate-only (harness route) -->

The SPEC opted in to the Foundry **Microsoft Copilot harness** (preview,
user opt-in, `skills/_shared/foundry-only-agents.md` item 2d). `{agent}` is a
Foundry **prompt agent** with `harness.type: {HARNESS_TYPE}`: Foundry runs the
agent loop; there is no agent container. The mock MCP tool server still builds
in ACR (`remoteBuild: true`).

## Deploy

```sh
export AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true   # every shell and CI job
azd env new {project}-dev
azd env set AZURE_LOCATION swedencentral              # a documented harness region
azd provision
azd deploy --all
azd ai agent show {agent} --output json               # harness.type, skills[name+version], rai_config
```

- Skills ({len(skills)}): {", ".join(f"`{s}`" for s in skills) or "none"}; each deploy publishes and pins
  an immutable version in the agent.
- Harness tools on: {on}; every other harness tool (shell, web, subagents,
  filesystem write) stays off unless SPEC § 11e `harness_toolset` names it.
- Egress: RAI policy `harness-egress` in **{settings["egress_mode"]}** mode, default Deny. Allowed: the
  telemetry and Agent 365 hosts ({", ".join(f"`{h}`" for h in TELEMETRY_HOSTS)}) plus {extra}.
  Move to Enforced with `azd env set THREADLIGHT_HARNESS_EGRESS_MODE Enforced && azd provision`, then
  test from a **new** conversation.
- `hooks/postdeploy.sh` grants Foundry User on the project to the agent instance identity. Without it a
  file passed by Foundry file ID is silently skipped. Wait up to five minutes, then use a new conversation.
- Invoke through the agent endpoint, always with a conversation (`azd ai agent invoke --new-conversation`).
- TPM: a model 429 fails the whole harness response; size the deployment at concurrent conversations x ~300K
  tokens (`threadlight-qualify` harness block).

## Not covered by this route

- `evals/run_evals.py` still builds the hosted Invocations body; harness evals need the Responses
  template input (tracked separately).
- Consequential writes are MCP tools that always need approval (`mcp_approval_request`); ACS/Agent Hooks
  do not run inside the harness, so the hosted LOCAL-14/CTK governance evidence does not apply here.
"""
