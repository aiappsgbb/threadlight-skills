from __future__ import annotations

from collections.abc import Mapping
import re

from skills._shared.manifest import (
    ManifestValidationError,
    is_draft7_integer,
    validate_iso8601_timestamp,
)


GOVERNANCE_MODES = frozenset({"off", "selective", "comprehensive"})
BINDING_STATUSES = frozenset(
    {"enforced", "observed", "unbound", "unsupported", "unverified", "bypassable"}
)
ENFORCEMENT_PATHS = frozenset(
    {"none", "local-agent-hooks", "governed-tool-gateway"}
)
CONSEQUENCES = frozenset(
    {"read", "write", "external-egress", "irreversible", "unknown"}
)

_INTERVENTION_POINTS = frozenset(
    {
        "input",
        "pre_model_call",
        "post_model_call",
        "pre_tool_call",
        "post_tool_call",
        "output",
        "startup",
        "shutdown",
    }
)
_ENFORCEMENT_MODES = frozenset({"enforce", "evaluate_only"})
_PROBE_STATUSES = frozenset({"pass", "fail"})
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
_VERSION_RE = re.compile(r"\d+(?:\.\d+)*(?:[A-Za-z][0-9A-Za-z.-]*)?")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_EVIDENCE_REF_RE = re.compile(r"EV-[A-Za-z0-9][A-Za-z0-9._:-]*")


class GovernanceContractError(ValueError):
    pass


def _raise(message):
    raise GovernanceContractError(message)


def _require_object(value, field):
    if not isinstance(value, Mapping):
        _raise(f"{field} must be an object")
    return value


def _require_list(value, field):
    if not isinstance(value, list):
        _raise(f"{field} must be a list")
    return value


def _require_exact_keys(value, field, required_keys):
    missing = required_keys.difference(value)
    if missing:
        _raise(f"{field} missing required keys: {', '.join(sorted(missing))}")
    extra = set(value).difference(required_keys)
    if extra:
        _raise(f"{field} contains unsupported keys: {', '.join(sorted(extra))}")


def _require_non_empty_string(value, field):
    if not isinstance(value, str) or not value:
        _raise(f"{field} must be a non-empty string")
    return value


def _require_identifier(value, field):
    value = _require_non_empty_string(value, field)
    if not _IDENTIFIER_RE.fullmatch(value):
        _raise(f"{field} must be a valid identifier")
    return value


def _require_version(value, field):
    value = _require_non_empty_string(value, field)
    if not _VERSION_RE.fullmatch(value):
        _raise(f"{field} must be a valid version")
    return value


def _require_sha256(value, field):
    value = _require_non_empty_string(value, field)
    if not _SHA256_RE.fullmatch(value):
        _raise(f"{field} must be an exact sha256 digest")
    return value


def _require_boolean(value, field):
    if not isinstance(value, bool):
        _raise(f"{field} must be a boolean")
    return value


def _require_non_negative_integer(value, field):
    if not is_draft7_integer(value) or value < 0:
        _raise(f"{field} must be a non-negative integer")
    return value


def _require_timestamp(value, field):
    value = _require_non_empty_string(value, field)
    try:
        validate_iso8601_timestamp(value, field)
    except ManifestValidationError as error:
        _raise(str(error))
    return value


def _require_member(value, allowed, field):
    value = _require_non_empty_string(value, field)
    if value not in allowed:
        _raise(f"{field} must be one of {', '.join(sorted(allowed))}")
    return value


def _require_unique_string_list(value, field, *, item_validator=None, allow_empty=True):
    items = _require_list(value, field)
    if not allow_empty and not items:
        _raise(f"{field} must not be empty")

    normalized = []
    seen = set()
    for index, item in enumerate(items):
        item_field = f"{field}[{index}]"
        item = (
            item_validator(item, item_field)
            if item_validator is not None
            else _require_non_empty_string(item, item_field)
        )
        if item in seen:
            _raise(f"{field} must not contain duplicates")
        seen.add(item)
        normalized.append(item)
    return normalized


def _require_evidence_ref(value, field):
    value = _require_non_empty_string(value, field)
    if not _EVIDENCE_REF_RE.fullmatch(value):
        _raise(f"{field} must be a valid evidence reference")
    return value


def _require_intervention_point(value, field):
    return _require_member(value, _INTERVENTION_POINTS, field)


def normalize_tool(raw, runtime=None):
    if isinstance(raw, str):
        tool_id = _require_identifier(raw, "id")
        return {
            "id": tool_id,
            "consequence": "unknown",
            "policy_binding": None,
            "enforcement_path": "none",
            "intervention_points": [],
        }

    if not isinstance(raw, Mapping):
        _raise("tool must be a string or object")

    tool_id = _require_identifier(raw.get("id"), "id")
    consequence = _require_member(raw.get("consequence", "unknown"), CONSEQUENCES, "consequence")
    enforcement_path = _require_member(
        raw.get("enforcement_path", "none"),
        ENFORCEMENT_PATHS,
        "enforcement_path",
    )
    policy_binding = raw.get("policy_binding")
    if policy_binding == "none":
        policy_binding = None
    if policy_binding is not None:
        policy_binding = _require_identifier(policy_binding, "policy_binding")

    intervention_points = _require_unique_string_list(
        raw.get("intervention_points", []),
        "intervention_points",
        item_validator=_require_intervention_point,
    )

    if runtime == "github-copilot-sdk" and enforcement_path == "local-agent-hooks":
        _raise("github-copilot-sdk does not support local-agent-hooks bindings")
    if enforcement_path != "none" and policy_binding is None:
        _raise("policy_binding is required when enforcement_path is not none")
    if enforcement_path == "none" and policy_binding is not None:
        _raise("unbound tools may not claim a policy binding")
    if enforcement_path == "none" and intervention_points:
        _raise("intervention_points must be empty when enforcement_path is none")

    return {
        "id": tool_id,
        "consequence": consequence,
        "policy_binding": policy_binding,
        "enforcement_path": enforcement_path,
        "intervention_points": list(intervention_points),
    }


def _validate_binding(binding, *, policy_digest):
    field = "bindings[]"
    binding = _require_object(binding, field)
    _require_exact_keys(
        binding,
        field,
        {
            "binding_id",
            "tool_id",
            "enforcement_path",
            "intervention_points",
            "mode",
            "safe_principles",
            "status",
            "policy_digest",
            "probe_ids",
            "evidence_refs",
        },
    )

    binding_id = _require_identifier(binding["binding_id"], "bindings[].binding_id")
    tool_id = _require_identifier(binding["tool_id"], "bindings[].tool_id")
    enforcement_path = _require_member(
        binding["enforcement_path"],
        ENFORCEMENT_PATHS,
        "bindings[].enforcement_path",
    )
    intervention_points = _require_unique_string_list(
        binding["intervention_points"],
        "bindings[].intervention_points",
        item_validator=_require_intervention_point,
    )
    mode = _require_member(binding["mode"], _ENFORCEMENT_MODES, "bindings[].mode")
    safe_principles = _require_unique_string_list(
        binding["safe_principles"],
        "bindings[].safe_principles",
        allow_empty=False,
    )
    status = _require_member(binding["status"], BINDING_STATUSES, "bindings[].status")
    binding_policy_digest = _require_sha256(
        binding["policy_digest"], "bindings[].policy_digest"
    )
    probe_ids = _require_unique_string_list(
        binding["probe_ids"],
        "bindings[].probe_ids",
        item_validator=_require_identifier,
    )
    evidence_refs = _require_unique_string_list(
        binding["evidence_refs"],
        "bindings[].evidence_refs",
        item_validator=_require_evidence_ref,
    )

    if binding_policy_digest != policy_digest:
        _raise("bindings[].policy_digest must match policy_bundle.digest")
    if enforcement_path == "none" and intervention_points:
        _raise("bindings[].intervention_points must be empty when enforcement_path is none")
    if status == "enforced" and mode != "enforce":
        _raise("bindings[].mode must be enforce when status is enforced")
    if status == "observed" and mode != "evaluate_only":
        _raise("bindings[].mode must be evaluate_only when status is observed")
    if status in {"enforced", "observed"}:
        if not probe_ids or not evidence_refs:
            _raise("enforced or observed bindings must carry live proof")
    if status == "enforced" and enforcement_path == "none":
        _raise("enforced bindings must declare a non-none enforcement_path")
    if status == "observed" and enforcement_path == "none":
        _raise("observed bindings must declare a non-none enforcement_path")
    if status == "bypassable" and enforcement_path == "none":
        _raise("bypassable bindings must declare a non-none enforcement_path")
    if status == "unbound" and enforcement_path != "none":
        _raise("unbound bindings must use enforcement_path none")

    return {
        "binding_id": binding_id,
        "tool_id": tool_id,
        "enforcement_path": enforcement_path,
        "intervention_points": intervention_points,
        "mode": mode,
        "safe_principles": safe_principles,
        "status": status,
        "policy_digest": binding_policy_digest,
        "probe_ids": probe_ids,
        "evidence_refs": evidence_refs,
    }


def _validate_live_probe(probe, *, known_binding_ids, agent_version):
    field = "live_probes[]"
    probe = _require_object(probe, field)
    _require_exact_keys(
        probe,
        field,
        {
            "probe_id",
            "binding_id",
            "environment",
            "agent_version",
            "decision",
            "downstream_effect_delta",
            "decision_receipt_ref",
            "service_oracle_ref",
            "status",
        },
    )

    probe_id = _require_identifier(probe["probe_id"], "live_probes[].probe_id")
    binding_id = _require_identifier(probe["binding_id"], "live_probes[].binding_id")
    if binding_id not in known_binding_ids:
        _raise("live_probes[].binding_id must resolve to a declared binding")
    environment = _require_non_empty_string(
        probe["environment"], "live_probes[].environment"
    )
    probe_agent_version = _require_version(
        probe["agent_version"], "live_probes[].agent_version"
    )
    if probe_agent_version != agent_version:
        _raise("live_probes[].agent_version must match agent.version")
    decision = _require_non_empty_string(probe["decision"], "live_probes[].decision")
    downstream_effect_delta = _require_non_negative_integer(
        probe["downstream_effect_delta"], "live_probes[].downstream_effect_delta"
    )
    decision_receipt_ref = _require_evidence_ref(
        probe["decision_receipt_ref"], "live_probes[].decision_receipt_ref"
    )
    service_oracle_ref = _require_evidence_ref(
        probe["service_oracle_ref"], "live_probes[].service_oracle_ref"
    )
    status = _require_member(probe["status"], _PROBE_STATUSES, "live_probes[].status")

    return {
        "probe_id": probe_id,
        "binding_id": binding_id,
        "environment": environment,
        "agent_version": probe_agent_version,
        "decision": decision,
        "downstream_effect_delta": downstream_effect_delta,
        "decision_receipt_ref": decision_receipt_ref,
        "service_oracle_ref": service_oracle_ref,
        "status": status,
    }


def _validate_gap(gap, *, binding_ids, evidence_refs):
    field = "gaps[]"
    gap = _require_object(gap, field)
    has_binding = "binding_id" in gap
    has_tool = "tool_id" in gap
    if has_binding == has_tool:
        _raise("gaps[] must carry exactly one of binding_id or tool_id")
    required = {"status", "reason_code", "evidence_refs", "binding_id" if has_binding else "tool_id"}
    _require_exact_keys(gap, field, required)

    if has_binding:
        binding_id = _require_identifier(gap["binding_id"], "gaps[].binding_id")
        if binding_id not in binding_ids:
            _raise("gaps[].binding_id must resolve to a declared binding")
    else:
        _require_identifier(gap["tool_id"], "gaps[].tool_id")

    status = _require_member(gap["status"], BINDING_STATUSES, "gaps[].status")
    if status == "enforced":
        _raise("gaps[].status may not be enforced")
    _require_identifier(gap["reason_code"], "gaps[].reason_code")
    refs = _require_unique_string_list(
        gap["evidence_refs"],
        "gaps[].evidence_refs",
        item_validator=_require_evidence_ref,
        allow_empty=False,
    )
    for reference in refs:
        if reference not in evidence_refs:
            _raise("gaps[].evidence_refs must resolve to live probe evidence")


def validate_governance_manifest(manifest):
    manifest = _require_object(manifest, "manifest")

    if "governed" in manifest or "verdict" in manifest:
        _raise("whole-agent governed booleans or verdicts are forbidden")

    required_keys = {
        "schema",
        "agent",
        "policy_bundle",
        "enforcement",
        "coverage",
        "bindings",
        "live_probes",
        "gaps",
    }
    missing = required_keys.difference(manifest)
    if missing:
        _raise(f"manifest missing required keys: {', '.join(sorted(missing))}")
    unsupported = set(manifest).difference(required_keys)
    if unsupported:
        _raise(f"unsupported top-level keys: {', '.join(sorted(unsupported))}")

    if manifest["schema"] != "threadlight-governance-manifest/v1":
        _raise("schema must be threadlight-governance-manifest/v1")

    agent = _require_object(manifest["agent"], "agent")
    _require_exact_keys(agent, "agent", {"runtime", "version", "image_digest"})
    _require_non_empty_string(agent["runtime"], "agent.runtime")
    agent_version = _require_version(agent["version"], "agent.version")
    _require_sha256(agent["image_digest"], "agent.image_digest")

    policy_bundle = _require_object(manifest["policy_bundle"], "policy_bundle")
    _require_exact_keys(
        policy_bundle,
        "policy_bundle",
        {"id", "version", "digest", "signature_verified", "expires_at"},
    )
    _require_identifier(policy_bundle["id"], "policy_bundle.id")
    _require_version(policy_bundle["version"], "policy_bundle.version")
    policy_digest = _require_sha256(policy_bundle["digest"], "policy_bundle.digest")
    _require_boolean(
        policy_bundle["signature_verified"], "policy_bundle.signature_verified"
    )
    signature_verified = policy_bundle["signature_verified"]
    # ``expires_at`` is intentionally shape-only here so the validator stays
    # deterministic; freshness against the caller/runtime clock is external.
    _require_timestamp(policy_bundle["expires_at"], "policy_bundle.expires_at")

    enforcement = _require_object(manifest["enforcement"], "enforcement")
    _require_exact_keys(
        enforcement,
        "enforcement",
        {
            "adapter",
            "mode",
            "agent_hooks_distribution",
            "agent_hooks_artifact_sha256",
            "acs_distribution",
            "acs_artifact_sha256",
        },
    )
    _require_non_empty_string(enforcement["adapter"], "enforcement.adapter")
    _require_member(enforcement["mode"], _ENFORCEMENT_MODES, "enforcement.mode")
    _require_version(
        enforcement["agent_hooks_distribution"],
        "enforcement.agent_hooks_distribution",
    )
    _require_sha256(
        enforcement["agent_hooks_artifact_sha256"],
        "enforcement.agent_hooks_artifact_sha256",
    )
    _require_version(
        enforcement["acs_distribution"], "enforcement.acs_distribution"
    )
    _require_sha256(
        enforcement["acs_artifact_sha256"], "enforcement.acs_artifact_sha256"
    )

    bindings = _require_list(manifest["bindings"], "bindings")
    normalized_bindings = []
    binding_ids = set()
    tool_ids = set()
    for binding in bindings:
        normalized = _validate_binding(binding, policy_digest=policy_digest)
        if (
            agent["runtime"] == "github-copilot-sdk"
            and normalized["enforcement_path"] == "local-agent-hooks"
        ):
            _raise("github-copilot-sdk does not support local-agent-hooks bindings")
        if normalized["binding_id"] in binding_ids:
            _raise("bindings.binding_id values must be unique")
        if normalized["tool_id"] in tool_ids:
            _raise("bindings.tool_id values must be unique")
        binding_ids.add(normalized["binding_id"])
        tool_ids.add(normalized["tool_id"])
        normalized_bindings.append(normalized)

    live_probes = _require_list(manifest["live_probes"], "live_probes")
    probe_ids = set()
    evidence_refs = set()
    probes_by_id = {}
    probes_by_binding = {}
    for probe in live_probes:
        normalized = _validate_live_probe(
            probe, known_binding_ids=binding_ids, agent_version=agent_version
        )
        if normalized["probe_id"] in probe_ids:
            _raise("live_probes.probe_id values must be unique")
        probe_ids.add(normalized["probe_id"])
        probes_by_id[normalized["probe_id"]] = normalized
        evidence_refs.add(normalized["decision_receipt_ref"])
        evidence_refs.add(normalized["service_oracle_ref"])
        probes_by_binding.setdefault(normalized["binding_id"], set()).update(
            {
                normalized["decision_receipt_ref"],
                normalized["service_oracle_ref"],
            }
        )
    for binding in normalized_bindings:
        if (
            enforcement["mode"] == "evaluate_only"
            and (binding["status"] == "enforced" or binding["mode"] == "enforce")
        ):
            _raise(
                "evaluate_only enforcement may not declare enforce-mode or enforced bindings"
            )
        for probe_id in binding["probe_ids"]:
            probe = probes_by_id.get(probe_id)
            if probe is None or probe["binding_id"] != binding["binding_id"]:
                _raise(
                    "bindings[].probe_ids must resolve to live_probes[].probe_id for the same binding"
                )
            if (
                binding["status"] in {"enforced", "observed"}
                and probe["status"] != "pass"
            ):
                _raise("enforced or observed bindings must cite passing live probes")
            if (
                binding["status"] == "enforced"
                and probe["decision"] == "deny"
                and probe["downstream_effect_delta"] != 0
            ):
                _raise(
                    "enforced deny probes must report live_probes[].downstream_effect_delta as 0"
                )
        for evidence_ref in binding["evidence_refs"]:
            if evidence_ref not in probes_by_binding.get(binding["binding_id"], set()):
                _raise("bindings[].evidence_refs must resolve to matching live probe evidence")
        if (
            not signature_verified
            and binding["status"] in {"enforced", "observed"}
        ):
            _raise(
                "policy_bundle.signature_verified must be true for enforced or observed bindings"
            )

    gaps = _require_list(manifest["gaps"], "gaps")
    gap_binding_ids = set()
    gap_tool_ids = set()
    for gap in gaps:
        _validate_gap(
            gap,
            binding_ids=binding_ids,
            evidence_refs=evidence_refs,
        )
        if "binding_id" in gap:
            if gap["binding_id"] in gap_binding_ids:
                _raise("gaps[].binding_id may not duplicate another gap binding_id")
            binding = next(
                binding
                for binding in normalized_bindings
                if binding["binding_id"] == gap["binding_id"]
            )
            if gap["status"] != binding["status"]:
                _raise("gaps[].status must match the referenced binding status")
            gap_binding_ids.add(gap["binding_id"])
        else:
            if gap["tool_id"] in gap_tool_ids:
                _raise("gaps[].tool_id may not duplicate another gap tool_id")
            if gap["tool_id"] in tool_ids:
                _raise("gaps[].tool_id may not duplicate a bound tool")
            gap_tool_ids.add(gap["tool_id"])

    for binding in normalized_bindings:
        if binding["status"] != "enforced" and binding["binding_id"] not in gap_binding_ids:
            _raise("gaps must cover every non-enforced binding")

    coverage = _require_object(manifest["coverage"], "coverage")
    coverage_keys = {
        "tools_total",
        "tools_bound",
        "tools_enforced",
        "tools_observed",
        "tools_unbound",
        "tools_unverified",
        "tools_unsupported",
        "tools_bypassable",
    }
    _require_exact_keys(coverage, "coverage", coverage_keys)
    expected_coverage = {
        "tools_total": len(normalized_bindings),
        "tools_bound": sum(
            1 for binding in normalized_bindings if binding["enforcement_path"] != "none"
        ),
        "tools_enforced": sum(
            1 for binding in normalized_bindings if binding["status"] == "enforced"
        ),
        "tools_observed": sum(
            1 for binding in normalized_bindings if binding["status"] == "observed"
        ),
        "tools_unbound": sum(
            1 for binding in normalized_bindings if binding["status"] == "unbound"
        ),
        "tools_unverified": sum(
            1 for binding in normalized_bindings if binding["status"] == "unverified"
        ),
        "tools_unsupported": sum(
            1 for binding in normalized_bindings if binding["status"] == "unsupported"
        ),
        "tools_bypassable": sum(
            1 for binding in normalized_bindings if binding["status"] == "bypassable"
        ),
    }
    for field, expected in expected_coverage.items():
        actual = _require_non_negative_integer(coverage[field], f"coverage.{field}")
        if actual != expected:
            _raise(f"coverage.{field} must equal the count derived from bindings")

    return manifest


__all__ = [
    "BINDING_STATUSES",
    "CONSEQUENCES",
    "ENFORCEMENT_PATHS",
    "GOVERNANCE_MODES",
    "GovernanceContractError",
    "normalize_tool",
    "validate_governance_manifest",
]
