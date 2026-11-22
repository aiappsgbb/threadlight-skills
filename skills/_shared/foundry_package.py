"""Full Foundry package delivery gate.

A Threadlight pilot is delivered only when all three items are evidenced:
a real Foundry hosted/prompt agent, or a voice agent or Copilot harness agent
(both preview) recorded with the user's explicit opt-in (no app-side loop, no
active fallback; a harness agent also records its egress policy),
Application Insights tracing with a trace visible in Foundry, and one executed
Foundry eval run against that agent with built-in evaluators plus a SPEC-derived
custom rubric that meets its threshold. Anything less is ``INCOMPLETE``.
Continuous evaluation is optional (one executed eval run is the gate; evaluation
rules are an extra); when recorded it must be valid. Mock MCP tool servers with synthetic data are legitimate:
they are tools called by the real agent, not a substitute for it.

CLI: ``python <threadlight-skills>/skills/_shared/foundry_package.py --workspace <pilot-root>``
exit 0 = COMPLETE, 3 = INCOMPLETE (including a missing manifest),
2 = malformed manifest.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

SCHEMA = "threadlight-foundry-package/v1"
MANIFEST_PATH = "specs/foundry-package-manifest.json"
AGENT_KINDS = ("foundry-hosted", "foundry-prompt", "foundry-voice", "foundry-harness")
HARNESS_TYPE = "microsoft_copilot_preview"
EGRESS_MODES = ("Audit", "Enforced")
EGRESS_DEFAULT_ACTIONS = ("Allow", "Deny")
SECTIONS = ("agent", "telemetry", "evaluation")
# Optional since 2.19.4, when GA evaluation_rules returned 400 "Hosted and external
# agents are not supported" for hosted agents. Validated when present.
OPTIONAL_SECTIONS = ("continuous_evaluation",)
AGENT_TARGET_TYPE = "azure_ai_agent"


class FoundryPackageError(ValueError):
    """The manifest is malformed (wrong schema, not an object, invalid JSON)."""


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _nonempty_text_list(value: Any, minimum: int = 1) -> bool:
    return isinstance(value, list) and len(value) >= minimum and all(_text(v) for v in value)


def _short(name: str) -> str:
    return name.split(".", 1)[-1]


def _clean(counts: Any) -> bool:
    """Evaluator result counts with at least one scored row and no errored row."""
    if not isinstance(counts, dict):
        return False
    scored, errors = counts.get("scored"), counts.get("errored")
    return isinstance(scored, int) and scored > 0 and errors == 0


def _rubric_problems(index: int, rubric: Any) -> list[str]:
    prefix = f"evaluation.custom_rubric_evaluators[{index}]"
    if not isinstance(rubric, dict):
        return [f"{prefix} (must be an object)"]
    problems = []
    if not _text(rubric.get("name")):
        problems.append(f"{prefix}.name")
    if not _nonempty_text_list(rubric.get("anchors"), 2):
        problems.append(f"{prefix}.anchors (at least 2 scoring anchors)")
    if not _nonempty_text_list(rubric.get("derived_from_acceptance_criteria")):
        problems.append(f"{prefix}.derived_from_acceptance_criteria (SPEC acceptance criteria ids)")
    threshold, score = rubric.get("threshold"), rubric.get("score")
    if not _number(threshold):
        problems.append(f"{prefix}.threshold (numeric)")
    if not _number(score):
        problems.append(f"{prefix}.score (numeric result from the Foundry eval run)")
    if _number(threshold) and _number(score) and score < threshold:
        problems.append(f"{prefix}.score below threshold ({score} < {threshold})")
    return problems


def _harness_problems(agent: dict[str, Any]) -> list[str]:
    """Copilot harness (preview): the user's opt-in, its fit record and the egress policy record."""
    problems = []
    if agent.get("harness_type") != HARNESS_TYPE:
        problems.append(f"agent.harness_type (must be {HARNESS_TYPE!r} for foundry-harness)")
    if agent.get("harness_opt_in_source") != "user":
        problems.append("agent.harness_opt_in_source (must be 'user' for foundry-harness)")
    if not _text(agent.get("harness_opt_in_evidence")):
        problems.append("agent.harness_opt_in_evidence (quote of the user's request, required for foundry-harness)")
    if agent.get("preview_acknowledged") is not True:
        problems.append("agent.preview_acknowledged (must be true for foundry-harness)")
    if not isinstance(agent.get("harness_fit"), dict) or not agent["harness_fit"]:
        problems.append("agent.harness_fit (the recorded fit answers, required for foundry-harness)")
    egress = agent.get("egress_policy")
    if not isinstance(egress, dict):
        problems.append("agent.egress_policy (RAI egress policy record, required for foundry-harness)")
        return problems
    if not _text(egress.get("rai_policy_id")):
        problems.append("agent.egress_policy.rai_policy_id (full ARM id attached as rai_config)")
    if egress.get("mode") not in EGRESS_MODES:
        problems.append(f"agent.egress_policy.mode (one of {', '.join(EGRESS_MODES)})")
    if egress.get("default_action") not in EGRESS_DEFAULT_ACTIONS:
        problems.append(f"agent.egress_policy.default_action (one of {', '.join(EGRESS_DEFAULT_ACTIONS)})")
    return problems


def evaluate(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise FoundryPackageError("foundry package manifest must be a JSON object")
    if manifest.get("schema") != SCHEMA:
        raise FoundryPackageError(f"foundry package manifest schema must be {SCHEMA!r}")

    missing: list[str] = []
    sections: dict[str, dict[str, Any]] = {}
    for name in SECTIONS:
        value = manifest.get(name)
        if isinstance(value, dict):
            sections[name] = value
        else:
            missing.append(f"{name} (section missing)")
            sections[name] = {}
    for name in OPTIONAL_SECTIONS:
        value = manifest.get(name)
        sections[name] = value if isinstance(value, dict) else {}
        if value is not None and not isinstance(value, dict):
            missing.append(f"{name} (must be an object)")

    agent = sections["agent"]
    if "agent" in sections and isinstance(manifest.get("agent"), dict):
        kind = agent.get("kind")
        if kind not in AGENT_KINDS:
            missing.append(f"agent.kind (must be one of {', '.join(AGENT_KINDS)}; got {kind!r})")
        for field in ("agent_name", "agent_id", "agent_version"):
            if not _text(agent.get(field)):
                missing.append(f"agent.{field}")
        if kind == "foundry-prompt" and not _text(agent.get("trivial_justification")):
            missing.append("agent.trivial_justification (required for foundry-prompt)")
        if kind == "foundry-voice":
            # Foundry voice agents are preview: delivered only on the user's explicit opt-in.
            if agent.get("voice_opt_in_source") != "user":
                missing.append("agent.voice_opt_in_source (must be 'user' for foundry-voice)")
            if not _text(agent.get("voice_opt_in_evidence")):
                missing.append("agent.voice_opt_in_evidence (quote of the user's request, required for foundry-voice)")
            if agent.get("preview_acknowledged") is not True:
                missing.append("agent.preview_acknowledged (must be true for foundry-voice)")
        if kind == "foundry-harness":
            missing.extend(_harness_problems(agent))
        if agent.get("fallback_active") is not False:
            if agent.get("fallback_active") is True:
                missing.append("agent.fallback_active (a fallback/fake agent is serving; not delivered)")
            else:
                missing.append("agent.fallback_active (must be false)")

    if isinstance(manifest.get("telemetry"), dict):
        for field in ("app_insights_connection_id", "trace_id"):
            if not _text(sections["telemetry"].get(field)):
                missing.append(f"telemetry.{field}")

    errored: list[str] = []
    if isinstance(manifest.get("evaluation"), dict):
        evaluation = sections["evaluation"]
        if not _text(evaluation.get("eval_run_id")):
            missing.append("evaluation.eval_run_id")
        results = evaluation.get("evaluator_results")
        results = results if isinstance(results, dict) else None
        builtins = evaluation.get("builtin_evaluators")
        if not _nonempty_text_list(builtins):
            missing.append("evaluation.builtin_evaluators")
        elif results is not None:
            # An evaluator error is a missing score, never a pass (#231: builtin.task_adherence
            # errored on 5 of 6 harness rows). One cleanly scored built-in satisfies the requirement.
            errored += [name for name in builtins if not _clean(results.get(_short(name)) or results.get(name))]
            if len(errored) == len(builtins):
                missing.append("evaluation.builtin_evaluators (every built-in evaluator errored or scored no row: "
                               + ", ".join(errored) + ")")
        rubrics = evaluation.get("custom_rubric_evaluators")
        if not isinstance(rubrics, list) or not rubrics:
            missing.append("evaluation.custom_rubric_evaluators (at least one SPEC-derived rubric)")
        else:
            for index, rubric in enumerate(rubrics):
                missing.extend(_rubric_problems(index, rubric))
                name = rubric.get("name") if isinstance(rubric, dict) else None
                if results is not None and _text(name) and not _clean(results.get(name)):
                    counts = results.get(name) if isinstance(results.get(name), dict) else {}
                    missing.append(f"evaluation.custom_rubric_evaluators[{index}] {name} errored or scored no row "
                                   f"(errored={counts.get('errored')}, scored={counts.get('scored')}); "
                                   "an evaluator error is a missing score")
        target = evaluation.get("target")
        if target is not None:
            if (not isinstance(target, dict) or target.get("type") != AGENT_TARGET_TYPE
                    or not _text(target.get("name")) or target.get("name") != agent.get("agent_name")):
                missing.append(f"evaluation.target (must be type {AGENT_TARGET_TYPE!r} naming agent.agent_name)")

    if isinstance(manifest.get("continuous_evaluation"), dict):
        continuous = sections["continuous_evaluation"]
        if not _text(continuous.get("rule_id")):
            missing.append("continuous_evaluation.rule_id")
        if continuous.get("enabled") is not True:
            missing.append("continuous_evaluation.enabled (must be true)")
        if continuous.get("agent_name") != agent.get("agent_name") or not _text(continuous.get("agent_name")):
            missing.append("continuous_evaluation.agent_name (must match agent.agent_name)")

    evidence = {
        "agent_id": agent.get("agent_id"),
        "trace_id": sections["telemetry"].get("trace_id"),
        "eval_run_id": sections["evaluation"].get("eval_run_id"),
        "continuous_evaluation_rule_id": sections["continuous_evaluation"].get("rule_id"),
        **({"errored_evaluators": errored} if errored else {}),
    }
    return {"status": "INCOMPLETE" if missing else "COMPLETE", "missing": missing, "evidence": evidence}


def evaluate_workspace(root: str | Path) -> dict[str, Any]:
    """Evaluate ``<root>/specs/foundry-package-manifest.json``.

    A missing manifest is INCOMPLETE; a malformed one raises FoundryPackageError.
    """
    path = Path(root) / MANIFEST_PATH
    if not path.is_file():
        return {
            "status": "INCOMPLETE",
            "missing": [f"{MANIFEST_PATH} (no full Foundry package evidence recorded)"],
            "evidence": {"agent_id": None, "trace_id": None, "eval_run_id": None,
                         "continuous_evaluation_rule_id": None},
        }
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise FoundryPackageError(f"{MANIFEST_PATH}: invalid JSON ({exc})") from exc
    try:
        return evaluate(manifest)
    except (OverflowError, RecursionError) as exc:
        raise FoundryPackageError(f"{MANIFEST_PATH}: unprocessable manifest ({exc})") from exc


def report_line(result: dict[str, Any]) -> str:
    if result.get("status") == "COMPLETE":
        line = "COMPLETE — full Foundry package evidenced (agent, tracing, executed eval + custom rubric)"
        if (result.get("evidence") or {}).get("continuous_evaluation_rule_id"):
            line += "; optional continuous evaluation also wired"
        return line
    return "INCOMPLETE — missing: " + "; ".join(result.get("missing") or ["unknown"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Threadlight full Foundry package gate")
    parser.add_argument("--workspace", default=".", help="pilot workspace root")
    parser.add_argument("--json", action="store_true", help="print the full JSON result")
    args = parser.parse_args(argv)
    try:
        result = evaluate_workspace(args.workspace)
    except FoundryPackageError as exc:
        print(f"MALFORMED — {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else report_line(result))
    return 0 if result["status"] == "COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
