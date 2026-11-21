#!/usr/bin/env python3
"""evals_check.py — DISCOVER/GOVERN/IMPROVE validator for threadlight-evals.

Verifies that a pilot has offline batch evals, live/continuous eval wiring,
and a champion-challenger comparison gate. Emits a manifest consumed by
`threadlight-production-ready` pillar 6 (`continuous-evals`).

Capability keys map to pillar-06 EVAL IDs:

    eval_scenarios_present      EVAL-001
    eval_datasets_present       EVAL-002
    dataset_shape_ok            EVAL-003
    thresholds_declared         EVAL-004
    schedule_present            EVAL-005
    run_history_present         EVAL-006
    online_eval_wired           EVAL-101
    latest_eval_run_fresh       EVAL-102/EVAL-103
    alert_wired                 EVAL-104
    latest_pass_rate_ok         EVAL-105
    ab_comparison_present       F3
    rubric_trajectory_mapped    EVAL-106
    gate_split_ok               EVAL-107
    gate_evaluator_suitable     EVAL-108
    gate_runs_recorded          EVAL-109

stdlib-only. Gracefully degrading — capabilities that cannot be checked are
reported as ``not-verified`` rather than crashing.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

VERSION = "0.3.0"
MANIFEST_SCHEMA = "threadlight-evals-manifest/v1"


def _packaged_agentops():
    path = Path(__file__).resolve().parents[2] / "_shared" / "agentops.py"
    if not path.is_file():
        return None
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("_threadlight_evals_agentops", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agentops = _packaged_agentops()
AGENTOPS_MANIFEST = "specs/agentops-manifest.json"
SELECTIVE_CAPABILITIES = (
    "run_history_present", "latest_eval_run_fresh", "thresholds_declared", "latest_pass_rate_ok",
)
STATUS_ORDER = {"must-fix": 0, "should-fix": 1, "not-verified": 2, "pass": 3}

CAPABILITY_IDS = {
    "eval_scenarios_present": "EVAL-001",
    "eval_datasets_present": "EVAL-002",
    "dataset_shape_ok": "EVAL-003",
    "thresholds_declared": "EVAL-004",
    "schedule_present": "EVAL-005",
    "run_history_present": "EVAL-006",
    "online_eval_wired": "EVAL-101",
    "latest_eval_run_fresh": "EVAL-102/EVAL-103",
    "alert_wired": "EVAL-104",
    "latest_pass_rate_ok": "EVAL-105",
    "ab_comparison_present": "F3",
    # Gate policy (#209, #210): evaluated from evals/azure.eval.yaml + evals/gate-runs.json.
    "rubric_trajectory_mapped": "EVAL-106",
    "gate_split_ok": "EVAL-107",
    "gate_evaluator_suitable": "EVAL-108",
    "gate_runs_recorded": "EVAL-109",
}
CAPABILITY_ORDER = tuple(CAPABILITY_IDS.keys())

TEXT_EXT = (
    ".py", ".ts", ".js", ".yaml", ".yml", ".json", ".jsonl",
    ".toml", ".md", ".bicep", ".txt",
)
DATASET_EXT = (".json", ".jsonl", ".csv")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".azure",
             ".agentops", ".threadlight"}
MAX_BYTES = 512 * 1024
GENERATED_FILES = {"docs/evals-report.md", "specs/evals-manifest.json"}

SCENARIO_MARKERS = re.compile(r"eval(?:uation)?\s+scenarios?|scenario[_ -]?id", re.I)
THRESHOLD_MARKERS = re.compile(r"\b(threshold|min[_-]?score|min[_-]?pass[_-]?rate|pass[_-]?threshold)\b", re.I)
PLAN_A_MARKERS = re.compile(r"(evaluation_rules|EvaluationRuleAction|create_agent_evaluation|"
                            r"continuous[-_ ]?eval|continuous[-_ ]?evaluation)", re.I)
SCHEDULE_MARKERS = re.compile(r"\b(schedule|cron|timer|recurrence|rate\(|0\s+\d+\s+\*|daily|weekly)\b", re.I)
PLAN_B_MARKERS = re.compile(r"(foundry[-_ ]?evals|evals?_check|evaluation|eval dataset|python.*eval)", re.I)
# Foundry evaluation rules (azure-ai-projects 2.x): continuous, scheduled trace or
# scheduled dataset rules, in code or declared in azure.eval.yaml.
RULE_ACTIONS = re.compile(r"\b(ContinuousEvaluationRuleAction|TraceEvaluationRuleAction|"
                          r"DatasetEvaluationRuleAction)\b")
RULE_CALL = re.compile(r"evaluation_rules\s*\.\s*create_or_update\s*\(|evaluationRules\.createOrUpdate\s*\(")
# Classic-portal API; absent from azure-ai-projects 2.x. Reported as legacy.
ONLINE_MARKERS = re.compile(r"create_agent_evaluation\s*\(", re.I)
APP_INSIGHTS_MARKERS = re.compile(r"(app[_-]?insights|applicationinsights|connection[_-]?string|APPINSIGHTS)", re.I)
AB_MARKERS = re.compile(r"(champion|challenger|baseline_vs|baseline[-_ ]?vs|A/B|ab[-_ ]?comparison)", re.I)
ALERT_MARKERS = re.compile(r"(metricAlerts|Microsoft\.Insights/metricAlerts|alert|threshold breach|notify|notification|actionGroups)", re.I)
EVAL_MARKERS = re.compile(r"\bevals?\b|evaluation|continuous[-_ ]?eval|pass[_-]?rate", re.I)
DATE_IN_NAME = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")


def _read(path: str) -> str:
    if ".agentops" in Path(path).resolve().parts:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(MAX_BYTES)
    except OSError:
        return ""


def _walk(root: str):
    if not os.path.isdir(root):
        return
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = os.path.join(base, name)
            if os.path.islink(path):
                continue
            if _rel(root, path) in GENERATED_FILES:
                continue
            if name == "agentops.yaml" or name.startswith("agentops-"):
                continue
            if name.endswith(TEXT_EXT):
                yield path


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _has_dir(root: str, rel: str) -> bool:
    return os.path.isdir(os.path.join(root, *rel.split("/")))


def _under(rel: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in prefixes)


def _cap(caps: dict, key: str, status: str, evidence=None, hint=None) -> None:
    caps[key] = {
        "check_id": CAPABILITY_IDS[key],
        "status": status,
        "evidence": evidence,
        "hint": hint,
    }


def _spec_files(root: str) -> list[str]:
    specs = os.path.join(root, "specs")
    out: list[str] = []
    if os.path.isdir(specs):
        for name in os.listdir(specs):
            low = name.lower()
            if low == "spec.md" or (low.startswith("spec") and low.endswith(".md")):
                out.append(os.path.join(specs, name))
    for name in ("SPEC.md", "spec.md"):
        cand = os.path.join(root, name)
        if os.path.isfile(cand):
            out.append(cand)
    return out


def _dataset_files(root: str) -> list[str]:
    out: list[str] = []
    for path in _walk(root) or []:
        rel = _rel(root, path)
        base = os.path.basename(path).lower()
        if not base.endswith(DATASET_EXT):
            continue
        if _under(rel, ("evals/runs/", "docs/eval-runs/", "evals/ab/")):
            continue
        if _under(rel, ("evals/", "specs/evals/")):
            out.append(path)
    return out


def _json_has_tool_shape(obj) -> bool:
    if isinstance(obj, dict):
        if "tool_calls" in obj and "tool_outputs" in obj:
            return True
        return any(_json_has_tool_shape(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_json_has_tool_shape(v) for v in obj)
    return False


def _dataset_shape_file(path: str) -> bool:
    try:
        if path.endswith(".jsonl"):
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        if _json_has_tool_shape(json.loads(line)):
                            return True
                    except json.JSONDecodeError:
                        continue
            return False
        if path.endswith(".json"):
            return _json_has_tool_shape(json.loads(_read(path)))
    except (OSError, json.JSONDecodeError):
        return False
    return False


def _find_scenarios(root: str) -> tuple[int, str | None]:
    for path in _spec_files(root):
        text = _read(path)
        if SCENARIO_MARKERS.search(text) and re.search(r"\b(eval|evaluation)\b", text, re.I):
            return 1, _rel(root, path)
    count = 0
    first = None
    for path in _walk(root) or []:
        rel = _rel(root, path)
        if not _under(rel, ("evals/", "specs/evals/")):
            continue
        if _under(rel, ("evals/runs/", "evals/ab/")):
            continue
        base = os.path.basename(rel).lower()
        text = _read(path)
        if "scenario" in base or SCENARIO_MARKERS.search(text):
            count += 1
            first = first or rel
    return count, first


def _find_threshold(root: str) -> str | None:
    for path in _walk(root) or []:
        rel = _rel(root, path)
        if _under(rel, ("evals/", "specs/")) or os.path.basename(rel).lower().startswith("spec"):
            if THRESHOLD_MARKERS.search(_read(path)):
                return rel
    return None


def _schedule_plan(root: str) -> tuple[str | None, str | None]:
    rules = _rules_wiring(root)
    if rules:
        return "Plan A", rules
    for path in _walk(root) or []:
        rel = _rel(root, path)
        text = _read(path)
        if _under(rel, ("infra/", "evals/")) and PLAN_A_MARKERS.search(text) and SCHEDULE_MARKERS.search(text):
            return "Plan A", rel
    workflows = os.path.join(root, ".github", "workflows")
    if os.path.isdir(workflows):
        for name in os.listdir(workflows):
            if not name.endswith((".yml", ".yaml")):
                continue
            path = os.path.join(workflows, name)
            text = _read(path)
            if "cron" in text and PLAN_B_MARKERS.search(text):
                return "Plan B", _rel(root, path)
    for path in _walk(root) or []:
        rel = _rel(root, path)
        text = _read(path)
        if _under(rel, ("infra/", "evals/")) and re.search(r"Microsoft\.App/jobs|containerapps.*job|aca[-_ ]?job", text, re.I):
            if SCHEDULE_MARKERS.search(text) and PLAN_B_MARKERS.search(text):
                return "Plan B", rel
    return None, None


def _run_files(root: str) -> list[str]:
    out: list[str] = []
    for path in _walk(root) or []:
        rel = _rel(root, path)
        if _under(rel, ("evals/runs/", "docs/eval-runs/")) and path.endswith((".json", ".md")):
            out.append(path)
    return out


def _date_from_path(path: str) -> _dt.datetime | None:
    m = DATE_IN_NAME.search(os.path.basename(path))
    if m:
        try:
            return _dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=_dt.timezone.utc)
        except ValueError:
            pass
    try:
        return _dt.datetime.fromtimestamp(os.path.getmtime(path), _dt.timezone.utc)
    except OSError:
        return None


def _latest_run(root: str) -> tuple[str | None, _dt.datetime | None]:
    latest_path = None
    latest_dt = None
    for path in _run_files(root):
        dt = _date_from_path(path)
        if dt is not None and (latest_dt is None or dt > latest_dt):
            latest_path = path
            latest_dt = dt
    return latest_path, latest_dt


def _rules_wiring(root: str) -> str | None:
    """Foundry evaluation rules: create_or_update with a rule action, or a rule
    action declared in azure.eval.yaml (continuous fires on response completion,
    trace/dataset rules on their own recurrence)."""
    for path in _walk(root) or []:
        rel = _rel(root, path)
        if rel.startswith(("docs/", "references/")):
            continue
        text = _read(path)
        if not RULE_ACTIONS.search(text):
            continue
        if RULE_CALL.search(text) or os.path.basename(path) in ("azure.eval.yaml", "azure.eval.yml"):
            return rel
    return None


def _online_wiring(root: str) -> str | None:
    for path in _walk(root) or []:
        rel = _rel(root, path)
        if rel.startswith(("docs/", "references/")):
            continue
        text = _read(path)
        if ONLINE_MARKERS.search(text) and APP_INSIGHTS_MARKERS.search(text):
            return rel
    return None


GATE_MIN_ROWS = 50
OPTIMIZER_EVAL_SUFFIX = "-optimizer-input"
HELDOUT_MARKERS = re.compile(r"held[-_ ]?out|holdout", re.I)
REFUSAL_MARKERS = re.compile(r"\b(escalat\w*|refus\w*|declin\w*|reject\w*|injection)\b", re.I)
TOOL_CODE_MARKERS = re.compile(r"@tool\b|FunctionTool\(|\"type\":\s*\"function_call\"")


def _eval_yaml(root: str):
    """(path, parsed) for evals/azure.eval.yaml; parsed is None when PyYAML is absent."""
    for name in ("azure.eval.yaml", "azure.eval.yml"):
        path = os.path.join(root, "evals", name)
        if os.path.isfile(path):
            try:
                import yaml  # optional: the rest of the checker stays stdlib-only
            except ImportError:
                return path, None
            try:
                data = yaml.safe_load(_read(path)) or {}
            except yaml.YAMLError:
                return path, {}
            return path, data if isinstance(data, dict) else {}
    return None, None


def _tool_using(root: str) -> bool:
    for path in _walk(root) or []:
        base = os.path.basename(path)
        if base == "tools.json":
            try:
                data = json.loads(_read(path))
            except ValueError:
                continue
            if isinstance(data, list) and data:
                return True
        elif base.endswith(".py") and TOOL_CODE_MARKERS.search(_read(path)):
            return True
        elif base.endswith(".jsonl") and '"tool_calls": [{' in _read(path).replace('":[{', '": [{'):
            return True
    return False


def _gate_evals(data: dict) -> list[dict]:
    return [e for e in (data.get("evals") or []) if isinstance(e, dict)
            and not str(e.get("name", "")).endswith(OPTIMIZER_EVAL_SUFFIX)]


def _dataset_rows(yaml_path: str, data: dict) -> tuple[dict, list[str]]:
    """Rows per declared dataset name (None when registered without a local file)."""
    rows, unknown = {}, []
    for item in data.get("datasets") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        file = item.get("file")
        path = os.path.normpath(os.path.join(os.path.dirname(yaml_path), file)) if file else None
        if not path or not os.path.isfile(path):
            rows[item["name"]] = None
            unknown.append(item["name"])
            continue
        lines = [json.loads(line) for line in _read(path).splitlines() if line.strip()]
        rows[item["name"]] = lines
    return rows, unknown


def _gate_policy(root: str, caps: dict) -> dict | None:
    yaml_path, data = _eval_yaml(root)
    keys = ("rubric_trajectory_mapped", "gate_split_ok", "gate_evaluator_suitable", "gate_runs_recorded")
    if yaml_path is None:
        _cap(caps, "rubric_trajectory_mapped", "not-applicable", None, "no evals/azure.eval.yaml")
        _cap(caps, "gate_split_ok", "should-fix", None,
             "declare a held-out gate in evals/azure.eval.yaml (scripts/eval_yaml.py split + scaffold)")
        _cap(caps, "gate_evaluator_suitable", "not-applicable", None, "no evals/azure.eval.yaml")
        _cap(caps, "gate_runs_recorded", "not-applicable", None, "no evals/azure.eval.yaml")
        return None
    rel = _rel(root, yaml_path)
    if data is None:
        for key in keys:
            _cap(caps, key, "not-verified", rel, "install PyYAML to read evals/azure.eval.yaml")
        return None
    gates = _gate_evals(data)
    if not gates:
        for key in keys:
            _cap(caps, key, "must-fix" if key == "gate_split_ok" else "not-verified", rel,
                 "azure.eval.yaml declares no gate eval (only *-optimizer-input evals)")
        return None

    # EVAL-106: a custom rubric on a tool-using agent must see the trajectory.
    unmapped = []
    for ev in gates:
        if (ev.get("target") or {}).get("type") != "agent":
            continue
        for ref in ev.get("evaluators") or []:
            name = str((ref or {}).get("evaluator", ""))
            if not name or name.startswith("builtin."):
                continue
            response = ((ref.get("dataMapping") or {}).get("response") or "{{sample.output_text}}")
            if "output_items" not in response:
                unmapped.append(f"{ev.get('name')}:{name}")
    if not _tool_using(root):
        _cap(caps, "rubric_trajectory_mapped", "not-applicable", rel, "agent declares no tools")
    elif unmapped:
        _cap(caps, "rubric_trajectory_mapped", "should-fix", ", ".join(unmapped),
             "custom rubric response maps to sample.output_text (azd default) on a tool-using agent; "
             "set dataMapping response {{sample.output_items}}, tool_calls, tool_definitions, ground_truth")
    else:
        _cap(caps, "rubric_trajectory_mapped", "pass", rel)

    # EVAL-107: held-out gate data and >= 50 golden rows.
    rows, unknown = _dataset_rows(yaml_path, data)
    gate_sets = {str(ev.get("dataset", "")) for ev in gates}
    held = [d for d in gate_sets if HELDOUT_MARKERS.search(d) or any(
        (r.get("split") in ("held_out", "heldout")) for r in (rows.get(d) or []))]
    optimizer_sets = {str(e.get("dataset", "")) for e in data.get("evals") or []
                      if isinstance(e, dict) and str(e.get("name", "")).endswith(OPTIMIZER_EVAL_SUFFIX)}
    total = sum(len(v) for v in rows.values() if v is not None)
    if not held or set(held) & optimizer_sets:
        _cap(caps, "gate_split_ok", "must-fix", rel,
             "gate eval must use a held-out dataset (~30%) that the optimizer eval never sees")
    elif unknown:
        _cap(caps, "gate_split_ok", "not-verified", rel,
             f"registered datasets without a local file cannot be counted: {', '.join(unknown)}")
    elif total < GATE_MIN_ROWS:
        _cap(caps, "gate_split_ok", "must-fix", f"{total} golden rows",
             f"need >= {GATE_MIN_ROWS} golden rows covering every decision branch and adversarial class")
    else:
        _cap(caps, "gate_split_ok", "pass", f"{total} golden rows; held-out: {', '.join(sorted(held))}")

    # EVAL-108: task_completion penalises correct refusals and escalations.
    completion = [str(ev.get("name")) for ev in gates
                  if any((r or {}).get("evaluator") == "builtin.task_completion" for r in ev.get("evaluators") or [])]
    spec_text = "\n".join(_read(p) for p in _spec_files(root))
    if completion and REFUSAL_MARKERS.search(spec_text):
        _cap(caps, "gate_evaluator_suitable", "should-fix", ", ".join(completion),
             "builtin.task_completion gates a SPEC with refusal/escalation criteria and scores them as "
             "failures; gate with the rubric + builtin.task_adherence or builtin.intent_resolution")
    else:
        _cap(caps, "gate_evaluator_suitable", "pass", rel)

    # EVAL-109: >= 2 gate runs, mean vs floor and vs baseline - tolerance.
    runs_path = os.path.join(root, "evals", "gate-runs.json")
    try:
        record = json.loads(_read(runs_path)) if os.path.isfile(runs_path) else None
    except ValueError:
        record = None
    if not isinstance(record, dict):
        _cap(caps, "gate_runs_recorded", "should-fix", None,
             "record >= 2 gate runs, baseline run id, tolerance and floor in evals/gate-runs.json")
        return None
    runs = [r for r in record.get("runs") or [] if isinstance(r, dict)
            and isinstance(r.get("pass_rate"), (int, float)) and r.get("run_id")]
    baseline = record.get("baseline") if isinstance(record.get("baseline"), dict) else None
    tolerance, floor = record.get("tolerance"), record.get("floor")
    mean = round(sum(r["pass_rate"] for r in runs) / len(runs), 4) if runs else None
    gate = {"eval": record.get("eval"), "runs": [{"run_id": r["run_id"], "pass_rate": r["pass_rate"]} for r in runs],
            "mean": mean, "baseline_run_id": (baseline or {}).get("run_id"),
            "baseline_pass_rate": (baseline or {}).get("pass_rate"), "tolerance": tolerance,
            "floor": floor, "decision": "not-decided"}
    if len(runs) < 2 or not isinstance(tolerance, (int, float)) or not isinstance(floor, (int, float)):
        _cap(caps, "gate_runs_recorded", "should-fix", _rel(root, runs_path),
             "a gate decision needs >= 2 runs (mean), a numeric tolerance and an absolute floor")
        return gate
    spread = max(r["pass_rate"] for r in runs) - min(r["pass_rate"] for r in runs)
    below_floor = mean < floor
    regressed = bool(baseline) and mean < baseline.get("pass_rate", 0) - tolerance
    gate["decision"] = "fail" if below_floor or regressed else "pass"
    if gate["decision"] == "fail":
        _cap(caps, "gate_runs_recorded", "must-fix", f"mean {mean}",
             f"gate fails: mean {mean} vs floor {floor}"
             + (f" and baseline {baseline.get('pass_rate')} - tolerance {tolerance}" if baseline else ""))
    elif tolerance < spread:
        _cap(caps, "gate_runs_recorded", "should-fix", f"mean {mean}",
             f"tolerance {tolerance} is below the measured run-to-run noise {round(spread, 4)}")
    elif not baseline:
        _cap(caps, "gate_runs_recorded", "should-fix", f"mean {mean}",
             "no baseline run id: record the last accepted run to compare against")
    else:
        _cap(caps, "gate_runs_recorded", "pass", f"mean {mean} over {len(runs)} runs")
    return gate


def _ab_comparison(root: str) -> str | None:
    if _has_dir(root, "evals/ab"):
        return "evals/ab/"
    for path in _walk(root) or []:
        rel = _rel(root, path)
        if _under(rel, ("evals/", "scripts/", "src/", "specs/")) and AB_MARKERS.search(_read(path)):
            return rel
    return None


def _alert(root: str) -> str | None:
    for path in _walk(root) or []:
        rel = _rel(root, path)
        text = _read(path)
        if ALERT_MARKERS.search(text) and EVAL_MARKERS.search(text):
            return rel
    return None


def _threshold_value(root: str) -> float | None:
    patterns = (
        re.compile(r"(?:threshold|min[_-]?score|min[_-]?pass[_-]?rate|pass[_-]?threshold)\s*[:=]\s*([0-9.]+)", re.I),
    )
    for path in _walk(root) or []:
        rel = _rel(root, path)
        if not (_under(rel, ("evals/", "specs/")) or os.path.basename(rel).lower().startswith("spec")):
            continue
        text = _read(path)
        for pat in patterns:
            m = pat.search(text)
            if m:
                try:
                    val = float(m.group(1))
                    return val / 100.0 if val > 1 else val
                except ValueError:
                    continue
    return None


def _pass_rate_from_run(path: str | None) -> float | None:
    if not path or not path.endswith(".json"):
        return None
    try:
        data = json.loads(_read(path))
    except json.JSONDecodeError:
        return None

    def scan(obj):
        if isinstance(obj, dict):
            for key in ("pass_rate", "passRate", "score", "quality_score"):
                if key in obj:
                    try:
                        val = float(obj[key])
                        return val / 100.0 if val > 1 else val
                    except (TypeError, ValueError):
                        pass
            for value in obj.values():
                found = scan(value)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = scan(item)
                if found is not None:
                    return found
        return None

    return scan(data)


def _agentops_evals(root: str, freshness_days: int) -> list[dict]:
    if agentops is None:
        return []
    try:
        selected = agentops.discover_opted_in_agents(Path(root))
    except agentops.AgentOpsValidationError:
        selected = [{"agent_key": "unresolved", "root": "."}]
    if not selected:
        return []
    try:
        document = agentops.load_manifest(Path(root))
        source_digest = hashlib.sha256(json.dumps(
            document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        agents = {item["agent_key"]: item for item in document["agents"]}
    except agentops.AgentOpsValidationError:
        agents = {}
        source_digest = None
    records = []
    for selected_agent in selected:
        key = selected_agent["agent_key"]
        domain = agents.get(key, {}).get("domains", {}).get("evals")
        record = {
            "agent_key": key, "root": selected_agent["root"],
            "service": selected_agent.get("service"),
            "source": AGENTOPS_MANIFEST, "evidence_refs": [],
            "source_manifest_sha256": source_digest,
            "artifacts": {}, "receipt_sha256": None,
            "domain_status": domain.get("status", "not-verified") if isinstance(domain, dict) else "not-verified",
            "represented_blockers": [],
            "execution_pass_rate": None, "aggregate_metrics": {}, "thresholds": [],
            "comparison": None, "finished_at": None,
            "capabilities": {name: "not-verified" for name in SELECTIVE_CAPABILITIES},
        }
        if isinstance(domain, dict) and domain.get("status") == "verified":
            summary = domain.get("summary") or {}
            thresholds = summary.get("thresholds") or []
            metrics = summary.get("aggregate_metrics") or {}
            finished = summary.get("finished_at")
            record.update(
                evidence_refs=sorted(agents[key].get("provenance", {}).get("artifacts", {})),
                artifacts=agents[key].get("provenance", {}).get("artifacts", {}),
                receipt_sha256=agents[key].get("provenance", {}).get("receipt_sha256"),
                execution_pass_rate=summary.get("items_pass_rate"),
                aggregate_metrics=metrics,
                thresholds=[{"metric": item["metric"], "passed": item["passed"]}
                            for item in thresholds],
                finished_at=finished,
            )
            states = record["capabilities"]
            if summary.get("items_total", 0) > 0:
                states["run_history_present"] = "pass"
                try:
                    stamp = _dt.datetime.fromisoformat(finished.replace("Z", "+00:00"))
                    age = (_dt.datetime.now(_dt.timezone.utc) - stamp).total_seconds()
                    states["latest_eval_run_fresh"] = (
                        "pass" if 0 <= age <= freshness_days * 86400 else "not-verified")
                except (ValueError, TypeError, AttributeError):
                    pass
            if thresholds and metrics:
                states["thresholds_declared"] = "pass"
                quality_passed = (
                    summary.get("overall_passed") is True
                    and summary.get("items_passed_all") == summary.get("items_total")
                    and all(item["passed"] is True for item in thresholds)
                )
                comparison = summary.get("comparison")
                if isinstance(comparison, dict):
                    record["comparison"] = {
                        "status": comparison.get("status"),
                        "regressions": comparison.get("regressions"),
                    }
                states["latest_pass_rate_ok"] = "pass" if quality_passed else "should-fix"
                if domain.get("verdict") == "fail":
                    states["latest_pass_rate_ok"] = "should-fix"
                elif domain.get("verdict") != "pass":
                    states["latest_pass_rate_ok"] = "not-verified"
                if any(item.get("severity") == "must-fix" and item.get("code") == "AOPS-EVAL-QUALITY"
                       for item in domain.get("blockers", [])):
                    states["latest_pass_rate_ok"] = "must-fix"
                    record["represented_blockers"] = ["AOPS-EVAL-QUALITY"]
                if states["latest_pass_rate_ok"] == "pass" and states["latest_eval_run_fresh"] != "pass":
                    states["latest_pass_rate_ok"] = "not-verified"
        records.append(record)
    return records


def _merge_agentops(caps: dict, records: list[dict]) -> None:
    for key in SELECTIVE_CAPABILITIES:
        native = dict(caps[key])
        statuses = [record["capabilities"][key] for record in records]
        # Missing legacy evidence may be filled; an observed legacy failure may not.
        if native["status"] == "pass" or (
            native["status"] in ("must-fix", "should-fix") and native.get("evidence")
        ):
            statuses.append(native["status"])
        status = min(statuses, key=lambda value: STATUS_ORDER[value])
        _cap(caps, key, status, AGENTOPS_MANIFEST,
             "worst validated per-agent result; continuous wiring is assessed separately")
        caps[key]["sources"] = [{
            "source": "threadlight-native", "status": native["status"],
            "evidence": native.get("evidence"),
        }] + [{
            "source": AGENTOPS_MANIFEST, "agent_key": record["agent_key"],
            "status": record["capabilities"][key], "evidence_refs": record["evidence_refs"],
        } for record in records]
    caps["run_history_present"]["agentops_agents"] = records


# ── capability evaluation ────────────────────────────────────────────────
def evaluate(root: str, freshness_days: int = 7, accept_legacy: bool = False) -> dict:
    root = os.path.abspath(root)
    caps: dict[str, dict] = {}

    scenario_count, scenario_evidence = _find_scenarios(root)
    if scenario_count > 0:
        _cap(caps, "eval_scenarios_present", "pass", f"{scenario_count} scenario marker(s); first: {scenario_evidence}")
    else:
        _cap(caps, "eval_scenarios_present", "must-fix", None,
             "SPEC § 9 has no eval scenarios and no evals/ scenario files were found")

    datasets = _dataset_files(root)
    if datasets:
        _cap(caps, "eval_datasets_present", "pass", ", ".join(_rel(root, p) for p in datasets[:3]))
    else:
        _cap(caps, "eval_datasets_present", "must-fix", None,
             "store held-out datasets under evals/ or specs/evals/")

    shaped = next((_rel(root, p) for p in datasets if _dataset_shape_file(p)), None)
    if shaped:
        _cap(caps, "dataset_shape_ok", "pass", shaped)
    elif datasets:
        _cap(caps, "dataset_shape_ok", "should-fix", None,
             "at least one dataset row must include tool_calls and tool_outputs")
    else:
        _cap(caps, "dataset_shape_ok", "not-verified", None, "no dataset to inspect")

    threshold_file = _find_threshold(root)
    if threshold_file:
        _cap(caps, "thresholds_declared", "pass", threshold_file)
    else:
        _cap(caps, "thresholds_declared", "should-fix", None,
             "declare per-scenario threshold / min_score / min_pass_rate values")

    plan, plan_file = _schedule_plan(root)
    if plan:
        _cap(caps, "schedule_present", "pass", f"{plan}: {plan_file}")
    else:
        _cap(caps, "schedule_present", "must-fix", None,
             "wire Plan A Foundry Continuous Evaluation or Plan B cron-based evals")

    runs = _run_files(root)
    if runs:
        _cap(caps, "run_history_present", "pass", ", ".join(_rel(root, p) for p in runs[:3]))
    else:
        _cap(caps, "run_history_present", "should-fix", None,
             "commit latest eval run output under evals/runs/*.json or docs/eval-runs/")

    rules = _rules_wiring(root)
    legacy = None if rules else _online_wiring(root)
    if rules:
        _cap(caps, "online_eval_wired", "pass", f"evaluation_rules: {rules}")
    elif legacy and accept_legacy:
        _cap(caps, "online_eval_wired", "pass", f"legacy create_agent_evaluation: {legacy}",
             "accepted with --accept-legacy-online-eval; migrate to Foundry evaluation_rules")
    elif legacy:
        _cap(caps, "online_eval_wired", "must-fix" if plan == "Plan A" else "should-fix", legacy,
             "legacy create_agent_evaluation (classic portal; absent from azure-ai-projects 2.x): "
             "replace with evaluation_rules.create_or_update using ContinuousEvaluationRuleAction, "
             "TraceEvaluationRuleAction or DatasetEvaluationRuleAction")
    elif plan == "Plan A":
        _cap(caps, "online_eval_wired", "must-fix", None,
             "Plan A declared but no evaluation_rules.create_or_update(...) rule wiring found")
    elif plan == "Plan B":
        _cap(caps, "online_eval_wired", "should-fix", None,
             "Plan B fallback detected; add a Foundry evaluation rule for live traffic when available")
    else:
        _cap(caps, "online_eval_wired", "should-fix", None,
             "no Foundry evaluation_rules wiring (continuous, scheduled trace or dataset rule) found")
    if legacy:
        caps["online_eval_wired"]["legacy"] = True

    latest_path, latest_dt = _latest_run(root)
    if latest_path and latest_dt:
        now = _dt.datetime.now(_dt.timezone.utc)
        age_days = round((now - latest_dt).total_seconds() / 86400.0, 1)
        if age_days <= freshness_days:
            _cap(caps, "latest_eval_run_fresh", "pass", f"{_rel(root, latest_path)} ({age_days}d old <= {freshness_days}d)")
        else:
            _cap(caps, "latest_eval_run_fresh", "should-fix", f"{_rel(root, latest_path)} ({age_days}d old)",
                 f"latest eval run is stale (> {freshness_days}d)")
    elif runs:
        _cap(caps, "latest_eval_run_fresh", "not-verified", None, "run history exists but no date/mtime could be read")
    else:
        _cap(caps, "latest_eval_run_fresh", "not-verified", None, "no eval run history to age")

    alert = _alert(root)
    if alert:
        _cap(caps, "alert_wired", "pass", alert)
    else:
        _cap(caps, "alert_wired", "must-fix", None,
             "wire an alert rule or workflow notification for eval threshold breach")

    pass_rate = _pass_rate_from_run(latest_path)
    min_rate = _threshold_value(root)
    if pass_rate is not None and min_rate is not None:
        if pass_rate >= min_rate:
            _cap(caps, "latest_pass_rate_ok", "pass", f"pass_rate={pass_rate:.2f} >= threshold={min_rate:.2f}")
        else:
            _cap(caps, "latest_pass_rate_ok", "should-fix", f"pass_rate={pass_rate:.2f} < threshold={min_rate:.2f}",
                 "latest eval pass rate is below the declared minimum")
    elif latest_path:
        _cap(caps, "latest_pass_rate_ok", "not-verified", _rel(root, latest_path),
             "could not read both pass_rate and declared threshold")
    else:
        _cap(caps, "latest_pass_rate_ok", "not-verified", None, "no latest eval run to score")

    ab = _ab_comparison(root)
    if ab:
        _cap(caps, "ab_comparison_present", "pass", ab)
    else:
        _cap(caps, "ab_comparison_present", "should-fix", None,
             "add champion/challenger comparison config or evals/ab/ gate before swaps")

    gate = _gate_policy(root, caps)
    if gate is not None:
        caps["gate_runs_recorded"]["gate"] = gate

    records = _agentops_evals(root, freshness_days)
    if records:
        _merge_agentops(caps, records)
    return {key: caps[key] for key in CAPABILITY_ORDER}


def manifest(root: str, caps: dict, freshness_days: int = 7) -> dict:
    must = [k for k in CAPABILITY_ORDER if caps[k]["status"] == "must-fix"]
    should = [k for k in CAPABILITY_ORDER if caps[k]["status"] == "should-fix"]
    notv = [k for k in CAPABILITY_ORDER if caps[k]["status"] == "not-verified"]

    offline_basics = all(caps[k]["status"] == "pass" for k in (
        "eval_scenarios_present", "eval_datasets_present", "dataset_shape_ok", "thresholds_declared"))
    if not must and not should and not notv:
        verdict = "comprehensive"
    elif must:
        verdict = "offline-only" if offline_basics else "none"
    else:
        verdict = "partial"

    latest_path, _ = _latest_run(root)
    metrics = {
        "pass_rate": _pass_rate_from_run(latest_path),
        "threshold": _threshold_value(root),
        "latest_run": _rel(root, latest_path) if latest_path else None,
        "gate": caps.get("gate_runs_recorded", {}).pop("gate", None),
    }

    result = {
        "schema": MANIFEST_SCHEMA,
        "tool_version": VERSION,
        "captured_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "freshness_window_days": freshness_days,
        "verdict": verdict,
        "must_fix": must,
        "should_fix": should,
        "not_verified": notv,
        "metrics": metrics,
        "capabilities": {key: caps[key] for key in CAPABILITY_ORDER},
    }
    records = caps["run_history_present"].get("agentops_agents")
    if records:
        result["agentops"] = {
            "source": AGENTOPS_MANIFEST, "agents": records,
            "source_manifest_sha256": records[0]["source_manifest_sha256"],
        }
        if metrics["latest_run"] is None and all(
            record["capabilities"]["run_history_present"] == "pass" for record in records
        ):
            metrics["latest_run"] = AGENTOPS_MANIFEST
        if any(record["capabilities"]["latest_pass_rate_ok"] != "pass" for record in records):
            if (metrics["pass_rate"] is not None and metrics["threshold"] is not None
                    and metrics["pass_rate"] >= metrics["threshold"]):
                metrics["pass_rate"] = None
        result["capabilities"] = {
            key: {k: v for k, v in value.items() if k != "agentops_agents"}
            for key, value in result["capabilities"].items()
        }
    return result


def _clean_cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(man: dict) -> str:
    icon = {"pass": "✅", "must-fix": "❌", "should-fix": "🟠",
            "not-verified": "⚪", "not-applicable": "➖"}
    lines = [
        "# Continuous evals — manifest report",
        "",
        f"> Verdict: **{man['verdict'].upper()}** · freshness `{man['freshness_window_days']}d` · captured {man['captured_at']}",
        "",
        "| Capability | Pillar ID | Status | Evidence / hint |",
        "|---|---|---|---|",
    ]
    for key, v in man["capabilities"].items():
        detail = v.get("evidence") or v.get("hint") or ""
        lines.append(
            f"| `{key}` | `{v.get('check_id', '')}` | {icon.get(v['status'], '?')} {v['status']} | {_clean_cell(detail)} |"
        )
    if man.get("agentops"):
        lines += ["", "## AgentOps batch evidence", "",
                  "Execution pass rate is not a per-row quality pass rate.",
                  "", "| Agent | Quality status | Execution pass rate | Source |",
                  "|---|---|---:|---|"]
        for agent in man["agentops"]["agents"]:
            lines.append(
                f"| `{_clean_cell(agent['agent_key'])}` | "
                f"{agent['capabilities']['latest_pass_rate_ok']} | "
                f"{agent['execution_pass_rate'] if agent['execution_pass_rate'] is not None else '—'} | "
                f"`{AGENTOPS_MANIFEST}` |")
    lines += ["", "Consumed by `threadlight-production-ready` pillar 6 (`continuous-evals`).", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="threadlight-evals manifest validator")
    ap.add_argument("--target", default=".", help="pilot repo root (default cwd)")
    ap.add_argument("--emit", action="store_true",
                    help="write specs/evals-manifest.json + docs/evals-report.md")
    ap.add_argument("--gate", action="store_true",
                    help="exit 2 when any capability is must-fix")
    ap.add_argument("--json", action="store_true", help="print manifest JSON to stdout")
    ap.add_argument("--accept-legacy-online-eval", action="store_true",
                    help="let create_agent_evaluation wiring satisfy EVAL-101 (labelled legacy)")
    ap.add_argument("--freshness-days", type=int, default=7,
                    help="eval run freshness window (default 7d)")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.target)
    try:
        caps = evaluate(root, args.freshness_days, accept_legacy=args.accept_legacy_online_eval)
        man = manifest(root, caps, args.freshness_days)
    except Exception as exc:  # graceful top-level degradation
        caps = {key: {
            "check_id": CAPABILITY_IDS[key],
            "status": "not-verified",
            "evidence": None,
            "hint": f"validator could not complete: {exc}",
        } for key in CAPABILITY_ORDER}
        man = manifest(root, caps, args.freshness_days)

    if args.emit:
        os.makedirs(os.path.join(root, "specs"), exist_ok=True)
        os.makedirs(os.path.join(root, "docs"), exist_ok=True)
        with open(os.path.join(root, "specs", "evals-manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(man, fh, indent=2)
            fh.write("\n")
        with open(os.path.join(root, "docs", "evals-report.md"), "w", encoding="utf-8") as fh:
            fh.write(render(man))

    if args.json:
        print(json.dumps(man, indent=2))
    else:
        print(render(man))

    if args.gate and man["must_fix"]:
        print(f"\nGATE: {len(man['must_fix'])} must-fix capability(ies): "
              f"{', '.join(man['must_fix'])}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
