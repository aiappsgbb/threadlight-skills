#!/usr/bin/env python3
"""A tiny, stdlib-only evidence gate used by production readiness lifecycle.

Implements a strict, minimal evaluator for the Task 1 tests. Designed to be
imported by tests (evaluate_evidence) and runnable as a CLI (main()).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict


class EvidenceGateError(ValueError):
    """Raised on any validation / evidence problem."""


# Allowed schemas and verdicts
_ASSURANCE_SPECS = {
    "govern": ("threadlight-governance-manifest/v1", {"enforced", "pass", "must-fix", "not-verified"}, "enforced"),
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
_AGT_PROFILES = {"auto", "v3_7", "v4_preview", "none"}
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?"
    r"(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


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


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_datetime_string(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _parse_rfc3339_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _RFC3339_RE.match(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _validate_datetime_field(manifest_name: str, data: Dict[str, Any], field: str) -> None:
    if not _is_datetime_string(data.get(field)):
        raise EvidenceGateError(f"{manifest_name} missing or invalid {field!r}")


def _validate_optional_non_negative_int(manifest_name: str, data: Dict[str, Any], field: str) -> None:
    if field in data and not _is_non_negative_int(data[field]):
        raise EvidenceGateError(f"{manifest_name} has invalid {field!r}")


def _validate_optional_string_list(manifest_name: str, data: Dict[str, Any], field: str) -> None:
    if field not in data:
        return
    values = data[field]
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise EvidenceGateError(f"{manifest_name} has invalid {field!r} (must be list of strings)")


def _validate_optional_enum_string(
    manifest_name: str,
    data: Dict[str, Any],
    field: str,
    allowed: set[str],
) -> None:
    if field not in data:
        return
    value = data[field]
    if not isinstance(value, str) or value not in allowed:
        raise EvidenceGateError(f"{manifest_name} has invalid {field!r}")


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
    _validate_optional_non_negative_int(path.name, data, "freshness_window_days")
    _validate_optional_enum_string(path.name, data, "agt_profile", _AGT_PROFILES)
    for list_field in ("must_fix", "should_fix", "not_verified"):
        _validate_optional_string_list(path.name, data, list_field)
    capabilities = data["capabilities"]
    missing = _GOVERN_CAPABILITIES - set(capabilities)
    extras = set(capabilities) - _GOVERN_CAPABILITIES
    if missing:
        raise EvidenceGateError(f"{path.name} missing capabilities: {sorted(missing)}")
    if extras:
        raise EvidenceGateError(f"{path.name} has unsupported capabilities: {sorted(extras)}")
    for capability_name in sorted(_GOVERN_CAPABILITIES):
        _validate_capability(path.name, capability_name, capabilities[capability_name])


def _validate_evals_manifest(path: Path, data: Dict[str, Any]) -> None:
    _validate_optional_non_negative_int(path.name, data, "freshness_window_days")
    for list_field in ("must_fix", "should_fix", "not_verified"):
        _validate_optional_string_list(path.name, data, list_field)
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
    for field in ("scan_result", "tool"):
        if field in data and not _is_string_or_none(data.get(field)):
            raise EvidenceGateError(f"{path.name} has invalid {field!r}")
    if "scan_captured_at" in data and data.get("scan_captured_at") is not None:
        _validate_datetime_field(path.name, data, "scan_captured_at")
    if "num_attacks" in data and data.get("num_attacks") is not None and not _is_non_negative_int(data["num_attacks"]):
        raise EvidenceGateError(f"{path.name} has invalid 'num_attacks'")
    if "strategies" in data:
        strategies = data.get("strategies")
        if not isinstance(strategies, list) or any(not isinstance(item, str) for item in strategies):
            raise EvidenceGateError(f"{path.name} has invalid 'strategies'")
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


def _release_stamp(value: Any, now: datetime, not_before: datetime, max_age_seconds: int) -> None:
    stamp = _parse_rfc3339_datetime(value)
    if (stamp is None or stamp > now or stamp < not_before.replace(microsecond=0)
            or (now - stamp).total_seconds() > max_age_seconds):
        raise EvidenceGateError("release evidence is outside the current execution window")


def _release_ratio(value: Any) -> bool:
    return _is_number(value) and 0 <= value <= 1 and math.isfinite(value)


def validate_release_manifest(
    domain: str, data: Dict[str, Any], *, now: datetime, not_before: datetime,
    max_age_seconds: int = 900, required_capabilities: list[str] | None = None,
    min_pass_rate: float = 0.95, max_asr: float = 0.1, min_attacks: int = 100,
) -> Dict[str, Any]:
    """Check release criteria; the caller must separately bind producer, source and target."""
    if (not isinstance(data, dict) or now.tzinfo is None or not_before.tzinfo is None
            or not_before > now or not _is_non_negative_int(max_age_seconds)
            or not 1 <= max_age_seconds <= 86400
            or not _release_ratio(min_pass_rate) or not _release_ratio(max_asr)
            or not _is_non_negative_int(min_attacks) or min_attacks < 1):
        raise EvidenceGateError("invalid release acceptance input")
    if domain == "mcp":
        summary = data.get("summary")
        servers = data.get("servers")
        counts = {"server_count", "pinned", "unpinned", "remote", "inline_creds", "must_fix", "should_fix"}
        if (required_capabilities is not None or data.get("schema_version") != "1.0"
                or data.get("generator") != "threadlight-production-ready/mcp_sbom"
                or not isinstance(data.get("generator_version"), str) or not data["generator_version"]
                or not isinstance(summary, dict) or not isinstance(servers, list)
                or any(not _is_non_negative_int(summary.get(key)) for key in counts)
                or summary["server_count"] != len(servers)
                or any(summary[key] for key in ("must_fix", "should_fix", "inline_creds", "unpinned"))
                or summary["pinned"] + summary["remote"] > len(servers)):
            raise EvidenceGateError("missing, negative or contradictory MCP release evidence")
        for server in servers:
            findings = server.get("findings") if isinstance(server, dict) else None
            if (not isinstance(findings, dict)
                    or set(findings) != {"SUP-010", "SUP-011", "SUP-012", "SUP-013"}
                    or any(value not in {"pass", "not-applicable"} for value in findings.values())
                    or server.get("parse_error") or server.get("creds_inline") is not False):
                raise EvidenceGateError("MCP server has unresolved or missing release checks")
        return {"domain": domain, "status": "pass", "server_count": len(servers)}
    if domain not in {"evals", "redteam"}:
        raise EvidenceGateError("unknown release evidence domain")
    schema, allowed_verdicts, passing = _ASSURANCE_SPECS[domain]
    if (data.get("schema") != schema or not isinstance(data.get("tool_version"), str)
            or not data["tool_version"] or data.get("verdict") not in allowed_verdicts
            or not isinstance(data.get("capabilities"), dict)):
        raise EvidenceGateError("missing or invalid canonical release manifest")
    _release_stamp(data.get("captured_at"), now, not_before, max_age_seconds)
    validator = _validate_evals_manifest if domain == "evals" else _validate_redteam_manifest
    validator(Path(f"{domain}-manifest.json"), data)
    known = _EVALS_CAPABILITIES if domain == "evals" else _REDTEAM_CAPABILITIES
    required = known if required_capabilities is None else required_capabilities
    if (not isinstance(required, (list, set)) or not required
            or any(not isinstance(key, str) for key in required)
            or len(set(required)) != len(required) or set(required) - known):
        raise EvidenceGateError("invalid required release capability scope")
    mandatory = ({"eval_scenarios_present", "eval_datasets_present", "dataset_shape_ok",
                  "thresholds_declared", "run_history_present", "latest_eval_run_fresh",
                  "latest_pass_rate_ok"} if domain == "evals" else known)
    if not mandatory <= set(required):
        raise EvidenceGateError("required release checks cannot be disabled")
    caps = data["capabilities"]
    for field, status in (("must_fix", "must-fix"), ("should_fix", "should-fix"),
                          ("not_verified", "not-verified")):
        values = data.get(field)
        expected = {key for key, value in caps.items() if value["status"] == status}
        if (not isinstance(values, list) or any(not isinstance(key, str) for key in values)
                or len(set(values)) != len(values) or set(values) != expected):
            raise EvidenceGateError("release findings contradict capability evidence")
    if (data["must_fix"] or any(caps[key]["status"] != "pass" for key in required)
            or data["verdict"] not in {passing, "partial"}
            or (data["verdict"] == passing) != (not data["should_fix"] and not data["not_verified"])):
        raise EvidenceGateError("required release capabilities have not passed")
    if domain == "evals":
        metrics = data.get("metrics")
        if not isinstance(metrics, dict) or not isinstance(metrics.get("latest_run"), str) or not metrics["latest_run"]:
            raise EvidenceGateError("missing executed evaluation metrics")
        if metrics.get("pass_rate") is None and isinstance(data.get("agentops"), dict):
            native = data.get("agentops")
            records = native.get("agents") if isinstance(native, dict) else None
            if not isinstance(records, list) or not records:
                raise EvidenceGateError("missing native evaluation records")
            for record in records:
                if not isinstance(record, dict):
                    raise EvidenceGateError("invalid native evaluation record")
                _release_stamp(record.get("finished_at"), now, not_before, max_age_seconds)
                thresholds = record.get("thresholds")
                rate = record.get("execution_pass_rate")
                native_caps = record.get("capabilities")
                if (record.get("domain_status") != "verified" or not _release_ratio(rate)
                        or rate < min_pass_rate or not isinstance(thresholds, list) or not thresholds
                        or any(not isinstance(item, dict) or item.get("passed") is not True for item in thresholds)
                        or not isinstance(native_caps, dict) or native_caps.get("latest_pass_rate_ok") != "pass"):
                    raise EvidenceGateError("native quality thresholds have not passed")
        elif (not _release_ratio(metrics.get("pass_rate")) or not _release_ratio(metrics.get("threshold"))
                or metrics["threshold"] < min_pass_rate or metrics["pass_rate"] < metrics["threshold"]):
            raise EvidenceGateError("measured quality does not satisfy the approved threshold")
    else:
        _release_stamp(data.get("scan_captured_at"), now, not_before, max_age_seconds)
        if (not _is_non_negative_int(data.get("num_attacks"))
                or data["num_attacks"] < max(min_attacks, data["thresholds"]["min_attacks"])
                or data["thresholds"]["max_asr"] > max_asr):
            raise EvidenceGateError("scan volume or threshold violates release policy")
        for category in ("jailbreak", "prompt_injection", "exfiltration", "harmful_content"):
            value = data["asr"].get(category)
            if not _release_ratio(value) or value > min(max_asr, data["thresholds"]["max_asr"]):
                raise EvidenceGateError(f"required attack domain has not passed: {category}")
    return {"domain": domain, "status": "pass", "required_capabilities": sorted(required)}


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
        if key == "govern" and (specs_dir / "governance-manifest.json").exists():
            repo = Path(__file__).resolve().parents[3]
            if str(repo) not in sys.path:
                sys.path.insert(0, str(repo))
            try:
                from skills._shared.governance import validate_governance_manifest
                from skills._shared.governance_readiness import assess, read_json
                validate_governance_manifest(read_json(specs_dir / "governance-manifest.json"))
            except (OSError, ValueError, ImportError) as error:
                raise EvidenceGateError("governance-manifest.json not-verified: invalid evidence or missing tooling") from error
            result = assess(root)
            verdicts[key] = "enforced" if result["status"] == "pass" and result["live"] else result["status"]
            if mode == "readiness-proof" and result["status"] != "pass":
                raise EvidenceGateError("governance-manifest.json " + result["status"] + ": " + result["reason"])
            continue
        path = specs_dir / f"{key}-manifest.json"
        if not path.exists():
            # absent manifest: in live-smoke we accept missing ones; in
            # readiness-proof it's an error (checked below).
            continue
        data = _load_json(path)
        schema = data.get("schema")
        if key == "govern":
            # Validate old provenance strictly, but never upgrade it to assurance.
            expected_schema = "threadlight-govern-manifest/v2"
            allowed = {"governed", "partial", "ungoverned"}
        if schema != expected_schema:
            raise EvidenceGateError(f"{path.name} schema expected {expected_schema!r}")
        # Common required fields for govern/evals/redteam
        # All assurance manifests must include tool_version and captured_at
        if "tool_version" not in data or not isinstance(data.get("tool_version"), str):
            raise EvidenceGateError(f"{path.name} missing or invalid 'tool_version'")
        _validate_datetime_field(path.name, data, "captured_at")
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
        verdicts[key] = "not-verified" if key == "govern" else verdict

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
        if key == "govern" and v in {"enforced", "pass"}:
            continue
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
    checked_at = _parse_rfc3339_datetime(post.get("checked_at"))
    if checked_at is None:
        raise EvidenceGateError("postdeploy-manifest checked_at must be a strict RFC3339 timestamp with timezone")
    age = datetime.now(timezone.utc) - checked_at
    if age.total_seconds() < 0:
        raise EvidenceGateError("postdeploy-manifest checked_at must not be in the future")
    if age >= timedelta(hours=24):
        raise EvidenceGateError("postdeploy-manifest checked_at must be fresher than 24h")
    manifest = _load_json(specs_dir / "manifest.json")
    current_deployment = manifest.get("deployment_manifest")
    postdeploy_deployment = post.get("deployment_manifest")
    if not isinstance(current_deployment, dict):
        raise EvidenceGateError("specs/manifest.json missing deployment_manifest")
    if not isinstance(postdeploy_deployment, dict):
        raise EvidenceGateError("postdeploy-manifest missing deployment_manifest snapshot")
    if _canonical_json(current_deployment) != _canonical_json(postdeploy_deployment):
        raise EvidenceGateError("postdeploy-manifest deployment_manifest no longer matches specs/manifest.json")

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
