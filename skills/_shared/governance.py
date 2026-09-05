from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re
from types import MappingProxyType

from skills._shared.manifest import (
    ManifestValidationError,
    is_draft7_integer,
    validate_iso8601_timestamp,
)


GOVERNANCE_MODES = frozenset({"off", "selective", "comprehensive"})
CONTRACT_FRAMEWORKS = frozenset(
    {"github-copilot-sdk", "microsoft-agent-framework"}
)
CONTRACT_RUNTIME_PATHS = {
    "github-copilot-sdk": ("none", "governed-tool-gateway"),
    "microsoft-agent-framework": (
        "none",
        "local-agent-hooks",
        "governed-tool-gateway",
    ),
}
AGENT_RUNTIMES = frozenset(
    {"github-copilot-sdk", "maf-responses", "microsoft-agent-framework"}
)
DEPLOYMENT_TARGETS = frozenset(
    {"demo-sandbox", "customer-pilot", "production-bound"}
)
BINDING_STATUSES = frozenset(
    {"enforced", "observed", "unbound", "unsupported", "unverified", "bypassable"}
)
ENFORCEMENT_PATHS = frozenset(
    {"none", "local-agent-hooks", "governed-tool-gateway"}
)
CONSEQUENCES = frozenset(
    {"read", "write", "external-egress", "irreversible", "unknown"}
)
CONTRACT_REQUIREMENT_TOKENS = frozenset(
    {
        "approval",
        "human-approval-record",
        "output",
        "output-mediation",
        "durable-audit",
        "audit",
        "decision-receipt",
        "idempotency",
        "idempotency-or-transaction",
        "signed-policy-bundle",
        "operator-review",
        "authorization",
    }
)
_PROBE_REQUIREMENT_DIMENSIONS = MappingProxyType(
    {
        "approval": "approval",
        "human-approval-record": "approval",
        "output": "output",
        "output-mediation": "output",
        "durable-audit": "durable_audit",
        "audit": "durable_audit",
        "decision-receipt": "durable_audit",
        "idempotency": None,
        "idempotency-or-transaction": None,
        "signed-policy-bundle": None,
        "operator-review": None,
        "authorization": None,
    }
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
_GOVERNANCE_ENVIRONMENT_EXPECTATIONS = {
    "development": "evaluate_only",
    "staging": "evaluate_only",
    "preproduction": "enforce",
    "production": "enforce",
}
_ENFORCEMENT_MODES = frozenset({"enforce", "evaluate_only"})
_PROBE_STATUSES = frozenset({"pass", "fail"})
LIVE_PROBE_DECISIONS = frozenset({"allow", "deny", "escalate", "transform"})
_CONSEQUENTIAL_OR_UNKNOWN = frozenset(
    {"write", "external-egress", "irreversible", "unknown"}
)
_LIFECYCLE_ENFORCEMENT_PATHS = frozenset(
    {"local-agent-hooks", "governed-tool-gateway"}
)
_RUNTIME_ENFORCEMENT_PATHS = {
    **CONTRACT_RUNTIME_PATHS,
    "maf-responses": CONTRACT_RUNTIME_PATHS["microsoft-agent-framework"],
}
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


def _require_non_blank_string(value, field):
    value = _require_non_empty_string(value, field)
    if not value.strip():
        _raise(f"{field} must be a non-empty string")
    return value.strip()


def _require_non_blank_list_string(value, field):
    value = _require_non_empty_string(value, field)
    if not value.strip():
        _raise(f"{field} must be a non-empty string")
    return value


def normalize_requirement_token(value, field="requirement"):
    value = _require_non_blank_string(value, field)
    normalized = value.lower().replace("_", "-")
    if normalized not in CONTRACT_REQUIREMENT_TOKENS:
        _raise(f"{field} declares unsupported requirement {value!r}")
    return normalized


def probe_requirement_dimension(value):
    return _PROBE_REQUIREMENT_DIMENSIONS[normalize_requirement_token(value)]


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


def _parse_timestamp(value, field):
    value = _require_timestamp(value, field)
    normalized = value[:-1] + "+00:00" if value[-1] in "Zz" else value
    return value, datetime.fromisoformat(normalized)


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
            else _require_non_blank_list_string(item, item_field)
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


def _validate_environment_modes(raw):
    field = "governance.environment_modes"
    modes = _require_object(raw, field)
    _require_exact_keys(modes, field, set(_GOVERNANCE_ENVIRONMENT_EXPECTATIONS))

    normalized = {}
    for environment, expected in _GOVERNANCE_ENVIRONMENT_EXPECTATIONS.items():
        actual = _require_member(
            modes[environment],
            _ENFORCEMENT_MODES,
            f"{field}.{environment}",
        )
        if actual != expected:
            _raise(f"{field}.{environment} must be {expected}")
        normalized[environment] = actual
    return normalized


def _normalize_as_of(as_of):
    if as_of is None:
        # Use the current UTC clock only when callers do not provide a
        # deterministic ``as_of`` value.
        return datetime.now(timezone.utc)
    if not isinstance(as_of, datetime):
        _raise("as_of must be a datetime when provided")
    return as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)


def _validate_acceptance_record(raw, *, as_of=None):
    field = "acceptance_record"
    record = _require_object(raw, field)
    _require_exact_keys(
        record,
        field,
        {"owner", "justification", "review_date", "expiry"},
    )
    owner = _require_non_blank_string(record["owner"], f"{field}.owner")
    justification = _require_non_blank_string(
        record["justification"], f"{field}.justification"
    )
    review_date, review_dt = _parse_timestamp(
        record["review_date"], f"{field}.review_date"
    )
    expiry, expiry_dt = _parse_timestamp(record["expiry"], f"{field}.expiry")
    if expiry_dt < review_dt:
        _raise(f"{field}.expiry must be greater than or equal to {field}.review_date")
    if expiry_dt < _normalize_as_of(as_of):
        _raise(f"{field}.expiry must be greater than or equal to as_of")
    return {
        "owner": owner,
        "justification": justification,
        "review_date": review_date,
        "expiry": expiry,
    }


def _validate_lifecycle_binding(raw, *, runtime):
    field = "governance.lifecycle_bindings[]"
    binding = _require_object(raw, field)
    _require_exact_keys(
        binding,
        field,
        {
            "lifecycle_point",
            "policy_binding",
            "enforcement_path",
            "safe_principles",
            "requires",
        },
    )
    lifecycle_point = _require_intervention_point(
        binding["lifecycle_point"], f"{field}.lifecycle_point"
    )
    enforcement_path = _require_member(
        binding["enforcement_path"],
        _LIFECYCLE_ENFORCEMENT_PATHS,
        f"{field}.enforcement_path",
    )
    normalized = normalize_tool(
        {
            "id": f"lifecycle.{lifecycle_point}",
            "consequence": "read",
            "policy_binding": binding["policy_binding"],
            "enforcement_path": enforcement_path,
            "intervention_points": [lifecycle_point],
        },
        runtime=runtime,
    )
    return {
        "lifecycle_point": lifecycle_point,
        "policy_binding": normalized["policy_binding"],
        "enforcement_path": normalized["enforcement_path"],
        "safe_principles": _require_unique_string_list(
            binding["safe_principles"],
            f"{field}.safe_principles",
            allow_empty=False,
        ),
        "requires": _require_unique_string_list(
            binding["requires"],
            f"{field}.requires",
            item_validator=normalize_requirement_token,
        ),
    }


def _validate_contract_tool(raw, *, runtime, as_of=None):
    if isinstance(raw, str):
        return normalize_tool(raw, runtime=runtime)

    field = "tools[]"
    tool = _require_object(raw, field)
    required_keys = {
        "id",
        "consequence",
        "policy_binding",
        "enforcement_path",
        "intervention_points",
        "safe_principles",
        "requires",
    }
    allowed_keys = required_keys | {"acceptance_record"}
    missing = required_keys.difference(tool)
    if missing:
        _raise(f"{field} missing required keys: {', '.join(sorted(missing))}")
    extra = set(tool).difference(allowed_keys)
    if extra:
        _raise(f"{field} contains unsupported keys: {', '.join(sorted(extra))}")
    if tool["policy_binding"] is None:
        _raise(f"{field}.policy_binding must be an identifier or 'none'")

    normalized = normalize_tool(
        {
            "id": tool["id"],
            "consequence": tool["consequence"],
            "policy_binding": tool["policy_binding"],
            "enforcement_path": tool["enforcement_path"],
            "intervention_points": tool["intervention_points"],
        },
        runtime=runtime,
    )
    normalized["safe_principles"] = _require_unique_string_list(
        tool["safe_principles"],
        f"{field}.safe_principles",
    )
    normalized["requires"] = _require_unique_string_list(
        tool["requires"],
        f"{field}.requires",
        item_validator=normalize_requirement_token,
    )
    if "acceptance_record" in tool:
        normalized["acceptance_record"] = _validate_acceptance_record(
            tool["acceptance_record"], as_of=as_of
        )
    return normalized


def normalize_tool(raw, runtime=None):
    if runtime is not None:
        runtime = _require_member(runtime, AGENT_RUNTIMES, "runtime")

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

    allowed_paths = _RUNTIME_ENFORCEMENT_PATHS.get(runtime)
    if allowed_paths is not None and enforcement_path not in allowed_paths:
        _raise(f"{runtime} does not support {enforcement_path} bindings")
    if enforcement_path != "none" and policy_binding is None:
        _raise("policy_binding is required when enforcement_path is not none")
    if enforcement_path == "none" and policy_binding is not None:
        _raise("unbound tools may not claim a policy binding")
    if enforcement_path == "none" and intervention_points:
        _raise("intervention_points must be empty when enforcement_path is none")
    if enforcement_path == "local-agent-hooks" and not intervention_points:
        _raise(
            "intervention_points must not be empty when enforcement_path is local-agent-hooks"
        )

    return {
        "id": tool_id,
        "consequence": consequence,
        "policy_binding": policy_binding,
        "enforcement_path": enforcement_path,
        "intervention_points": list(intervention_points),
    }


def validate_governance_contract(document, *, deployment_target, runtime=None, as_of=None):
    document = _require_object(document, "document")
    _require_exact_keys(document, "document", {"framework", "governance", "tools"})

    framework = _require_member(
        document["framework"], CONTRACT_FRAMEWORKS, "document.framework"
    )
    if runtime is not None:
        runtime = _require_member(runtime, CONTRACT_FRAMEWORKS, "runtime")
        if runtime != framework:
            _raise("runtime must match document.framework; do not silently switch runtime")
    else:
        runtime = framework
    deployment_target = _require_member(
        deployment_target, DEPLOYMENT_TARGETS, "deployment_target"
    )
    acceptance_as_of = _normalize_as_of(as_of)

    governance = _require_object(document["governance"], "governance")
    _require_exact_keys(
        governance,
        "governance",
        {"mode", "environment_modes", "lifecycle_bindings"},
    )
    mode = _require_member(governance["mode"], GOVERNANCE_MODES, "governance.mode")
    environment_modes = _validate_environment_modes(governance["environment_modes"])
    lifecycle_bindings = [
        _validate_lifecycle_binding(binding, runtime=runtime)
        for binding in _require_list(
            governance["lifecycle_bindings"], "governance.lifecycle_bindings"
        )
    ]
    seen_lifecycle_points = set()
    for binding in lifecycle_bindings:
        if binding["lifecycle_point"] in seen_lifecycle_points:
            _raise("governance.lifecycle_bindings.lifecycle_point values must be unique")
        seen_lifecycle_points.add(binding["lifecycle_point"])
    if mode == "off" and lifecycle_bindings:
        _raise("off mode forbids lifecycle bindings")

    tools = []
    seen_ids = set()
    for raw_tool in _require_list(document["tools"], "tools"):
        normalized = _validate_contract_tool(
            raw_tool, runtime=runtime, as_of=acceptance_as_of
        )
        if normalized["id"] in seen_ids:
            _raise("tools.id values must be unique")
        seen_ids.add(normalized["id"])

        if mode == "off" and normalized["enforcement_path"] != "none":
            _raise("off mode forbids bound tools")
        if (
            mode == "comprehensive"
            and normalized["consequence"] in _CONSEQUENTIAL_OR_UNKNOWN
            and normalized["enforcement_path"] == "none"
        ):
            _raise(
                "comprehensive governance rejects consequential or unknown tools with enforcement_path none"
            )
        if (
            deployment_target == "production-bound"
            and normalized["consequence"] in _CONSEQUENTIAL_OR_UNKNOWN
            and normalized["enforcement_path"] == "none"
            and "acceptance_record" not in normalized
        ):
            _raise(
                "production-bound unbound consequential or unknown tools require acceptance_record"
            )

        tools.append(normalized)

    return {
        "framework": framework,
        "deployment_target": deployment_target,
        "governance": {
            "mode": mode,
            "environment_modes": environment_modes,
            "lifecycle_bindings": lifecycle_bindings,
        },
        "tools": tools,
    }


def _validate_binding(binding, *, policy_digest, offline=False, collection=False):
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
        allow_empty=offline or (collection and enforcement_path == "none"),
    )
    status = _require_member(binding["status"], BINDING_STATUSES, "bindings[].status")
    binding_policy_digest = binding["policy_digest"]
    if not offline or binding_policy_digest is not None:
        _require_sha256(binding_policy_digest, "bindings[].policy_digest")
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
    decision = _require_member(
        probe["decision"], LIVE_PROBE_DECISIONS, "live_probes[].decision"
    )
    downstream_effect_delta = _require_non_negative_integer(
        probe["downstream_effect_delta"], "live_probes[].downstream_effect_delta"
    )
    decision_receipt_ref = _require_evidence_ref(
        probe["decision_receipt_ref"], "live_probes[].decision_receipt_ref"
    )
    service_oracle_ref = _require_evidence_ref(
        probe["service_oracle_ref"], "live_probes[].service_oracle_ref"
    )
    if decision_receipt_ref == service_oracle_ref:
        _raise(
            "live_probes[].decision_receipt_ref and live_probes[].service_oracle_ref must differ"
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


def _validate_gap(gap, *, binding_ids, evidence_refs, evidence_refs_by_binding, collection=False):
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
        allow_empty=collection and status in {"unverified", "unbound"},
    )
    for reference in refs:
        if reference not in evidence_refs:
            _raise("gaps[].evidence_refs must resolve to live probe evidence")
        if has_binding and reference not in evidence_refs_by_binding.get(binding_id, set()):
            _raise(
                "gaps[].evidence_refs for binding-scoped gaps must resolve to that binding's live probe evidence"
            )


def validate_governance_manifest(manifest):
    manifest = _require_object(manifest, "manifest")

    if "governed" in manifest or "verdict" in manifest:
        _raise("whole-agent governed booleans or verdicts are forbidden")

    if "offline_evidence" in manifest:
        return _validate_offline_manifest(manifest)

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
    collection = "collection_evidence" in manifest
    if collection:
        required_keys.add("collection_evidence")
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
    agent_runtime = _require_member(agent["runtime"], AGENT_RUNTIMES, "agent.runtime")
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
        ({"adapter", "mode"} if collection else {
            "adapter",
            "mode",
            "agent_hooks_distribution",
            "agent_hooks_artifact_sha256",
            "acs_distribution",
            "acs_artifact_sha256",
        }),
    )
    _require_non_empty_string(enforcement["adapter"], "enforcement.adapter")
    _require_member(enforcement["mode"], _ENFORCEMENT_MODES, "enforcement.mode")
    if not collection:
        _require_version(
            enforcement["agent_hooks_distribution"], "enforcement.agent_hooks_distribution")
        _require_sha256(
            enforcement["agent_hooks_artifact_sha256"], "enforcement.agent_hooks_artifact_sha256")
        _require_version(enforcement["acs_distribution"], "enforcement.acs_distribution")
        _require_sha256(enforcement["acs_artifact_sha256"], "enforcement.acs_artifact_sha256")

    bindings = _require_list(manifest["bindings"], "bindings")
    normalized_bindings = []
    binding_ids = set()
    tool_ids = set()
    for binding in bindings:
        normalized = _validate_binding(binding, policy_digest=policy_digest, collection=collection)
        if (
            agent_runtime == "github-copilot-sdk"
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
    evidence_ref_owners = {}
    evidence_refs = set()
    probes_by_id = {}
    probes_by_binding = {}
    evidence_refs_by_binding = {}
    passing_evidence_refs_by_binding = {}
    for probe in live_probes:
        normalized = _validate_live_probe(
            probe, known_binding_ids=binding_ids, agent_version=agent_version
        )
        if normalized["probe_id"] in probe_ids:
            _raise("live_probes.probe_id values must be unique")
        probe_ids.add(normalized["probe_id"])
        probes_by_id[normalized["probe_id"]] = normalized
        probes_by_binding.setdefault(normalized["binding_id"], []).append(normalized)
        evidence_refs_by_binding.setdefault(normalized["binding_id"], set())
        passing_evidence_refs_by_binding.setdefault(normalized["binding_id"], set())
        for field_name in ("decision_receipt_ref", "service_oracle_ref"):
            evidence_ref = normalized[field_name]
            if evidence_ref in evidence_ref_owners:
                _raise("live_probes evidence refs must be globally unique across probes")
            evidence_ref_owners[evidence_ref] = (normalized["probe_id"], field_name)
            evidence_refs.add(evidence_ref)
            evidence_refs_by_binding[normalized["binding_id"]].add(evidence_ref)
            if normalized["status"] == "pass":
                passing_evidence_refs_by_binding[normalized["binding_id"]].add(
                    evidence_ref
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
        for evidence_ref in binding["evidence_refs"]:
            if evidence_ref not in evidence_refs_by_binding.get(binding["binding_id"], set()):
                _raise("bindings[].evidence_refs must resolve to matching live probe evidence")
            if (
                binding["status"] in {"enforced", "observed"}
                and evidence_ref
                not in passing_evidence_refs_by_binding.get(binding["binding_id"], set())
            ):
                _raise(
                    "bindings[].evidence_refs for enforced or observed bindings must resolve to passing live probe evidence"
                )
        for probe in probes_by_binding.get(binding["binding_id"], []):
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
            evidence_refs_by_binding=evidence_refs_by_binding,
            collection=collection,
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

    _validate_coverage(manifest["coverage"], normalized_bindings)
    if collection:
        _validate_collection_evidence(manifest)
    return manifest


def _validate_collection_evidence(manifest):
    from skills._shared.probe_evidence import evaluate_pair
    evidence = _require_object(manifest["collection_evidence"], "collection_evidence")
    _require_exact_keys(evidence, "collection_evidence", {
        "source", "declared_selection", "observed_target", "started_at", "finished_at",
        "registration_scope", "records", "expected_target", "configuration",
    })
    if evidence["source"] != "authenticated-service-reads-and-azure-observation-not-attestation":
        _raise("collection_evidence source must identify the collector, not local conformance")
    try:
        target = evidence["observed_target"]
        if (target["source"] != "azure-arm-and-foundry"
                or target["agent_version"] != manifest["agent"]["version"]
                or target["image_digest"] != manifest["agent"]["image_digest"]
                or evidence["registration_scope"]["registration"]["policy_digest"] != manifest["policy_bundle"]["digest"]):
            _raise("collection_evidence target mismatch")
        _, started = _parse_timestamp(evidence["started_at"], "collection_evidence.started_at")
        _, finished = _parse_timestamp(evidence["finished_at"], "collection_evidence.finished_at")
        if finished < started:
            _raise("collection_evidence time reversed")
        configuration = evidence["configuration"]
        from skills._shared.governance_configuration import (
            AGENT_ENVIRONMENT, SERVICE_ENVIRONMENT, DECLARED_FILES, validate_digests,
        )
        _require_exact_keys(configuration, "collection_evidence.configuration", {
            "declared", "observed", "file_visibility", "declared_file_digests",
        })
        validate_digests(configuration["declared_file_digests"], DECLARED_FILES)
        for part in ("declared", "observed"):
            projection = configuration[part]
            _require_exact_keys(projection, "configuration." + part, {"agent", "services"})
            if set(validate_digests(projection["agent"], AGENT_ENVIRONMENT)) != AGENT_ENVIRONMENT:
                _raise("collection_evidence host configuration incomplete")
            services = {"control_plane", "fixture"}
            if evidence["registration_scope"]["producer"] == "gateway":
                services.add("producer")
            _require_exact_keys(projection["services"], "configuration.services", services)
            for digests in projection["services"].values():
                validate_digests(digests, SERVICE_ENVIRONMENT)
        if (configuration["declared"] != configuration["observed"]
                or configuration["observed"]["agent"] != target["configuration_digests"]
                or configuration["file_visibility"] != "image-and-mounted-file-interiors-not-observed-by-azure"):
            _raise("collection_evidence configuration mismatch")
        expected = evaluate_pair(evidence["records"], target=target,
            expected_target=evidence["expected_target"],
            registration_scope=evidence["registration_scope"], started_at=started, finished_at=finished)
        if manifest["live_probes"] != expected:
            _raise("collection_evidence does not match live probes")
        for binding in manifest["bindings"]:
            if binding["status"] == "enforced" and (
                binding["tool_id"] != "governance_probe_noop"
                or binding["binding_id"] != "governance_probe_noop"
                or binding["intervention_points"] != ["pre_tool_call"]):
                _raise("noop evidence cannot certify other bindings or intervention points")
    except GovernanceContractError:
        raise
    except Exception:
        _raise("collection_evidence failed strict probe evaluation")


def _validate_coverage(coverage, normalized_bindings):
    coverage = _require_object(coverage, "coverage")
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


def _validate_offline_manifest(manifest):
    """An explicitly offline variant cannot contain deployment or live proof."""
    _require_exact_keys(manifest, "manifest", {
        "schema", "agent", "policy_bundle", "enforcement", "coverage",
        "bindings", "live_probes", "gaps", "offline_evidence",
    })
    if manifest["schema"] != "threadlight-governance-manifest/v1":
        _raise("schema must be threadlight-governance-manifest/v1")
    agent = _require_object(manifest["agent"], "agent")
    _require_exact_keys(agent, "agent", {"runtime", "version", "image_digest"})
    if agent["runtime"] is not None:
        _require_member(agent["runtime"], AGENT_RUNTIMES, "agent.runtime")
    if agent["version"] is not None or agent["image_digest"] is not None:
        _raise("offline reports cannot assert a deployed agent version or image")
    enforcement = _require_object(manifest["enforcement"], "enforcement")
    if dict(enforcement) != {
        "adapter": "offline-inventory", "mode": "evaluate_only",
        "agent_hooks_distribution": None, "agent_hooks_artifact_sha256": None,
        "acs_distribution": None, "acs_artifact_sha256": None,
    }:
        _raise("offline reports cannot assert deployed enforcement artifacts")
    if manifest["live_probes"] != []:
        _raise("offline reports cannot carry live probes")
    bundle = manifest["policy_bundle"]
    digest = None
    if bundle is not None:
        bundle = _require_object(bundle, "policy_bundle")
        _require_exact_keys(bundle, "policy_bundle", {
            "id", "version", "digest", "signature_verified", "expires_at",
        })
        _require_identifier(bundle["id"], "policy_bundle.id")
        _require_version(bundle["version"], "policy_bundle.version")
        digest = _require_sha256(bundle["digest"], "policy_bundle.digest")
        if bundle["signature_verified"] is not False or bundle["expires_at"] is not None:
            _raise("offline bundle integrity is not signature or freshness verification")

    evidence_refs = set()
    for evidence in _require_list(manifest["offline_evidence"], "offline_evidence"):
        _require_object(evidence, "offline_evidence[]")
        _require_exact_keys(evidence, "offline_evidence[]", {
            "evidence_ref", "source", "sha256", "reason_code",
        })
        ref = _require_evidence_ref(evidence["evidence_ref"], "offline_evidence[].evidence_ref")
        if ref in evidence_refs:
            _raise("offline evidence refs must be unique")
        evidence_refs.add(ref)
        if evidence["source"] is not None:
            _require_non_blank_string(evidence["source"], "offline_evidence[].source")
        if evidence["sha256"] is not None:
            _require_sha256(evidence["sha256"], "offline_evidence[].sha256")
        _require_identifier(evidence["reason_code"], "offline_evidence[].reason_code")
    if not evidence_refs:
        _raise("offline reports require explicit evidence or an inventory gap")

    bindings = []
    ids, tool_ids = set(), set()
    refs_by_binding = {}
    for binding in _require_list(manifest["bindings"], "bindings"):
        normalized = _validate_binding(binding, policy_digest=digest, offline=True)
        if normalized["status"] not in {"unverified", "unbound", "unsupported"}:
            _raise("offline bindings cannot assert live observation or enforcement")
        if normalized["mode"] != "evaluate_only" or normalized["probe_ids"]:
            _raise("offline bindings cannot claim runtime probes or enforce mode")
        if not normalized["evidence_refs"] or not set(normalized["evidence_refs"]) <= evidence_refs:
            _raise("offline binding evidence must resolve")
        if normalized["binding_id"] in ids or normalized["tool_id"] in tool_ids:
            _raise("offline binding and tool ids must be unique")
        if agent["runtime"] == "github-copilot-sdk" and normalized["enforcement_path"] == "local-agent-hooks":
            _raise("github-copilot-sdk does not support local-agent-hooks bindings")
        ids.add(normalized["binding_id"])
        tool_ids.add(normalized["tool_id"])
        refs_by_binding[normalized["binding_id"]] = set(normalized["evidence_refs"])
        bindings.append(normalized)

    gap_ids = set()
    for gap in _require_list(manifest["gaps"], "gaps"):
        _validate_gap(gap, binding_ids=ids, evidence_refs=evidence_refs,
                      evidence_refs_by_binding=refs_by_binding)
        if "binding_id" not in gap or gap["binding_id"] in gap_ids:
            _raise("offline gaps must uniquely reference a declared binding")
        binding = next(b for b in bindings if b["binding_id"] == gap["binding_id"])
        if gap["status"] != binding["status"]:
            _raise("offline gap status must match binding")
        gap_ids.add(gap["binding_id"])
    if gap_ids != ids:
        _raise("offline gaps must cover all declared bindings")
    _validate_coverage(manifest["coverage"], bindings)
    return manifest


__all__ = [
    "AGENT_RUNTIMES",
    "BINDING_STATUSES",
    "CONSEQUENCES",
    "CONTRACT_FRAMEWORKS",
    "CONTRACT_RUNTIME_PATHS",
    "DEPLOYMENT_TARGETS",
    "ENFORCEMENT_PATHS",
    "GOVERNANCE_MODES",
    "GovernanceContractError",
    "LIVE_PROBE_DECISIONS",
    "normalize_tool",
    "validate_governance_contract",
    "validate_governance_manifest",
]
