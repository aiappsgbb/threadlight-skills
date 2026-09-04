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
runs before the action executes, ``must-fix`` only when *positive* evidence
proves a state change happens without that seam (a proven bypass), and
``not-verified`` whenever the assessor cannot find enough evidence — no
dispatch code, no adapter observation, nothing — to decide either way.
Declared equivalent server-side controls remain ``not-verified`` until an
independent verifier exists. ``not-applicable`` is reserved for a
provider-hosted tool whose consequence class does not require
pre-interception at all (read-only).

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
written after a direct provider call, or even a real call to the Agent
Hooks pre-tool-call function itself made only *after* the state change has
already run) is never treated as pre-action control, no matter how it
looks superficially or where its node happens to sort in
``CANONICAL_NODE_ORDER``: coverage is decided from each call's actual
relative position in the source, never merely from both nodes being
present somewhere in the path.

Static evidence is gathered from every candidate module that defines a
matching dispatch function, not just the first one found: if the same
dispatch name is defined more than once across a target's own source (for
example, a legacy mediated implementation left behind alongside a newer
bypassing one), any evidenced bypass among those definitions always wins
over a mediated duplicate — a real bypass is never masked by an
alphabetically-earlier or otherwise first-scanned file that happens to
look clean. See the recognition-conventions comment ahead of the static
call-evidence section below for the exact dispatch-function-name and
receiver-name conventions this scanner recognizes; any code that does not
match those exact names is invisible to it, and a mode with no matching
evidence anywhere stays ``not-verified`` — indeterminate, never a false
``pass``.

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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Mapping, Optional, Sequence, Tuple

import canonical
from contracts import ActionRecord, Finding, PathRecord

if TYPE_CHECKING:
    # Import-time-only: this module has no runtime dependency on
    # ``maf_adapter`` (which itself only imports this module's
    # ``MediationGraph`` under its own ``TYPE_CHECKING`` guard), so there is
    # no cycle in either direction. ``from __future__ import annotations``
    # already makes every annotation in this module lazy at runtime; this
    # import exists purely so static type checkers can resolve
    # ``RuntimeAdapter`` as the real adapter contract instead of the
    # untyped ``object``.
    from contracts import ProbeResult
    from maf_adapter import RuntimeAdapter


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
#
# Recognition conventions (exhaustive — anything not matching these exact
# names is invisible to this scanner and leaves the corresponding node
# absent, never falsely inferred as present):
#
# - Dispatch function name: ``_mode_action_function_name`` below defines the
#   only name this scanner ever looks for — the mode with hyphens replaced
#   by underscores, an underscore, then the action id with every ``.`` and
#   ``-`` replaced by ``_`` (e.g. mode ``direct-tool`` + action
#   ``payments.refund`` => ``direct_tool_payments_refund``). A target that
#   implements a mode's dispatch under any other name is indistinguishable
#   from "no dispatch code at all" to this scanner: the mode stays
#   ``not-verified``, never assumed covered *or* bypassed.
# - Receiver/attribute names for each node, matched via
#   ``_call_qualified_name`` against a call of the exact shape
#   ``<receiver>.<method>(...)`` (a bare local-variable attribute access; a
#   call reached through any other indirection — a decorator, a stored
#   callable, a dynamically dispatched attribute, an aliased import, a
#   wrapper class instance under a different variable name — is not
#   recognized):
#     * ``agent_hooks.pre_tool_call``   -> pre-action-seam evidence
#     * ``agent_hooks.require_approval`` -> approval-check evidence
#     * ``agent_hooks.post_tool_call``  -> post-action-seam evidence
#     * ``tool_service.*`` (any method) -> tool-service evidence (governed
#       execution)
#     * ``provider.*`` (any method)     -> tool-service-equivalent evidence
#       (a raw, unmediated call directly to the external provider/API
#       client — also counts as "the state change happened" for bypass
#       detection, exactly like a ``tool_service.*`` call would)
#     * ``output_mediator.*`` (any method) -> output-mediator evidence
#     * ``audit_sink.*`` (any method)      -> audit-sink evidence
# - Only the *relative source position* of the first ``pre_tool_call`` and
#   the first state-changing (``tool_service.*``/``provider.*``) call within
#   one dispatch function's body decides whether that seam call counts:
#   calling ``pre_tool_call`` at all is not enough on its own, and a call
#   that happens later in the function than the state change is a post-hoc
#   observation (see the module docstring), never pre-action mediation.
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
    """Return the one dispatch-function name this scanner recognizes.

    ``<mode-with-underscores>_<action-id-with-underscores>`` — e.g. mode
    ``direct-tool`` and action ``payments.refund`` yields
    ``direct_tool_payments_refund``. See the recognition-conventions
    comment above this section for the full, exhaustive list of names and
    call shapes this scanner can see; anything else is invisible to it.
    """
    slug_mode = mode.replace("-", "_")
    slug_action = action_id.replace(".", "_").replace("-", "_")
    return f"{slug_mode}_{slug_action}"


def _index_functions_by_name(
    tree: ast.Module,
) -> Dict[str, Tuple["ast.FunctionDef | ast.AsyncFunctionDef", ...]]:
    """Index *every* function definition in *tree* by name, in source order.

    A file can legally define the same dispatch name more than once (a
    stale copy left behind alongside a newer one, a stub above a real
    implementation, ...); every one of those definitions is kept — not
    just the first found by ``ast.walk`` — so :func:`_static_evidence` can
    reduce them itself rather than silently discarding all but one. Nodes
    sharing a name are ordered by ``(lineno, col_offset)`` so the *last*
    entry is always the definition whose name binding a caller would
    actually resolve at runtime (Python's own "later ``def`` rebinds the
    name" semantics), regardless of ``ast.walk``'s own traversal order.

    Built once per file (see :func:`_build_ast_index`) rather than walked
    afresh for every ``(action, mode)`` lookup that might need it.
    """
    functions: Dict[str, List["ast.FunctionDef | ast.AsyncFunctionDef"]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
    return {
        name: tuple(sorted(nodes, key=lambda n: (n.lineno, n.col_offset)))
        for name, nodes in functions.items()
    }


def _call_qualified_name(call: ast.Call) -> Optional[str]:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    if isinstance(func, ast.Name):
        return func.id
    return None


# Statement/expression types under which a nested call might not actually
# execute at runtime — an ``if``/``elif``/``else`` branch, a ternary
# expression's branch, a ``try``/``except``/``finally`` block, a
# ``with``/``async with`` body (whose context-manager entry can fail), or a
# ``while``/``for``/``async for`` loop body (which can run zero times).
# Every call reached only through one of these is "conditional".
_CONDITIONAL_NODE_TYPES: Tuple[type, ...] = (
    ast.If,
    ast.IfExp,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.While,
    ast.For,
    ast.AsyncFor,
)


def _iter_calls_in_order(node: ast.AST, conditional: bool = False):
    """Yield ``(call, conditional)`` pairs for every ``ast.Call`` under
    *node*, in (approximate) source order.

    A pre-order walk: a call is yielded before recursing into its own
    arguments, which is exactly source order for the simple sequential
    statements ("call this, then call that") this scanner is designed to
    read. Good enough to determine relative ordering between the seam call
    and the state-changing call within one dispatch function's body.

    Two structural exclusions keep this from being tricked into crediting
    a call that a dispatch function's own straight-line execution never
    actually makes:

    - Nested ``def``/``async def``/``lambda`` bodies are never descended
      into. A call made only when such a closure is itself later invoked
      is invisible to this scanner and never credited to the *outer*
      dispatch function's own trace — the closure might never be called
      at all, or called from somewhere this scanner cannot see.
    - ``conditional`` is ``True`` for every call reached only through a
      branch that might not execute (see :data:`_CONDITIONAL_NODE_TYPES`),
      so a genuinely unconditional call and one that only "sometimes" runs
      can be told apart by the caller.
    """
    # Guard against a nested closure passed in directly as *node* itself
    # (not just as a child encountered mid-walk): a top-level statement
    # in a dispatch function's own body can itself be a ``def``/``async
    # def``/``lambda``, and its entire subtree must be invisible here too.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return

    # A node's own children (e.g. an ``If``'s ``test``/``body``/``orelse``
    # statements) are conditional whenever *this* node is itself one of
    # the conditional container types — not whenever a child happens to
    # be one, which would miss every call directly inside the branch
    # (only a *nested* conditional inside that branch would ever be
    # detected). Propagating from ``node`` downward means the whole
    # subtree of a conditional container is correctly marked, however
    # deep the calls inside it are nested.
    here_conditional = conditional or isinstance(node, _CONDITIONAL_NODE_TYPES)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Call):
            yield child, here_conditional
        yield from _iter_calls_in_order(child, here_conditional)


@dataclass
class _CallTrace:
    pre_action_seam: Optional[int] = None
    pre_action_seam_candidate: Optional[int] = None
    approval_check: Optional[int] = None
    tool_service: Optional[int] = None
    provider: Optional[int] = None
    post_action_seam: Optional[int] = None
    output_mediator: Optional[int] = None
    audit_sink: Optional[int] = None


def _trace_calls(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> _CallTrace:
    """Trace one dispatch function's own recognized calls, in source order.

    State-changing evidence (``tool_service``/``provider``/
    ``output_mediator``/``audit_sink``) is recorded the same way whether
    the call is reached unconditionally or only through one branch — a
    bypass that triggers under just one ``if``/``while``/``for`` path is
    still a real bypass, and under-reporting it would be its own false
    pass in the other direction.

    Mediation-seam evidence (``pre_action_seam``, ``approval_check``,
    ``post_action_seam``) is different: crediting a call found only
    inside a conditional branch would let one branch's incidental call
    make the *whole* dispatch function look unconditionally mediated, so
    only a genuinely unconditional occurrence of those three ever sets the
    corresponding trace field. If the first occurrence found is
    conditional, tracing continues past it in case a later, truly
    unconditional occurrence of the same call exists.
    """
    trace = _CallTrace()
    position = 0
    for statement in func_node.body:
        for call, conditional in _iter_calls_in_order(statement):
            qualified = _call_qualified_name(call)
            if qualified == _PRE_ACTION_SEAM_CALL:
                if trace.pre_action_seam_candidate is None:
                    trace.pre_action_seam_candidate = position
                if not conditional and trace.pre_action_seam is None:
                    trace.pre_action_seam = position
            elif (
                qualified == _APPROVAL_CHECK_CALL
                and not conditional
                and trace.approval_check is None
            ):
                trace.approval_check = position
            elif (
                qualified == _POST_ACTION_SEAM_CALL
                and not conditional
                and trace.post_action_seam is None
            ):
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

    Called at most once per action for a whole :func:`build_mediation_graph`
    assessment (see :func:`_collect_candidate_files_by_action`) rather than
    once per ``(action, mode)`` pair, since its answer never varies by
    mode and the root-wide fallback branch performs a real filesystem
    walk.
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


def _collect_candidate_files_by_action(
    root: Path, actions: Tuple[ActionRecord, ...]
) -> Dict[str, Tuple[Path, ...]]:
    """Resolve every action's candidate files exactly once per assessment.

    ``_candidate_files`` falls back to a full ``root.rglob("*.py")`` walk
    for any action that declares no ``known_runtime_paths``/
    ``implementation_refs`` of its own. Recomputing that walk for every
    one of the five required modes of every such action — the same,
    action-independent answer five times over — is pure repeated
    filesystem work for a result that never changes within one
    assessment, so it is computed exactly once per action here and reused
    by every mode's lookup (see :func:`_static_evidence`).
    """
    return {action.action_id: _candidate_files(root, action) for action in actions}


_AstIndex = Dict[Path, Dict[str, Tuple["ast.FunctionDef | ast.AsyncFunctionDef", ...]]]


def _build_ast_index(
    actions: Tuple[ActionRecord, ...],
    candidates_by_action: Mapping[str, Tuple[Path, ...]],
) -> _AstIndex:
    """Parse and index every candidate file's functions exactly once.

    Collects the union of every file already resolved for any of *actions*
    in *candidates_by_action* (whether from declared
    ``known_runtime_paths``/``implementation_refs`` or the root-wide
    fallback scan), then parses and indexes each distinct file exactly
    once. Built fresh for one :func:`build_mediation_graph` call and
    shared by every ``(action, mode)`` lookup that call makes, so a target
    with many actions and five required modes each never causes the same
    file to be re-read and re-walked over and over — parsing (and the
    ``ast.walk`` needed to find every function by name) happens once per
    distinct file for the whole assessment, not once per lookup.
    """
    all_candidates: "Dict[Path, None]" = {}
    for action in actions:
        for candidate in candidates_by_action[action.action_id]:
            all_candidates.setdefault(candidate, None)

    index: _AstIndex = {}
    for candidate in all_candidates:
        tree = _parse_python(candidate)
        if tree is None:
            continue
        index[candidate] = _index_functions_by_name(tree)
    return index


def _node_evidence(
    func_node: "ast.FunctionDef | ast.AsyncFunctionDef", mode: str
) -> Tuple[Tuple[str, ...], str]:
    """Return ``(nodes, static_assessment)`` for one located dispatch
    function definition, from its own traced calls alone.

    A state change is ``bypass-proven`` only when no recognized seam-call
    candidate occurs before it. A candidate hidden by conditional control
    flow is not enough to prove mediation, but its presence also prevents
    static analysis from claiming that the path definitely has no seam;
    that path is ``incomplete`` until execution evidence resolves it.
    """
    trace = _trace_calls(func_node)
    state_positions = [
        p for p in (trace.tool_service, trace.provider) if p is not None
    ]
    state_change_pos = min(state_positions) if state_positions else None
    # A pre-action-seam call only counts when its own source position
    # actually precedes the first state-changing call — a call to
    # ``agent_hooks.pre_tool_call`` made *after* the state change has
    # already run is a post-hoc observation (like a trailing audit
    # record), never pre-action mediation, no matter how it looks in
    # the canonical, structurally-ordered node list below.
    seam_precedes_service = (
        trace.pre_action_seam is not None
        and state_change_pos is not None
        and trace.pre_action_seam < state_change_pos
    )
    seam_candidate_precedes_service = (
        trace.pre_action_seam_candidate is not None
        and state_change_pos is not None
        and trace.pre_action_seam_candidate < state_change_pos
    )

    nodes: List[str] = ["entry"]
    if mode in ("batch", "background"):
        nodes.append("host/worker")
    if mode == "subagent":
        nodes.append("agent/subagent")
    nodes.append("tool-router")
    if trace.pre_action_seam is not None and (
        state_change_pos is None or seam_precedes_service
    ):
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

    if seam_precedes_service:
        static_assessment = "mediated-candidate"
    elif state_change_pos is not None and not seam_candidate_precedes_service:
        static_assessment = "bypass-proven"
    else:
        static_assessment = "incomplete"
    return tuple(nodes), static_assessment


def _best_definition_evidence(
    func_nodes: Tuple["ast.FunctionDef | ast.AsyncFunctionDef", ...], mode: str
) -> Tuple[Tuple[str, ...], str]:
    """Reduce every same-named definition within one file to one verdict.

    Any evidenced bypass among the definitions always wins over a
    mediated one, regardless of definition order: Python's own "the last
    ``def`` rebinds the name" runtime semantics do not make an earlier
    bypass safe, because whichever definition actually executes at any
    given moment (before or after a later redefinition lands), a bypass
    anywhere in this set is real, exploitable behavior. Only when *none*
    of the definitions is a bypass is the *last* one (by source position,
    i.e. the actual name binding a caller would resolve at runtime) used —
    matching real Python semantics rather than assuming an earlier,
    possibly-incomplete definition (e.g. a stub with no dispatch logic
    yet) is the one that matters.
    """
    evaluated = [_node_evidence(node, mode) for node in func_nodes]
    for nodes, static_assessment in evaluated:
        if static_assessment == "bypass-proven":
            return nodes, static_assessment
    return evaluated[-1]


def _static_evidence(
    root: Path,
    action: ActionRecord,
    mode: str,
    ast_index: _AstIndex,
    candidate_files: Tuple[Path, ...],
) -> Optional[Tuple[Tuple[str, ...], str, str]]:
    """Return ``(nodes, found_relative, static_assessment)`` from static AST scanning, or
    ``None`` if no matching dispatch function was found anywhere.

    Every candidate file is scanned — not just the first one where the
    dispatch function name is found — because the *same* dispatch name can
    be defined more than once across a target's modules (e.g. a legacy
    mediated implementation left behind alongside a newer bypassing one),
    and (via :func:`_best_definition_evidence`) more than once *within*
    one module too. Whenever any candidate's evidence proves a bypass (a
    state change with no recognized pre-action seam candidate preceding
    it, and never inferred from a closure that is merely defined but never
    proven called — see :func:`_trace_calls`), that bypass evidence always wins over a
    duplicate's mediated evidence — a real bypass is never masked just
    because a differently-named or earlier-sorted file happens to look
    clean. Only when no candidate shows a bypass does the first file (in
    ``candidate_files``' deterministic order) supply the node evidence,
    matching prior behavior when there is no such ambiguity. ``ast_index``
    and ``candidate_files`` are both built once for the whole
    :func:`build_mediation_graph` assessment and reused here rather than
    re-parsed/re-walked per lookup.
    """
    function_name = _mode_action_function_name(action.action_id, mode)
    matches: List[Tuple[Tuple[str, ...], str, str]] = []

    for candidate in candidate_files:
        functions = ast_index.get(candidate)
        if not functions:
            continue
        func_nodes = functions.get(function_name)
        if not func_nodes:
            continue

        nodes, static_assessment = _best_definition_evidence(func_nodes, mode)
        found_relative = candidate.relative_to(root.resolve()).as_posix()
        matches.append((nodes, found_relative, static_assessment))

    if not matches:
        return None

    for nodes, found_relative, static_assessment in matches:
        if static_assessment == "bypass-proven":
            return nodes, found_relative, static_assessment
    return matches[0]


# ---------------------------------------------------------------------------
# Coverage recomputation: the one place any path's verdict is decided
# ---------------------------------------------------------------------------


def _recompute_coverage(nodes: Tuple[str, ...]) -> Tuple[bool, str]:
    """Recompute ``(covered, status)``.

    This is the sole place a path's verdict is decided, and it is applied
    identically no matter where *nodes* came from — a static AST scan of
    the target's own source, or a ``RuntimeAdapter``'s declared
    observation. Any externally-supplied ``covered``/``status`` is never
    consulted here at all; only the node evidence itself is.

    - No ``tool-service`` node at all means no evidence a state change was
      ever reached: indeterminate, ``not-verified``.
    - ``tool-service`` reached with ``pre-action-seam`` proven to precede
      it: ``pass``.
    - ``tool-service`` reached without that ordering: a proven bypass,
      ``must-fix``. Declared equivalent controls are reported separately as
      unverified and can never cover this path.
    """
    if "tool-service" not in nodes:
        return False, "not-verified"

    service_index = nodes.index("tool-service")
    seam_index = nodes.index("pre-action-seam") if "pre-action-seam" in nodes else None
    if seam_index is not None and seam_index < service_index:
        return True, "pass"
    return False, "must-fix"


def _matching_receipts(
    path: PathRecord, probe_results: Sequence["ProbeResult"]
) -> Tuple["ProbeResult", ...]:
    return tuple(
        probe
        for probe in probe_results
        if (
            probe.action_id == path.action_id
            and probe.path_id == path.path_id
            and probe.mode == path.mode
        )
    )


def _receipt_evidence_kinds(probe: "ProbeResult") -> frozenset[str]:
    items_by_id = {item.evidence_id: item for item in probe.evidence_items}
    if (
        not probe.evidence_refs
        or len(items_by_id) != len(probe.evidence_items)
        or any(reference not in items_by_id for reference in probe.evidence_refs)
    ):
        return frozenset()
    return frozenset(items_by_id[reference].kind for reference in probe.evidence_refs)


def _receipt_status(probe: "ProbeResult") -> str:
    kinds = _receipt_evidence_kinds(probe)
    if not kinds:
        return "not-verified"
    has_decision = "path-pre-action-decision" in kinds
    has_invocation = "path-tool-invocation" in kinds
    if probe.status == "must-fix":
        return "must-fix" if has_invocation else "not-verified"
    if probe.status == "should-fix":
        return "should-fix"
    if probe.status == "pass" and has_decision:
        if probe.observed not in (
            "pre_action_decision_before_invocation",
            "deny_decision_without_invocation",
        ):
            return "not-verified"
        return "pass"
    return "not-verified"


def _receipt_is_executed(probe: "ProbeResult") -> bool:
    kinds = _receipt_evidence_kinds(probe)
    return bool(
        kinds
        & {
            "path-pre-action-decision",
            "path-tool-invocation",
        }
    )


def _mediation_findings(
    paths: Sequence[PathRecord], phase: str = "design"
) -> Tuple[Finding, ...]:
    uncovered = [
        path
        for path in paths
        if path.mode != "provider-hosted-tool" and path.status != "pass"
    ]
    findings: List[Finding] = []
    for path in paths:
        if path.mode == "provider-hosted-tool" or path.status not in ("must-fix", "should-fix"):
            continue
        findings.append(
            Finding(
                finding_id="MED-001",
                status=path.status,
                phase=phase,
                plane="runtime",
                reason_code="bypass",
                summary=f"{path.mode} path for {path.action_id} lacks pre-action mediation",
                details=(
                    f"The {path.mode} dispatch path for '{path.action_id}' "
                    "was executed with evidence that did not prove a "
                    "pre-action mediation decision before tool execution. "
                    "Only a correlated, path-bound execution receipt can "
                    "elevate a mediation path above not-verified; an "
                    "executed bypass remains must-fix."
                ),
                affected_actions=(path.action_id,),
                affected_paths=(path.path_id,),
                evidence_refs=path.evidence_refs,
            )
        )
    if uncovered:
        findings.append(
            Finding(
                finding_id="MED-002",
                status=(
                    "must-fix"
                    if any(path.status in ("must-fix", "should-fix") for path in uncovered)
                    else "not-verified"
                ),
                phase=phase,
                plane="runtime",
                reason_code="coverage-incomplete",
                summary="declared mediation coverage is incomplete",
                details=(
                    "One or more interactive/batch/background/subagent/"
                    "direct-tool paths for a consequential action are "
                    "either still only statically discovered or were "
                    "executed without verified pre-action mediation; every "
                    "required family must be backed by correlated "
                    "execution evidence before it can pass."
                ),
                affected_actions=tuple(sorted({path.action_id for path in uncovered})),
                affected_paths=tuple(sorted(path.path_id for path in uncovered)),
                evidence_refs=tuple(
                    sorted(set().union(*(path.evidence_refs for path in uncovered)))
                ),
            )
        )
    findings.sort(key=lambda finding: (finding.finding_id, finding.summary))
    return tuple(findings)


def apply_execution_receipts(
    paths: Tuple[PathRecord, ...],
    probe_results: Sequence["ProbeResult"],
    *,
    phase: str = "design",
) -> Tuple[Tuple[PathRecord, ...], Tuple[Finding, ...]]:
    updated: List[PathRecord] = []
    for path in paths:
        matches = _matching_receipts(path, probe_results)
        if not matches:
            updated.append(path)
            continue
        duplicate_receipts = len(
            {(probe.probe_id, probe.path_id) for probe in matches}
        ) != len(matches)
        receipt_statuses = {_receipt_status(probe) for probe in matches}
        executed = any(_receipt_is_executed(probe) for probe in matches)
        if (
            duplicate_receipts
            or "must-fix" in receipt_statuses
            or {"pass", "should-fix"} <= receipt_statuses
        ):
            status = "must-fix"
        elif "not-verified" in receipt_statuses:
            status = "not-verified"
        elif "should-fix" in receipt_statuses:
            status = "should-fix"
        elif receipt_statuses == {"pass"} and executed:
            if path.static_assessment == "bypass-proven":
                status = "must-fix"
            elif path.static_assessment == "mediated-candidate":
                status = "pass"
            else:
                status = "not-verified"
        else:
            status = "not-verified"
        updated.append(
            replace(
                path,
                executed=executed,
                status=status,
                evidence_refs=tuple(
                    sorted(set(path.evidence_refs).union(*(probe.evidence_refs for probe in matches)))
                ),
            )
        )
    return tuple(updated), _mediation_findings(updated, phase=phase)


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


def _has_complete_equivalent_control_declaration(
    control: Optional[Mapping[str, object]],
) -> bool:
    """True only if *control* names all three required server-side refs.

    Each of ``authorization_ref``, ``idempotency_ref``, and
    ``transaction_ref`` must be present as a non-empty string. This validates
    declaration shape only; target-controlled strings are not independent
    evidence that the section 5 equivalent-control criteria are satisfied.
    """
    if control is None:
        return False
    for key in _REQUIRED_EQUIVALENT_CONTROL_REFS:
        value = control.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


# ---------------------------------------------------------------------------
# build_mediation_graph
# ---------------------------------------------------------------------------


def _build_path(
    root: Path,
    action: ActionRecord,
    mode: str,
    entry_refs: Tuple[str, ...],
    adapter_declared: Optional[PathRecord],
    ast_index: _AstIndex,
    candidate_files: Tuple[Path, ...],
) -> PathRecord:
    """Build the single, authoritative path for one ``(action, mode)`` pair.

    Static call evidence from the target's own source takes precedence:
    if a matching dispatch function is found, its node evidence is used.
    Only when no such function can be found at all does an adapter's own
    declared observation for the same ``(action_id, mode)`` — if any —
    supply the node evidence instead. Either way, ``covered``/``status``/
    ``equivalent_control_ref`` are always recomputed by this function from
    the node evidence; declared equivalent controls never cover a path.
    Nothing supplied by the adapter is ever trusted verbatim. If neither
    source has anything, the path is ``not-verified`` — evidence absent,
    not falsely assumed passing or failing. ``ast_index`` and
    ``candidate_files`` are the shared, once-per-assessment index and
    file list built by :func:`_build_ast_index` and
    :func:`_collect_candidate_files_by_action` for the whole
    :func:`build_mediation_graph` call.
    """
    static = _static_evidence(root, action, mode, ast_index, candidate_files)
    if static is not None:
        nodes, found_relative, static_assessment = static
        evidence_refs = tuple(
            sorted(set(entry_refs) | {found_relative})
        )
    elif adapter_declared is not None:
        nodes = adapter_declared.nodes
        static_assessment = "incomplete"
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
            discovered=False,
            executed=False,
        )

    covered, _status = _recompute_coverage(nodes)

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
        equivalent_control_ref=None,
        covered=covered,
        status="not-verified",
        evidence_refs=evidence_refs,
        discovered=True,
        executed=False,
        static_assessment=static_assessment,
    )


def build_mediation_graph(
    root: Path, actions: Tuple[ActionRecord, ...], adapter: RuntimeAdapter
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

    Candidate-file discovery (:func:`_collect_candidate_files_by_action`)
    and AST parsing/indexing (:func:`_build_ast_index`) are each performed
    exactly once per action for the whole assessment — not once per
    ``(action, mode)`` pair — and every candidate module with a matching
    dispatch name is scanned rather than stopping at the first match,
    across files and (via :func:`_best_definition_evidence`) within one
    file's own duplicate definitions: any evidenced bypass anywhere always
    wins over a mediated duplicate.

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

    non_provider_actions = tuple(
        action
        for action in sorted(actions, key=lambda a: a.action_id)
        if not _is_exclusively_provider_hosted(action)
    )
    # Candidate-file discovery and AST parsing/indexing are each performed
    # once per action for the whole assessment below, then reused for
    # every (action, mode) lookup — see _collect_candidate_files_by_action
    # and _build_ast_index.
    candidates_by_action = _collect_candidate_files_by_action(
        root_path, non_provider_actions
    )
    ast_index = _build_ast_index(non_provider_actions, candidates_by_action)

    paths: List[PathRecord] = []
    for action in non_provider_actions:
        candidate_files = candidates_by_action[action.action_id]
        for mode in REQUIRED_NON_PROVIDER_MODES:
            record = _build_path(
                root_path,
                action,
                mode,
                entry_refs,
                adapter_declared_by_key.get((action.action_id, mode)),
                ast_index,
                candidate_files,
            )
            paths.append(record)
    return MediationGraph(
        nodes=_GRAPH_NODES,
        edges=_GRAPH_EDGES,
        paths=tuple(paths),
        findings=_mediation_findings(paths),
    )


# ---------------------------------------------------------------------------
# assess_provider_paths
# ---------------------------------------------------------------------------


def assess_provider_paths(
    root: Path, actions: Tuple[ActionRecord, ...]
) -> MediationGraph:
    """Assess every provider-hosted-tool path for pre-interception support.

    A provider-hosted tool has no pre-tool interception point of its own.
    Target-owned reference strings are declarations, not independent proof
    of the seven section 5 equivalent-control criteria, so a complete
    declaration remains ``MED-003``/``not-verified``. A missing or incomplete
    declaration for a side-effecting action is
    ``MED-003``/``must-fix``/``unsupported``. A read-only provider-hosted tool
    has no state to protect, so it is ``not-applicable`` instead. This family
    is assessed here exclusively; ``build_mediation_graph`` never builds or
    recomputes a ``provider-hosted-tool`` path.
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
            control_complete = _has_complete_equivalent_control_declaration(control)
            side_effecting = action.consequence in _SIDE_EFFECTING_CONSEQUENCES

            if not side_effecting:
                status = "not-applicable"
                covered = False
                equivalent_control_ref: Optional[str] = None
            elif control_complete:
                status = "not-verified"
                covered = False
                equivalent_control_ref = None
                findings.append(
                    Finding(
                        finding_id="MED-003",
                        status="not-verified",
                        phase="design",
                        plane="runtime",
                        reason_code="equivalent-control-not-verified",
                        summary=(
                            f"{action.action_id} provider-hosted equivalent "
                            "control is declared but not independently verified"
                        ),
                        details=(
                            f"'{action.action_id}' declares authorization, "
                            "idempotency, and transaction references, but "
                            "target-controlled strings do not prove the "
                            "equivalent-control criteria: pre-side-effect "
                            "execution, complete path coverage, deny/transform "
                            "behavior, fail-closed faults, bound approval, "
                            "payload-free audit, and non-bypassability. This "
                            "path remains not verified until independent "
                            "evidence or a probe validates those properties."
                        ),
                        affected_actions=(action.action_id,),
                        affected_paths=(path_id,),
                        evidence_refs=action.declaration_refs,
                    )
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
                    discovered=True,
                    executed=False,
                )
            )

    findings.sort(key=lambda finding: (finding.finding_id, finding.summary))
    return MediationGraph(
        nodes=_GRAPH_NODES,
        edges=_GRAPH_EDGES,
        paths=tuple(paths),
        findings=tuple(findings),
    )
