"""Contract tests for threadlight-deploy ``generate_only: true``.

A generate-only run produces a complete, deploy-ready repository from a design
workspace without an Azure subscription, az/azd login or network access. The
user later installs the plugin locally and deploys with their own credentials.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
SKILLS = DEPLOY.parent
REPO = SKILLS.parent
GENERATOR = DEPLOY / "scripts" / "generate_only.py"
SELFCHECK = DEPLOY / "scripts" / "generate_only_selfcheck.py"
HARNESS = SKILLS / "threadlight-local-test" / "scripts" / "project_tools.py"
FOUNDRY_PACKAGE = SKILLS / "_shared" / "foundry_package.py"
FIXTURE = DEPLOY / "tests" / "fixtures" / "generate-only" / "order-returns"
PILOT = DEPLOY / "references" / "hosted-agent" / "ghcp" / "references" / "pilot"


def _offline_env(tmp_path: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AZURE_", "AZD_", "ARM_", "GITHUB_TOKEN", "GH_TOKEN"))
    }
    # No az/azd on PATH and no Azure config: generation must not need them.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
    env["AZURE_CONFIG_DIR"] = str(tmp_path / "no-azure-config")
    env["HOME"] = str(tmp_path / "home")
    env["no_proxy"] = env["NO_PROXY"] = ""
    env["http_proxy"] = env["https_proxy"] = env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    # Shared module-scoped outputs must stay free of __pycache__ (issue #161).
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(args: list[str], tmp_path: Path, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        env=_offline_env(tmp_path),
        cwd=cwd,
        timeout=600,
    )


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("gen")
    project = tmp / "order-returns"
    shutil.copytree(FIXTURE, project)
    result = _run([str(GENERATOR), "--project", str(project)], tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    return project


def _load_yaml(path: Path):
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --- (a) canonical pilot layout, inline agent definition -----------------------

def test_generator_and_selfcheck_exist():
    assert GENERATOR.is_file(), "scripts/generate_only.py missing"
    assert SELFCHECK.is_file(), "scripts/generate_only_selfcheck.py missing"
    assert HARNESS.is_file(), "threadlight-local-test/scripts/project_tools.py missing"


def test_tree_follows_pilot_scaffold(generated: Path):
    for rel in (
        "azure.yaml",
        ".dockerignore",
        "hooks/postdeploy.sh",
        "infra/main.bicep",
        "infra/main.parameters.json",
        "src/agent/container.py",
        "src/agent/Dockerfile",
        "src/agent/pyproject.toml",
        "src/agent/requirements.lock",
        "src/agent/copilot-instructions.md",
        "src/agent/skills/return-decision/SKILL.md",
        "src/mcp/server.py",
        "src/mcp/Dockerfile",
        "src/mcp/requirements.lock",
        "DEPLOY.md",
    ):
        assert (generated / rel).is_file(), rel
    # One agent definition: inline in azure.yaml. No root/agent-dir agent.yaml,
    # no infra/core from `azd ai agent init`.
    assert not (generated / "agent.yaml").exists()
    assert not (generated / "src" / "agent" / "agent.yaml").exists()
    assert not (generated / "infra" / "core").exists()
    assert (generated / "infra" / "main.bicep").read_bytes() == (PILOT / "infra" / "main.bicep").read_bytes()


def test_azure_yaml_declares_inline_hosted_agent(generated: Path):
    doc = _load_yaml(generated / "azure.yaml")
    assert doc["name"] == "order-returns-agent"
    services = doc["services"]
    agent = next(s for s in services.values() if s.get("host") == "azure.ai.agent")
    assert agent["project"] == "src/agent"
    # Pilot shape: the hosted-agent definition is inline on the service.
    assert agent["kind"] == "hosted"
    assert agent["name"] == "order-returns-agent"
    assert "__" not in json.dumps(doc), "unreplaced __PLACEHOLDER__ token"
    assert any(s.get("host") == "containerapp" for s in services.values())


def test_prompt_agent_type_is_refused(tmp_path: Path):
    project = tmp_path / "p"
    shutil.copytree(FIXTURE, project)
    spec = project / "specs" / "SPEC.md"
    spec.write_text(
        spec.read_text().replace("agent_type: hosted", "agent_type: prompt"), encoding="utf-8"
    )
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "prompt" in (result.stdout + result.stderr).lower()


def test_generation_is_deterministic_and_refuses_overwrite(generated: Path, tmp_path: Path):
    again = _run([str(GENERATOR), "--project", str(generated)], tmp_path)
    assert again.returncode != 0, "must refuse to overwrite generated files without --force"
    forced = _run([str(GENERATOR), "--project", str(generated), "--force"], tmp_path)
    assert forced.returncode == 0, forced.stderr


# --- (b) posture + deferred ------------------------------------------------------

def test_posture_defaults_to_demo_sandbox(generated: Path):
    text = (generated / "specs" / "deployment-posture.md").read_text()
    assert "deployment_target: demo-sandbox" in text
    assert "source: defaulted-after-skip" in text


def test_deferred_steps_recorded(generated: Path):
    text = (generated / "specs" / "deferred-steps.md").read_text()
    for marker in ("T-0", "3.5", "6.5", "Phase 7", "Citadel"):
        assert marker in text, marker
    assert text.lower().count("deferred") >= 4


# --- (c) exact pins, hashed locks, offline unit tests ---------------------------

LOCKS = ("src/agent/requirements.lock", "src/mcp/requirements.lock", "evals/requirements.lock")


@pytest.mark.parametrize("rel", LOCKS)
def test_locks_are_exact_and_hashed(generated: Path, rel: str):
    text = (generated / rel).read_text()
    reqs = re.findall(r"^([A-Za-z0-9_.\-\[\]]+)(==|>=|~=|<=|>|<)?([^\s\\;]*)", text, re.M)
    reqs = [r for r in reqs if r[0] and not r[0].startswith("#")]
    assert reqs
    for name, op, _ in reqs:
        assert op == "==", f"{rel}: {name} is not pinned with =="
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text.split("\n", 1)[1] if text.startswith("#") else text)
    for block in blocks:
        if block.strip() and not block.lstrip().startswith("#"):
            assert "--hash=sha256:" in block, block[:80]
    assert "md5" not in text
    assert "pythonhosted" not in text and "packagefeedproxy" not in text


def test_pyproject_pins_are_exact(generated: Path):
    text = (generated / "src" / "agent" / "pyproject.toml").read_text()
    deps = re.findall(r'"([A-Za-z0-9_.\-]+)\s*([=~<>!]=?)\s*[^"]*"', text)
    assert deps
    assert all(op == "==" for _, op in deps), deps


def test_dockerfiles_install_with_require_hashes(generated: Path):
    for rel in ("src/agent/Dockerfile", "src/mcp/Dockerfile"):
        text = (generated / rel).read_text()
        assert "--require-hashes" in text, rel
        assert "mcr.microsoft.com/" in text, rel


def test_pilot_mcp_dependency_is_exact():
    text = (PILOT / "mcp" / "Dockerfile").read_text()
    assert "--require-hashes" in text
    assert ">=" not in text
    lock = (PILOT / "mcp" / "requirements.lock").read_text()
    assert re.search(r"^mcp==\d+\.\d+\.\d+", lock, re.M)


def test_generated_offline_unit_tests_pass(generated: Path, tmp_path: Path):
    tests = generated / "src" / "agent" / "tests"
    assert list(tests.glob("test_*.py"))
    result = _run(["-m", "pytest", "-q", "-p", "no:cacheprovider", str(tests)], tmp_path, cwd=generated)
    assert result.returncode == 0, result.stdout + result.stderr


# --- (d) eval runner + config ----------------------------------------------------

def test_eval_dataset_from_spec_scenarios(generated: Path):
    rows = [json.loads(l) for l in (generated / "evals" / "eval_dataset.jsonl").read_text().splitlines() if l]
    assert [r["id"] for r in rows] == ["S-001", "S-002", "S-003"]
    assert rows[2]["expected"] == "HUMAN_REVIEW"
    assert rows[2]["business_rules"] == ["BR-002", "BR-003"]
    assert all(r["query"] for r in rows)


def test_eval_config_has_builtins_and_spec_rubric(generated: Path):
    cfg = json.loads((generated / "evals" / "eval-config.json").read_text())
    names = {e["evaluator_name"] for e in cfg["builtin_evaluators"]}
    assert {"builtin.task_adherence", "builtin.intent_resolution"} <= names
    assert cfg["target"] == {"type": "azure_ai_agent", "name": "order-returns-agent"}
    rubric = cfg["custom_rubric_evaluators"][0]
    assert len(rubric["anchors"]) >= 2
    assert 0 < rubric["threshold"] <= 5
    assert {"S-001", "S-003", "BR-003"} <= set(rubric["derived_from_acceptance_criteria"])


def test_run_evals_is_one_command_and_offline_safe(generated: Path, tmp_path: Path):
    runner = generated / "evals" / "run_evals.py"
    text = runner.read_text()
    assert "azure_ai_target_completions" in text and "azure_ai_agent" in text
    assert 'reconfigure(encoding="utf-8"' in text
    dry = _run([str(runner), "--dry-run"], tmp_path, cwd=generated)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    payload = json.loads(dry.stdout)
    assert payload["data_source"]["target"]["name"] == "order-returns-agent"
    assert any(c["type"] == "score_model" for c in payload["testing_criteria"])
    assert "python evals/run_evals.py" in (generated / "DEPLOY.md").read_text()


# --- (e) foundry package manifest: incomplete, never malformed -------------------

def test_foundry_package_manifest_is_incomplete_not_malformed(generated: Path, tmp_path: Path):
    manifest = generated / "specs" / "foundry-package-manifest.json"
    doc = json.loads(manifest.read_text())
    assert doc["schema"] == "threadlight-foundry-package/v1"
    assert doc["agent"]["kind"] == "foundry-hosted"
    result = _run([str(FOUNDRY_PACKAGE), "--workspace", str(generated)], tmp_path)
    assert result.returncode == 3, result.stdout + result.stderr


# --- (f) project-tools harness ---------------------------------------------------

def test_generated_mcp_server_exposes_spec_tools(generated: Path):
    text = (generated / "src" / "mcp" / "server.py").read_text()
    for tool in ("get_order", "get_customer", "list_customer_orders", "record_decision"):
        assert f"def {tool}(" in text, tool
    assert "SAMPLE_DATA_DIR" in text and "parents[" not in text


def test_project_tools_harness_calls_real_tools(generated: Path, tmp_path: Path):
    out = tmp_path / "project-tools-report.json"
    result = _run([str(HARNESS), "--project", str(generated), "--report", str(out)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(out.read_text())
    calls = {c["tool"]: c for c in report["calls"]}
    assert set(calls) >= {"get_order", "get_customer", "list_customer_orders", "record_decision"}
    assert calls["get_order"]["arguments"]["order_id"] == "ORD-1001"
    assert calls["get_order"]["result"]["customer_id"] == "CUS-201"
    assert calls["get_customer"]["arguments"]["customer_id"] == "CUS-201"
    assert all(c["ok"] for c in calls.values())
    assert report["pattern"] == "project-tools"


# --- (g) postdeploy manifest ----------------------------------------------------

def test_postdeploy_manifest_is_pending_not_green(generated: Path):
    doc = json.loads((generated / "tests" / "postdeploy-manifest.json").read_text())
    assert doc["phase"] == "pending-deploy"
    assert doc["generate_only"] is True
    assert doc["gaps"]
    assert doc["deployment_manifest"]["module_selectors"]["hosting"] == "foundry-hosted"
    assert {"get_order", "record_decision"} <= set(doc["expected_tools"])


# --- (h)/(i) offline self-check ---------------------------------------------------

def test_selfcheck_passes_offline(generated: Path, tmp_path: Path):
    report = tmp_path / "selfcheck.json"
    result = _run(
        [str(SELFCHECK), "--project", str(generated), "--report", str(report), "--skip-docker", "--skip-bicep"],
        tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads(report.read_text())
    status = {c["name"]: c["status"] for c in doc["checks"]}
    assert status["azure-yaml-schema"] == "pass"
    assert status["hosted-agent-definition"] == "pass"
    assert status["foundry-package-incomplete"] == "pass"
    assert status["secret-scan"] == "pass"
    assert status["docker-build"] == "skipped"


def test_selfcheck_detects_secret_and_personal_target(generated: Path, tmp_path: Path):
    bad = tmp_path / "bad"
    shutil.copytree(generated, bad)
    (bad / "src" / "agent" / "leak.py").write_text(
        'SUB = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"\nKEY = "AccountKey=abc123def456ghi789=="\n'
    )
    report = tmp_path / "bad.json"
    result = _run(
        [str(SELFCHECK), "--project", str(bad), "--report", str(report), "--skip-docker", "--skip-bicep"],
        tmp_path,
    )
    assert result.returncode != 0
    status = {c["name"]: c["status"] for c in json.loads(report.read_text())["checks"]}
    assert status["secret-scan"] == "fail"


def test_selfcheck_never_uses_login_or_push():
    text = SELFCHECK.read_text()
    assert "docker login" not in text and "az login" not in text and "azd auth" not in text
    assert "--push" not in text and "docker push" not in text
    assert "DOCKER_CONFIG" in text


# --- docs ------------------------------------------------------------------------

def test_skill_documents_generate_only_mode():
    text = (DEPLOY / "SKILL.md").read_text()
    m = re.search(r"^## Generate-only mode.*?(?=^## )", text, re.M | re.S)
    assert m, "SKILL.md needs a '## Generate-only mode' section"
    section = m.group(0)
    assert "generate_only: true" in section
    assert "scripts/generate_only.py" in section and "generate_only_selfcheck.py" in section
    assert "references/pilot" in section
    assert "no `azd ai agent init`" in section.lower() or "never runs `azd ai agent init`" in section
    for phase in ("Phase 0", "Phase 1", "Phase 2", "Phase 3", "Phase 5", "Phase 6"):
        assert phase in section
    assert "deferred-steps.md" in section and "deployment-posture.md" in section


def test_phase3_checklist_matches_pilot_layout():
    text = (DEPLOY / "SKILL.md").read_text()
    phase3 = re.search(r"^## Phase 3: Validate.*?(?=^## Phase 4)", text, re.M | re.S).group(0)
    assert "src/agent/agent.yaml` — copy of root" not in phase3
    assert "infra/core/` — vendored modules present" not in phase3
    assert "references/pilot" in phase3


def test_local_test_skill_documents_project_tools_harness():
    text = (SKILLS / "threadlight-local-test" / "SKILL.md").read_text()
    assert "project-tools" in text and "scripts/project_tools.py" in text


def test_existing_design_eval_dataset_is_reused_verbatim(tmp_path: Path):
    project = tmp_path / "ws"
    shutil.copytree(FIXTURE, project)
    rows = [
        {"id": "S-900", "query": "Killer prompt literal, verbatim.", "expected": "AUTO_REFUND",
         "business_rules": ["BR-001"], "category": "killer"},
    ]
    (project / "tests").mkdir(exist_ok=True)
    (project / "tests" / "eval_dataset.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    out = [json.loads(line) for line in (project / "evals" / "eval_dataset.jsonl").read_text().splitlines()]
    assert [r["query"] for r in out] == ["Killer prompt literal, verbatim."]
    assert out[0]["id"] == "S-900"


def test_selfcheck_ignores_public_constants(generated: Path, tmp_path: Path):
    ok = tmp_path / "ok"
    shutil.copytree(generated, ok)
    (ok / ".gitignore").write_text(".azure/\n.env\n")
    (ok / "infra" / "rbac.bicep").write_text(
        "var r = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', "
        "'eed3b665-ab3a-47b6-8f48-c9382fb1dad6')\n"
    )
    report = tmp_path / "ok.json"
    _run([str(SELFCHECK), "--project", str(ok), "--report", str(report), "--skip-docker", "--skip-bicep"], tmp_path)
    scan = next(c for c in json.loads(report.read_text())["checks"] if c["name"] == "secret-scan")
    assert scan["status"] == "pass", scan


def test_prose_tool_inputs_become_typed_optional_args(tmp_path: Path):
    project = tmp_path / "ws"
    shutil.copytree(FIXTURE, project)
    spec = project / "specs" / "SPEC.md"
    spec.write_text(spec.read_text().replace(
        "| `record_decision` |",
        "| `authorize_refund` | case + order + amount + refund method | transaction id | 3 retries |\n"
        "| `record_decision` |",
    ))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    plan = {t["name"]: t for t in json.loads((project / "specs" / "project-tools.json").read_text())["tools"]}
    args = [a["name"] for a in plan["authorize_refund"]["args"]]
    assert args == ["case_id", "order_id", "amount", "refund_method"]
    assert all(a.get("optional") for a in plan["authorize_refund"]["args"])
    report = tmp_path / "pt.json"
    _run([str(HARNESS), "--project", str(project), "--report", str(report)], tmp_path)
    call = next(c for c in json.loads(report.read_text())["calls"] if c["tool"] == "authorize_refund")
    assert call["ok"] and call["arguments"].get("order_id") == "ORD-1001"


def test_ghcp_copy_instructions_include_requirements_lock():
    readme = (DEPLOY / "references" / "hosted-agent" / "ghcp" / "README.md").read_text(encoding="utf-8")
    skill = (DEPLOY / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"Copy\*\* `references/Dockerfile` and `references/requirements\.lock`", readme)
    assert re.search(r"Copy `references/Dockerfile`, `references/requirements\.lock`", skill)
    assert "`uv sync`, copies all agent files" not in skill


def test_real_os_errors_exit_2_not_refusal_codes(tmp_path: Path):
    project = tmp_path / "order-returns"
    shutil.copytree(FIXTURE, project)
    # A regular file where the generator needs a directory: a genuine OSError.
    (project / "evals").write_text("not a directory", encoding="utf-8")
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr


# --- issue #161: Studio-format SPEC (section 6 headings) end-to-end regression ----

STUDIO_FIXTURE = DEPLOY / "tests" / "fixtures" / "generate-only" / "equipment-booking"
DEGENERATE_ARGS = {"none_id", "none", "see_below", "see_below_id", "none_see_below"}


@pytest.fixture(scope="module")
def studio(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("studio")
    project = tmp / "equipment-booking"
    shutil.copytree(STUDIO_FIXTURE, project)
    result = _run([str(GENERATOR), "--project", str(project)], tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    return project


def _plan(project: Path) -> dict[str, dict]:
    return {t["name"]: t for t in json.loads((project / "specs" / "project-tools.json").read_text())["tools"]}


def test_section6_subsection_inputs_become_real_args(studio: Path):
    plan = _plan(studio)
    assert [a["name"] for a in plan["get_member"]["args"]] == ["member_id"]
    assert [a["name"] for a in plan["verify_equipment_availability"]["args"]] == ["equipment_type", "start_at", "end_at"]
    assert [a["name"] for a in plan["get_booking_request"]["args"]] == ["request_id"]
    for tool in plan.values():
        assert not any(a.get("optional") for a in tool["args"]), tool
        assert not DEGENERATE_ARGS & {a["name"] for a in tool["args"]}, tool
    assert plan["get_member"]["description"].startswith("Look up a makerspace member")


def test_no_placeholder_args_in_generated_server(studio: Path):
    text = (studio / "src" / "mcp" / "server.py").read_text()
    assert "none_id" not in text
    assert "def verify_equipment_availability(equipment_type: str, start_at: str, end_at: str)" in text
    assert "def get_member(member_id: str)" in text


def test_kind_follows_5b_and_11a_not_name_heuristic(studio: Path):
    plan = _plan(studio)
    assert {name: t["kind"] for name, t in plan.items()} == {
        "get_member": "read", "verify_equipment_availability": "read", "get_booking_request": "read"}


def test_11a_consequence_overrides_5b(tmp_path: Path):
    project = tmp_path / "ws"
    shutil.copytree(STUDIO_FIXTURE, project)
    spec = project / "specs" / "SPEC.md"
    spec.write_text(spec.read_text().replace(
        "  - id: get_booking_request\n    consequence: read",
        "  - id: get_booking_request\n    consequence: reversible-write"))
    assert _run([str(GENERATOR), "--project", str(project)], tmp_path).returncode == 0
    assert _plan(project)["get_booking_request"]["kind"] == "write"


def test_placeholder_only_inputs_never_become_none_id(tmp_path: Path):
    project = tmp_path / "ws"
    shutil.copytree(FIXTURE, project)
    spec = project / "specs" / "SPEC.md"
    spec.write_text(spec.read_text().replace("| `get_customer` | `customer_id` |", "| `get_customer` | none / see below |"))
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    names = [a["name"] for a in _plan(project)["get_customer"]["args"]]
    assert not DEGENERATE_ARGS & set(names)
    assert "none_id" not in (project / "src" / "mcp" / "server.py").read_text()


def test_studio_harness_calls_tools_with_sample_data_ids(studio: Path, tmp_path: Path):
    out = tmp_path / "pt.json"
    result = _run([str(HARNESS), "--project", str(studio), "--report", str(out)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = {c["tool"]: c for c in json.loads(out.read_text())["calls"]}
    assert calls["get_member"]["arguments"] == {"member_id": "MEM-001"}
    assert calls["get_member"]["result"]["status"] == "active"
    assert calls["verify_equipment_availability"]["arguments"] == {
        "equipment_type": "laser_cutter", "start_at": "2030-01-10T09:00:00Z", "end_at": "2030-01-10T11:00:00Z"}
    assert calls["verify_equipment_availability"]["result"]["slot_id"] == "SLT-001"
    assert calls["get_booking_request"]["arguments"] == {"request_id": "REQ-001"}
    assert all(c["arguments"] for c in calls.values())


def _harness_with_plan(studio: Path, tmp_path: Path, mutate) -> tuple[int, dict]:
    project = tmp_path / "copy"
    shutil.copytree(studio, project, ignore=shutil.ignore_patterns("__pycache__"))
    plan_path = project / "specs" / "project-tools.json"
    doc = json.loads(plan_path.read_text())
    mutate(doc["tools"])
    plan_path.write_text(json.dumps(doc))
    out = tmp_path / "pt.json"
    result = _run([str(HARNESS), "--project", str(project), "--report", str(out)], tmp_path)
    return result.returncode, json.loads(out.read_text())


def test_harness_fails_on_degenerate_args(studio: Path, tmp_path: Path):
    def mutate(tools):
        tools[0]["args"] = [{"name": "none_id", "type": "str", "optional": True}]
    code, report = _harness_with_plan(studio, tmp_path, mutate)
    assert code == 1
    call = next(c for c in report["calls"] if c["tool"] == "get_member")
    assert not call["ok"] and "degenerate" in call["failure"]


def test_harness_fails_on_empty_args(studio: Path, tmp_path: Path):
    def mutate(tools):
        tools[0]["args"] = []
    code, report = _harness_with_plan(studio, tmp_path, mutate)
    assert code == 1
    call = next(c for c in report["calls"] if c["tool"] == "get_member")
    assert not call["ok"] and "no arguments" in call["failure"]


def test_harness_fails_on_unresolvable_required_id(studio: Path, tmp_path: Path):
    def mutate(tools):
        tools[2]["args"] = [{"name": "ticket_id", "type": "str"}]
    code, report = _harness_with_plan(studio, tmp_path, mutate)
    assert code == 1
    call = next(c for c in report["calls"] if c["tool"] == "get_booking_request")
    assert not call["ok"] and "ticket_id" in call["failure"]
    assert call["arguments"].get("ticket_id") != "sample"


def test_design_prompt_field_is_accepted_as_query(tmp_path: Path):
    project = tmp_path / "ws"
    shutil.copytree(STUDIO_FIXTURE, project)
    (project / "tests").mkdir()
    (project / "tests" / "eval_dataset.jsonl").write_text(
        json.dumps({"id": "S-001", "prompt": "Check request REQ-001", "expected": "APPROVE",
                    "business_rules": ["BR-001"]}) + "\n")
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads((project / "evals" / "eval_dataset.jsonl").read_text().splitlines()[0])
    assert row["query"] == "Check request REQ-001"


def test_run_evals_load_rows_accepts_prompt(studio: Path, tmp_path: Path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_run_evals", studio / "evals" / "run_evals.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec.loader.exec_module(module)
    data = tmp_path / "ds.jsonl"
    data.write_text(json.dumps({"id": "X", "prompt": "hello"}) + "\n" + json.dumps({"id": "Y", "query": "hi"}) + "\n")
    rows = module.load_rows({"dataset": str(data)})
    assert [r["query"] for r in rows] == ["hello", "hi"]


def test_pilot_project_capability_host_has_no_bcp037_property():
    text = (PILOT / "infra" / "main.bicep").read_text()
    block = re.search(r"resource projectCapabilityHost\b.*?\n}\n", text, re.S)
    assert block, "projectCapabilityHost resource missing"
    assert "capabilityHostKind" not in block.group(0)
    shared = SKILLS / "threadlight-deploy" / "references" / "azd-modules" / "references" / "bicep" / "foundry-project-capability-host.bicep"
    if shared.is_file():
        assert "capabilityHostKind" not in shared.read_text()


def test_pilot_bicep_builds_without_bcp037(tmp_path: Path):
    bicep = shutil.which("bicep") or (str(Path.home() / ".azure" / "bin" / "bicep") if (Path.home() / ".azure" / "bin" / "bicep").is_file() else None)
    if not bicep:
        pytest.skip("bicep CLI not available")
    result = subprocess.run([bicep, "build", str(PILOT / "infra" / "main.bicep"), "--outfile", str(tmp_path / "m.json")],
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    assert "BCP037" not in result.stdout + result.stderr


def test_no_bytecode_after_generate_selfcheck_and_harness(studio: Path, tmp_path: Path):
    _run([str(HARNESS), "--project", str(studio), "--report", str(tmp_path / "pt.json")], tmp_path)
    _run([str(SELFCHECK), "--project", str(studio), "--report", str(tmp_path / "sc.json"), "--skip-docker", "--skip-bicep"], tmp_path)
    leaked = [p for p in studio.rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc"]
    assert not leaked, leaked


def test_selfcheck_flags_bytecode(generated: Path, tmp_path: Path):
    project = tmp_path / "copy"
    shutil.copytree(generated, project)
    cache = project / "src" / "mcp" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "server.cpython-312.pyc").write_bytes(b"\x00")
    report = tmp_path / "sc.json"
    result = _run([str(SELFCHECK), "--project", str(project), "--report", str(report), "--skip-docker", "--skip-bicep"], tmp_path)
    assert result.returncode == 1
    status = {c["name"]: c["status"] for c in json.loads(report.read_text())["checks"]}
    assert status["no-bytecode"] == "fail"


def test_section6_headings_without_summary_table(tmp_path: Path):
    project = tmp_path / "ws"
    shutil.copytree(STUDIO_FIXTURE, project)
    spec = project / "specs" / "SPEC.md"
    text = spec.read_text()
    text = re.sub(r"(## 6\. Tool Contracts\n\n)\|.*?\n\n", r"\1", text, flags=re.S)
    assert "none / see below" not in text
    spec.write_text(text)
    result = _run([str(GENERATOR), "--project", str(project)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    plan = _plan(project)
    assert list(plan) == ["get_member", "verify_equipment_availability", "get_booking_request"]
    assert [a["name"] for a in plan["verify_equipment_availability"]["args"]] == ["equipment_type", "start_at", "end_at"]
    assert plan["verify_equipment_availability"]["kind"] == "read"


def _import(path: Path, name: str):
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MEMBER_INPUTS = """- **Inputs**:
  | Parameter | Type | Required | Description |
  |-----------|------|----------|-------------|
  | `member_id` | string | yes | member identifier |
"""


@pytest.mark.parametrize("table", [True, False])
def test_template_style_unticked_headings_are_tool_subsections(table: bool):
    gen = _import(GENERATOR, "generate_only_t1")
    summary = "| Tool | Inputs | Output |\n|---|---|---|\n| `get_member` | none / see below | member |\n\n" if table else ""
    spec = ("## 6. Tool Contracts\n\n" + summary + "### get_member\n- **Description**: Look up a member\n" + MEMBER_INPUTS
            + "\n### Notes\nFree text, not a tool.\n\n## 7. Knowledge\n")
    plan = {t["name"]: t for t in gen.tools(spec)}
    assert list(plan) == ["get_member"]
    assert [a["name"] for a in plan["get_member"]["args"]] == ["member_id"]


def test_unparseable_subsection_inputs_keep_summary_table_args():
    gen = _import(GENERATOR, "generate_only_t2")
    spec = ("## 6. Tool Contracts\n\n| Tool | Inputs | Output |\n|---|---|---|\n| `get_member` | `member_id` | member |\n\n"
            "### `get_member`\n- **Inputs**:\n  - `member_id` — the member identifier\n- **Output Schema**: x\n\n## 7. K\n")
    assert [a["name"] for a in gen.tools(spec)[0]["args"]] == ["member_id"]


def test_explicit_no_input_tool_is_marked_and_callable_with_empty_args():
    gen = _import(GENERATOR, "generate_only_t3")
    spec = ("## 6. Tool Contracts\n\n| Tool | Inputs | Output | Idempotency |\n|---|---|---|---|\n"
            "| `list_open_cases` | — | cases | read-only |\n| `get_case` | none / see below | case | read-only |\n\n## 7. K\n")
    plan = {t["name"]: t for t in gen.tools(spec)}
    assert plan["list_open_cases"]["args"] == [] and plan["list_open_cases"].get("no_inputs") is True
    assert not plan["get_case"].get("no_inputs")
    harness = _import(HARNESS, "project_tools_t3")
    assert harness._contract_failure(plan["list_open_cases"]) == ""
    assert harness._arguments(None, plan["list_open_cases"], {}, 0) == ({}, "")
    assert "no arguments" in harness._contract_failure(plan["get_case"])
