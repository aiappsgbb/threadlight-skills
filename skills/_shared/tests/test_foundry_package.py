"""Full Foundry package delivery gate (release 2.19.3).

A Threadlight pilot is delivered only with a real Foundry agent, Foundry
tracing and an executed Foundry eval run with a SPEC-derived custom rubric.
Continuous evaluation is optional since 2.19.4 (validated only when recorded).
Anything less is INCOMPLETE and names the missing evidence.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from skills._shared import foundry_package as fp  # noqa: E402
from skills._shared.tests.foundry_package_fixtures import complete_manifest  # noqa: E402


def write(root: Path, value) -> Path:
    path = root / fp.MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_complete_package_is_complete():
    result = fp.evaluate(complete_manifest())
    assert result["status"] == "COMPLETE", result
    assert result["missing"] == []
    assert result["evidence"]["agent_id"] == "returns-triage:3"
    assert result["evidence"]["trace_id"]
    assert result["evidence"]["eval_run_id"] == "evalrun_123"
    assert result["evidence"]["continuous_evaluation_rule_id"] == "cer_returns_triage"


def test_missing_manifest_is_incomplete_and_named(tmp_path):
    result = fp.evaluate_workspace(tmp_path)
    assert result["status"] == "INCOMPLETE"
    assert fp.MANIFEST_PATH in " ".join(result["missing"])


@pytest.mark.parametrize("kind", ["app-loop", "aca-agent-loop", "responses-api", "rules-fallback", "mock-agent", ""])
def test_non_foundry_agent_kinds_are_incomplete(kind):
    value = complete_manifest()
    value["agent"]["kind"] = kind
    result = fp.evaluate(value)
    assert result["status"] == "INCOMPLETE"
    assert any("agent.kind" in item for item in result["missing"])


def test_active_fallback_is_not_a_delivered_agent():
    value = complete_manifest()
    value["agent"]["fallback_active"] = True
    result = fp.evaluate(value)
    assert result["status"] == "INCOMPLETE"
    assert any("fallback" in item for item in result["missing"])


def test_prompt_agent_requires_trivial_justification():
    value = complete_manifest()
    value["agent"]["kind"] = "foundry-prompt"
    assert fp.evaluate(value)["status"] == "INCOMPLETE"
    value["agent"]["trivial_justification"] = "single model, no code tools, single turn, thread-only state"
    assert fp.evaluate(value)["status"] == "COMPLETE"


@pytest.mark.parametrize("section,field", [
    ("agent", "agent_id"), ("agent", "agent_version"), ("agent", "agent_name"),
    ("telemetry", "app_insights_connection_id"), ("telemetry", "trace_id"),
    ("evaluation", "eval_run_id"), ("evaluation", "builtin_evaluators"),
    ("evaluation", "custom_rubric_evaluators"),
])
def test_each_required_evidence_item_is_named_when_missing(section, field):
    value = complete_manifest()
    del value[section][field]
    result = fp.evaluate(value)
    assert result["status"] == "INCOMPLETE"
    assert any(f"{section}.{field}" in item for item in result["missing"]), result["missing"]


@pytest.mark.parametrize("section", ["agent", "telemetry", "evaluation"])
def test_missing_section_is_incomplete(section):
    value = complete_manifest()
    del value[section]
    result = fp.evaluate(value)
    assert result["status"] == "INCOMPLETE"
    assert any(item.startswith(section) for item in result["missing"])


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(anchors=["only one"]),
    lambda r: r.update(derived_from_acceptance_criteria=[]),
    lambda r: r.pop("threshold"),
    lambda r: r.pop("score"),
    lambda r: r.update(score=2.0),
    lambda r: r.update(threshold="high"),
])
def test_custom_rubric_must_be_anchored_spec_derived_and_pass(mutate):
    value = complete_manifest()
    mutate(value["evaluation"]["custom_rubric_evaluators"][0])
    result = fp.evaluate(value)
    assert result["status"] == "INCOMPLETE"
    assert any("custom_rubric_evaluators" in item for item in result["missing"])


def test_continuous_evaluation_must_be_enabled_for_the_same_agent():
    value = complete_manifest()
    value["continuous_evaluation"]["enabled"] = False
    assert fp.evaluate(value)["status"] == "INCOMPLETE"
    value = complete_manifest()
    value["continuous_evaluation"]["agent_name"] = "other-agent"
    assert fp.evaluate(value)["status"] == "INCOMPLETE"


def test_continuous_evaluation_is_optional():
    # 2.19.4: GA evaluation_rules reject hosted agents and beta.schedules is preview.
    value = complete_manifest()
    del value["continuous_evaluation"]
    result = fp.evaluate(value)
    assert result["status"] == "COMPLETE", result
    assert result["evidence"]["continuous_evaluation_rule_id"] is None


def test_present_continuous_evaluation_is_still_validated():
    value = complete_manifest()
    del value["continuous_evaluation"]["rule_id"]
    result = fp.evaluate(value)
    assert result["status"] == "INCOMPLETE"
    assert any("continuous_evaluation.rule_id" in item for item in result["missing"])


def test_complete_report_line_does_not_claim_continuous_eval_is_required():
    value = complete_manifest()
    del value["continuous_evaluation"]
    line = fp.report_line(fp.evaluate(value))
    assert line.startswith("COMPLETE")
    assert "continuous eval)" not in line


def test_batch_eval_target_when_recorded_must_be_the_same_foundry_agent():
    value = complete_manifest()
    value["evaluation"]["data_source_type"] = "azure_ai_target_completions"
    value["evaluation"]["target"] = {"type": "azure_ai_agent", "name": "returns-triage"}
    assert fp.evaluate(value)["status"] == "COMPLETE"
    value["evaluation"]["target"] = {"type": "azure_openai_model", "name": "gpt-4o"}
    result = fp.evaluate(value)
    assert result["status"] == "INCOMPLETE"
    assert any("evaluation.target" in item for item in result["missing"])
    value["evaluation"]["target"] = {"type": "azure_ai_agent", "name": "other-agent"}
    assert fp.evaluate(value)["status"] == "INCOMPLETE"


def test_wrong_schema_is_malformed():
    value = complete_manifest()
    value["schema"] = "something/v0"
    with pytest.raises(fp.FoundryPackageError):
        fp.evaluate(value)


def test_report_line_leads_with_status_and_missing_items():
    value = complete_manifest()
    del value["telemetry"]["trace_id"]
    line = fp.report_line(fp.evaluate(value))
    assert line.startswith("INCOMPLETE")
    assert "telemetry.trace_id" in line
    assert fp.report_line(fp.evaluate(complete_manifest())).startswith("COMPLETE")


def test_cli_exit_codes(tmp_path):
    script = REPO / "skills/_shared/foundry_package.py"
    run = lambda: subprocess.run([sys.executable, str(script), "--workspace", str(tmp_path)],
                                 capture_output=True, text=True, cwd=REPO)
    assert run().returncode == 3
    write(tmp_path, complete_manifest())
    done = run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "COMPLETE" in done.stdout
    write(tmp_path, {"schema": "nope"})
    assert run().returncode == 2
    (tmp_path / fp.MANIFEST_PATH).write_text("{not json", encoding="utf-8")
    assert run().returncode == 2


def test_input_is_not_mutated():
    value = complete_manifest()
    before = copy.deepcopy(value)
    fp.evaluate(value)
    assert value == before


def test_huge_integer_fails_closed_instead_of_crashing(tmp_path):
    manifest = complete_manifest()
    manifest["evaluation"]["custom_rubric_evaluators"][0]["threshold"] = 10 ** 400
    result = fp.evaluate(manifest)
    assert result["status"] == "INCOMPLETE"
    path = write(tmp_path, {"schema": fp.SCHEMA})
    path.write_text('{"schema": "%s", "evaluation": {"custom_rubric_evaluators": [{"score": 1%s}]}}'
                    % (fp.SCHEMA, "0" * 400), encoding="utf-8")
    assert fp.evaluate_workspace(tmp_path)["status"] == "INCOMPLETE"


def test_documented_cli_resolves_from_the_skills_checkout_not_the_pilot():
    text = (REPO / "skills/_shared/foundry-only-agents.md").read_text(encoding="utf-8")
    assert "python skills/_shared/foundry_package.py --workspace ." not in text
    assert "<threadlight-skills>/skills/_shared/foundry_package.py --workspace <pilot-root>" in text
