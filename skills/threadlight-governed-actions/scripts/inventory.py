"""Explicit action inventory for threadlight-governed-actions.

Discovers and normalizes the set of governed actions an agent can invoke by
merging three independent, read-only sources — never inferring one from
another:

- SPEC section 8 (``specs/SPEC.md``), which names actions that require
  explicit approval;
- declarative tool registries (``agent.yaml``/``agent.yml``/
  ``tool-registry.json``/``tool_registry.json``), which are the *only*
  authoritative source of consequence class, execution modes, and other
  governance metadata; and
- Python source, walked with ``ast`` (never imported/executed) to find
  ``@tool``/``@function_tool``/``@kernel_function``-decorated functions and
  ``register_tool(...)`` calls.

Nothing here infers a consequence class from a name or verb, invents an
authorization/approval/policy rule, or executes target repository code.
Every ambiguity (a drifted source set, a duplicate alias, a missing
consequence, a missing SAFE declaration) becomes an explicit
:class:`contracts.Finding` or an explicit ``not-verified``/``must-fix``
status — never a silently-assumed ``pass``. Contradictory declarations
(conflicting duplicate IDs, an unrecognized consequence string) raise
:class:`InventoryError` rather than being silently merged or guessed at.

Every ID named by any of the three sources — including a SPEC-only or
Python-only mention with no counterpart elsewhere — joins the merged
union exactly once; none of them are ever silently dropped. A discovered
Python or SPEC identifier that matches one of a registry action's
declared aliases resolves to that action's canonical ID rather than
becoming a separate phantom action, but only when the alias is
unambiguous (claimed by exactly one action) and does not collide with
another action's own registered ID — an ambiguous or colliding alias is
left unresolved rather than guessed at.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Set, Tuple

from dataclasses import dataclass, replace

import canonical
import contracts
from contracts import ActionRecord, Finding, Status


class InventoryError(ValueError):
    """Raised when action metadata is contradictory or cannot be trusted.

    Examples: the same normalized action ID declared twice with different
    metadata, an unrecognized consequence string, or a registry file that
    cannot be parsed as the expected structure. These are all situations
    where silently merging or guessing would hide a real authoring error —
    the caller must fix the source, not have the inventory paper over it.
    """


@dataclass(frozen=True)
class InventoryResult:
    actions: Tuple[ActionRecord, ...]
    findings: Tuple[Finding, ...]
    spec_section_sha256: str
    safe_requirements: Mapping[str, Status]
    policy_paths: Tuple[Path, ...]


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Precedence when a single action legitimately fits more than one
# consequence class: the first entry here always wins as the primary class;
# the rest (if also declared) become ``secondary_consequences``, in the same
# relative order. Unknown consequence is deliberately *not* in this table —
# it is never treated as ``read``.
_CONSEQUENCE_PRECEDENCE: Tuple[str, ...] = (
    "irreversible",
    "external-egress",
    "write",
    "read",
)
_CONSEQUENCE_RANK = {name: index for index, name in enumerate(_CONSEQUENCE_PRECEDENCE)}

_REGISTRY_FILENAMES: Tuple[str, ...] = (
    "agent.yaml",
    "agent.yml",
    "tool-registry.json",
    "tool_registry.json",
)

_SAFE_REQUIREMENT_KEYS: Tuple[str, ...] = (
    "authorization",
    "approval",
    "idempotency-or-transaction",
    "output-mediation",
    "audit",
)

_POLICY_GLOBS: Tuple[str, ...] = (
    "governance/**/*.json",
    "governance/**/*.yaml",
    "governance/**/*.yml",
    "policies/**/*.json",
    "policies/**/*.yaml",
    "policies/**/*.yml",
)

# Action tokens: a lowercase dotted identifier, e.g. ``payments.refund``.
# Requires at least one dot (a bare word is never an action ID here).
_ACTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")
_BACKTICK_TOKEN_PATTERN = re.compile(r"`([^`\n]+)`")

_RECOGNIZED_DECORATORS: frozenset = frozenset(
    {"tool", "function_tool", "kernel_function"}
)
_RECOGNIZED_CALLS: frozenset = frozenset({"register_tool"})

# Level-2 markdown heading: exactly two ``#`` characters (a third ``#``
# would make it level-3), followed by whitespace and the heading text.
_HEADING_PATTERN = re.compile(r"^##(?!#)\s*(.*)$")

_CHECKBOX_PATTERN = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+?)\s*$")


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{canonical.sha256_hex(data)}"


def _normalize_action_id(raw: str) -> str:
    return str(raw).strip().lower()


# ---------------------------------------------------------------------------
# SPEC section 8
# ---------------------------------------------------------------------------


def _read_section_8_text(path: Path) -> str:
    """Return the raw text of the level-2 heading section starting with "8".

    Reads only ``path`` (the caller passes ``specs/SPEC.md``). If the file
    does not exist, or exists but has no level-2 heading whose normalized
    text starts with ``"8"``, returns ``""`` — never raises, since a missing
    or SPEC-less target is a normal (if incomplete) state to report through
    findings, not a hard failure.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return ""

    lines = text.splitlines()
    section_lines: list[str] = []
    in_section = False
    for line in lines:
        match = _HEADING_PATTERN.match(line)
        if match is not None:
            if in_section:
                break
            heading_text = match.group(1).strip()
            if heading_text.startswith("8"):
                in_section = True
                section_lines.append(line)
            continue
        if in_section:
            section_lines.append(line)
    return "\n".join(section_lines)


def _extract_action_ids(section_text: str) -> Set[str]:
    ids: Set[str] = set()
    for token in _BACKTICK_TOKEN_PATTERN.findall(section_text):
        if _ACTION_ID_PATTERN.match(token):
            ids.add(_normalize_action_id(token))
    return ids


def _parse_safe_requirements(section_text: str) -> Dict[str, Status]:
    """Report each SAFE key as ``pass`` (checked box) or ``not-verified``.

    A requirement that never appears as a checklist item, or appears but is
    unchecked, is ``not-verified`` — a missing declaration is never inferred
    as a passing one.
    """
    declared: Dict[str, Status] = {key: "not-verified" for key in _SAFE_REQUIREMENT_KEYS}
    for line in section_text.splitlines():
        match = _CHECKBOX_PATTERN.match(line)
        if match is None:
            continue
        checked = match.group(1).lower() == "x"
        label = match.group(2).split(":", 1)[0].strip().lower()
        if label in declared and checked:
            declared[label] = "pass"
    return declared


def parse_spec_section_8(path: Path) -> Tuple[Set[str], str]:
    """Return (action IDs mentioned, sha256 of the section 8 text).

    Only ``specs/SPEC.md``'s level-2 heading section whose normalized text
    starts with ``"8"`` is read (stopping at the next level-2 heading).
    Action IDs are extracted only from backtick-delimited tokens matching
    ``^[a-z][a-z0-9_-]*(\\.[a-z][a-z0-9_-]*)+$``. The digest binds the exact
    section text (missing file/section hashes as the digest of ``""``), so
    it is reproducible and detects any change to the approval declarations.
    """
    text = _read_section_8_text(Path(path))
    return _extract_action_ids(text), _sha256_prefixed(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Registries (agent.yaml / agent.yml / tool-registry.json / tool_registry.json)
# ---------------------------------------------------------------------------


def _load_registry_document(path: Path) -> Any:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise InventoryError(f"cannot read registry file {path}: {error}") from error

    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as error:  # pragma: no cover - environment guard
            raise InventoryError(
                "pyyaml is required to parse YAML tool registries "
                f"({path}); install it with `pip install pyyaml`"
            ) from error
        try:
            document = yaml.safe_load(raw_text)
        except yaml.YAMLError as error:
            raise InventoryError(f"invalid YAML in registry file {path}: {error}") from error
    else:
        try:
            document = json.loads(raw_text)
        except json.JSONDecodeError as error:
            raise InventoryError(f"invalid JSON in registry file {path}: {error}") from error
    return document


def _load_registry_entries(path: Path) -> list:
    document = _load_registry_document(path)
    if document is None:
        return []
    if not isinstance(document, Mapping) or "tools" not in document:
        raise InventoryError(
            f"registry file {path} must be a mapping with a top-level 'tools' list"
        )
    tools = document["tools"]
    if not isinstance(tools, list):
        raise InventoryError(f"registry file {path}: 'tools' must be a list")
    return tools


def _normalize_consequence(
    raw: object, action_id: str, source_desc: str
) -> Tuple[Optional[str], Tuple[str, ...]]:
    """Return (primary consequence, secondary consequences) from *raw*.

    *raw* may be absent (``None``: unclassified, never inferred), a single
    consequence string, or a list of consequence strings for an action that
    legitimately fits more than one class. Primary is always the
    highest-precedence declared class (``irreversible`` > ``external-egress``
    > ``write`` > ``read``); the rest — in the same precedence order — are
    preserved as ``secondary_consequences`` rather than discarded. An
    unrecognized string is never silently dropped or coerced: it raises.
    """
    if raw is None:
        return None, ()
    values = [raw] if isinstance(raw, str) else list(raw)
    normalized: list[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text not in contracts.CONSEQUENCE_CLASSES:
            raise InventoryError(
                f"{source_desc}: action '{action_id}' declares unknown "
                f"consequence {value!r}; must be one of "
                f"{contracts.CONSEQUENCE_CLASSES}"
            )
        if text not in normalized:
            normalized.append(text)
    if not normalized:
        return None, ()
    ordered = sorted(normalized, key=lambda item: _CONSEQUENCE_RANK[item])
    primary = ordered[0]
    secondary = tuple(ordered[1:])
    return primary, secondary


def _normalize_modes(raw: object, action_id: str, source_desc: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    values = [raw] if isinstance(raw, str) else list(raw)
    normalized: Set[str] = set()
    for value in values:
        text = str(value).strip().lower()
        if text not in contracts.EXECUTION_MODES:
            raise InventoryError(
                f"{source_desc}: action '{action_id}' declares unknown "
                f"execution mode {value!r}; must be one of "
                f"{contracts.EXECUTION_MODES}"
            )
        normalized.add(text)
    return tuple(sorted(normalized))


def _normalize_string_list(raw: object) -> Tuple[str, ...]:
    if raw is None:
        return ()
    values = [raw] if isinstance(raw, str) else list(raw)
    return tuple(sorted({str(value).strip() for value in values}))


def _schema_hash(raw: object) -> Optional[str]:
    if raw is None:
        return None
    return _sha256_prefixed(canonical.canonical_bytes(raw))


def _build_registry_action_record(
    raw_entry: Mapping[str, object], declaration_ref: str
) -> ActionRecord:
    if "id" not in raw_entry or not str(raw_entry["id"]).strip():
        raise InventoryError(
            f"registry entry in {declaration_ref} is missing a required 'id'"
        )
    action_id = _normalize_action_id(raw_entry["id"])
    source_desc = f"registry file {declaration_ref}"

    consequence, secondary = _normalize_consequence(
        raw_entry.get("consequence"), action_id, source_desc
    )
    execution_modes = _normalize_modes(
        raw_entry.get("execution_modes"), action_id, source_desc
    )
    aliases = _normalize_string_list(raw_entry.get("aliases"))
    policy_ids = _normalize_string_list(raw_entry.get("policy_ids"))

    reversible = raw_entry.get("reversible")
    if reversible is not None and not isinstance(reversible, bool):
        raise InventoryError(
            f"{source_desc}: action '{action_id}' 'reversible' must be a boolean"
        )
    approval_required = raw_entry.get("approval_required")
    if approval_required is not None and not isinstance(approval_required, bool):
        raise InventoryError(
            f"{source_desc}: action '{action_id}' 'approval_required' must be a boolean"
        )
    provider_hosted = raw_entry.get("provider_hosted", False)
    if not isinstance(provider_hosted, bool):
        raise InventoryError(
            f"{source_desc}: action '{action_id}' 'provider_hosted' must be a boolean"
        )

    owner = raw_entry.get("owner")
    compensation_ref = raw_entry.get("compensation_ref")
    display_name = raw_entry.get("display_name", action_id)

    return ActionRecord(
        action_id=action_id,
        display_name=str(display_name),
        aliases=aliases,
        owner=str(owner) if owner is not None else None,
        declaration_refs=(declaration_ref,),
        implementation_refs=(),
        input_schema_sha256=_schema_hash(raw_entry.get("input_schema")),
        output_schema_sha256=_schema_hash(raw_entry.get("output_schema")),
        source="registry",
        consequence=consequence,
        secondary_consequences=secondary,
        reversible=reversible,
        compensation_ref=str(compensation_ref) if compensation_ref is not None else None,
        execution_modes=execution_modes,
        provider_hosted=bool(provider_hosted),
        approval_required=approval_required,
        policy_ids=policy_ids,
        known_runtime_paths=(),
        inventory_status="not-verified",
    )


def _fields_match(left: ActionRecord, right: ActionRecord) -> bool:
    comparable = (
        "display_name",
        "aliases",
        "owner",
        "input_schema_sha256",
        "output_schema_sha256",
        "consequence",
        "secondary_consequences",
        "reversible",
        "compensation_ref",
        "execution_modes",
        "provider_hosted",
        "approval_required",
        "policy_ids",
    )
    return all(getattr(left, field) == getattr(right, field) for field in comparable)


def parse_action_registries(root: Path) -> Dict[str, ActionRecord]:
    """Parse every present registry file into normalized :class:`ActionRecord`.

    Reads ``agent.yaml``, ``agent.yml``, ``tool-registry.json``, and
    ``tool_registry.json`` directly under *root* when present. Registry
    metadata is authoritative and is never inferred from names or verbs.

    The same normalized action ID may legitimately appear more than once
    (e.g. once per registry file) *only* if every declaration agrees; a
    conflicting duplicate — the same ID with different metadata — raises
    :class:`InventoryError` rather than silently keeping one side. An
    unrecognized consequence or execution-mode string likewise raises.
    """
    root_path = Path(root)
    merged: Dict[str, ActionRecord] = {}
    for filename in _REGISTRY_FILENAMES:
        candidate = root_path / filename
        if not candidate.is_file():
            continue
        declaration_ref = candidate.relative_to(root_path).as_posix()
        for raw_entry in _load_registry_entries(candidate):
            if not isinstance(raw_entry, Mapping):
                raise InventoryError(
                    f"registry file {declaration_ref} has a non-mapping tool entry"
                )
            record = _build_registry_action_record(raw_entry, declaration_ref)
            existing = merged.get(record.action_id)
            if existing is None:
                merged[record.action_id] = record
            elif _fields_match(existing, record):
                merged[record.action_id] = replace(
                    existing,
                    declaration_refs=tuple(
                        sorted(set(existing.declaration_refs) | set(record.declaration_refs))
                    ),
                )
            else:
                raise InventoryError(
                    f"action '{record.action_id}' is declared more than once with "
                    "conflicting metadata (registries must agree on every field)"
                )
    return merged


def _alias_owner_map(registry: Mapping[str, ActionRecord]) -> Dict[str, Set[str]]:
    """Map each normalized registry alias to the set of action IDs claiming it.

    Alias strings are compared case-insensitively (normalized the same way
    as an action ID), since discovered Python/SPEC identifiers are always
    lowercase-normalized before being looked up. This single map is the
    shared source of truth for both alias *resolution* (an alias claimed
    by exactly one action) and alias *ambiguity reporting* (an alias
    claimed by more than one action) so the two can never diverge.
    """
    owners: Dict[str, Set[str]] = {}
    for action_id, record in registry.items():
        for alias in record.aliases:
            owners.setdefault(_normalize_action_id(alias), set()).add(action_id)
    return owners


def _build_alias_index(registry: Mapping[str, ActionRecord]) -> Dict[str, str]:
    """Map each *unambiguous* registry alias to its one canonical action ID.

    An alias claimed by more than one action is ambiguous — that ambiguity
    is already reported separately as an ``ACT-002`` ``duplicate-alias``
    finding — and is deliberately excluded from the returned index:
    guessing which of the two actions it refers to would itself be an
    invented mapping.
    """
    owners = _alias_owner_map(registry)
    return {
        alias: next(iter(candidate_owners))
        for alias, candidate_owners in owners.items()
        if len(candidate_owners) == 1
    }


def _canonicalize_action_id(
    raw_id: str, registry: Mapping[str, ActionRecord], alias_index: Mapping[str, str]
) -> str:
    """Resolve a discovered (Python/SPEC) *raw_id* to its canonical action ID.

    An ID that is itself a registered canonical action ID is always
    returned unchanged — a real registered action is never folded into
    someone else's alias, even if its own ID string happens to also be
    used as an alias elsewhere. Otherwise, an unambiguous registry alias
    match resolves to the one action that owns it. Anything else (no
    match at all, or an alias claimed by more than one action) is
    returned unchanged rather than guessed at.
    """
    if raw_id in registry:
        return raw_id
    return alias_index.get(raw_id, raw_id)


def _canonicalize_ref_map(
    refs: Mapping[str, Tuple[str, ...]],
    registry: Mapping[str, ActionRecord],
    alias_index: Mapping[str, str],
) -> Dict[str, Tuple[str, ...]]:
    """Resolve every key of a discovered ``action_id -> paths`` map to its
    canonical action ID (see :func:`_canonicalize_action_id`), merging the
    path sets of any raw IDs that resolve to the same canonical ID."""
    merged: Dict[str, Set[str]] = {}
    for raw_id, paths in refs.items():
        canonical_id = _canonicalize_action_id(raw_id, registry, alias_index)
        merged.setdefault(canonical_id, set()).update(paths)
    return {action_id: tuple(sorted(paths)) for action_id, paths in merged.items()}


# ---------------------------------------------------------------------------
# Python tool discovery (static AST only — never imported/executed)
# ---------------------------------------------------------------------------


def _decorator_or_call_name(node: ast.expr) -> Optional[str]:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _literal_name_kwarg(call: ast.Call) -> Optional[str]:
    for keyword in call.keywords:
        if (
            keyword.arg == "name"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            return keyword.value.value
    return None


def _discover_python_tool_refs(root: Path) -> Dict[str, Tuple[str, ...]]:
    root_path = Path(root).resolve()
    refs: Dict[str, Set[str]] = {}
    for py_file in sorted(root_path.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError, UnicodeDecodeError):
            # Not a source of trustworthy tool declarations; skip rather
            # than fail the whole inventory over one unparsable file.
            continue
        relative = py_file.relative_to(root_path).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    dec_name = _decorator_or_call_name(decorator)
                    if dec_name not in _RECOGNIZED_DECORATORS:
                        continue
                    literal = (
                        _literal_name_kwarg(decorator)
                        if isinstance(decorator, ast.Call)
                        else None
                    )
                    action_id = _normalize_action_id(literal or node.name)
                    refs.setdefault(action_id, set()).add(relative)
            elif isinstance(node, ast.Call):
                call_name = _decorator_or_call_name(node)
                if call_name in _RECOGNIZED_CALLS:
                    literal = _literal_name_kwarg(node)
                    if literal is not None:
                        refs.setdefault(_normalize_action_id(literal), set()).add(relative)
    return {action_id: tuple(sorted(paths)) for action_id, paths in refs.items()}


def discover_python_tools(root: Path) -> Set[str]:
    """Return the set of action IDs discoverable via static AST inspection.

    Recognizes ``@tool``/``@function_tool``/``@kernel_function`` decorators
    (bare or called) and ``register_tool(...)`` calls, anywhere under
    *root*. Only a literal ``name=`` string keyword or (for a decorator with
    no such keyword) the decorated function's own name is accepted — never
    a computed or non-literal value.
    """
    return set(_discover_python_tool_refs(root))


# ---------------------------------------------------------------------------
# Policy evidence discovery
# ---------------------------------------------------------------------------


def discover_policy_files(root: Path) -> Tuple[Path, ...]:
    """Return sorted, root-relative policy evidence paths.

    Only ``governance/**/*.json``, ``governance/**/*.yaml``,
    ``governance/**/*.yml``, ``policies/**/*.json``, ``policies/**/*.yaml``,
    and ``policies/**/*.yml`` are ever considered; nothing outside those two
    top-level directories is treated as policy evidence.
    """
    root_path = Path(root)
    found: Set[Path] = set()
    for pattern in _POLICY_GLOBS:
        for match in root_path.glob(pattern):
            if match.is_file():
                found.add(match.relative_to(root_path))
    return tuple(sorted(found, key=lambda p: p.as_posix()))


# ---------------------------------------------------------------------------
# Drift severity
# ---------------------------------------------------------------------------


def _drift_severity(consequences: Iterable[Optional[str]]) -> Status:
    """Severity for a source-mismatch/alias-drift finding.

    Unknown consequence is fail-closed: treated the same as a consequential
    (write/external-egress/irreversible) class, i.e. ``must-fix``. Only a
    group where every action is explicitly and solely ``read`` is
    downgraded to ``should-fix`` (stale, non-consequential documentation).
    """
    for consequence in consequences:
        if consequence is None or consequence != "read":
            return "must-fix"
    return "should-fix"


# ---------------------------------------------------------------------------
# build_action_inventory
# ---------------------------------------------------------------------------


def _empty_action_record(action_id: str, source: str) -> ActionRecord:
    return ActionRecord(
        action_id=action_id,
        display_name=action_id,
        aliases=(),
        owner=None,
        declaration_refs=(),
        implementation_refs=(),
        input_schema_sha256=None,
        output_schema_sha256=None,
        source=source,
        consequence=None,
        secondary_consequences=(),
        reversible=None,
        compensation_ref=None,
        execution_modes=(),
        provider_hosted=False,
        approval_required=None,
        policy_ids=(),
        known_runtime_paths=(),
        inventory_status="not-verified",
    )


def build_action_inventory(root: Path) -> InventoryResult:
    """Build the deterministic, explicit action inventory for *root*.

    Merges SPEC section 8, tool registries, and Python-discovered tools;
    normalizes IDs/modes/policy IDs; classifies consequence with explicit
    precedence; and reports every ambiguity as a finding or an explicit
    ``not-verified`` status rather than inferring a pass. Action order is
    always sorted by normalized ``action_id`` — never dependent on
    filesystem enumeration order.
    """
    root_path = Path(root)

    registry = parse_action_registries(root_path)
    alias_index = _build_alias_index(registry)

    raw_python_refs = _discover_python_tool_refs(root_path)
    python_refs = _canonicalize_ref_map(raw_python_refs, registry, alias_index)

    spec_path = root_path / "specs" / "SPEC.md"
    section_text = _read_section_8_text(spec_path)
    raw_spec_action_ids = _extract_action_ids(section_text)
    spec_action_ids = {
        _canonicalize_action_id(action_id, registry, alias_index)
        for action_id in raw_spec_action_ids
    }
    spec_section_sha256 = _sha256_prefixed(section_text.encode("utf-8"))
    safe_requirements = _parse_safe_requirements(section_text)

    findings: list[Finding] = []
    actions: list[ActionRecord] = []

    # SPEC-declared IDs join the union alongside registry/Python IDs so a
    # SPEC-only action (named in section 8 but declared in neither a
    # registry nor Python) still gets exactly one inventory entry rather
    # than silently disappearing.
    all_ids = set(registry) | set(python_refs) | spec_action_ids
    for action_id in sorted(all_ids):
        in_registry = action_id in registry
        in_python = action_id in python_refs
        in_spec = action_id in spec_action_ids
        sources = [
            name
            for name, present in (
                ("python", in_python),
                ("registry", in_registry),
                ("spec", in_spec),
            )
            if present
        ]
        source = "+".join(sorted(sources))

        base = registry[action_id] if in_registry else _empty_action_record(action_id, source)
        implementation_refs = python_refs.get(action_id, ())

        approval_required = base.approval_required
        if approval_required is None and in_spec:
            approval_required = True

        is_must_fix = False

        if base.consequence is None:
            is_must_fix = True
            findings.append(
                Finding(
                    finding_id="ACT-001",
                    status="must-fix",
                    phase="design",
                    plane="runtime",
                    reason_code="unclassified-consequence",
                    summary=(
                        f"Action '{action_id}' has no explicit consequence "
                        "classification."
                    ),
                    details=(
                        "The action was discovered via "
                        f"{source or 'an unknown source'} but no registry "
                        "declares an explicit consequence class for it. "
                        "Consequence is never inferred from a name or verb."
                    ),
                    affected_actions=(action_id,),
                )
            )

        if not (in_registry and in_python):
            severity = _drift_severity([base.consequence])
            is_must_fix = is_must_fix or severity == "must-fix"
            missing_labels = []
            if not in_registry:
                missing_labels.append("a registry declaration")
            if not in_python:
                missing_labels.append("a Python implementation")
            missing = " and ".join(missing_labels)
            findings.append(
                Finding(
                    finding_id="ACT-002",
                    status=severity,
                    phase="design",
                    plane="runtime",
                    reason_code="source-mismatch",
                    summary=f"Action '{action_id}' is missing {missing}.",
                    details=(
                        f"Action '{action_id}' was found via source set "
                        f"{{{source}}}; a declared action needs both a "
                        "registry declaration and an implementation to be "
                        "trusted as fully governed."
                    ),
                    affected_actions=(action_id,),
                )
            )

        record = replace(
            base,
            implementation_refs=implementation_refs,
            source=source,
            approval_required=approval_required,
            inventory_status="must-fix" if is_must_fix else "pass",
        )
        actions.append(record)

    # Duplicate aliases: the same alias string (compared case-insensitively,
    # so case-only variants are treated as the same alias) claimed by two
    # or more distinct canonical actions is ambiguous and is reported
    # (never silently resolved to either action). Uses the same normalized
    # owner map as alias *resolution* so the two can never disagree.
    alias_owners = _alias_owner_map(registry)
    for alias, owners in sorted(alias_owners.items()):
        if len(owners) <= 1:
            continue
        affected = tuple(sorted(owners))
        severity = _drift_severity(registry[a].consequence for a in affected)
        findings.append(
            Finding(
                finding_id="ACT-002",
                status=severity,
                phase="design",
                plane="runtime",
                reason_code="duplicate-alias",
                summary=(
                    f"Alias '{alias}' is claimed by more than one action: "
                    f"{', '.join(affected)}."
                ),
                details=(
                    "An alias must resolve to exactly one canonical action; "
                    "it is never guessed which action a shared alias means."
                ),
                affected_actions=affected,
            )
        )

    # SAFE declarations (authorization, approval, idempotency-or-transaction,
    # output-mediation, audit): a missing or unchecked declaration is
    # reported as ``not-verified`` — never an inferred pass — via a single,
    # deterministic ACT-001 finding for the whole agent (these are
    # properties of section 8 as a whole, not of any one action), listing
    # every not-verified key in the fixed catalog order regardless of dict
    # iteration order.
    not_verified_keys = tuple(
        key for key in _SAFE_REQUIREMENT_KEYS if safe_requirements.get(key) != "pass"
    )
    if not_verified_keys:
        findings.append(
            Finding(
                finding_id="ACT-001",
                status="not-verified",
                phase="design",
                plane="runtime",
                reason_code="safe-declaration-not-verified",
                summary=(
                    "SAFE declaration(s) not verified in SPEC section 8: "
                    f"{', '.join(not_verified_keys)}."
                ),
                details=(
                    "Each of authorization, approval, idempotency-or-transaction, "
                    "output-mediation, and audit must be explicitly checked in "
                    "SPEC section 8; a missing or unchecked declaration is "
                    "reported as not-verified and is never inferred as a pass."
                ),
                affected_actions=(),
            )
        )

    findings.sort(key=lambda f: (f.finding_id, f.affected_actions, f.reason_code))

    return InventoryResult(
        actions=tuple(actions),
        findings=tuple(findings),
        spec_section_sha256=spec_section_sha256,
        safe_requirements=MappingProxyType(dict(safe_requirements)),
        policy_paths=discover_policy_files(root_path),
    )
