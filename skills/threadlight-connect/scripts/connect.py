#!/usr/bin/env python3
"""connect.py — the CONNECT leg: swap a mocked Foundry tool for a real one.

`threadlight-design` / `threadlight-demo-data-factory` scaffold pilots against
**mocked** tools (a JSON sample-data file standing in for a real backend, per
`AGENTS.md` / `specs/SPEC.md` § "Foundry tools required"). Sooner or later a
real endpoint replaces that mock. This is the evidence-based leg that makes
that swap safe: it never trusts an endpoint just because it *responds* — it
extracts the data contract the tool source actually reads, generates
executable conformance tests, checks field-level conformance against a real
sample, and only calls the swap `real-verified` once conformance passes *and*
OBO (on-behalf-of) user-scoped evidence *and* required-role revalidation are
both in hand.

Customer-specific field **mapping** (e.g. renaming `cust_id` → `customer_id`)
is explicitly out of scope — this leg proves *conformance*, it does not
transform payloads. OBO token exchange itself is scaffolding only; the actual
Entra Agent Identity / OAuth-on-behalf-of implementation is owned by the
upstream `entra-agent-id` catalog skill (see SKILL.md "See also").

Phases (also the CLI's conceptual pipeline, run in one invocation):

    inspect          -> read tool source + mock sample
    contract         -> extract_contract(): fields actually READ, never sample keys
    generate-tests   -> write_conformance_tests(): executable pytest module
    verify           -> check_conformance(): field-level diff against a real sample
    plan             -> build_apply_plan(): file-by-file plan (always built, no I/O)
    apply            -> apply_changes(): atomic SPEC.md + mcp-config.json write
                         (only when --apply AND target_state == real-verified)
    emit             -> write_connect_manifest(): schema-validated, atomic

State machine — exactly four states: `mock`, `real-unverified`,
`real-verified`, `real-drift`. `integration_state` is the *persisted* current
state (read from a prior manifest; defaults to `mock`) and only ever advances
on a *successful* `--apply`. `target_state` is what `transition_integration()`
computes purely from evidence, independent of whether `--apply` was passed —
so a dry run (`--apply` omitted) can report `target_state: real-verified`
while `integration_state` stays `mock`, with a nonempty apply plan describing
exactly what publishing would change.

Publishing (or re-publishing) a `real-verified` swap requires required-role
revalidation against the **current** agent identity, not a stale grant — see
`transition_integration(..., current_agent_identity=...)`.

stdlib-only. No network calls; no endpoint is ever invoked by this script —
callers supply the "real" sample/response evidence themselves (e.g. captured
via `entra-agent-id` + a manual test call). Malformed evidence and any write
failure during apply are non-destructive: nothing is written, so whatever
valid `connect-manifest.json` / SPEC.md / mcp-config.json already existed on
disk is preserved untouched.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared envelope (skills/_shared/manifest.py) — insert repo root on sys.path
# so `skills._shared.manifest` resolves as an implicit namespace package both
# in-repo and when this script is invoked standalone.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills._shared.manifest import (  # noqa: E402
    ManifestValidationError,
    atomic_write_json,
    build_envelope,
    validate_envelope,
)

TOOL_VERSION = "0.1.0"
DATA_CONTRACT_SCHEMA = "threadlight-connect-data-contract/v1"
CONNECT_MANIFEST_SCHEMA = "threadlight-connect-manifest/v1"

VALID_STATES = ("mock", "real-unverified", "real-verified", "real-drift")

DEFAULT_SPEC_PATH = "specs/SPEC.md"
DEFAULT_MCP_CONFIG_PATH = "infra/mcp-config.json"
DEFAULT_MANIFEST_PATH = "specs/connect-manifest.json"

_MISSING = object()

_FORBIDDEN_KEY_MARKERS = (
    "token", "secret", "password", "credential", "authorization",
    "api_key", "apikey", "connection_string", "access_key",
)


class ConnectEvidenceError(ValueError):
    """Raised when OBO / required-role evidence is not the expected shape.

    Deliberately distinct from "evidence is honestly absent/false" (which is
    a normal `real-unverified` finding, not an error) — this is for evidence
    that is structurally malformed and cannot be evaluated at all. Raising
    here happens *before* any file is touched, so a malformed-evidence run
    never disturbs a prior valid manifest/SPEC/mcp-config.
    """


class ConnectApplyError(RuntimeError):
    """Raised when writing SPEC.md / mcp-config.json fails during apply.

    Always raised before any destination file is replaced (or with both
    destinations rolled back if only one temp file could be staged), so a
    write failure never leaves a partially-applied swap on disk.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_identifier(name: str) -> str:
    ident = re.sub(r"\W", "_", name).strip("_") or "tool"
    if ident[0].isdigit():
        ident = f"tool_{ident}"
    return ident


# ---------------------------------------------------------------------------
# Type / cardinality inference (shared by extraction, conformance checking,
# and the generated conformance test module — kept intentionally tiny and
# duplicated in the generated source so the generated test has zero runtime
# dependency on this package).
# ---------------------------------------------------------------------------
def _infer_type(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return None


def _infer_cardinality(value):
    if value is None:
        return None
    if isinstance(value, list):
        return "array"
    return "single"


# ---------------------------------------------------------------------------
# Phase: inspect + contract — extract fields actually READ by the tool source
# ---------------------------------------------------------------------------
def _parse_tool_module(tool_source: str) -> ast.Module:
    """Parse a tool source snippet. Accepts a full function def OR a bare
    statement/expression (e.g. just the `return {...}` line) by wrapping the
    latter in a dummy function body so `ast.parse` succeeds either way.
    """
    try:
        return ast.parse(tool_source)
    except SyntaxError:
        indented = "\n".join(
            ("    " + line if line.strip() else line)
            for line in tool_source.splitlines()
        )
        wrapped = "def _threadlight_connect_tool():\n" + indented + "\n"
        return ast.parse(wrapped)


def _subscript_key(node: ast.Subscript):
    slice_node = node.slice
    index_type = getattr(ast, "Index", None)  # removed in newer ast; guard
    if index_type is not None and isinstance(slice_node, index_type):
        slice_node = slice_node.value
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return slice_node.value
    return None


def _extract_read_fields(tool_source: str) -> dict:
    """Return {field_name: required} for every field the tool source actually
    reads off *some* source object — `obj['field']` (required: raises if
    absent) or `obj.get('field', ...)` (optional). Any name never read (e.g.
    an `internal` column present only in the sample) is excluded entirely —
    this is what keeps the contract from leaking un-read sample fields.
    A field read via subscript anywhere wins over an optional `.get()` read
    elsewhere (required is the stronger claim).
    """
    tree = _parse_tool_module(tool_source)
    required_fields: set[str] = set()
    optional_fields: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            key = _subscript_key(node)
            if key is not None:
                required_fields.add(key)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            optional_fields.add(node.args[0].value)

    fields = {name: True for name in required_fields}
    for name in optional_fields:
        fields.setdefault(name, False)
    return fields


def _representative_record(sample):
    if isinstance(sample, list):
        return sample[0] if sample else {}
    if isinstance(sample, dict):
        return sample
    return {}


def extract_contract(tool_name: str, tool_source: str, sample, generated_at=None) -> dict:
    """Build a data contract of ONLY the fields the tool source reads.

    Customer-specific field *mapping* stays out of scope: this records the
    source field names as read, their inferred type/cardinality (from the
    sample, when evidence exists — otherwise left `None`), and requiredness.
    """
    read_fields = _extract_read_fields(tool_source)
    record = _representative_record(sample)

    fields = []
    for name in sorted(read_fields):
        required = read_fields[name]
        value = record.get(name, _MISSING) if isinstance(record, dict) else _MISSING
        if value is _MISSING:
            field_type, cardinality = None, None
        else:
            field_type = _infer_type(value)
            cardinality = _infer_cardinality(value)
        fields.append(
            {
                "name": name,
                "required": required,
                "type": field_type,
                "cardinality": cardinality,
            }
        )

    return {
        "schema": DATA_CONTRACT_SCHEMA,
        "tool_name": tool_name,
        "generated_at": generated_at or _now_iso(),
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# Phase: generate-tests — an executable, dependency-free pytest module
# ---------------------------------------------------------------------------
def generate_conformance_test_source(tool_name: str, contract: dict, default_sample_path=None) -> str:
    default_sample_path = default_sample_path or f"tests/fixtures/{tool_name}-real-sample.json"
    ident = _safe_identifier(tool_name)
    fields_literal = repr(
        [
            {"name": f["name"], "required": f["required"], "type": f["type"]}
            for f in contract["fields"]
        ]
    )
    return f'''"""Auto-generated by threadlight-connect scripts/connect.py.

DO NOT EDIT BY HAND — regenerate via the connect leg's generate-tests phase.
Executable pytest module: asserts a captured real "{tool_name}" tool response
conforms to the extracted data contract (fields, types, requiredness). Reads
the real response from CONNECT_REAL_SAMPLE_PATH (default: {default_sample_path}).
No network call is made here — this only checks evidence already captured.
"""
import json
import os

CONTRACT_FIELDS = {fields_literal}
REAL_SAMPLE_PATH = os.environ.get(
    "CONNECT_REAL_SAMPLE_PATH", {default_sample_path!r}
)


def _infer_type(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return None


def check_conformance(real_response):
    items = real_response.get("items", []) if isinstance(real_response, dict) else []
    differences = []
    for index, item in enumerate(items):
        item = item if isinstance(item, dict) else {{}}
        for field in CONTRACT_FIELDS:
            name = field["name"]
            required = field["required"]
            expected_type = field.get("type") or "unknown"
            expected = "{{}}|{{}}".format(expected_type, "required" if required else "optional")
            path = "$.items[{{}}].{{}}".format(index, name)
            if name not in item:
                if required:
                    differences.append(
                        {{"field": name, "expected": expected, "actual": "missing", "path": path}}
                    )
                continue
            actual_type = _infer_type(item[name]) or "unknown"
            if field.get("type") is not None and actual_type != field["type"]:
                differences.append(
                    {{"field": name, "expected": expected, "actual": actual_type, "path": path}}
                )
    return differences


def test_{ident}_conformance():
    with open(REAL_SAMPLE_PATH, "r", encoding="utf-8") as fh:
        real_response = json.load(fh)
    differences = check_conformance(real_response)
    assert not differences, "mock-to-real conformance differences: {{}}".format(differences)
'''


def _write_temp_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return temp_path


def _atomic_write_text(path: Path, content: str) -> None:
    temp_path = None
    try:
        temp_path = _write_temp_file(path, content)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def write_conformance_tests(project_root, tool_name: str, contract: dict, dest_rel_path=None) -> str:
    """Write the generated conformance test module into the generated
    project. This is an independent test-scaffolding artifact (not a
    production config write) so it is produced on every run, `--apply` or
    not — it is what `pytest`-runs against captured real evidence.
    """
    dest_rel_path = dest_rel_path or (
        f"tests/threadlight_connect/test_{_safe_identifier(tool_name)}_conformance.py"
    )
    dest_full = Path(project_root) / dest_rel_path
    source = generate_conformance_test_source(tool_name, contract)
    _atomic_write_text(dest_full, source)
    return dest_rel_path


# ---------------------------------------------------------------------------
# Phase: verify — field-level conformance against a captured real sample
# ---------------------------------------------------------------------------
def check_conformance(contract: dict, real_response) -> dict:
    """Diff a captured real response against the contract. `real_response`
    is `{"items": [...]}` (or a bare list of records). Every difference has
    the exact shape `{field, expected, actual, path}`, e.g.
    `status/string|required/missing/$.items[0].status`.
    """
    if isinstance(real_response, dict):
        items = real_response.get("items", [])
    elif isinstance(real_response, list):
        items = real_response
    else:
        items = []

    differences = []
    for index, item in enumerate(items):
        record = item if isinstance(item, dict) else {}
        for field in contract.get("fields", []):
            name = field["name"]
            required = field["required"]
            expected_type = field.get("type") or "unknown"
            expected = f"{expected_type}|{'required' if required else 'optional'}"
            path = f"$.items[{index}].{name}"
            if name not in record:
                if required:
                    differences.append(
                        {"field": name, "expected": expected, "actual": "missing", "path": path}
                    )
                continue
            actual_type = _infer_type(record[name]) or "unknown"
            if field.get("type") is not None and actual_type != field["type"]:
                differences.append(
                    {"field": name, "expected": expected, "actual": actual_type, "path": path}
                )

    return {"passed": not differences, "differences": differences}


# ---------------------------------------------------------------------------
# Evidence validation — OBO (on-behalf-of) + required-role revalidation.
# Malformed shapes raise (abort before any write); an honestly-absent/false
# evidence value is a normal `real-unverified` finding, not an error.
# ---------------------------------------------------------------------------
def _validate_obo_evidence(evidence) -> dict:
    if evidence is None:
        return {"present": False, "user_scoped": False}
    if not isinstance(evidence, dict):
        raise ConnectEvidenceError("obo_evidence must be an object (dict) or null")
    present = evidence.get("present", False)
    if not isinstance(present, bool):
        raise ConnectEvidenceError("obo_evidence.present must be a boolean")
    user_scoped = evidence.get("user_scoped", False)
    if user_scoped is None:
        user_scoped = False
    if not isinstance(user_scoped, bool):
        raise ConnectEvidenceError("obo_evidence.user_scoped must be a boolean or null")
    return {"present": present, "user_scoped": user_scoped}


def _validate_role_evidence(evidence) -> dict:
    if evidence is None:
        return {
            "revalidated": False,
            "required_roles": [],
            "validated_roles": [],
            "agent_identity": None,
        }
    if not isinstance(evidence, dict):
        raise ConnectEvidenceError("role_evidence must be an object (dict) or null")
    revalidated = evidence.get("revalidated", False)
    if not isinstance(revalidated, bool):
        raise ConnectEvidenceError("role_evidence.revalidated must be a boolean")

    required_roles = evidence.get("required_roles") or []
    validated_roles = evidence.get("validated_roles", evidence.get("granted_roles")) or []
    for label, roles in (("required_roles", required_roles), ("validated_roles", validated_roles)):
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise ConnectEvidenceError(f"role_evidence.{label} must be a list of strings")

    agent_identity = evidence.get("agent_identity")
    if agent_identity is not None and not isinstance(agent_identity, str):
        raise ConnectEvidenceError("role_evidence.agent_identity must be a string or null")

    return {
        "revalidated": revalidated,
        "required_roles": required_roles,
        "validated_roles": validated_roles,
        "agent_identity": agent_identity,
    }


def _obo_ok(normalized_obo: dict) -> bool:
    return normalized_obo["present"] is True and normalized_obo["user_scoped"] is True


def _roles_ok(normalized_role: dict, current_agent_identity) -> bool:
    if normalized_role["revalidated"] is not True:
        return False
    required = set(normalized_role["required_roles"])
    validated = set(normalized_role["validated_roles"])
    if not required.issubset(validated):
        return False
    identity = normalized_role["agent_identity"]
    # Publishing/republishing must revalidate against the CURRENT agent
    # identity — a revalidation recorded against a stale/different identity
    # does not carry over.
    if current_agent_identity is not None and identity is not None and identity != current_agent_identity:
        return False
    return True


def transition_integration(conformance: dict, obo_evidence, role_evidence, *, current_agent_identity=None) -> str:
    """Pure function computing target_state from evidence:

    - field-level conformance failure/differences => real-drift
    - conformance passed but OBO user-scoped evidence missing/false, or
      required roles not revalidated (incl. against a stale identity)
      => real-unverified
    - real-verified only after conformance passes AND OBO is user-scoped
      AND required roles are revalidated against the current identity

    Raises ConnectEvidenceError (not a state) for malformed evidence shapes —
    callers must not persist anything when this raises.
    """
    normalized_obo = _validate_obo_evidence(obo_evidence)
    normalized_role = _validate_role_evidence(role_evidence)

    if not conformance.get("passed", False) or conformance.get("differences"):
        return "real-drift"
    if not _obo_ok(normalized_obo) or not _roles_ok(normalized_role, current_agent_identity):
        return "real-unverified"
    return "real-verified"


# ---------------------------------------------------------------------------
# Phase: plan — always built, pure (read-only), no writes
# ---------------------------------------------------------------------------
def build_apply_plan(project_root, spec_path, mcp_config_path, tool_name: str) -> list:
    root = Path(project_root)
    plan = []
    for rel_path, description in (
        (spec_path, f"Record {tool_name} as a real, evidence-verified integration"),
        (mcp_config_path, f"Point the {tool_name} MCP server entry at the real endpoint"),
    ):
        action = "update" if (root / rel_path).exists() else "create"
        plan.append({"path": str(rel_path), "action": action, "description": description})
    return plan


# ---------------------------------------------------------------------------
# Phase: apply — atomic, transactional across SPEC.md + mcp-config.json
# ---------------------------------------------------------------------------
SPEC_SECTION_HEADING = "## Real integrations (threadlight-connect)"


def _spec_marker(tool_name: str) -> str:
    return f"<!-- threadlight-connect:{tool_name} -->"


def _render_spec_block(tool_name: str, contract: dict, generated_at: str) -> str:
    lines = [_spec_marker(tool_name), f"### {tool_name} — real, evidence-verified"]
    lines.append(f"- verified_at: {generated_at}")
    lines.append("- contract fields:")
    for field in contract["fields"]:
        req = "required" if field["required"] else "optional"
        ftype = field["type"] or "unknown"
        lines.append(f"  - `{field['name']}` ({ftype}, {req})")
    lines.append(_spec_marker(tool_name) + " END")
    return "\n".join(lines)


def _update_spec_text(existing_text: str, tool_name: str, contract: dict, generated_at: str) -> str:
    block = _render_spec_block(tool_name, contract, generated_at)
    begin, end = _spec_marker(tool_name), _spec_marker(tool_name) + " END"
    if begin in existing_text and end in existing_text:
        start_idx = existing_text.index(begin)
        end_idx = existing_text.index(end) + len(end)
        return existing_text[:start_idx] + block + existing_text[end_idx:]
    prefix = existing_text.rstrip("\n")
    if prefix and SPEC_SECTION_HEADING not in prefix:
        prefix += "\n\n" + SPEC_SECTION_HEADING
    elif not prefix:
        prefix = SPEC_SECTION_HEADING
    return prefix + "\n\n" + block + "\n"


def _update_mcp_config(existing_data, tool_name: str, contract: dict, generated_at: str) -> dict:
    data = dict(existing_data) if isinstance(existing_data, dict) else {}
    integrations = dict(data.get("integrations", {}))
    integrations[tool_name] = {
        "state": "real-verified",
        "verified_at": generated_at,
        "fields": [f["name"] for f in contract["fields"]],
    }
    data["integrations"] = integrations
    return data


def apply_changes(project_root, spec_path, mcp_config_path, tool_name: str, contract: dict, generated_at: str) -> list:
    """Atomically update SPEC.md + mcp-config.json as a unit. Either both
    destinations are replaced, or (on any failure) neither is — temp files
    are staged for both before either `os.replace` runs, and any failure
    cleans up temps and re-raises `ConnectApplyError` without touching the
    real destinations, preserving whatever was previously on disk.
    """
    root = Path(project_root)
    spec_full = root / spec_path
    mcp_full = root / mcp_config_path

    existing_spec = spec_full.read_text(encoding="utf-8") if spec_full.exists() else ""
    existing_mcp: dict = {}
    if mcp_full.exists():
        try:
            existing_mcp = json.loads(mcp_full.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_mcp = {}

    new_spec_text = _update_spec_text(existing_spec, tool_name, contract, generated_at)
    new_mcp_data = _update_mcp_config(existing_mcp, tool_name, contract, generated_at)
    new_mcp_text = json.dumps(new_mcp_data, indent=2, sort_keys=True) + "\n"

    spec_tmp = mcp_tmp = None
    try:
        spec_tmp = _write_temp_file(spec_full, new_spec_text)
        mcp_tmp = _write_temp_file(mcp_full, new_mcp_text)
        os.replace(spec_tmp, spec_full)
        spec_tmp = None
        os.replace(mcp_tmp, mcp_full)
        mcp_tmp = None
    except BaseException as exc:
        if spec_tmp is not None:
            spec_tmp.unlink(missing_ok=True)
        if mcp_tmp is not None:
            mcp_tmp.unlink(missing_ok=True)
        raise ConnectApplyError(f"failed to apply mock-to-real swap: {exc}") from exc

    return [str(spec_path), str(mcp_config_path)]


# ---------------------------------------------------------------------------
# Phase: emit — schema-validated, atomic manifest write (shared envelope)
# ---------------------------------------------------------------------------
def _contains_forbidden_keys(obj) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and any(marker in key.lower() for marker in _FORBIDDEN_KEY_MARKERS):
                return True
            if _contains_forbidden_keys(value):
                return True
        return False
    if isinstance(obj, list):
        return any(_contains_forbidden_keys(item) for item in obj)
    return False


def load_current_state(manifest_full_path) -> str:
    """Read the *persisted* integration_state from a prior manifest, if any.
    Defaults to `mock` — the starting state for every integration.
    """
    path = Path(manifest_full_path)
    if not path.exists():
        return "mock"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "mock"
    state = data.get("integration_state")
    return state if state in VALID_STATES else "mock"


def _build_findings(conformance: dict, target_state: str) -> list:
    findings = [
        {"id": f"CONNECT-DRIFT-{diff['field']}", "status": "must-fix", "detail": diff}
        for diff in conformance.get("differences", [])
    ]
    if target_state == "real-unverified":
        findings.append(
            {
                "id": "CONNECT-EVIDENCE-INCOMPLETE",
                "status": "should-fix",
                "detail": (
                    "OBO user-scoped evidence and/or required-role "
                    "revalidation against the current agent identity is missing"
                ),
            }
        )
    return findings


def _manifest_status(real_response) -> str:
    if isinstance(real_response, dict):
        items = real_response.get("items", [])
    elif isinstance(real_response, list):
        items = real_response
    else:
        items = []
    return "complete" if items else "partial"


def build_connect_manifest(
    *,
    tool_name,
    integration_state,
    target_state,
    contract,
    conformance,
    evidence_summary,
    apply_plan,
    changed_paths,
    apply,
    status,
    findings,
    generated_at,
    valid_for_hours=24,
) -> dict:
    payload = {
        "tool_name": tool_name,
        "integration_state": integration_state,
        "target_state": target_state,
        "contract": contract,
        "conformance": conformance,
        "evidence_summary": evidence_summary,
        "apply_plan": apply_plan,
        "changed_paths": changed_paths,
        "apply": apply,
    }
    return build_envelope(
        schema=CONNECT_MANIFEST_SCHEMA,
        tool_version=TOOL_VERSION,
        status=status,
        generated_at=generated_at,
        valid_for_hours=valid_for_hours,
        source_oldest_at=generated_at,
        findings=findings,
        payload=payload,
    )


def validate_connect_manifest(manifest: dict) -> None:
    """Hand-rolled schema check (mirrors references/connect-manifest.schema.json)
    layered on top of the shared envelope's own validation — stdlib-only, no
    `jsonschema` dependency, consistent with the rest of the repo.
    """
    validate_envelope(manifest)

    required_keys = {
        "tool_name", "integration_state", "target_state", "contract",
        "conformance", "evidence_summary", "apply_plan", "changed_paths", "apply",
    }
    missing = required_keys.difference(manifest)
    if missing:
        raise ManifestValidationError(
            "connect manifest missing keys: " + ", ".join(sorted(missing))
        )

    for key in ("integration_state", "target_state"):
        if manifest[key] not in VALID_STATES:
            raise ManifestValidationError(f"unknown {key}: {manifest[key]!r}")

    conformance = manifest["conformance"]
    if not isinstance(conformance, dict) or "passed" not in conformance or "differences" not in conformance:
        raise ManifestValidationError("conformance must include passed and differences")
    for diff in conformance["differences"]:
        if not {"field", "expected", "actual", "path"}.issubset(diff):
            raise ManifestValidationError(
                "conformance difference missing field/expected/actual/path"
            )

    if not isinstance(manifest["apply_plan"], list):
        raise ManifestValidationError("apply_plan must be a list")
    if not isinstance(manifest["changed_paths"], list):
        raise ManifestValidationError("changed_paths must be a list")
    if not isinstance(manifest["apply"], bool):
        raise ManifestValidationError("apply must be a boolean")

    if _contains_forbidden_keys(manifest):
        raise ManifestValidationError(
            "connect manifest must not contain credential/token/secret-shaped keys"
        )


def write_connect_manifest(path, manifest: dict) -> None:
    validate_connect_manifest(manifest)
    atomic_write_json(path, manifest)


# ---------------------------------------------------------------------------
# Orchestration — wires every phase together for one CLI/API invocation
# ---------------------------------------------------------------------------
def run_connect(
    *,
    project_root,
    tool_name: str,
    tool_source: str,
    sample,
    real_response,
    obo_evidence=None,
    role_evidence=None,
    apply: bool = False,
    spec_path=DEFAULT_SPEC_PATH,
    mcp_config_path=DEFAULT_MCP_CONFIG_PATH,
    manifest_path=DEFAULT_MANIFEST_PATH,
    current_agent_identity=None,
    generated_at=None,
    valid_for_hours=24,
) -> dict:
    generated_at = generated_at or _now_iso()
    root = Path(project_root)

    contract = extract_contract(tool_name, tool_source, sample, generated_at=generated_at)
    conformance = check_conformance(contract, real_response)

    # Evidence validation gate — raises BEFORE any write when malformed, so a
    # malformed-evidence run never disturbs a prior valid manifest/config.
    target_state = transition_integration(
        conformance, obo_evidence, role_evidence, current_agent_identity=current_agent_identity
    )

    manifest_full_path = root / manifest_path
    integration_state = load_current_state(manifest_full_path)

    test_rel_path = write_conformance_tests(root, tool_name, contract)
    apply_plan = build_apply_plan(root, spec_path, mcp_config_path, tool_name)

    changed_paths: list = []
    if apply and target_state == "real-verified":
        # apply_changes() raises ConnectApplyError (no partial writes) on
        # failure — propagated here before the manifest is ever written, so
        # the prior manifest/config on disk is preserved.
        changed_paths = apply_changes(root, spec_path, mcp_config_path, tool_name, contract, generated_at)
        integration_state = "real-verified"

    normalized_obo = _validate_obo_evidence(obo_evidence)
    normalized_role = _validate_role_evidence(role_evidence)
    evidence_summary = {
        "obo_present": normalized_obo["present"],
        "obo_user_scoped": normalized_obo["user_scoped"],
        "roles_revalidated": normalized_role["revalidated"],
        "required_roles": sorted(normalized_role["required_roles"]),
    }

    manifest = build_connect_manifest(
        tool_name=tool_name,
        integration_state=integration_state,
        target_state=target_state,
        contract=contract,
        conformance=conformance,
        evidence_summary=evidence_summary,
        apply_plan=apply_plan,
        changed_paths=changed_paths,
        apply=apply,
        status=_manifest_status(real_response),
        findings=_build_findings(conformance, target_state),
        generated_at=generated_at,
        valid_for_hours=valid_for_hours,
    )
    write_connect_manifest(manifest_full_path, manifest)

    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "contract": contract,
        "conformance": conformance,
        "integration_state": integration_state,
        "target_state": target_state,
        "apply_plan": apply_plan,
        "changed_paths": changed_paths,
        "test_path": test_rel_path,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_json_arg(path):
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="connect.py",
        description="threadlight-connect — evidence-based mock-to-real integration swap.",
    )
    parser.add_argument("--project-root", default=".", help="generated project root")
    parser.add_argument("--tool-name", required=True)
    parser.add_argument("--tool-source-file", required=True, help="path to the tool's Python source")
    parser.add_argument("--sample-file", required=True, help="JSON mock sample record the tool source reads")
    parser.add_argument(
        "--real-response-file", required=True,
        help='captured real response, JSON {"items": [...]} (or a bare list)',
    )
    parser.add_argument("--obo-evidence-file", default=None, help="JSON OBO evidence (present/user_scoped)")
    parser.add_argument("--role-evidence-file", default=None, help="JSON required-role revalidation evidence")
    parser.add_argument("--current-agent-identity", default=None)
    parser.add_argument(
        "--apply", action="store_true",
        help="write SPEC + mcp-config when fully verified (default: dry-run plan only)",
    )
    parser.add_argument("--spec-path", default=DEFAULT_SPEC_PATH)
    parser.add_argument("--mcp-config-path", default=DEFAULT_MCP_CONFIG_PATH)
    parser.add_argument("--manifest-path", default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--json", action="store_true", help="print the manifest JSON to stdout")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        result = run_connect(
            project_root=args.project_root,
            tool_name=args.tool_name,
            tool_source=Path(args.tool_source_file).read_text(encoding="utf-8"),
            sample=_load_json_arg(args.sample_file),
            real_response=_load_json_arg(args.real_response_file),
            obo_evidence=_load_json_arg(args.obo_evidence_file),
            role_evidence=_load_json_arg(args.role_evidence_file),
            apply=args.apply,
            spec_path=args.spec_path,
            mcp_config_path=args.mcp_config_path,
            manifest_path=args.manifest_path,
            current_agent_identity=args.current_agent_identity,
        )
    except ConnectEvidenceError as exc:
        print(f"error: malformed evidence — {exc} (nothing written)", file=sys.stderr)
        return 2
    except (ConnectApplyError, ManifestValidationError, OSError) as exc:
        print(f"error: {exc} (no files were changed)", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(result["manifest"], indent=2, sort_keys=True))
        return 0

    print(f"integration_state: {result['integration_state']}")
    print(f"target_state: {result['target_state']}")
    print(f"conformance passed: {result['conformance']['passed']}")
    for diff in result["conformance"]["differences"]:
        print(f"  drift: {diff['field']} expected={diff['expected']} actual={diff['actual']} path={diff['path']}")
    print("apply plan:")
    for step in result["apply_plan"]:
        print(f"  [{step['action']}] {step['path']}: {step['description']}")
    if result["changed_paths"]:
        print("changed paths:")
        for changed in result["changed_paths"]:
            print(f"  - {changed}")
    print(f"conformance tests written: {result['test_path']}")
    print(f"manifest written: {result['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
