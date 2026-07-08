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

stdlib-only. Gracefully degrading — capabilities that cannot be checked are
reported as ``not-verified`` rather than crashing.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

VERSION = "0.1.1"
MANIFEST_SCHEMA = "threadlight-evals-manifest/v1"

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
}
CAPABILITY_ORDER = tuple(CAPABILITY_IDS.keys())

TEXT_EXT = (
    ".py", ".ts", ".js", ".yaml", ".yml", ".json", ".jsonl",
    ".toml", ".md", ".bicep", ".txt",
)
DATASET_EXT = (".json", ".jsonl", ".csv")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".azure"}
MAX_BYTES = 512 * 1024
GENERATED_FILES = {"docs/evals-report.md", "specs/evals-manifest.json"}

SCENARIO_MARKERS = re.compile(r"eval(?:uation)?\s+scenarios?|scenario[_ -]?id", re.I)
THRESHOLD_MARKERS = re.compile(r"\b(threshold|min[_-]?score|min[_-]?pass[_-]?rate|pass[_-]?threshold)\b", re.I)
PLAN_A_MARKERS = re.compile(r"(create_agent_evaluation|continuous[-_ ]?eval|continuous[-_ ]?evaluation)", re.I)
SCHEDULE_MARKERS = re.compile(r"\b(schedule|cron|timer|recurrence|rate\(|0\s+\d+\s+\*|daily|weekly)\b", re.I)
PLAN_B_MARKERS = re.compile(r"(foundry[-_ ]?evals|evals?_check|evaluation|eval dataset|python.*eval)", re.I)
ONLINE_MARKERS = re.compile(r"create_agent_evaluation\s*\(", re.I)
APP_INSIGHTS_MARKERS = re.compile(r"(app[_-]?insights|applicationinsights|connection[_-]?string|APPINSIGHTS)", re.I)
AB_MARKERS = re.compile(r"(champion|challenger|baseline_vs|baseline[-_ ]?vs|A/B|ab[-_ ]?comparison)", re.I)
ALERT_MARKERS = re.compile(r"(metricAlerts|Microsoft\.Insights/metricAlerts|alert|threshold breach|notify|notification|actionGroups)", re.I)
EVAL_MARKERS = re.compile(r"\bevals?\b|evaluation|continuous[-_ ]?eval|pass[_-]?rate", re.I)
DATE_IN_NAME = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")


def _read(path: str) -> str:
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
            if _rel(root, path) in GENERATED_FILES:
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


def _online_wiring(root: str) -> str | None:
    for path in _walk(root) or []:
        rel = _rel(root, path)
        if rel.startswith(("docs/", "references/")):
            continue
        text = _read(path)
        if ONLINE_MARKERS.search(text) and APP_INSIGHTS_MARKERS.search(text):
            return rel
    return None


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


# ── capability evaluation ────────────────────────────────────────────────
def evaluate(root: str, freshness_days: int = 7) -> dict:
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

    online = _online_wiring(root)
    if online:
        _cap(caps, "online_eval_wired", "pass", online)
    elif plan == "Plan A":
        _cap(caps, "online_eval_wired", "must-fix", None,
             "Plan A declared but no create_agent_evaluation(..., app_insights_connection_string=...) wiring found")
    elif plan == "Plan B":
        _cap(caps, "online_eval_wired", "should-fix", None,
             "Plan B fallback detected; wire Foundry Continuous Evaluation for live threads when available")
    else:
        _cap(caps, "online_eval_wired", "should-fix", None,
             "no live create_agent_evaluation wiring with Application Insights connection found")

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
    }

    return {
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
    ap.add_argument("--freshness-days", type=int, default=7,
                    help="eval run freshness window (default 7d)")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.target)
    try:
        caps = evaluate(root, args.freshness_days)
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
