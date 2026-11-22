"""generate_only harness route (#229): container-less azd project for the Foundry Copilot harness.

Offline only. Live acceptance (azd deploy --all, azd ai agent show, file-ID input,
traces with egress Enforced) is recorded in the PR, not here.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from test_generate_only import FIXTURE, GENERATOR, SELFCHECK, _load_yaml, _run

FIT = """harness_fit:
  read_mostly: true
  citadel_model_path_required: false
  acs_in_process_pep_required: false
  foundry_memory_required: false
  voice_frontend_required: false
  region: swedencentral
  consequential_write_seam: governed-mcp
"""
OPT_IN = ("agent_type: harness\nharness_opt_in_source: user\n"
          "harness_opt_in_evidence: \"User: run the returns PoC on the Foundry Copilot harness, preview accepted\"\n"
          "preview_acknowledged: true\n" + FIT)
TELEMETRY = ("*.in.applicationinsights.azure.com", "*.livediagnostics.monitor.azure.com",
             "settings.sdk.monitor.azure.com", "agent365.svc.cloud.microsoft")


def _workspace(tmp_path: Path, block: str) -> Path:
    project = tmp_path / "order-returns"
    shutil.copytree(FIXTURE, project)
    spec = project / "specs" / "SPEC.md"
    spec.write_text(spec.read_text().replace("agent_type: hosted\n", block, 1), encoding="utf-8")
    return project


def _generate(tmp_path: Path, block: str = OPT_IN) -> Path:
    project = _workspace(tmp_path, block)
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    return project


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    return _generate(tmp_path_factory.mktemp("harness"))


def _agent(project: Path) -> dict:
    return _load_yaml(project / "azure.yaml")["services"]["order-returns-agent"]


def _toolset(agent: dict) -> dict:
    sets = [t for t in agent["tools"] if t["type"] == "microsoft_copilot_toolset_preview"]
    assert len(sets) == 1
    return sets[0]


def test_opted_in_harness_is_a_container_less_prompt_agent(harness):
    agent = _agent(harness)
    assert agent["host"] == "azure.ai.agent" and agent["kind"] == "prompt"
    assert agent["harness"] == {"type": "microsoft_copilot_preview"}
    assert agent["model"] == "${AZURE_AI_MODEL_DEPLOYMENT_NAME}"
    assert "Contoso" in agent["instructions"] or agent["instructions"].strip()
    assert agent["project"] == "src/agent"  # anchors skills/ for azd version resolution (live 2026-11-21)
    for field in ("docker", "protocols", "container", "environmentVariables"):
        assert field not in agent
    for rel in ("src/agent/Dockerfile", "src/agent/container.py", "src/agent/requirements.lock"):
        assert not (harness / rel).exists(), rel


def test_each_spec_skill_is_an_azure_ai_skill_service_pinned_into_the_agent(harness):
    services = _load_yaml(harness / "azure.yaml")["services"]
    assert _agent(harness)["skills"] == ["return-decision"]
    skill = services["return-decision"]
    assert skill["host"] == "azure.ai.skill" and skill["archive"] == "./src/agent/skills/return-decision"
    assert "return-decision" in _agent(harness)["uses"]


def test_shell_and_web_are_disabled_unless_the_spec_enables_them(harness, tmp_path):
    toolset = _toolset(_agent(harness))
    assert toolset["defaultConfig"] == {"enabled": False}
    assert [(c["name"], c["enabled"]) for c in toolset["configs"]] == [("filesystem_read", True)]
    opened = _generate(tmp_path, OPT_IN + "harness_toolset: filesystem_read,shell,web\n")
    enabled = {c["name"] for c in _toolset(_agent(opened))["configs"] if c["enabled"]}
    assert enabled == {"filesystem_read", "shell", "web"}


def test_unknown_harness_tool_is_rejected(tmp_path):
    project = _workspace(tmp_path, OPT_IN + "harness_toolset: filesystem_read,bash\n")
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 2 and "harness_toolset" in result.stderr


def test_writes_always_need_approval_and_reads_do_not(harness):
    mcp = [t for t in _agent(harness)["tools"] if t["type"] == "mcp"]
    assert len(mcp) == 1 and mcp[0]["server_url"] == "https://${MCP_SERVER_FQDN}/mcp"
    approval = mcp[0]["require_approval"]
    # {"never": {"tool_names": [...]}}: the array form on Learn was rejected live (2026-11-21).
    never = approval["never"]["tool_names"]
    assert set(approval) == {"never"} and "record_decision" not in never
    assert {"get_order", "get_customer"} <= set(never)


def test_egress_policy_is_attached_starts_in_audit_and_allows_telemetry(harness, tmp_path):
    assert _agent(harness)["policies"] == [{"type": "rai_policy", "raiPolicyName": "${RAI_POLICY_ID}"}]
    egress = (harness / "infra" / "harness-egress.bicep").read_text()
    for host in TELEMETRY:
        assert f"'{host}'" in egress
    assert "defaultAction: 'Deny'" in egress and "raiPolicies@2026-05-15-preview" in egress
    main = (harness / "infra" / "main.bicep").read_text()
    assert "module harnessEgress 'harness-egress.bicep'" in main and "output RAI_POLICY_ID" in main
    params = json.loads((harness / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["harnessEgressMode"]["value"] == "${THREADLIGHT_HARNESS_EGRESS_MODE=Audit}"
    assert params["harnessEgressAllowHosts"]["value"] == []
    custom = _generate(tmp_path, OPT_IN + "harness_egress_allow: learn.microsoft.com\nharness_egress_mode: Enforced\n")
    params = json.loads((custom / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["harnessEgressAllowHosts"]["value"] == ["learn.microsoft.com"]
    assert params["harnessEgressMode"]["value"] == "${THREADLIGHT_HARNESS_EGRESS_MODE=Enforced}"


def test_hooks_require_the_preview_flag_and_grant_foundry_user_to_the_agent_identity(harness):
    doc = _load_yaml(harness / "azure.yaml")
    assert "preflight.sh" in doc["hooks"]["preprovision"]["posix"]["run"]
    assert "preflight.sh" in doc["hooks"]["predeploy"]["posix"]["run"]
    assert "AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS" in (harness / "hooks" / "preflight.sh").read_text()
    post = (harness / "hooks" / "postdeploy.sh").read_text()
    assert "53ca6127-db72-4b80-b1b0-d745d6d5456d" in post and 'AGENT_NAME="${THREADLIGHT_AGENT_NAME:-order-returns-agent}"' in post
    assert "PROJECT_SCOPE" in post and "ACCOUNT_SCOPE" not in post


def test_package_and_postdeploy_manifests_name_the_harness_kind(harness):
    manifest = json.loads((harness / "specs" / "foundry-package-manifest.json").read_text())
    agent = manifest["agent"]
    assert agent["kind"] == "foundry-harness" and agent["harness_type"] == "microsoft_copilot_preview"
    assert agent["harness_opt_in_source"] == "user" and agent["preview_acknowledged"] is True
    assert agent["harness_fit"]["region"] == "swedencentral"
    assert agent["egress_policy"]["mode"] == "Audit" and agent["egress_policy"]["default_action"] == "Deny"
    post = json.loads((harness / "tests" / "postdeploy-manifest.json").read_text())
    assert post["agent_kind"] == "foundry-harness"
    assert any("Foundry User on the Foundry ACCOUNT" in gap for gap in post["gaps"])


def test_selfcheck_passes_offline_for_the_harness_project(harness, tmp_path):
    report = tmp_path / "selfcheck.json"
    result = _run([str(SELFCHECK), "--project", str(harness), "--report", str(report), "--skip-docker",
                   "--skip-bicep"], tmp_path)
    assert result.returncode == 0, report.read_text() if report.exists() else result.stderr
    names = {c["name"]: c["status"] for c in json.loads(report.read_text())["checks"]}
    assert names["harness-agent-definition"] == "pass"
    assert names["agent-unit-tests"] == "pass"


def test_function_tool_write_seam_and_governance_are_not_this_route(tmp_path):
    project = _workspace(tmp_path, OPT_IN.replace("governed-mcp", "function-tool"))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 4 and "function-tool" in result.stdout + result.stderr


def test_write_tools_with_seam_none_are_inconsistent(tmp_path):
    project = _workspace(tmp_path, OPT_IN.replace("governed-mcp", "none"))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 2 and "consequential_write_seam" in result.stderr


def test_private_profile_is_refused_for_the_harness(tmp_path):
    project = _workspace(tmp_path, OPT_IN)
    result = _run([str(GENERATOR), "--project", str(project), "--profile", "private"], tmp_path)
    assert result.returncode == 2 and "harness" in result.stderr.lower()


def test_route_is_documented_with_its_live_findings():
    deploy = Path(__file__).resolve().parents[1]
    readme = (deploy / "references" / "harness" / "README.md").read_text()
    for needle in ("project: src/agent", "tool_names", "RequestConflict", "AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS",
                   "LOCAL-14/CTK", *TELEMETRY):
        assert needle in readme, needle
    mode = (deploy / "references" / "generate-only-mode.md").read_text()
    assert "harness/README.md" in mode and "until the harness route ships" not in mode
    spec = (deploy.parent / "threadlight-design" / "references" / "speckit-template.md").read_text()
    for field in ("harness_toolset:", "harness_egress_allow:", "harness_egress_mode:"):
        assert field in spec


def test_harness_pins_a_fixed_model_not_the_router_default(harness):
    # #218 made the pilot default a model router; model router support in the
    # Copilot harness (preview) is not documented, so the harness stays fixed.
    params = json.loads((harness / "infra" / "main.parameters.json").read_text())["parameters"]
    assert params["modelStrategy"]["value"] == "${AZURE_AI_MODEL_STRATEGY=fixed}"
    bicep = (harness / "infra" / "main.bicep").read_text()
    assert "dependsOn: [ project, modelDeployments, router ]" in bicep


RESPONSES_TEMPLATE = {"type": "template", "template": [
    {"type": "message", "role": "user", "content": {"type": "input_text", "text": "{{item.query}}"}}]}


def test_harness_eval_dry_run_snapshot_uses_the_responses_template(harness, tmp_path):
    result = _run([str(harness / "evals" / "run_evals.py"), "--dry-run"], tmp_path, cwd=harness)
    assert result.returncode == 0, result.stderr
    request = json.loads(result.stdout)
    source = request["data_source"]
    assert source["type"] == "azure_ai_target_completions"
    assert source["input_messages"] == RESPONSES_TEMPLATE
    assert source["target"]["type"] == "azure_ai_agent" and source["target"]["name"] == "order-returns-agent"
    criteria = {c["name"]: c for c in request["testing_criteria"]}
    for c in criteria.values():
        if c["type"] == "azure_ai_evaluator":
            assert c["data_mapping"]["response"] == "{{sample.output_text}}"
    assert "{{sample.output_text}}" in criteria["spec_business_rules_rubric"]["input"][1]["content"]
    cfg = json.loads((harness / "evals" / "eval-config.json").read_text())
    flagged = {e["evaluator_name"] for e in cfg["builtin_evaluators"] if e.get("may_error_on_harness_output")}
    assert flagged == {"builtin.task_adherence"}


def test_hosted_eval_dry_run_keeps_the_invocations_body(tmp_path):
    project = _generate(tmp_path, "agent_type: hosted\n")
    result = _run([str(project / "evals" / "run_evals.py"), "--dry-run"], tmp_path, cwd=project)
    assert json.loads(result.stdout)["data_source"]["input_messages"] == {
        "input": "{{item.query}}", "stream": False, "new_conversation": True}


def test_run_evals_counts_evaluator_errors_as_missing_scores(harness):
    import importlib.util
    spec = importlib.util.spec_from_file_location("generated_run_evals", harness / "evals" / "run_evals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    items = [
        {"results": [{"name": "task_adherence", "score": None, "sample": {"error": {
            "message": "(UserError) The 'arguments' field must be a dictionary in tool_call content items"}}},
            {"name": "spec_business_rules_rubric", "score": 5, "passed": True}]},
        {"results": [{"name": "task_adherence", "score": 4, "passed": True},
                     {"name": "spec_business_rules_rubric", "score": 3, "passed": False}]},
    ]
    summary = module.summarize(items)
    assert summary["task_adherence"] == {"scored": 1, "errored": 1, "scores": [4]}
    assert summary["spec_business_rules_rubric"] == {"scored": 2, "errored": 0, "scores": [5, 3]}
