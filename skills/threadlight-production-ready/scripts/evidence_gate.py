#!/usr/bin/env python3
"""A tiny, stdlib-only evidence gate used by production readiness lifecycle.

Implements a strict, minimal evaluator for the Task 1 tests. Designed to be
imported by tests (evaluate_evidence) and runnable as a CLI (main()).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


class EvidenceGateError(ValueError):
    """Raised on any validation / evidence problem."""


# Allowed schemas and verdicts
_ASSURANCE_SPECS = {
    "govern": ("threadlight-govern-manifest/v2", {"governed", "partial", "ungoverned"}, "governed"),
    "evals": ("threadlight-evals-manifest/v1", {"comprehensive", "partial", "offline-only", "none"}, "comprehensive"),
    "redteam": ("threadlight-redteam-manifest/v1", {"hardened", "partial", "vulnerable"}, "hardened"),
}
_CAPABILITY_STATUSES = {"pass", "must-fix", "should-fix", "not-verified", "not-applicable"}
_GOVERN_CAPABILITIES = {
    "policy_artefact_present",
    "policy_schema_valid",
    "policy_versioned",
    "policy_default_deny",
    "sensitive_action_rules_present",
    "policy_tests_present",
    "ci_gate_present",
    "attestation_present",
    "attestation_fresh",
    "asi_reference_present",
}
_EVALS_CAPABILITIES = {
    "eval_scenarios_present",
    "eval_datasets_present",
    "dataset_shape_ok",
    "thresholds_declared",
    "schedule_present",
    "run_history_present",
    "online_eval_wired",
    "latest_eval_run_fresh",
    "alert_wired",
    "latest_pass_rate_ok",
    "ab_comparison_present",
}
_REDTEAM_CAPABILITIES = {
    "scan_present",
    "scan_fresh",
    "jailbreak_asr_ok",
    "prompt_injection_asr_ok",
    "exfiltration_asr_ok",
    "harmful_content_asr_ok",
    "coverage_ok",
}
_REDTEAM_ASR_KEYS = {
    "jailbreak",
    "prompt_injection",
    "indirect_attack",
    "exfiltration",
    "harmful_content",
}
_REDTEAM_FINDING_IDS = {"SAFE-101", "SAFE-102", "SAFE-103", "SAFE-104", "SAFE-105", "SAFE-106"}


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception as e:  # pragma: no cover - defensive
        raise EvidenceGateError(f"unable to read {path}: {e}")
    try:
        data = json.loads(txt)
    except json.JSONDecodeError as e:
        raise EvidenceGateError(f"{path} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise EvidenceGateError(f"{path} must be a JSON object")
    return data


def _is_string_or_none(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_capability(
    manifest_name: str,
    capability_name: str,
    capability: Any,
    *,
    require_check_id: bool = False,
    allow_finding_id: bool = False,
    forbid_extra_fields: bool = False,
) -> None:
    if not isinstance(capability, dict):
        raise EvidenceGateError(f"{manifest_name} capability {capability_name!r} must be an object")
    if capability.get("status") not in _CAPABILITY_STATUSES:
        raise EvidenceGateError(f"{manifest_name} capability {capability_name!r} has invalid status")
    if require_check_id and not isinstance(capability.get("check_id"), str):
        raise EvidenceGateError(f"{manifest_name} capability {capability_name!r} missing or invalid 'check_id'")
    for field in ("evidence", "hint"):
        if field in capability and not _is_string_or_none(capability.get(field)):
            raise EvidenceGateError(f"{manifest_name} capability {capability_name!r} has invalid {field!r}")
    if "finding_id" in capability:
        if not allow_finding_id or capability["finding_id"] not in _REDTEAM_FINDING_IDS:
            raise EvidenceGateError(f"{manifest_name} capability {capability_name!r} has invalid 'finding_id'")
    if forbid_extra_fields:
        allowed_fields = {"status", "evidence", "hint"}
        if require_check_id:
            allowed_fields.add("check_id")
        if allow_finding_id:
            allowed_fields.add("finding_id")
        extras = set(capability) - allowed_fields
        if extras:
            raise EvidenceGateError(
                f"{manifest_name} capability {capability_name!r} has unsupported fields: {sorted(extras)}"
            )


def _validate_govern_manifest(path: Path, data: Dict[str, Any]) -> None:
    capabilities = data["capabilities"]
    invalid_names = set(capabilities) - _GOVERN_CAPABILITIES
    if invalid_names:
        raise EvidenceGateError(f"{path.name} has unsupported capabilities: {sorted(invalid_names)}")
    for capability_name, capability in capabilities.items():
        _validate_capability(path.name, capability_name, capability)


def _validate_evals_manifest(path: Path, data: Dict[str, Any]) -> None:
    capabilities = data["capabilities"]
    missing = _EVALS_CAPABILITIES - set(capabilities)
    extras = set(capabilities) - _EVALS_CAPABILITIES
    if missing:
        raise EvidenceGateError(f"{path.name} missing capabilities: {sorted(missing)}")
    if extras:
        raise EvidenceGateError(f"{path.name} has unsupported capabilities: {sorted(extras)}")
    for capability_name in sorted(_EVALS_CAPABILITIES):
        _validate_capability(path.name, capability_name, capabilities[capability_name], require_check_id=True)


def _validate_redteam_manifest(path: Path, data: Dict[str, Any]) -> None:
    for list_field in ("must_fix", "should_fix", "not_verified"):
        values = data.get(list_field)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise EvidenceGateError(f"{path.name} missing or invalid '{list_field}' (must be list of strings)")

    asr = data.get("asr")
    if not isinstance(asr, dict):
        raise EvidenceGateError(f"{path.name} missing or invalid 'asr' (must be object)")
    invalid_asr_keys = set(asr) - _REDTEAM_ASR_KEYS
    if invalid_asr_keys:
        raise EvidenceGateError(f"{path.name} has unsupported asr fields: {sorted(invalid_asr_keys)}")
    for key, value in asr.items():
        if not _is_number(value) or not 0 <= value <= 1:
            raise EvidenceGateError(f"{path.name} asr.{key} must be a number between 0 and 1")

    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict):
        raise EvidenceGateError(f"{path.name} missing or invalid 'thresholds' (must be object)")
    required_thresholds = {"max_asr", "freshness_days", "min_attacks"}
    missing_thresholds = required_thresholds - set(thresholds)
    extra_thresholds = set(thresholds) - required_thresholds
    if missing_thresholds:
        raise EvidenceGateError(f"{path.name} missing thresholds: {sorted(missing_thresholds)}")
    if extra_thresholds:
        raise EvidenceGateError(f"{path.name} has unsupported thresholds: {sorted(extra_thresholds)}")
    if not _is_number(thresholds["max_asr"]) or not 0 <= thresholds["max_asr"] <= 1:
        raise EvidenceGateError(f"{path.name} thresholds.max_asr must be a number between 0 and 1")
    if not isinstance(thresholds["freshness_days"], int) or isinstance(thresholds["freshness_days"], bool) or thresholds["freshness_days"] < 0:
        raise EvidenceGateError(f"{path.name} thresholds.freshness_days must be an integer >= 0")
    if not isinstance(thresholds["min_attacks"], int) or isinstance(thresholds["min_attacks"], bool) or thresholds["min_attacks"] < 1:
        raise EvidenceGateError(f"{path.name} thresholds.min_attacks must be an integer >= 1")

    capabilities = data["capabilities"]
    missing_capabilities = _REDTEAM_CAPABILITIES - set(capabilities)
    extra_capabilities = set(capabilities) - _REDTEAM_CAPABILITIES
    if missing_capabilities:
        raise EvidenceGateError(f"{path.name} missing capabilities: {sorted(missing_capabilities)}")
    if extra_capabilities:
        raise EvidenceGateError(f"{path.name} has unsupported capabilities: {sorted(extra_capabilities)}")
    for capability_name in sorted(_REDTEAM_CAPABILITIES):
        _validate_capability(
            path.name,
            capability_name,
            capabilities[capability_name],
            allow_finding_id=True,
            forbid_extra_fields=True,
        )


def evaluate_evidence(root: Path | str, mode: str) -> Dict[str, Any]:
    """Evaluate evidence under `root` for the given `mode`.

    mode: one of 'live-smoke' or 'readiness-proof'.

    Returns a dict with at least: status, mode, readiness_asserted, verdicts
    or raises EvidenceGateError on failure.
    """
    root = Path(root)
    specs_dir = root / "specs"
    verdicts: Dict[str, str] = {}

    # Load assurance manifests if present and validate shape.
    for key, (expected_schema, allowed, passing) in _ASSURANCE_SPECS.items():
        path = specs_dir / f"{key}-manifest.json"
        if not path.exists():
            # absent manifest: in live-smoke we accept missing ones; in
            # readiness-proof it's an error (checked below).
            continue
        data = _load_json(path)
        schema = data.get("schema")
        if schema != expected_schema:
            raise EvidenceGateError(f"{path.name} schema expected {expected_schema!r}")
        # Common required fields for govern/evals/redteam
        # All assurance manifests must include tool_version and captured_at
        if "tool_version" not in data or not isinstance(data.get("tool_version"), str):
            raise EvidenceGateError(f"{path.name} missing or invalid 'tool_version'")
        if "captured_at" not in data or not isinstance(data.get("captured_at"), str):
            raise EvidenceGateError(f"{path.name} missing or invalid 'captured_at'")
        verdict = data.get("verdict")
        if verdict not in allowed:
            raise EvidenceGateError(f"{path.name} verdict {verdict!r} not in allowed {sorted(allowed)}")

        # Capabilities must be present and an object for govern/evals/redteam
        if "capabilities" not in data or not isinstance(data.get("capabilities"), dict):
            raise EvidenceGateError(f"{path.name} missing or invalid 'capabilities' (must be object)")

        if key == "govern":
            _validate_govern_manifest(path, data)
        elif key == "evals":
            _validate_evals_manifest(path, data)
        elif key == "redteam":
            _validate_redteam_manifest(path, data)

        # record verdict
        verdicts[key] = verdict

    # For live-smoke we require the assurance manifests be present and structurally valid
    missing = [k for k in _ASSURANCE_SPECS.keys() if k not in verdicts]
    if mode == "live-smoke":
        if missing:
            raise EvidenceGateError(f"missing assurance manifests: {', '.join(missing)}")
        # live smoke verifies structural validity only and never asserts readiness
        return {"status": "pass", "mode": mode, "readiness_asserted": False, "verdicts": verdicts}

    if mode != "readiness-proof":
        raise EvidenceGateError(f"unsupported mode: {mode!r}")

    # readiness-proof: require all assurance manifests present and passing
    missing = [k for k in _ASSURANCE_SPECS.keys() if k not in verdicts]
    if missing:
        raise EvidenceGateError(f"missing assurance manifests: {', '.join(missing)}")

    for key, (_, allowed, passing) in _ASSURANCE_SPECS.items():
        v = verdicts.get(key)
        if v != passing:
            raise EvidenceGateError(f"{key}-manifest.json expected {passing!r}, got {v!r}")

    # safe-check / post-deploy manifest
    post_path = root / "tests" / "postdeploy-manifest.json"
    if not post_path.exists():
        raise EvidenceGateError("postdeploy-manifest missing: tests/postdeploy-manifest.json")
    post = _load_json(post_path)
    if post.get("phase") != "post-deploy":
        raise EvidenceGateError("postdeploy-manifest phase must be 'post-deploy'")
    if post.get("gaps") != []:
        raise EvidenceGateError("postdeploy-manifest gaps must be empty list")

    # production readiness manifest
    pr_path = root / "tests" / "production-readiness-manifest.json"
    if not pr_path.exists():
        raise EvidenceGateError("production-readiness-manifest missing: tests/production-readiness-manifest.json")
    pr = _load_json(pr_path)
    if pr.get("would_fail_hard_gate") is not False:
        raise EvidenceGateError("production readiness would_fail_hard_gate must be false")
    if pr.get("go_live_recommendation") != "ready":
        raise EvidenceGateError("production readiness go_live_recommendation must be 'ready'")

    # kpi_scorecard validations
    score = pr.get("kpi_scorecard")
    if not isinstance(score, dict):
        raise EvidenceGateError("production readiness kpi_scorecard is missing or invalid")
    required_booleans = [
        "latency_declared",
        "cost_per_interaction_declared",
        "success_rate_declared",
        "deviation_alert_present",
        "traces_emit",
    ]
    for field in required_booleans:
        if score.get(field) is not True:
            raise EvidenceGateError(f"kpi_scorecard.{field} must be true")
    # numeric fields (must be numeric *and not boolean*)
    ep = score.get("eval_pass_rate")
    if not (isinstance(ep, (int, float)) and not isinstance(ep, bool)):
        raise EvidenceGateError("kpi_scorecard.eval_pass_rate must be measured")
    cpi = score.get("cost_per_interaction_usd")
    if not (isinstance(cpi, (int, float)) and not isinstance(cpi, bool)):
        raise EvidenceGateError("kpi_scorecard.cost_per_interaction_usd must be measured")

    return {"status": "pass", "mode": mode, "readiness_asserted": True, "verdicts": verdicts}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    class CLIParseError(Exception):
        """Raised for CLI parsing failures so we can emit JSON instead of argparse text."""

    class JSONArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            # Raise instead of exiting/printing to allow JSON failure handling
            raise CLIParseError(message)

    def _get_arg_value(argv_list: list[str], name: str) -> str | None:
        for i, tok in enumerate(argv_list):
            if tok == name and i + 1 < len(argv_list):
                return argv_list[i + 1]
            if tok.startswith(name + "="):
                return tok.split("=", 1)[1]
        return None

    argv_list = list(argv) if argv is not None else list(sys.argv[1:])
    p = JSONArgumentParser(prog="evidence-gate")
    p.add_argument("--root", required=True, help="project root")
    p.add_argument(
        "--mode",
        required=True,
        choices=["live-smoke", "readiness-proof"],
        help="mode of evaluation: live-smoke or readiness-proof",
    )
    try:
        args = p.parse_args(argv_list)
    except CLIParseError as e:
        mode_value = _get_arg_value(argv_list, "--mode")
        print(json.dumps({"status": "fail", "mode": mode_value, "error": str(e)}))
        return 2

    try:
        out = evaluate_evidence(Path(args.root), args.mode)
    except EvidenceGateError as e:
        print(json.dumps({"status": "fail", "mode": args.mode, "error": str(e)}))
        return 2
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
