"""Runtime mediation-path graph: construction and provider-path assessment.

Builds the deterministic, evidence-bound graph of every consequential
execution path an action can be reached through — interactive, batch,
background, subagent, and direct-tool dispatch via
:func:`build_mediation_graph`, and provider-hosted tool paths (which are
never pre-interceptable through the same seam) via
:func:`assess_provider_paths`. Neither function ever assigns a passing
status by inference: a path is ``pass`` only when *positive* call evidence
proves the pre-action seam runs before the action executes, ``must-fix``
only when *positive* evidence proves it does not (a proven bypass), and
``not-verified`` whenever the assessor cannot find enough evidence to
decide either way. ``not-applicable`` is reserved for a provider-hosted
tool whose consequence class does not require pre-interception at all
(read-only).

Node vocabulary and ordering are fixed (see :data:`CANONICAL_NODE_ORDER`):
``entry``, ``host/worker``, ``agent/subagent``, ``tool-router``,
``pre-action-seam``, ``approval-check``, ``tool-service``,
``post-action-seam``, ``output-mediator``, ``caller``, ``audit-sink``. A
path is covered only when a pre-tool Agent Hooks/ACS call — or a declared
equivalent server-side control — runs *before* ``tool-service``; a call
observed only after the action has already executed (a "post-model"
observation, e.g. an audit record written after a direct provider call) is
never treated as pre-action control, no matter how it looks superficially.

Trust boundaries are preserved rather than inferred across: a cooperative
host/worker process is never treated as a security boundary in its own
right (batch/background dispatch running on a "trusted" worker still needs
its own pre-action seam), and a downstream tool-service is expected to
independently re-check authorization/idempotency — this module does not
assume a host-level or caller-level check makes a service-level check
redundant, and it never fabricates policy, approver, or threshold context
that was not actually declared in the target repository.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Set, Tuple

import canonical
from contracts import ActionRecord, Finding, PathRecord


# ---------------------------------------------------------------------------
# Canonical node vocabulary and graph topology
# ---------------------------------------------------------------------------

CANONICAL_NODE_ORDER: Tuple[str, ...] = (
    "entry",
    "host/worker",
    "agent/subagent",
    "tool-router",
    "pre-action-seam",
    "approval-check",
    "tool-service",
    "post-action-seam",
    "output-mediator",
    "caller",
    "audit-sink",
)

_GRAPH_NODES: Tuple[Dict[str, str], ...] = tuple(
    {"id": name} for name in CANONICAL_NODE_ORDER
)
_GRAPH_EDGES: Tuple[Dict[str, str], ...] = tuple(
    {"from": left, "to": right}
    for left, right in zip(CANONICAL_NODE_ORDER, CANONICAL_NODE_ORDER[1:])
)

# Execution modes assessed by build_mediation_graph. provider-hosted-tool is
# assess_provider_paths' responsibility exclusively — it is never pre-
# interceptable through the same tool-router seam these modes share.
_NON_PROVIDER_MODES: Tuple[str, ...] = (
    "interactive",
    "batch",
    "background",
    "subagent",
    "direct-tool",
)

_SIDE_EFFECTING_CONSEQUENCES: frozenset = frozenset(
    {"write", "external-egress", "irreversible"}
)

_REQUIRED_EQUIVALENT_CONTROL_REFS: Tuple[str, ...] = (
    "authorization_ref",
    "idempotency_ref",
    "transaction_ref",
)


@dataclass(frozen=True)
class MediationGraph:
    nodes: Tuple[Dict[str, str], ...]
    edges: Tuple[Dict[str, str], ...]
    paths: Tuple[PathRecord, ...]
    findings: Tuple[Finding, ...]


def _path_id(action_id: str, mode: str, nodes: Tuple[str, ...]) -> str:
    """Return ``sha256(canonical_bytes({action_id, mode, nodes}))[:16]``."""
    return canonical.sha256_hex(
        canonical.canonical_bytes(
            {"action_id": action_id, "mode": mode, "nodes": list(nodes)}
        )
    )[:16]


# ---------------------------------------------------------------------------
# Static call-evidence scanning (build_mediation_graph)
# ---------------------------------------------------------------------------

_EXCLUDED_DIR_NAMES: frozenset = frozenset(
    {"venv", "node_modules", "site-packages", "__pycache__", "build", "dist"}
)

_PRE_ACTION_SEAM_CALL = "agent_hooks.pre_tool_call"
_APPROVAL_CHECK_CALL = "agent_hooks.require_approval"
_POST_ACTION_SEAM_CALL = "agent_hooks.post_tool_call"
_TOOL_SERVICE_PREFIX = "tool_service."
_PROVIDER_PREFIX = "provider."
_OUTPUT_MEDIATOR_PREFIX = "output_mediator."
_AUDIT_SINK_PREFIX = "audit_sink."


def _is_excluded(relative: Path) -> bool:
    return any(
        part.startswith(".") or part in _EXCLUDED_DIR_NAMES for part in relative.parts
    )


def _iter_python_files(root: Path) -> Tuple[Path, ...]:
    return tuple(
        candidate
        for candidate in sorted(root.rglob("*.py"))
        if not _is_excluded(candidate.relative_to(root))
    )


def _parse_python(path: Path) -> Optional[ast.Module]:
    try:
        source = path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, MemoryError, RecursionError):
        # One unparsable file is treated exactly like "no evidence found in
        # this file" — never a hard failure that blocks every other mode's
        # discovery.
        return None


def _mode_action_function_name(action_id: str, mode: str) -> str:
    slug_mode = mode.replace("-", "_")
    slug_action = action_id.replace(".", "_").replace("-", "_")
    return f"{slug_mode}_{slug_action}"


def _find_function(
    tree: ast.Module, name: str
) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_qualified_name(call: ast.Call) -> Optional[str]:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    if isinstance(func, ast.Name):
        return func.id
    return None


def _iter_calls_in_order(node: ast.AST):
    """Yield every ``ast.Call`` under *node* in (approximate) source order.

    A pre-order walk: a call is yielded before recursing into its own
    arguments, which is exactly source order for the simple sequential
    statements ("call this, then call that") this scanner is designed to
    read. Good enough to determine relative ordering between the seam call
    and the state-changing call within one dispatch function's body.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Call):
            yield child
        yield from _iter_calls_in_order(child)


@dataclass
class _CallTrace:
    pre_action_seam: Optional[int] = None
    approval_check: Optional[int] = None
    tool_service: Optional[int] = None
    provider: Optional[int] = None
    post_action_seam: Optional[int] = None
    output_mediator: Optional[int] = None
    audit_sink: Optional[int] = None


def _trace_calls(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> _CallTrace:
    trace = _CallTrace()
    position = 0
    for statement in func_node.body:
        for call in _iter_calls_in_order(statement):
            qualified = _call_qualified_name(call)
            if qualified == _PRE_ACTION_SEAM_CALL and trace.pre_action_seam is None:
                trace.pre_action_seam = position
            elif qualified == _APPROVAL_CHECK_CALL and trace.approval_check is None:
                trace.approval_check = position
            elif qualified == _POST_ACTION_SEAM_CALL and trace.post_action_seam is None:
                trace.post_action_seam = position
            elif (
                qualified is not None
                and qualified.startswith(_TOOL_SERVICE_PREFIX)
                and trace.tool_service is None
            ):
                trace.tool_service = position
            elif (
                qualified is not None
                and qualified.startswith(_PROVIDER_PREFIX)
                and trace.provider is None
            ):
                trace.provider = position
            elif (
                qualified is not None
                and qualified.startswith(_OUTPUT_MEDIATOR_PREFIX)
                and trace.output_mediator is None
            ):
                trace.output_mediator = position
            elif (
                qualified is not None
                and qualified.startswith(_AUDIT_SINK_PREFIX)
                and trace.audit_sink is None
            ):
                trace.audit_sink = position
            position += 1
    return trace


def _candidate_files(root: Path, action: ActionRecord) -> Tuple[Path, ...]:
    """Return the Python files to scan for *action*'s static call evidence.

    Prefers the action's own declared ``known_runtime_paths`` (the
    registry's authoritative record of which files actually implement the
    action at runtime) and falls back to ``implementation_refs``; only
    when neither is declared does this fall back to scanning every Python
    file under *root* (excluding vendored/hidden trees). A declared path
    that resolves outside *root* — including via a symlink — is dropped
    rather than trusted.
    """
    declared = tuple(action.known_runtime_paths) or tuple(action.implementation_refs)
    if not declared:
        return _iter_python_files(root)

    root_resolved = root.resolve()
    candidates: List[Path] = []
    for relative in declared:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            continue
        if candidate.is_file():
            candidates.append(candidate)
    return tuple(candidates)


def _build_static_path(
    root: Path, action: ActionRecord, mode: str, entry_refs: Tuple[str, ...]
) -> PathRecord:
    function_name = _mode_action_function_name(action.action_id, mode)
    func_node: Optional[ast.FunctionDef | ast.AsyncFunctionDef] = None
    found_relative: Optional[str] = None
    for candidate in _candidate_files(root, action):
        tree = _parse_python(candidate)
        if tree is None:
            continue
        node = _find_function(tree, function_name)
        if node is not None:
            func_node = node
            found_relative = candidate.relative_to(root.resolve()).as_posix()
            break

    if func_node is None:
        nodes = ("entry",)
        return PathRecord(
            path_id=_path_id(action.action_id, mode, nodes),
            action_id=action.action_id,
            mode=mode,
            nodes=nodes,
            pre_action_seam=None,
            equivalent_control_ref=None,
            covered=False,
            status="not-verified",
            evidence_refs=entry_refs,
        )

    trace = _trace_calls(func_node)
    state_positions = [p for p in (trace.tool_service, trace.provider) if p is not None]
    state_change_pos = min(state_positions) if state_positions else None
    covered = (
        trace.pre_action_seam is not None
        and state_change_pos is not None
        and trace.pre_action_seam < state_change_pos
    )

    nodes: List[str] = ["entry"]
    if mode in ("batch", "background"):
        nodes.append("host/worker")
    if mode == "subagent":
        nodes.append("agent/subagent")
    nodes.append("tool-router")
    if trace.pre_action_seam is not None:
        nodes.append("pre-action-seam")
    if trace.approval_check is not None:
        nodes.append("approval-check")
    nodes.append("tool-service")
    if trace.post_action_seam is not None:
        nodes.append("post-action-seam")
    if trace.output_mediator is not None:
        nodes.append("output-mediator")
    if mode in ("interactive", "subagent"):
        nodes.append("caller")
    if trace.audit_sink is not None:
        nodes.append("audit-sink")
    nodes_tuple = tuple(nodes)

    evidence_refs = tuple(sorted(set(entry_refs) | ({found_relative} if found_relative else set())))

    if state_change_pos is None:
        status = "not-verified"
    elif covered:
        status = "pass"
    else:
        status = "must-fix"

    return PathRecord(
        path_id=_path_id(action.action_id, mode, nodes_tuple),
        action_id=action.action_id,
        mode=mode,
        nodes=nodes_tuple,
        pre_action_seam=_PRE_ACTION_SEAM_CALL if trace.pre_action_seam is not None else None,
        equivalent_control_ref=None,
        covered=covered,
        status=status,
        evidence_refs=evidence_refs,
    )


def build_mediation_graph(
    root: Path, actions: Tuple[ActionRecord, ...], adapter: object
) -> MediationGraph:
    """Build the mediation-path graph for every non-provider-hosted mode.

    Paths are built from ``adapter.discover_entry_points`` (entry evidence
    folded into every path's ``evidence_refs``), ``adapter.discover_mediation``
    (any path an adapter itself already declares is merged in verbatim,
    de-duplicated by ``path_id``), each action's declared
    ``known_runtime_paths``/``implementation_refs`` (used to scope static
    scanning), and static call evidence read from the target's own Python
    source. The adapter only ever supplies observations — this function,
    not the adapter, is the sole place a ``pass``/``must-fix``/
    ``not-verified`` status is assigned.

    Emits one ``MED-001`` per proven bypass path (a mode whose real
    dispatch function was found but does not run the pre-action seam
    before the action executes) and exactly one aggregate ``MED-002``
    whenever any non-provider mode is not explicitly covered — whether
    because a bypass was proven or because no dispatch evidence could be
    found at all (``not-verified``).
    """
    root_path = Path(root).resolve()
    entry_points = adapter.discover_entry_points(root_path)
    entry_refs = tuple(
        sorted(
            {
                str(entry["declaration_ref"])
                for entry in entry_points
                if isinstance(entry, Mapping) and entry.get("declaration_ref")
            }
        )
    )

    paths: List[PathRecord] = []
    uncovered: List[PathRecord] = []
    findings: List[Finding] = []

    for action in sorted(actions, key=lambda a: a.action_id):
        modes = tuple(mode for mode in action.execution_modes if mode in _NON_PROVIDER_MODES)
        for mode in modes:
            record = _build_static_path(root_path, action, mode, entry_refs)
            paths.append(record)
            if not record.covered:
                uncovered.append(record)
            if record.status == "must-fix":
                findings.append(
                    Finding(
                        finding_id="MED-001",
                        status="must-fix",
                        phase="design",
                        plane="runtime",
                        reason_code="bypass",
                        summary=(
                            f"{mode} path for {action.action_id} lacks "
                            "pre-action mediation"
                        ),
                        details=(
                            f"The {mode} dispatch path for '{action.action_id}' "
                            "reaches tool-service (or an equivalent direct "
                            "provider call) without ever calling the Agent "
                            "Hooks pre-action seam first. A control observed "
                            "only after the action already executed is never "
                            "treated as pre-action mediation."
                        ),
                        affected_actions=(action.action_id,),
                        affected_paths=(record.path_id,),
                        evidence_refs=record.evidence_refs,
                    )
                )

    known_ids: Set[str] = {path.path_id for path in paths}
    for declared_path in adapter.discover_mediation(root_path):
        if declared_path.path_id in known_ids:
            continue
        paths.append(declared_path)
        known_ids.add(declared_path.path_id)
        if not declared_path.covered:
            uncovered.append(declared_path)

    if uncovered:
        med002_status = (
            "must-fix"
            if any(path.status == "must-fix" for path in uncovered)
            else "not-verified"
        )
        findings.append(
            Finding(
                finding_id="MED-002",
                status=med002_status,
                phase="design",
                plane="runtime",
                reason_code="coverage-incomplete",
                summary="declared mediation coverage is incomplete",
                details=(
                    "One or more interactive/batch/background/subagent/"
                    "direct-tool paths for a consequential action are "
                    "either a proven bypass or could not be verified as "
                    "covered; every such path must be either explicitly "
                    "mediated or evidenced absent, never assumed covered."
                ),
                affected_actions=tuple(
                    sorted({path.action_id for path in uncovered})
                ),
                affected_paths=tuple(sorted(path.path_id for path in uncovered)),
            )
        )

    findings.sort(key=lambda finding: (finding.finding_id, finding.summary))
    return MediationGraph(
        nodes=_GRAPH_NODES,
        edges=_GRAPH_EDGES,
        paths=tuple(paths),
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Provider-hosted tool paths: declared equivalent-control evidence
# ---------------------------------------------------------------------------


def _load_yaml_document(path: Path):
    import yaml

    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _read_declared_tools(root: Path) -> Tuple[Mapping[str, object], ...]:
    for name in ("agent.yaml", "agent.yml"):
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            document = _load_yaml_document(candidate)
        except Exception:
            # An unparsable registry is never a source of trustworthy
            # equivalent-control evidence; treat it exactly like "no
            # declared tools" rather than raising out of this best-effort
            # evidence reader (inventory.py is the authoritative, strict
            # registry parser and will have already raised/reported on a
            # genuinely malformed file).
            return ()
        if isinstance(document, Mapping) and isinstance(document.get("tools"), list):
            return tuple(
                entry for entry in document["tools"] if isinstance(entry, Mapping)
            )
    return ()


def _equivalent_control_for(
    root: Path, action_id: str
) -> Optional[Mapping[str, object]]:
    for entry in _read_declared_tools(root):
        if str(entry.get("id", "")).strip().lower() != action_id:
            continue
        control = entry.get("equivalent_control")
        return control if isinstance(control, Mapping) else None
    return None


def _is_complete_equivalent_control(control: Optional[Mapping[str, object]]) -> bool:
    """True only if *control* names all three required server-side refs.

    Each of ``authorization_ref``, ``idempotency_ref``, and
    ``transaction_ref`` must be present as a non-empty string — naming only
    one or two of the three is never sufficient equivalent-control proof.
    """
    if control is None:
        return False
    for key in _REQUIRED_EQUIVALENT_CONTROL_REFS:
        value = control.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def assess_provider_paths(
    root: Path, actions: Tuple[ActionRecord, ...]
) -> MediationGraph:
    """Assess every provider-hosted-tool path for pre-interception support.

    A provider-hosted tool has no pre-tool interception point of its own,
    so it is supported only via a *declared* equivalent server-side
    control naming all three of ``authorization_ref``, ``idempotency_ref``,
    and ``transaction_ref`` (read directly from the target's own
    ``agent.yaml``/``agent.yml``, never inferred). A side-effecting
    (write/external-egress/irreversible) provider-hosted tool without that
    complete evidence is ``MED-003``/``must-fix``/``unsupported``. A
    read-only provider-hosted tool has no state to protect, so it is
    ``not-applicable`` instead — never a false ``must-fix``, and never a
    silently-assumed ``pass``.
    """
    root_path = Path(root).resolve()
    paths: List[PathRecord] = []
    findings: List[Finding] = []

    for action in sorted(actions, key=lambda a: a.action_id):
        if not action.provider_hosted:
            continue
        modes = tuple(
            mode for mode in action.execution_modes if mode == "provider-hosted-tool"
        ) or ("provider-hosted-tool",)
        for mode in modes:
            nodes = ("entry", "tool-router", "tool-service")
            path_id = _path_id(action.action_id, mode, nodes)
            control = _equivalent_control_for(root_path, action.action_id)
            control_complete = _is_complete_equivalent_control(control)
            side_effecting = action.consequence in _SIDE_EFFECTING_CONSEQUENCES

            if not side_effecting:
                status = "not-applicable"
                covered = False
                equivalent_control_ref: Optional[str] = None
            elif control_complete:
                status = "pass"
                covered = True
                assert control is not None  # narrowed by control_complete
                equivalent_control_ref = ";".join(
                    f"{key}={control[key]}" for key in _REQUIRED_EQUIVALENT_CONTROL_REFS
                )
            else:
                status = "must-fix"
                covered = False
                equivalent_control_ref = None
                findings.append(
                    Finding(
                        finding_id="MED-003",
                        status="must-fix",
                        phase="design",
                        plane="runtime",
                        reason_code="unsupported",
                        summary=(
                            f"{action.action_id} provider-hosted tool is not "
                            "pre-interceptable and has no equivalent "
                            "server-side control"
                        ),
                        details=(
                            f"'{action.action_id}' is a side-effecting "
                            f"({action.consequence}) provider-hosted tool. "
                            "Provider-hosted tools have no pre-tool "
                            "interception point, so support requires a "
                            "declared equivalent server-side control naming "
                            "authorization_ref, idempotency_ref, and "
                            "transaction_ref; none (or an incomplete set) "
                            "was declared, so this path is unsupported."
                        ),
                        affected_actions=(action.action_id,),
                        affected_paths=(path_id,),
                        evidence_refs=action.declaration_refs,
                    )
                )

            paths.append(
                PathRecord(
                    path_id=path_id,
                    action_id=action.action_id,
                    mode=mode,
                    nodes=nodes,
                    pre_action_seam=None,
                    equivalent_control_ref=equivalent_control_ref,
                    covered=covered,
                    status=status,
                    evidence_refs=action.declaration_refs,
                )
            )

    findings.sort(key=lambda finding: (finding.finding_id, finding.summary))
    return MediationGraph(
        nodes=_GRAPH_NODES,
        edges=_GRAPH_EDGES,
        paths=tuple(paths),
        findings=tuple(findings),
    )
