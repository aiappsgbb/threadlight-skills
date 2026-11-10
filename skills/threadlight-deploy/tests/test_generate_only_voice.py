"""generate_only: opt-in Foundry Hosted Voice front-end on the GHCP hosted agent.

The official route (preview) pairs the hosted target with a managed
``kind: voice`` wrapper that uses it as its conversation engine
(https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent,
https://learn.microsoft.com/azure/foundry/agents/concepts/azure-yaml-reference#voice-services).
The target declares ``invocations_ws`` 1.0.0 with ``voiceLiveCompatible`` and
``bridgeProtocolVersion`` metadata; the wrapper carries no model, instructions,
tools, protocols or code. Voice is preview, so it is generated only on an
explicit, preview-acknowledged user opt-in.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from test_generate_only import FIXTURE, GENERATOR, SELFCHECK, _load_yaml, _run

OPT_IN = """agent_type: hosted
voice_frontend: hosted-voice
voice_opt_in_source: user
voice_opt_in_evidence: "User: the returns desk must also answer by voice (preview accepted)"
preview_acknowledged: true
voice_name: it-IT-ElsaNeural
"""


def _workspace(tmp_path: Path, block: str) -> Path:
    project = tmp_path / "order-returns"
    shutil.copytree(FIXTURE, project)
    spec = project / "specs" / "SPEC.md"
    spec.write_text(spec.read_text().replace("agent_type: hosted\n", block, 1), encoding="utf-8")
    return project


@pytest.fixture(scope="module")
def voiced(tmp_path_factory) -> tuple[Path, str]:
    tmp = tmp_path_factory.mktemp("voice")
    project = _workspace(tmp, OPT_IN)
    result = _run([str(GENERATOR), "--project", str(project)], tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    return project, result.stdout + result.stderr


def _services(project: Path) -> dict:
    return _load_yaml(project / "azure.yaml")["services"]


def test_opted_in_target_speaks_voice_live_bridge_and_keeps_invocations(voiced):
    project, _ = voiced
    target = _services(project)["order-returns-agent"]
    assert target["kind"] == "hosted" and target["project"] == "src/agent"
    protocols = {(p["protocol"], p["version"]) for p in target["protocols"]}
    assert protocols == {("invocations", "2.0.0"), ("invocations_ws", "1.0.0")}
    assert target["metadata"] == {"voiceLiveCompatible": "true", "bridgeProtocolVersion": "1.0"}
    env = {e["name"]: e["value"] for e in target["environmentVariables"]}
    assert env["THREADLIGHT_VOICE_BRIDGE"] == "1"


def test_opted_in_wrapper_is_a_managed_voice_service_with_the_target_as_engine(voiced):
    project, _ = voiced
    services = _services(project)
    wrapper = services["order-returns-agent-voice"]
    assert wrapper["host"] == "azure.ai.agent" and wrapper["kind"] == "voice"
    assert wrapper["name"] == "order-returns-agent-voice" != services["order-returns-agent"]["name"]
    assert wrapper["uses"] == ["ai-project", "order-returns-agent"]
    assert wrapper["conversationEngine"] == {"type": "hosted_agent", "name": "order-returns-agent"}
    for owned_by_target in ("model", "modelType", "instructions", "tools", "protocols", "project",
                            "codeConfiguration", "environmentVariables", "env", "docker", "language"):
        assert owned_by_target not in wrapper, owned_by_target
    assert wrapper["audio"]["output"]["voice"]["name"] == "it-IT-ElsaNeural"
    assert wrapper["outputModalities"] == ["audio"]
    assert wrapper["store"] is False


def test_opted_in_azure_yaml_requires_voice_capable_azd(voiced):
    project, _ = voiced
    required = _load_yaml(project / "azure.yaml")["requiredVersions"]
    assert required["azd"] == ">=1.32.0"
    assert required["extensions"]["azure.ai.agents"] == ">=1.0.0-beta.13"


def test_opted_in_project_passes_selfcheck_and_its_offline_tests(voiced, tmp_path: Path):
    project, _ = voiced
    report = tmp_path / "selfcheck.json"
    result = _run([str(SELFCHECK), "--project", str(project), "--report", str(report),
                   "--skip-docker", "--skip-bicep"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    status = {c["name"]: c["status"] for c in json.loads(report.read_text())["checks"]}
    assert status["hosted-agent-definition"] == "pass"
    tests = _run(["-m", "pytest", "-q", "-p", "no:cacheprovider", "src/agent/tests"], tmp_path, cwd=project)
    assert tests.returncode == 0, tests.stdout + tests.stderr


def test_opted_in_deploy_guide_names_the_official_smoke_test_and_the_preview(voiced):
    project, _ = voiced
    guide = (project / "DEPLOY.md").read_text(encoding="utf-8")
    assert "order-returns-agent-voice" in guide
    assert "smoke_test_voice.py" in guide and "microsoft-foundry" in guide
    assert "preview" in guide.lower()
    assert "not validated" in guide.lower()
    gaps = json.loads((project / "tests" / "postdeploy-manifest.json").read_text())["gaps"]
    assert any("voice" in gap for gap in gaps)


@pytest.mark.parametrize("block", [
    OPT_IN.replace("voice_opt_in_source: user", "voice_opt_in_source: agent"),
    OPT_IN.replace("preview_acknowledged: true", "preview_acknowledged: null"),
    OPT_IN.replace('voice_opt_in_evidence: "User: the returns desk must also answer by voice (preview accepted)"',
                   "voice_opt_in_evidence: null"),
])
def test_voice_frontend_without_a_user_opt_in_stays_text_only_and_says_so(tmp_path: Path, block: str):
    project = _workspace(tmp_path, block)
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "voice" in (result.stdout + result.stderr).lower() and "opt-in" in (result.stdout + result.stderr)
    text = (project / "azure.yaml").read_text(encoding="utf-8")
    assert "kind: voice" not in text and "invocations_ws" not in text and "THREADLIGHT_VOICE_BRIDGE" not in text
    gaps = json.loads((project / "tests" / "postdeploy-manifest.json").read_text())["gaps"]
    assert any("voice_frontend ignored" in gap for gap in gaps)


def test_voice_frontend_needs_a_hosted_agent(tmp_path: Path):
    project = _workspace(tmp_path, OPT_IN.replace("agent_type: hosted", "agent_type: prompt"))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 4


def test_default_generation_has_no_voice_surface(tmp_path: Path):
    project = _workspace(tmp_path, "agent_type: hosted\n")
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    text = (project / "azure.yaml").read_text(encoding="utf-8")
    assert "kind: voice" not in text and "invocations_ws" not in text and "THREADLIGHT_VOICE_BRIDGE" not in text
    assert "azd:" not in text


@pytest.mark.parametrize("old,new,needle", [
    ("    conversationEngine:\n", "    model:\n      id: gpt-realtime\n    conversationEngine:\n",
     "model belongs to the hosted target"),
    ('      voiceLiveCompatible: "true"\n', "", "voice target metadata"),
    ("      - name: THREADLIGHT_VOICE_BRIDGE\n        value: \"1\"\n", "", "THREADLIGHT_VOICE_BRIDGE=1"),
])
def test_selfcheck_rejects_an_inconsistent_voice_wrapper(voiced, tmp_path: Path, old: str, new: str, needle: str):
    project, _ = voiced
    bad = tmp_path / "bad"
    shutil.copytree(project, bad)
    text = (bad / "azure.yaml").read_text(encoding="utf-8")
    assert text.count(old) == 1
    (bad / "azure.yaml").write_text(text.replace(old, new), encoding="utf-8")
    report = tmp_path / "bad.json"
    _run([str(SELFCHECK), "--project", str(bad), "--report", str(report), "--skip-docker", "--skip-bicep"], tmp_path)
    checks = {c["name"]: c for c in json.loads(report.read_text())["checks"]}
    assert checks["hosted-agent-definition"]["status"] == "fail"
    assert needle in checks["hosted-agent-definition"]["detail"]
