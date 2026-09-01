"""Runtime mediation-path graph: construction and provider-path assessment.

Builds the deterministic, evidence-bound graph of every required
consequential execution family a non-provider-hosted action can be reached
through — interactive, batch, background, subagent, and direct-tool — via
:func:`build_mediation_graph`, and provider-hosted tool paths (which have
no pre-tool interception point of their own) via
:func:`assess_provider_paths`. Every one of the five non-provider families
is always assessed for every non-exclusively-provider-hosted action,
whether or not the target's own registry happens to declare it: MED-002
requires each family to be *explicitly covered or evidenced absent*, and a
family a target simply never mentions is exactly the "cannot determine"
case that must default to ``not-verified`` rather than being silently
skipped.

Neither function ever assigns a passing status by inference: a path is
``pass`` only when *positive* call evidence proves the pre-action seam
runs before the action executes (or a fully-named equivalent server-side
control is declared for it), ``must-fix`` only when *positive* evidence
proves a state change happens with neither of those in place (a proven
bypass), and ``not-verified`` whenever the assessor cannot find enough
evidence — no dispatch code, no adapter observation, nothing — to decide
either way. ``not-applicable`` is reserved for a provider-hosted tool whose
consequence class does not require pre-interception at all (read-only).

Every candidate path, wherever it came from, is subject to exactly the
same recomputation before it is trusted. A ``RuntimeAdapter``'s own
``discover_mediation`` output is folded in only as raw node evidence: its
``covered``/``status``/``equivalent_control_ref`` fields are never taken
at face value, because "adapters emit observations only and never assign
a passing status" must hold even against an adapter that does not follow
that rule itself. The core recomputes every verdict identically whether
the underlying nodes came from a static AST scan of the target's own
source or from an adapter's declared evidence, so a non-conforming
adapter's false ``pass`` claim can never suppress a MED-001/MED-002
finding a genuine bypass would otherwise produce. Provider-hosted-tool
paths are excluded from this adapter merge entirely — that family is
``assess_provider_paths``' domain exclusively, never
``build_mediation_graph``'s.

Node vocabulary and ordering are fixed (see :data:`CANONICAL_NODE_ORDER`):
``entry``, ``host/worker``, ``agent/subagent``, ``tool-router``,
``pre-action-seam``, ``approval-check``, ``tool-service``,
``post-action-seam``, ``output-mediator``, ``caller``, ``audit-sink``. A
path is covered only when a pre-tool Agent Hooks/ACS call — or a declared
equivalent server-side control naming authorization, idempotency, and
transaction-boundary evidence — is proven to apply before the state change
represented by ``tool-service``; a control observed only after the action
has already executed (a "post-model" observation, e.g. an audit record
written after a direct provider call) is never treated as pre-action
control, no matter how it looks superficially.

Trust boundaries are preserved rather than inferred across: a cooperative
host/worker process is never treated as a security boundary in its own
right (batch/background dispatch running on a "trusted" worker still needs
its own pre-action seam or declared equivalent control), and a downstream
tool-service is expected to independently re-check authorization/
idempotency/transaction constraints — this module does not assume a
host-level or caller-level check makes a service-level check redundant,
and it never fabricates policy, approver, or threshold context that was
not actually declared in the target repository.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

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

# The five execution families build_mediation_graph must assess for every
# non-exclusively-provider-hosted action, per MED-002's own requirement,
# regardless of whether a target's registry declares any of them.
# provider-hosted-tool is assess_provider_paths' family exclusively — it
# is never pre-interceptable through the same tool-router seam these five
# share, so it is never enumerated here.
REQUIRED_NON_PROVIDER_MODES: Tuple[str, ...] = (
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


def _is_exclusively_provider_hosted(action: ActionRecord) -> bool:
    """True if *action* has no non-provider family to assess at all.

    An action that declares ``provider_hosted`` and names no execution
    mode outside ``provider-hosted-tool`` (including one that names none
    at all) is entirely ``assess_provider_paths``' responsibility;
    ``build_mediation_graph`` must never manufacture five spurious
    ``not-verified`` non-provider paths (and a matching MED-002) for it.
    An action that mixes a provider-hosted mode with a genuine non-provider
    one is still assessed for its non-provider families here.
    """
    if not action.provider_hosted:
        return False
    return not any(
        mode in REQUIRED_NON_PROVIDER_MODES for mode in action.execution_modes
    )


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


def _static_evidence(
    root: Path, action: ActionRecord, mode: str
) -> Optional[Tuple[Tuple[str, ...], str]]:
    """Return ``(nodes, found_relative)`` from static AST scanning, or
    ``None`` if no matching dispatch function was found anywhere.
    """
    function_name = _mode_action_function_name(action.action_id, mode)
    for candidate in _candidate_files(root, action):
        tree = _parse_python(candidate)
        if tree is None:
            continue
        func_node = _find_function(tree, function_name)
        if func_node is None:
            continue

        trace = _trace_calls(func_node)
        state_positions = [
            p for p in (trace.tool_service, trace.provider) if p is not None
        ]
        state_change_pos = min(state_positions) if state_positions else None

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
        if state_change_pos is not None:
            nodes.append("tool-service")
        if trace.post_action_seam is not None:
            nodes.append("post-action-seam")
        if trace.output_mediator is not None:
            nodes.append("output-mediator")
        if mode in ("interactive", "subagent"):
            nodes.append("caller")
        if trace.audit_sink is not None:
            nodes.append("audit-sink")

        found_relative = candidate.relative_to(root.resolve()).as_posix()
        return tuple(nodes), found_relative
    return None


# ---------------------------------------------------------------------------
# Coverage recomputation: the one place any path's verdict is decided
# ---------------------------------------------------------------------------


def _recompute_coverage(
    nodes: Tuple[str, ...], equivalent_control_applies: bool
) -> Tuple[bool, str, bool]:
    """Recompute ``(covered, status, covered_via_equivalent_control)``.

    This is the sole place a path's verdict is decided, and it is applied
    identically no matter where *nodes* came from — a static AST scan of
    the target's own source, or a ``RuntimeAdapter``'s declared
    observation. Any externally-supplied ``covered``/``status`` is never
    consulted here at all; only the node evidence itself is.

    - No ``tool-service`` node at all means no evidence a state change was
      ever reached: indeterminate, ``not-verified``.
    - ``tool-service`` reached with ``pre-action-seam`` proven to precede
      it: ``pass``.
    - ``tool-service`` reached without that ordering, but a fully-named
      equivalent server-side control applies: ``pass``, credited to the
      equivalent control rather than to a client-side seam.
    - ``tool-service`` reached with neither: a proven bypass, ``must-fix``.
    """
    if "tool-service" not in nodes:
        return False, "not-verified", False

    service_index = nodes.index("tool-service")
    seam_index = nodes.index("pre-action-seam") if "pre-action-seam" in nodes else None
    if seam_index is not None and seam_index < service_index:
        return True, "pass", False
    if equivalent_control_applies:
        return True, "pass", True
    return False, "must-fix", False


# ---------------------------------------------------------------------------
# Declared equivalent server-side control (shared by both graph builders)
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


def _serialize_control(control: Mapping[str, object]) -> str:
    return ";".join(
        f"{key}={control[key]}" for key in _REQUIRED_EQUIVALENT_CONTROL_REFS
    )


def _equivalent_control_applies(
    root: Path, action: ActionRecord
) -> Tuple[bool, Optional[Mapping[str, object]]]:
    """Whether a fully-named equivalent control is declared for *action*.

    Only relevant for side-effecting actions (read actions have no state
    to protect, so a "control" over them is meaningless); consequence is
    read directly from the registry, never inferred.
    """
    if action.consequence not in _SIDE_EFFECTING_CONSEQUENCES:
        return False, None
    control = _equivalent_control_for(root, action.action_id)
    return _is_complete_equivalent_control(control), control


# ---------------------------------------------------------------------------
# build_mediation_graph
# ---------------------------------------------------------------------------


def _build_path(
    root: Path,
    action: ActionRecord,
    mode: str,
    entry_refs: Tuple[str, ...],
    adapter_declared: Optional[PathRecord],
) -> PathRecord:
    """Build the single, authoritative path for one ``(action, mode)`` pair.

    Static call evidence from the target's own source takes precedence:
    if a matching dispatch function is found, its node evidence is used.
    Only when no such function can be found at all does an adapter's own
    declared observation for the same ``(action_id, mode)`` — if any —
    supply the node evidence instead. Either way, ``covered``/``status``/
    ``equivalent_control_ref`` are always recomputed by this function from
    the node evidence and independently-checked equivalent-control
    evidence; nothing supplied by the adapter is ever trusted verbatim. If
    neither source has anything, the path is ``not-verified`` — evidence
    absent, not falsely assumed passing or failing.
    """
    static = _static_evidence(root, action, mode)
    if static is not None:
        nodes, found_relative = static
        evidence_refs = tuple(
            sorted(set(entry_refs) | {found_relative})
        )
    elif adapter_declared is not None:
        nodes = adapter_declared.nodes
        evidence_refs = tuple(sorted(set(entry_refs) | set(adapter_declared.evidence_refs)))
    else:
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

    control_applies, control = _equivalent_control_applies(root, action)
    covered, status, via_control = _recompute_coverage(nodes, control_applies)

    pre_action_seam: Optional[str] = None
    if "pre-action-seam" in nodes:
        pre_action_seam = _PRE_ACTION_SEAM_CALL
        if static is None and adapter_declared is not None:
            pre_action_seam = adapter_declared.pre_action_seam or _PRE_ACTION_SEAM_CALL

    return PathRecord(
        path_id=_path_id(action.action_id, mode, nodes),
        action_id=action.action_id,
        mode=mode,
        nodes=nodes,
        pre_action_seam=pre_action_seam,
        equivalent_control_ref=(_serialize_control(control) if via_control and control else None),
        covered=covered,
        status=status,
        evidence_refs=evidence_refs,
    )


def build_mediation_graph(
    root: Path, actions: Tuple[ActionRecord, ...], adapter: object
) -> MediationGraph:
    """Build the mediation-path graph for every required non-provider family.

    Every one of the five required families — interactive, batch,
    background, subagent, direct-tool — is assessed for every action that
    is not exclusively provider-hosted, whether or not the target's own
    registry declares that family: MED-002 requires each to be explicitly
    covered or evidenced absent, so a family the registry never mentions
    is exactly the "cannot determine" case, not one to silently skip.

    Paths are built from static call evidence read from the target's own
    Python source first; only when no matching dispatch function can be
    found at all does an adapter-declared observation (from
    ``adapter.discover_mediation``, excluding the ``provider-hosted-tool``
    family, which is ``assess_provider_paths``' domain exclusively) supply
    node evidence instead. ``adapter.discover_entry_points`` evidence is
    folded into every path's ``evidence_refs``. Wherever the node evidence
    came from, ``covered``/``status``/``equivalent_control_ref`` are
    always recomputed by this function alone — an adapter's own claims for
    those fields are never trusted, so a non-conforming adapter's false
    ``pass`` cannot suppress a genuine bypass's MED-001/MED-002 findings.

    Emits one ``MED-001`` per proven bypass path (a family whose real
    dispatch function was found, or whose adapter-declared evidence
    proves, a state change with no pre-action seam and no declared
    equivalent server-side control ahead of it) and exactly one aggregate
    ``MED-002`` whenever any family is not explicitly covered — whether
    because a bypass was proven or because no evidence at all could be
    found (``not-verified``). ``MED-002`` always carries the evidence
    references for every uncovered family it aggregates.
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

    adapter_declared_by_key: Dict[Tuple[str, str], PathRecord] = {}
    for declared_path in adapter.discover_mediation(root_path):
        if declared_path.mode == "provider-hosted-tool":
            # Provider-hosted paths are assess_provider_paths' family
            # exclusively; build_mediation_graph never touches them, even
            # if an adapter declares one.
            continue
        key = (declared_path.action_id, declared_path.mode)
        adapter_declared_by_key.setdefault(key, declared_path)

    paths: List[PathRecord] = []
    uncovered: List[PathRecord] = []
    findings: List[Finding] = []

    for action in sorted(actions, key=lambda a: a.action_id):
        if _is_exclusively_provider_hosted(action):
            continue
        for mode in REQUIRED_NON_PROVIDER_MODES:
            record = _build_path(
                root_path,
                action,
                mode,
                entry_refs,
                adapter_declared_by_key.get((action.action_id, mode)),
            )
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
                            "Hooks pre-action seam first, and no fully-named "
                            "equivalent server-side control is declared for "
                            "it either. A control observed only after the "
                            "action already executed is never treated as "
                            "pre-action mediation."
                        ),
                        affected_actions=(action.action_id,),
                        affected_paths=(record.path_id,),
                        evidence_refs=record.evidence_refs,
                    )
                )

    if uncovered:
        med002_status = (
            "must-fix"
            if any(path.status == "must-fix" for path in uncovered)
            else "not-verified"
        )
        med002_evidence = tuple(
            sorted(set().union(*(path.evidence_refs for path in uncovered)))
        ) or entry_refs
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
                    "covered; every required family must be either "
                    "explicitly mediated or evidenced absent, never "
                    "assumed covered."
                ),
                affected_actions=tuple(
                    sorted({path.action_id for path in uncovered})
                ),
                affected_paths=tuple(sorted(path.path_id for path in uncovered)),
                evidence_refs=med002_evidence,
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
# assess_provider_paths
# ---------------------------------------------------------------------------


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
    silently-assumed ``pass``. This family is assessed here exclusively;
    ``build_mediation_graph`` never builds or recomputes a
    ``provider-hosted-tool`` path.
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
                equivalent_control_ref = _serialize_control(control)
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
