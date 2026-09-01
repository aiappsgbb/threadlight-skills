"""MAF-first runtime adapter and upstream pin comparison.

The Microsoft Agent Framework (MAF) adapter is the first implementation of
the framework-agnostic ``RuntimeAdapter`` protocol: it emits normalized,
read-only observations (entry points, action candidates, mediation-seam
evidence, probe cases, and probe results) but never assigns a passing
status itself — the core assessor (a later task) owns every status
decision. ``MAFAdapter.detect`` in particular only claims MAF integration on
*real* evidence: an actual ``import``/attribute-call under the
``agent_framework`` namespace found by static AST inspection (never an
import/execution of target code). A dependency file merely *naming*
``agent-framework-core`` is disclosed as evidence but is never, by itself,
sufficient to claim conformance — and a target with no MAF evidence at all
gets no adapter claim rather than a guessed one.

This module also owns the upstream pin: Agent Hooks (``AGENT-HOOKS-0.1``) is
Draft/alpha and cooperative — never a security boundary — and MAF
integration is explicitly experimental (see
``references/upstream-pin.md``). ``load_upstream_pin`` reads the complete
tested tuple recorded in ``references/upstream-pin.json``, and
``compare_upstream_tuple`` accepts only an *exact* match to that tuple; any
difference at all — a newer package version, a different resolved commit, a
missing observed key — is ``PIN-001``/``must-fix`` and requires rerunning
CTK and every application-path probe. No compatibility is ever inferred
from semantic-version ordering.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Dict, Mapping, Optional, Protocol, Tuple, runtime_checkable

import canonical
from contracts import ActionRecord, DetectionEvidence, Finding, PathRecord, ProbeResult, Status

if TYPE_CHECKING:
    # Import-time-only: mediation.py does not import this module, so this
    # carries no runtime circular-import risk, but keeping it behind
    # TYPE_CHECKING (rather than a real top-level import) means a future
    # change that *does* have mediation.py depend on maf_adapter can never
    # create a cycle through this annotation. ``from __future__ import
    # annotations`` already makes every annotation in this module lazy at
    # runtime; this import exists purely so static type checkers can
    # resolve ``MediationGraph`` instead of treating it as an undefined
    # forward reference.
    from mediation import MediationGraph


class UpstreamPinError(ValueError):
    """Raised when the upstream pin file cannot be loaded or trusted.

    Examples: the file cannot be read, is not valid JSON, is not a JSON
    object, or is missing one of the top-level keys the complete tested
    tuple requires. A pin that cannot be fully read must never be silently
    treated as "no pin" (which would read as nothing to compare against);
    it is a hard failure.
    """


@dataclass(frozen=True)
class PinComparison:
    status: Status
    finding: Optional[Finding]
    observed: Mapping[str, str]
    expected: Mapping[str, str]


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Framework-specific discovery, isolated behind one normalized shape.

    Every method is read-only and observational: an adapter emits evidence
    and normalized observations, never a ``pass`` status — the core
    assessor owns every status decision.
    """

    adapter_id: str

    def detect(self, target: Path) -> DetectionEvidence: ...

    def resolved_tuple(self, target: Path) -> Mapping[str, str]: ...

    def discover_entry_points(
        self, target: Path
    ) -> Tuple[Dict[str, object], ...]: ...

    def discover_actions(self, target: Path) -> Tuple[ActionRecord, ...]: ...

    def discover_mediation(self, target: Path) -> Tuple[PathRecord, ...]: ...

    def build_probe_cases(
        self,
        target: Path,
        inventory: Tuple[ActionRecord, ...],
        graph: MediationGraph,
    ) -> Tuple[Dict[str, object], ...]: ...

    def run_local_probe(self, case: Mapping[str, object]) -> ProbeResult: ...


# ---------------------------------------------------------------------------
# Shared AST-discovery helpers
# ---------------------------------------------------------------------------

_EXCLUDED_DIR_NAMES: frozenset = frozenset(
    {"venv", "node_modules", "site-packages", "__pycache__", "build", "dist"}
)


def _is_vendored_or_hidden(relative: Path) -> bool:
    return any(
        part.startswith(".") or part in _EXCLUDED_DIR_NAMES
        for part in relative.parts
    )


def _iter_python_files(root: Path):
    for candidate in sorted(root.rglob("*.py")):
        relative = candidate.relative_to(root)
        if _is_vendored_or_hidden(relative):
            continue
        yield candidate, relative


def _parse_python_source(path: Path):
    try:
        source = path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, MemoryError, RecursionError):
        # Not a source of trustworthy AST evidence; the caller treats this
        # exactly like "no evidence found in this file", never a hard
        # failure — one unparsable file must not block discovery in every
        # other file.
        return None


# ---------------------------------------------------------------------------
# detect(): real agent_framework import/call vs. dependency-only vs. unknown
# ---------------------------------------------------------------------------

_AGENT_FRAMEWORK_MODULE = "agent_framework"
_DEPENDENCY_EVIDENCE_NAMES: Tuple[str, ...] = ("pyproject.toml", "requirements.txt")


def _module_matches_agent_framework(module: Optional[str]) -> bool:
    return module is not None and (
        module == _AGENT_FRAMEWORK_MODULE
        or module.startswith(_AGENT_FRAMEWORK_MODULE + ".")
    )


def _scan_agent_framework_python_evidence(root: Path) -> Tuple[str, ...]:
    """Return relative paths whose AST proves real ``agent_framework`` usage.

    Only an ``import agent_framework``/``import agent_framework.x``,
    ``from agent_framework[.x] import ...``, or an attribute access rooted
    at a bare ``agent_framework`` name (e.g. ``agent_framework.ChatAgent``)
    counts as evidence — never a dependency-manifest mention alone (see
    :func:`_scan_agent_framework_dependency_evidence`).
    """
    references = []
    for path, relative in _iter_python_files(root):
        tree = _parse_python_source(path)
        if tree is None:
            continue
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    _module_matches_agent_framework(alias.name)
                    for alias in node.names
                ):
                    found = True
                    break
            elif isinstance(node, ast.ImportFrom):
                if _module_matches_agent_framework(node.module):
                    found = True
                    break
            elif isinstance(node, ast.Attribute):
                value = node.value
                if isinstance(value, ast.Name) and value.id == _AGENT_FRAMEWORK_MODULE:
                    found = True
                    break
        if found:
            references.append(relative.as_posix())
    return tuple(references)


def _scan_agent_framework_dependency_evidence(root: Path) -> Tuple[str, ...]:
    """Return dependency-manifest paths that merely *name* the package.

    Checked textually (not parsed as TOML/requirements syntax) since only
    presence-as-a-dependency-listing matters here, never the resolved
    version — that is what :meth:`MAFAdapter.resolved_tuple` and the
    upstream pin comparison are for.
    """
    references = []
    candidates = list(_DEPENDENCY_EVIDENCE_NAMES)
    for match in sorted(root.glob("requirements*.txt")):
        name = match.relative_to(root).as_posix()
        if name not in candidates:
            candidates.append(name)
    for name in candidates:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "agent-framework-core" in text or "agent_framework_core" in text:
            references.append(name)
    return tuple(references)


# ---------------------------------------------------------------------------
# Provider-hosted tool disclosure
# ---------------------------------------------------------------------------

_HOSTED_TOOL_CLASS_PREFIX = "Hosted"


def _call_class_name(call: ast.Call) -> Optional[str]:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
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


def _normalize_action_id(raw: str) -> str:
    return str(raw).strip().lower()


def _discover_provider_hosted_actions(root: Path) -> Tuple[ActionRecord, ...]:
    """Return one :class:`ActionRecord` per ``Hosted*Tool(...)`` instantiation.

    Recognizes a top-level assignment whose right-hand side calls a class
    whose name starts with ``Hosted`` (MAF's own naming convention for its
    provider-hosted built-in tools, e.g. ``HostedWebSearchTool``,
    ``HostedCodeInterpreterTool``). A literal ``name=`` keyword becomes the
    action ID; otherwise the assigned variable name becomes the action ID
    under a ``maf.`` prefix (action IDs always require at least one dot).
    ``provider_hosted=True`` is always set explicitly here — MAF's built-in
    hosted tools are *disclosed*, never left ambiguous — while
    ``consequence`` is left unclassified (``None``) since the adapter
    cannot know the tool's true consequence class without SPEC/registry
    context; only the core assessor may decide that.
    """
    found: Dict[str, ActionRecord] = {}
    for path, relative in _iter_python_files(root):
        tree = _parse_python_source(path)
        if tree is None:
            continue
        rel_posix = relative.as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            class_name = _call_class_name(call)
            if class_name is None or not class_name.startswith(
                _HOSTED_TOOL_CLASS_PREFIX
            ):
                continue
            literal = _literal_name_kwarg(call)
            if literal is not None:
                action_id = _normalize_action_id(literal)
            else:
                target_names = [
                    target.id
                    for target in node.targets
                    if isinstance(target, ast.Name)
                ]
                if not target_names:
                    continue
                action_id = _normalize_action_id(f"maf.{target_names[0]}")
            if action_id in found:
                continue
            found[action_id] = ActionRecord(
                action_id=action_id,
                display_name=action_id,
                aliases=(),
                owner=None,
                declaration_refs=(rel_posix,),
                implementation_refs=(rel_posix,),
                input_schema_sha256=None,
                output_schema_sha256=None,
                source="maf-adapter",
                consequence=None,
                secondary_consequences=(),
                reversible=None,
                compensation_ref=None,
                execution_modes=("provider-hosted-tool",),
                provider_hosted=True,
                approval_required=None,
                policy_ids=(),
                known_runtime_paths=(rel_posix,),
                inventory_status="not-verified",
            )
    return tuple(sorted(found.values(), key=lambda record: record.action_id))


# ---------------------------------------------------------------------------
# Upstream pin: load + exact-tuple comparison
# ---------------------------------------------------------------------------

# The exact flat tuple keys `compare_upstream_tuple` observes and compares.
_OBSERVED_TUPLE_KEYS: Tuple[str, ...] = (
    "agent-hooks-spec",
    "agent-hooks-sdk",
    "agent-framework-core",
    "ctk-vectors",
    "conformance-python",
    "acs-policy-schema",
)

_PIN_DRIFT_DETAILS = (
    "The observed Agent Hooks/agent-framework/CTK/ACS tuple differs from "
    "the complete tested tuple pinned in upstream-pin.json; rerun CTK and "
    "all application-path probes before treating this pin as valid again."
)


def load_upstream_pin(path: Path) -> Mapping[str, object]:
    """Load and deeply validate the complete tested tuple pin file.

    Raises :class:`UpstreamPinError` if the file cannot be read, is not
    valid JSON, is not a JSON object, or is missing/mistyped any required
    top-level or nested field the pin format requires (every leaf under
    ``agent_hooks``/``sdk``/``ctk``/``maf``/``acs``/``conformance_report``
    is checked, not just top-level key presence). Every error message
    includes the dotted path to the offending field (e.g.
    ``"agent_hooks.spec_version"``) — a malformed pin never surfaces as a
    raw ``KeyError``/``AttributeError`` from somewhere downstream. Returns
    an immutable mapping view of the parsed pin — never silently repaired
    or partially trusted.
    """
    pin_path = Path(path)
    try:
        text = pin_path.read_text(encoding="utf-8")
    except OSError as error:
        raise UpstreamPinError(
            f"cannot read upstream pin file {pin_path}: {error}"
        ) from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise UpstreamPinError(
            f"invalid JSON in upstream pin file {pin_path}: {error}"
        ) from error
    if not isinstance(data, Mapping):
        raise UpstreamPinError(
            f"upstream pin file {pin_path} must contain a JSON object"
        )
    _validate_pin_shape(data, pin_path)
    return MappingProxyType(dict(data))


def _validate_pin_shape(data: Mapping[str, object], pin_path: Path) -> None:
    """Deeply validate every required nested mapping/leaf/type the pin
    format requires, raising :class:`UpstreamPinError` with dotted-path
    context for the first problem found.

    This exists so a malformed or incomplete pin is rejected up front, at
    the trust boundary, rather than surfacing later as a raw
    ``KeyError``/``AttributeError`` out of :func:`_expected_tuple_from_pin`
    or any other code that flattens the pin's nested structure.
    """

    def fail(field_path: str, message: str) -> None:
        raise UpstreamPinError(
            f"upstream pin file {pin_path}: '{field_path}' {message}"
        )

    def require_mapping(
        container: Mapping[str, object], key: str, field_path: str
    ) -> Mapping[str, object]:
        if key not in container:
            fail(field_path, "is required and was not found")
        value = container[key]
        if not isinstance(value, Mapping):
            fail(field_path, f"must be a JSON object, got {type(value).__name__}")
        return value

    def require(
        container: Mapping[str, object],
        key: str,
        field_path: str,
        expected_type,
        allow_none: bool = False,
    ) -> None:
        if key not in container:
            fail(field_path, "is required and was not found")
        value = container[key]
        if allow_none and value is None:
            return
        # bool is an int subtype in Python; vector counters must be real ints.
        if expected_type is int and isinstance(value, bool):
            fail(field_path, f"must be int, got {type(value).__name__}")
        if not isinstance(value, expected_type):
            fail(field_path, f"must be {expected_type.__name__}, got {type(value).__name__}")

    require(data, "pin_schema_version", "pin_schema_version", str)
    require(data, "status", "status", str)
    require(data, "conformance_python", "conformance_python", str)
    require(data, "drift_policy", "drift_policy", str)

    agent_hooks = require_mapping(data, "agent_hooks", "agent_hooks")
    for key in ("spec", "spec_version", "repository", "repository_commit", "spec_blob_sha"):
        require(agent_hooks, key, f"agent_hooks.{key}", str)

    sdk = require_mapping(data, "sdk", "sdk")
    for key in ("distribution", "version", "source_commit", "artifact", "artifact_sha256"):
        require(sdk, key, f"sdk.{key}", str)

    ctk = require_mapping(data, "ctk", "ctk")
    require(ctk, "vector_source_commit", "ctk.vector_source_commit", str)
    for key in ("total_vectors", "applicable_vectors", "passed_vectors", "skipped_vectors"):
        require(ctk, key, f"ctk.{key}", int)

    maf = require_mapping(data, "maf", "maf")
    for key in ("distribution", "version", "source_commit", "integration_status"):
        require(maf, key, f"maf.{key}", str)
    integration_packages = require_mapping(
        maf, "integration_packages", "maf.integration_packages"
    )
    if not integration_packages:
        fail("maf.integration_packages", "must be a nonempty mapping")
    for package_name, package_version in integration_packages.items():
        if not isinstance(package_name, str) or not isinstance(package_version, str):
            fail(
                "maf.integration_packages",
                "must map each package name (str) to a version (str)",
            )

    acs = require_mapping(data, "acs", "acs")
    require(acs, "policy_schema", "acs.policy_schema", str, allow_none=True)
    require(acs, "status", "acs.status", str)

    conformance_report = require_mapping(
        data, "conformance_report", "conformance_report"
    )
    for key in ("repository_commit", "path", "blob_sha", "claim"):
        require(conformance_report, key, f"conformance_report.{key}", str)
    require(conformance_report, "certification", "conformance_report.certification", bool)


def _expected_tuple_from_pin(pin: Mapping[str, object]) -> Dict[str, str]:
    """Flatten the nested pin into the same six-key shape as ``observed``."""
    agent_hooks = pin["agent_hooks"]
    sdk = pin["sdk"]
    ctk = pin["ctk"]
    maf = pin["maf"]
    acs = pin["acs"]

    policy_schema = acs.get("policy_schema")
    acs_value = "not-applicable" if policy_schema is None else str(policy_schema)

    return {
        "agent-hooks-spec": (
            f"{agent_hooks['spec_version']}@{agent_hooks['repository_commit']}"
        ),
        "agent-hooks-sdk": f"{sdk['version']}@sha256:{sdk['artifact_sha256']}",
        "agent-framework-core": f"{maf['version']}@{maf['source_commit']}",
        "ctk-vectors": str(ctk["vector_source_commit"]),
        "conformance-python": str(pin["conformance_python"]),
        "acs-policy-schema": acs_value,
    }


def compare_upstream_tuple(
    observed: Mapping[str, str], pin: Mapping[str, object]
) -> PinComparison:
    """Compare *observed* against the pinned complete tested tuple.

    Only an exact match — every one of the six tuple keys present with the
    identical value, no extra or missing keys — is ``pass``. Any
    difference at all (a newer version, a different resolved commit, a
    missing or extra key) is ``PIN-001``/``must-fix``: no compatibility is
    ever inferred from semantic-version ordering, and drift always requires
    rerunning CTK and every application-path probe before the pin can be
    trusted again.
    """
    expected = _expected_tuple_from_pin(pin)
    observed_dict = dict(observed)
    if observed_dict == expected:
        return PinComparison(
            status="pass", finding=None, observed=observed_dict, expected=expected
        )

    finding = Finding(
        finding_id="PIN-001",
        status="must-fix",
        phase="pre-deploy",
        plane="both",
        reason_code="pin-drift",
        summary=(
            "Observed upstream dependency/specification tuple no longer "
            "matches the pinned complete tested tuple."
        ),
        details=_PIN_DRIFT_DETAILS,
    )
    return PinComparison(
        status="must-fix", finding=finding, observed=observed_dict, expected=expected
    )


# ---------------------------------------------------------------------------
# MAFAdapter
# ---------------------------------------------------------------------------


class MAFAdapter:
    """The first ``RuntimeAdapter`` implementation: Microsoft Agent Framework.

    Every method here only emits evidence and normalized observations; it
    never assigns a passing status. In particular, ``run_local_probe``
    always reports ``not-verified`` in this milestone — deterministic
    application-path probe *execution* (deny/transform/fail-closed
    behavior) is a later task's responsibility, not this adapter's.
    """

    adapter_id = "maf/v1"

    def detect(self, target: Path) -> DetectionEvidence:
        root = Path(target).resolve()
        import_refs = _scan_agent_framework_python_evidence(root)
        if import_refs:
            return DetectionEvidence(
                detected=True, references=import_refs, ambiguity=None
            )
        dependency_refs = _scan_agent_framework_dependency_evidence(root)
        if dependency_refs:
            return DetectionEvidence(
                detected=False,
                references=dependency_refs,
                ambiguity=(
                    "agent-framework-core is declared as a dependency but no "
                    "import or call under the agent_framework namespace was "
                    "found in application source; a dependency listing "
                    "alone is not sufficient evidence of MAF integration"
                ),
            )
        return DetectionEvidence(detected=False, references=(), ambiguity=None)

    def resolved_tuple(self, target: Path) -> Mapping[str, str]:
        """Return the observed dependency/specification tuple for *target*.

        Prefers an explicit ``governance/installed-packages.json`` evidence
        file — the assessor's own recorded observation of what is actually
        installed/pinned for this target. That file is read only through
        repository containment (a symlink cannot be used to smuggle in an
        observed tuple from outside *target*), must parse as a JSON object,
        and is strictly projected to exactly the six known observed-tuple
        keys (:data:`_OBSERVED_TUPLE_KEYS`) — any other key present in the
        file (payload, secrets, unrelated metadata) is ignored and never
        flows into the returned evidence. Every required key must be
        present with a string value; a present-but-incomplete or
        wrong-typed file is treated as corrupt evidence and rejected rather
        than silently treated as absent. When the file is absent entirely,
        every key is reported as the literal string ``"not-verified"``
        rather than guessed: a resolved package version alone can never
        stand in for the source commit, spec revision, artifact hash, or
        CTK vector source the tuple actually requires.
        """
        root = Path(target).resolve()
        recorded_relative = Path("governance") / "installed-packages.json"
        recorded_path = root / recorded_relative
        if not recorded_path.is_file():
            return {key: "not-verified" for key in _OBSERVED_TUPLE_KEYS}

        resolved = recorded_path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise UpstreamPinError(
                f"{recorded_relative} escapes the repository root (possibly "
                "via a symlink) and cannot be trusted as installed-packages "
                "evidence"
            ) from error

        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError as error:
            raise UpstreamPinError(
                f"cannot read recorded installed-packages evidence "
                f"{recorded_relative}: {error}"
            ) from error
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise UpstreamPinError(
                f"{recorded_relative} is not valid JSON: {error}"
            ) from error
        if not isinstance(data, Mapping):
            raise UpstreamPinError(
                f"{recorded_relative} must contain a JSON object mapping "
                "each tuple key to its observed value"
            )

        observed: Dict[str, str] = {}
        for key in _OBSERVED_TUPLE_KEYS:
            if key not in data:
                raise UpstreamPinError(
                    f"{recorded_relative} is missing required tuple key "
                    f"'{key}'"
                )
            value = data[key]
            if not isinstance(value, str):
                raise UpstreamPinError(
                    f"{recorded_relative} tuple key '{key}' must be a "
                    f"string, got {type(value).__name__}"
                )
            observed[key] = value
        return observed

    def discover_entry_points(self, target: Path) -> Tuple[Dict[str, object], ...]:
        root = Path(target).resolve()
        entries = []
        for name in ("agent.yaml", "agent.yml"):
            candidate = root / name
            if candidate.is_file():
                entries.append(
                    {
                        "kind": "maf-agent-registry",
                        "declaration_ref": name,
                        "framework": "agent_framework",
                    }
                )
        return tuple(entries)

    def discover_actions(self, target: Path) -> Tuple[ActionRecord, ...]:
        return _discover_provider_hosted_actions(Path(target).resolve())

    def discover_mediation(self, target: Path) -> Tuple[PathRecord, ...]:
        """Emit uncovered ``provider-hosted-tool`` paths for hosted actions.

        Only the provider-hosted-tool path family is emitted here — MAF's
        own AST evidence is the sole basis for it. Cross-referencing this
        against the full mediation graph (all execution-mode families,
        pre-action seam evidence, and coverage status) is a later task's
        mediation-graph builder, which consumes this adapter's output as
        one of its inputs.
        """
        root = Path(target).resolve()
        records = []
        for action in self.discover_actions(root):
            if not action.provider_hosted:
                continue
            nodes = ("entry", "tool-router", "tool-service")
            path_id = canonical.sha256_hex(
                canonical.canonical_bytes(
                    {
                        "action_id": action.action_id,
                        "mode": "provider-hosted-tool",
                        "nodes": list(nodes),
                    }
                )
            )[:16]
            records.append(
                PathRecord(
                    path_id=path_id,
                    action_id=action.action_id,
                    mode="provider-hosted-tool",
                    nodes=nodes,
                    pre_action_seam=None,
                    equivalent_control_ref=None,
                    covered=False,
                    status="not-verified",
                    evidence_refs=action.declaration_refs,
                )
            )
        return tuple(records)

    def build_probe_cases(
        self,
        target: Path,
        inventory: Tuple[ActionRecord, ...],
        graph: MediationGraph,
    ) -> Tuple[Dict[str, object], ...]:
        """Derive one deterministic deny-probe case per graph path.

        *graph* is duck-typed here (only its ``paths`` attribute is used):
        this milestone does not construct or depend on the real mediation
        graph type, which a later task defines. ``None`` or a graph with no
        paths yields no cases rather than raising, since "nothing to
        probe yet" is a normal state before the graph exists.
        """
        paths = getattr(graph, "paths", None) or ()
        cases = []
        for path in paths:
            path_id = getattr(path, "path_id", None)
            cases.append(
                {
                    "probe_id": f"maf-deny-{path.action_id}-{path.mode}-{path_id}",
                    "action_id": path.action_id,
                    "path_id": path_id,
                    "mode": path.mode,
                    "probe_kind": "deny",
                }
            )
        return tuple(cases)

    def run_local_probe(self, case: Mapping[str, object]) -> ProbeResult:
        """Return a ``not-verified`` placeholder result for *case*.

        Deterministic application-path probe *execution* (driving the
        target's real dispatch path and proving deny/transform/fail-closed
        behavior) is implemented by a later task's probe runner. This
        method exists to satisfy the adapter protocol now, and it never
        reports ``pass`` — an adapter cannot decide that on its own.
        """
        return ProbeResult(
            probe_id=str(case.get("probe_id", "")),
            action_id=case.get("action_id"),  # type: ignore[arg-type]
            path_id=case.get("path_id"),  # type: ignore[arg-type]
            status="not-verified",
            reason_code="not-implemented",
            expected=str(case.get("probe_kind", "")),
            observed=(
                "maf_adapter.run_local_probe does not execute probes in "
                "this milestone"
            ),
            evidence_refs=(),
        )
