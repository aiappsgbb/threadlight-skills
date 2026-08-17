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
via `entra-agent-id` + a manual test call). Malformed evidence never writes
anything, so a prior valid `connect-manifest.json` / SPEC.md / mcp-config.json
is preserved untouched. A write failure *during* apply is transactional: SPEC
and the MCP config move together — an in-process failure after the first of
the two `os.replace` calls rolls the already-applied file back to its captured
prior bytes, so the pair never diverges. The one thing this cannot defend
against is a hard crash / power loss between the two individually-atomic
replaces (see `apply_changes`); if the compensating rollback itself fails, a
`ConnectInconsistentStateError` names the unreconciled path(s) rather than
claiming success.
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
    _validate_iso8601_timestamp,
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
    """Raised when applying the SPEC.md + mcp-config.json swap fails.

    `apply_changes()` stages a temp file for both destinations before
    replacing either, and captures each destination's prior existence, bytes,
    and mode up front. If a replace fails *after* an earlier one already
    succeeded, every already-replaced destination is rolled back to its
    captured prior bytes (or removed if it did not previously exist) before
    this is raised — so a recoverable failure leaves the prior SPEC.md /
    mcp-config.json on disk byte-for-byte, never a half-applied swap.

    Honest limitation: each `os.replace` is individually atomic, but the two
    replaces are not a single filesystem transaction. This defends against
    in-process failures (an `os.replace` error, an injected fault); it cannot
    defend against a hard crash / power loss / SIGKILL in the narrow window
    *between* the two replaces. If the compensating rollback itself fails, the
    stronger `ConnectInconsistentStateError` is raised instead and success is
    never reported.
    """


class ConnectInconsistentStateError(ConnectApplyError):
    """Raised when an apply failed AND the compensating rollback also failed,
    leaving SPEC.md / mcp-config.json possibly divergent on disk.

    The message names the exact destination path(s) that could not be
    restored. It subclasses `ConnectApplyError` so existing handlers still
    catch it, but it deliberately does NOT claim "no files were changed": the
    caller must treat the identified paths as being in an unknown, possibly
    partially-applied state and reconcile them by hand.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_identifier(name: str) -> str:
    ident = re.sub(r"\W", "_", name).strip("_") or "tool"
    if ident[0].isdigit():
        ident = f"tool_{ident}"
    return ident


def _docstring_safe(text) -> str:
    """Render an arbitrary string safe to embed inside a generated triple-quoted
    docstring. Collapses all whitespace/newlines to single spaces and
    neutralizes backslashes and double quotes so a hostile value (triple
    quotes, embedded newline, trailing code) can never terminate the generated
    module's docstring and spill into executable module code. Purely for human
    display; the machine-read literals in the generated module always go
    through `repr()`, never this.
    """
    collapsed = " ".join(str(text).split())
    return collapsed.replace("\\", "_").replace('"', "'")


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


def _type_compatible(expected_type, actual_value) -> bool:
    """Whether an observed value satisfies an expected contract type, allowing
    ONLY lossless numeric widening:

    - an integer actual satisfies an expected ``number`` (int is a valid
      number);
    - a float actual satisfies an expected ``integer`` only when it is
      mathematically integral (``1.0`` conforms; ``1.5`` still drifts —
      accepting it would lose information);
    - every other type must match exactly.

    Booleans are never numeric here (``_infer_type`` maps ``bool`` to
    ``boolean``), so a bool never widens into ``integer``/``number``.
    """
    actual_type = _infer_type(actual_value) or "unknown"
    if actual_type == expected_type:
        return True
    if expected_type == "number" and actual_type == "integer":
        return True
    if expected_type == "integer" and actual_type == "number":
        return float(actual_value).is_integer()
    return False


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
    # Display-only, injection-proof renderings for the generated docstring. The
    # machine-read literals below always go through repr()/safe identifiers,
    # never raw interpolation — so a hostile tool_name (triple quotes, newline,
    # code) can never break out of the docstring into executable module code.
    tool_name_display = _docstring_safe(tool_name)
    default_sample_path_display = _docstring_safe(default_sample_path)
    fields_literal = repr(
        [
            {"name": f["name"], "required": f["required"], "type": f["type"]}
            for f in contract["fields"]
        ]
    )
    return f'''"""Auto-generated by threadlight-connect scripts/connect.py.

DO NOT EDIT BY HAND — regenerate via the connect leg's generate-tests phase.
Executable pytest module: asserts a captured real "{tool_name_display}" tool
response conforms to the extracted data contract (fields, types,
requiredness). Reads the real response from CONNECT_REAL_SAMPLE_PATH
(default: {default_sample_path_display}).
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


def _type_compatible(expected_type, actual_value):
    # Lossless numeric widening only: an integer satisfies an expected number;
    # an integral float (1.0) satisfies an expected integer; a non-integral
    # float (1.5) still drifts. bool is never numeric here.
    actual_type = _infer_type(actual_value) or "unknown"
    if actual_type == expected_type:
        return True
    if expected_type == "number" and actual_type == "integer":
        return True
    if expected_type == "integer" and actual_type == "number":
        return float(actual_value).is_integer()
    return False


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
            if field.get("type") is not None and not _type_compatible(field["type"], item[name]):
                actual_type = _infer_type(item[name]) or "unknown"
                differences.append(
                    {{"field": name, "expected": expected, "actual": actual_type, "path": path}}
                )
    return differences


def test_{ident}_conformance():
    with open(REAL_SAMPLE_PATH, "r", encoding="utf-8") as fh:
        real_response = json.load(fh)
    items = real_response.get("items", []) if isinstance(real_response, dict) else []
    assert items, (
        "no real items captured in " + str(REAL_SAMPLE_PATH) + " — conformance "
        "cannot be verified against an empty sample (insufficient evidence)"
    )
    differences = check_conformance(real_response)
    assert not differences, "mock-to-real conformance differences: {{}}".format(differences)
'''


def _default_new_file_mode() -> int:
    """A predictable, non-executable permission mode for a *newly created*
    file, honoring the process umask (``0o666 & ~umask`` — ``0o644`` under the
    usual ``022``). The base is ``0o666`` so the result is never executable.
    """
    current_umask = os.umask(0)
    os.umask(current_umask)
    return 0o666 & ~current_umask


def _mode_for_destination(path: Path) -> int:
    """Permission bits to stamp on a freshly staged temp *before* it replaces
    ``path``: an existing destination keeps its EXACT current mode; a new file
    gets the predictable non-executable default. Applying the mode on the
    forward write (not only on rollback) keeps a restrictive prior file
    restrictive after the swap, and a world-readable one world-readable —
    ``os.replace`` otherwise leaks the temp's private ``0o600``.
    """
    if path.exists():
        return path.stat().st_mode & 0o7777
    return _default_new_file_mode()


def _write_temp_file(path: Path, content: str, mode: int | None = None) -> Path:
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
    os.chmod(temp_path, _default_new_file_mode() if mode is None else mode)
    return temp_path


def _write_temp_bytes(path: Path, content: bytes, mode: int | None = None) -> Path:
    """Stage exact bytes in the destination's own directory (so a follow-up
    `os.replace` is atomic). Used by rollback to restore prior file contents
    verbatim. The target mode is stamped on the temp before it is returned, so
    the replaced file carries the intended permissions with no window at the
    temp's private default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_path, _default_new_file_mode() if mode is None else mode)
    return temp_path


def _atomic_write_text(path: Path, content: str) -> None:
    temp_path = None
    try:
        temp_path = _write_temp_file(path, content, _mode_for_destination(path))
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
    is `{"items": [...]}` (or a bare list of records). Every field-level
    difference has the exact shape `{field, expected, actual, path}`, e.g.
    `status/string|required/missing/$.items[0].status`.

    An empty or missing `items` list is NOT a pass. With no real records to
    check, conformance is *unevaluated*: `evaluated` is False and `passed` is
    False. An unevaluated result is insufficient evidence and must target
    `real-unverified` — never a vacuous `real-verified`, and distinct from
    `real-drift` (which means records WERE checked and found to diverge).
    `item_count` reports how many records were actually checked.
    """
    if isinstance(real_response, dict):
        items = real_response.get("items", [])
    elif isinstance(real_response, list):
        items = real_response
    else:
        items = []
    if not isinstance(items, list):
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
            if field.get("type") is not None and not _type_compatible(
                field["type"], record[name]
            ):
                differences.append(
                    {"field": name, "expected": expected, "actual": actual_type, "path": path}
                )

    evaluated = len(items) > 0
    return {
        "passed": evaluated and not differences,
        "evaluated": evaluated,
        "item_count": len(items),
        "differences": differences,
    }


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
    """Required-role revalidation is verified ONLY when it is tied to the exact
    CURRENT agent identity — it can never be opt-in. Verifying requires (1) a
    caller-supplied `current_agent_identity` and (2) role evidence that records
    exactly that identity. A missing current identity, or evidence whose
    recorded identity is absent or does not match, is unverified — a stale
    grant from a previous publish never carries over.
    """
    if normalized_role["revalidated"] is not True:
        return False
    required = set(normalized_role["required_roles"])
    validated = set(normalized_role["validated_roles"])
    if not required.issubset(validated):
        return False
    # Current identity is mandatory: revalidation cannot be verified against a
    # missing identity, and the evidence must name exactly the current one.
    if current_agent_identity is None:
        return False
    if normalized_role["agent_identity"] != current_agent_identity:
        return False
    return True


def transition_integration(conformance: dict, obo_evidence, role_evidence, *, current_agent_identity=None) -> str:
    """Pure function computing target_state from evidence:

    - conformance could not be evaluated (no real items captured)
      => real-unverified (insufficient evidence — never a vacuous pass, and
      distinct from real-drift)
    - field-level conformance failure/differences => real-drift
    - conformance passed but OBO user-scoped evidence missing/false, or
      required roles not revalidated against the CURRENT agent identity
      (a missing current identity, or a stale/mismatched one) => real-unverified
    - real-verified only after conformance passes AND OBO is user-scoped AND
      required roles are revalidated against the supplied current identity

    Raises ConnectEvidenceError (not a state) for malformed evidence shapes —
    callers must not persist anything when this raises.
    """
    normalized_obo = _validate_obo_evidence(obo_evidence)
    normalized_role = _validate_role_evidence(role_evidence)

    # Insufficient evidence (nothing real to check) can NEVER be a pass — it is
    # unverified, checked BEFORE the drift branch so an empty response is not
    # mistaken for evaluated-and-clean.
    if not conformance.get("evaluated", True):
        return "real-unverified"
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


def _capture_prior(path: Path) -> dict:
    """Snapshot a destination's prior state for transactional rollback: whether
    it existed, its exact bytes, and its mode. Read once, up front, before any
    replace runs, so rollback can restore it verbatim.
    """
    if path.exists():
        return {"existed": True, "bytes": path.read_bytes(), "mode": path.stat().st_mode}
    return {"existed": False, "bytes": None, "mode": None}


def _restore_prior(path: Path, prior: dict) -> None:
    """Roll a destination back to its captured prior state: rewrite the exact
    prior bytes via a same-directory atomic temp + `os.replace` (restoring the
    prior mode by stamping it on the temp file before the replace), or remove
    the file if it did not previously exist. Raises if the restore itself
    cannot complete (a failed rollback).
    """
    if not prior["existed"]:
        path.unlink(missing_ok=True)
        return
    restore_mode = (prior["mode"] & 0o7777) if prior["mode"] is not None else None
    temp_path = None
    try:
        temp_path = _write_temp_bytes(path, prior["bytes"], restore_mode)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _load_existing_mcp_config(mcp_full: Path, mcp_config_path) -> dict:
    if not mcp_full.exists():
        return {}
    raw_mcp = mcp_full.read_text(encoding="utf-8")
    try:
        existing_mcp = json.loads(raw_mcp)
    except json.JSONDecodeError as exc:
        raise ConnectEvidenceError(
            f"existing mcp-config at {mcp_config_path} is not valid JSON "
            f"({exc}); refusing to overwrite — repair or remove it first"
        ) from exc
    if not isinstance(existing_mcp, dict):
        raise ConnectEvidenceError(
            f"existing mcp-config at {mcp_config_path} must be a JSON object; "
            f"got {type(existing_mcp).__name__} — refusing to overwrite"
        )
    return existing_mcp


def apply_changes(project_root, spec_path, mcp_config_path, tool_name: str, contract: dict, generated_at: str) -> list:
    """Apply the SPEC.md + mcp-config.json swap as one transactional unit.

    Guarantee (within a single process): either BOTH destinations are updated
    or NEITHER is left changed. A temp file is staged for both destinations
    before either is replaced, and each destination's prior existence, bytes,
    and mode are captured up front. If a replace fails after an earlier one
    already succeeded, every already-replaced destination is rolled back to
    its captured prior bytes (or removed if it did not previously exist) via a
    same-directory atomic temp + `os.replace`, and a `ConnectApplyError` is
    raised — the prior files survive byte-for-byte.

    Honest limitation: each `os.replace` is individually atomic, but the two
    replaces are not a single filesystem transaction, so this cannot defend
    against a hard crash / power loss / SIGKILL in the window *between* the two
    replaces — that (and only that) can leave SPEC.md and mcp-config.json
    divergent on disk. If the compensating rollback itself fails, a
    `ConnectInconsistentStateError` naming the unreconciled path(s) is raised
    and success is never reported.
    """
    root = Path(project_root)
    spec_full = root / spec_path
    mcp_full = root / mcp_config_path

    existing_spec = spec_full.read_text(encoding="utf-8") if spec_full.exists() else ""
    existing_mcp = _load_existing_mcp_config(mcp_full, mcp_config_path)

    new_spec_text = _update_spec_text(existing_spec, tool_name, contract, generated_at)
    new_mcp_data = _update_mcp_config(existing_mcp, tool_name, contract, generated_at)
    new_mcp_text = json.dumps(new_mcp_data, indent=2, sort_keys=True) + "\n"

    # Fixed order; each entry is (destination, relative-path, new-text).
    targets = [
        (spec_full, spec_path, new_spec_text),
        (mcp_full, mcp_config_path, new_mcp_text),
    ]

    # Snapshot prior state for EVERY destination before touching anything, so
    # a mid-apply failure can be fully compensated.
    priors = {dest: _capture_prior(dest) for dest, _, _ in targets}

    # Stage a temp file for every destination before replacing ANY of them.
    # Each staged temp is stamped with the mode the destination should end up
    # with: an existing destination keeps its exact prior mode; a brand-new
    # destination gets the umask-honoring non-executable default.
    staged: list = []  # (destination, temp_path)
    try:
        for dest, _, text in targets:
            prior = priors[dest]
            desired_mode = (
                (prior["mode"] & 0o7777) if prior["existed"] else _default_new_file_mode()
            )
            staged.append((dest, _write_temp_file(dest, text, desired_mode)))
    except BaseException as exc:
        for _, temp_path in staged:
            temp_path.unlink(missing_ok=True)
        raise ConnectApplyError(f"failed to stage mock-to-real swap: {exc}") from exc

    # Commit one destination at a time, tracking which have been replaced so a
    # later failure can roll the earlier ones back.
    replaced: list = []
    pending = dict(staged)  # destination -> temp_path not yet replaced
    try:
        for dest, temp_path in staged:
            os.replace(temp_path, dest)
            replaced.append(dest)
            del pending[dest]
    except BaseException as exc:
        # Discard temp files for destinations we never reached.
        for temp_path in pending.values():
            temp_path.unlink(missing_ok=True)
        # Roll every already-committed destination back to its prior state.
        unreconciled = []
        for dest in reversed(replaced):
            try:
                _restore_prior(dest, priors[dest])
            except BaseException:
                unreconciled.append(dest)
        if unreconciled:
            names = ", ".join(str(p) for p in unreconciled)
            raise ConnectInconsistentStateError(
                "apply failed and rollback could not restore "
                f"{names}: SPEC.md / mcp-config.json may be divergent on disk "
                "and must be reconciled by hand"
            ) from exc
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
    Defaults to `mock` — the starting state for every integration — but ONLY
    when there is no prior manifest at all. A prior manifest that exists yet
    cannot be read or parsed is NOT silently reset to `mock`: that would
    discard a real prior integration_state (e.g. downgrade a live
    `real-verified` back to `mock`) and mask on-disk corruption. Raise a clean
    `ConnectEvidenceError` before any write so the bytes on disk survive
    untouched for the operator to repair.
    """
    path = Path(manifest_full_path)
    if not path.exists():
        return "mock"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConnectEvidenceError(
            f"prior connect manifest at {path} cannot be read ({exc}); refusing "
            "to reset integration state — repair or remove it first"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectEvidenceError(
            f"prior connect manifest at {path} is not valid JSON ({exc}); "
            "refusing to reset integration state to mock — repair or remove it first"
        ) from exc
    if not isinstance(data, dict):
        raise ConnectEvidenceError(
            f"prior connect manifest at {path} must be a JSON object; got "
            f"{type(data).__name__} — refusing to reset integration state"
        )
    state = data.get("integration_state")
    return state if state in VALID_STATES else "mock"


def _build_findings(conformance: dict, target_state: str) -> list:
    findings = [
        {"id": f"CONNECT-DRIFT-{diff['field']}", "status": "must-fix", "detail": diff}
        for diff in conformance.get("differences", [])
    ]
    if not conformance.get("evaluated", True):
        findings.append(
            {
                "id": "CONNECT-EVIDENCE-EMPTY",
                "status": "not-verified",
                "detail": (
                    "captured real response contained no items to check — "
                    "conformance cannot be verified vacuously; target held at "
                    "real-unverified until a non-empty real sample is provided"
                ),
            }
        )
    elif target_state == "real-unverified":
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
    evidence_captured_at=None,
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
        source_oldest_at=evidence_captured_at,
        findings=findings,
        payload=payload,
    )


def _reject_unknown_keys(obj, allowed: set, label: str) -> None:
    """Enforce the schema's ``additionalProperties: false`` for one controlled
    object: raise if *obj* carries any key outside *allowed*. Kept stdlib-only
    (no `jsonschema` runtime dependency) so validation stays consistent with
    the rest of the repo.
    """
    if not isinstance(obj, dict):
        return
    unknown = set(obj).difference(allowed)
    if unknown:
        raise ManifestValidationError(
            f"{label} has unknown key(s): " + ", ".join(sorted(unknown))
        )


_MANIFEST_TOP_LEVEL_KEYS = {
    "schema", "tool_version", "generated_at", "freshness", "status", "findings",
    "tool_name", "integration_state", "target_state", "contract", "conformance",
    "evidence_summary", "apply_plan", "changed_paths", "apply",
}
_CONFORMANCE_KEYS = {"passed", "evaluated", "item_count", "differences"}
_DIFFERENCE_KEYS = {"field", "expected", "actual", "path"}
_EVIDENCE_SUMMARY_KEYS = {"obo_present", "obo_user_scoped", "roles_revalidated", "required_roles"}
_CONTRACT_KEYS = {"schema", "tool_name", "generated_at", "fields"}
_CONTRACT_FIELD_KEYS = {"name", "required", "type", "cardinality"}
_FINDING_REQUIRED_KEYS = {"id", "status"}
_APPLY_PLAN_ITEM_REQUIRED_KEYS = {"path", "action", "description"}


def _require_object_keys(value, required: set, label: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{label} must be an object")
    missing = required.difference(value)
    if missing:
        raise ManifestValidationError(
            f"{label} missing required key(s): " + ", ".join(sorted(missing))
        )
    return value


def validate_connect_manifest(manifest: dict) -> None:
    """Hand-rolled schema check (mirrors references/connect-manifest.schema.json)
    layered on top of the shared envelope's own validation — stdlib-only, no
    `jsonschema` dependency, consistent with the rest of the repo. Unknown keys
    are rejected (the schema's ``additionalProperties: false``) at the top level
    and in every controlled nested object: `conformance`, each `differences`
    item, `evidence_summary`, and the `contract` (+ its field items). Required
    keys and object shapes are enforced for findings and apply-plan items too.
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

    _reject_unknown_keys(manifest, _MANIFEST_TOP_LEVEL_KEYS, "connect manifest")

    for key in ("integration_state", "target_state"):
        if manifest[key] not in VALID_STATES:
            raise ManifestValidationError(f"unknown {key}: {manifest[key]!r}")

    contract = _require_object_keys(manifest["contract"], _CONTRACT_KEYS, "contract")
    _reject_unknown_keys(contract, _CONTRACT_KEYS, "contract")
    contract_fields = contract["fields"]
    if not isinstance(contract_fields, list):
        raise ManifestValidationError("contract.fields must be a list")
    for field in contract_fields:
        field = _require_object_keys(field, _CONTRACT_FIELD_KEYS, "contract field")
        _reject_unknown_keys(field, _CONTRACT_FIELD_KEYS, "contract field")

    for finding in manifest["findings"]:
        _require_object_keys(finding, _FINDING_REQUIRED_KEYS, "finding")

    conformance = manifest["conformance"]
    conformance = _require_object_keys(conformance, _CONFORMANCE_KEYS, "conformance")
    _reject_unknown_keys(conformance, _CONFORMANCE_KEYS, "conformance")
    if not isinstance(conformance["differences"], list):
        raise ManifestValidationError("conformance.differences must be a list")
    for diff in conformance["differences"]:
        diff = _require_object_keys(diff, _DIFFERENCE_KEYS, "conformance difference")
        _reject_unknown_keys(diff, _DIFFERENCE_KEYS, "conformance difference")

    evidence_summary = _require_object_keys(
        manifest["evidence_summary"], _EVIDENCE_SUMMARY_KEYS, "evidence_summary"
    )
    _reject_unknown_keys(evidence_summary, _EVIDENCE_SUMMARY_KEYS, "evidence_summary")

    if not isinstance(manifest["apply_plan"], list):
        raise ManifestValidationError("apply_plan must be a list")
    for item in manifest["apply_plan"]:
        _require_object_keys(item, _APPLY_PLAN_ITEM_REQUIRED_KEYS, "apply_plan item")
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
def _validate_evidence_captured_at(value):
    """Validate an optional caller-supplied evidence-capture timestamp EARLY —
    before any file is written — so a malformed value aborts the run cleanly
    without disturbing a prior manifest/config. `None` (unknown) is allowed and
    is recorded as such in `freshness.source_oldest_at`; the manifest never
    borrows `generated_at` to fake source freshness.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConnectEvidenceError(
            "evidence_captured_at must be an ISO-8601 timestamp string or null"
        )
    try:
        _validate_iso8601_timestamp(value, "evidence_captured_at", nullable=True)
    except ManifestValidationError as exc:
        raise ConnectEvidenceError(str(exc)) from exc
    return value


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
    evidence_captured_at=None,
    valid_for_hours=24,
) -> dict:
    generated_at = generated_at or _now_iso()
    # Fail fast on a malformed capture timestamp, before any artifact is
    # written (the manifest — where source_oldest_at lands — is emitted only
    # after write_conformance_tests / apply_changes).
    evidence_captured_at = _validate_evidence_captured_at(evidence_captured_at)
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

    if apply and target_state == "real-verified":
        # Validate the apply destination before generating any artifact. This
        # keeps the CLI's "(nothing written)" guarantee literal when an
        # existing MCP config is corrupt or is not a JSON object.
        _load_existing_mcp_config(root / mcp_config_path, mcp_config_path)

    test_rel_path = write_conformance_tests(root, tool_name, contract)
    apply_plan = build_apply_plan(root, spec_path, mcp_config_path, tool_name)

    changed_paths: list = []
    if apply and target_state == "real-verified":
        # apply_changes() is transactional across SPEC.md + mcp-config.json: on
        # an in-process failure it rolls any already-applied file back to its
        # prior bytes and raises ConnectApplyError (or, if rollback itself
        # fails, ConnectInconsistentStateError). Either propagates here before
        # the manifest is written, so the prior manifest on disk is preserved.
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
        evidence_captured_at=evidence_captured_at,
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
        "--evidence-captured-at", default=None,
        help=(
            "ISO-8601 timestamp the real evidence was captured at; recorded as "
            "freshness.source_oldest_at. Omit when unknown (recorded as null — "
            "never faked from the run's own generated_at)."
        ),
    )
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
            evidence_captured_at=args.evidence_captured_at,
        )
    except ConnectEvidenceError as exc:
        print(f"error: malformed evidence — {exc} (nothing written)", file=sys.stderr)
        return 2
    except ConnectInconsistentStateError as exc:
        # Rollback failed: SPEC.md / mcp-config.json may be divergent. Report
        # the unreconciled paths honestly — do NOT claim nothing changed.
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except (json.JSONDecodeError, SyntaxError) as exc:
        # Unparseable CLI input — malformed JSON in a --*-file argument, or a
        # tool source that isn't valid Python. Surface a clean one-line error
        # (no traceback), a stable nonzero code, and write nothing: these are
        # raised during parsing, before any artifact is produced.
        print(f"error: could not parse input — {exc} (nothing written)", file=sys.stderr)
        return 5
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
