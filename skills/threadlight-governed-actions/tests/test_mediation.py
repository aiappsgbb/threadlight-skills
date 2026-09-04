"""Tests for threadlight-governed-actions' runtime mediation graph (Task 4).

Exercises ``mediation.build_mediation_graph`` (interactive/batch/background/
subagent/direct-tool paths, built from the MAF adapter's entry-point/
mediation evidence plus static call-evidence scanning of the target's own
Python source) and ``mediation.assess_provider_paths`` (provider-hosted
tool paths, which are never pre-interceptable and instead require declared
equivalent server-side control evidence).

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_mediation.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import textwrap
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import canonical
import contracts
import inventory
import maf_adapter
import mediation
from contracts import ActionRecord, Finding, PathRecord
from mediation import (
    CANONICAL_NODE_ORDER,
    MediationGraph,
    apply_execution_receipts,
    assess_provider_paths,
    build_mediation_graph,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURES_DIR


def _action_record(
    action_id: str,
    *,
    consequence,
    execution_modes,
    provider_hosted: bool = False,
    known_runtime_paths=(),
    implementation_refs=(),
) -> ActionRecord:
    """Build a minimal, explicit :class:`ActionRecord` for ad hoc scenarios.

    Every field this dataclass requires (no defaults) is supplied
    explicitly; only the handful of fields a given scenario actually varies
    are exposed as keyword arguments here — nothing is inferred.
    """
    return ActionRecord(
        action_id=action_id,
        display_name=action_id,
        aliases=(),
        owner=None,
        declaration_refs=("agent.yaml",),
        implementation_refs=implementation_refs,
        input_schema_sha256=None,
        output_schema_sha256=None,
        source="registry",
        consequence=consequence,
        secondary_consequences=(),
        reversible=None,
        compensation_ref=None,
        execution_modes=execution_modes,
        provider_hosted=provider_hosted,
        approval_required=None,
        known_runtime_paths=known_runtime_paths,
    )


def _probe_result(
    probe_id: str,
    action_id: str,
    path_id: str,
    *,
    status: str,
    observed: str,
    evidence_refs: tuple[str, ...] = ("EVID-receipt",),
) -> contracts.ProbeResult:
    return contracts.ProbeResult(
        probe_id=probe_id,
        action_id=action_id,
        path_id=path_id,
        status=status,
        reason_code="ENF-002" if status == "must-fix" else "probe-ok",
        expected="tool_received_transformed_arguments",
        observed=observed,
        evidence_refs=evidence_refs,
    )


# ---------------------------------------------------------------------------
# MediationGraph shape
# ---------------------------------------------------------------------------


def test_mediation_graph_is_frozen_and_shaped():
    graph = MediationGraph(nodes=(), edges=(), paths=(), findings=())
    assert graph.nodes == ()
    assert graph.edges == ()
    assert graph.paths == ()
    assert graph.findings == ()
    with pytest.raises(FrozenInstanceError):
        graph.nodes = ({"id": "entry"},)  # type: ignore[misc]


def test_canonical_node_order_matches_the_approved_vocabulary():
    assert CANONICAL_NODE_ORDER == (
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


def test_build_mediation_graph_adapter_parameter_is_typed_as_runtime_adapter():
    """``adapter`` must be typed ``RuntimeAdapter``, not the untyped ``object``.

    ``mediation.py`` uses ``from __future__ import annotations``, so this
    reads the raw (unresolved) annotation string via
    ``inspect.signature(..., eval_str=False)`` rather than
    ``typing.get_type_hints``. That is deliberate: ``RuntimeAdapter`` is
    only importable under ``TYPE_CHECKING`` here (to keep this module free
    of any runtime dependency on ``maf_adapter``), so eagerly resolving
    the annotation at runtime would raise ``NameError`` even when the
    contract is correctly declared.
    """
    import inspect

    signature = inspect.signature(build_mediation_graph)
    adapter_param = signature.parameters["adapter"]
    assert adapter_param.annotation == "RuntimeAdapter"


# ---------------------------------------------------------------------------
# build_mediation_graph: unmediated-background fixture
# ---------------------------------------------------------------------------


def test_every_consequential_mode_becomes_a_graph_path(fixture_root: Path):
    root = fixture_root / "unmediated-background"
    actions = inventory.build_action_inventory(root).actions
    adapter = maf_adapter.MAFAdapter()

    graph = build_mediation_graph(root, actions, adapter)

    assert {(path.action_id, path.mode) for path in graph.paths} == {
        ("customer.lookup", "interactive"),
        ("customer.lookup", "batch"),
        ("customer.lookup", "background"),
        ("customer.lookup", "subagent"),
        ("customer.lookup", "direct-tool"),
        ("payments.refund", "interactive"),
        ("payments.refund", "batch"),
        ("payments.refund", "background"),
        ("payments.refund", "subagent"),
        ("payments.refund", "direct-tool"),
    }
    assert {(finding.finding_id, finding.summary) for finding in graph.findings} == {
        ("MED-002", "declared mediation coverage is incomplete"),
    }
    med002 = next(f for f in graph.findings if f.finding_id == "MED-002")
    assert med002.evidence_refs != ()


def test_covered_paths_route_through_pre_action_seam_before_tool_service(
    fixture_root: Path,
):
    root = fixture_root / "unmediated-background"
    actions = inventory.build_action_inventory(root).actions
    graph = build_mediation_graph(root, actions, maf_adapter.MAFAdapter())

    by_mode = {path.mode: path for path in graph.paths}
    for mode in ("interactive", "subagent", "direct-tool"):
        path = by_mode[mode]
        assert path.discovered is True
        assert path.executed is False
        assert path.covered is True
        assert path.status == "not-verified"
        assert path.pre_action_seam is not None
        assert "pre-action-seam" in path.nodes
        assert path.nodes.index("pre-action-seam") < path.nodes.index("tool-service")


def test_bypass_paths_reach_tool_service_without_a_pre_action_seam(
    fixture_root: Path,
):
    root = fixture_root / "unmediated-background"
    actions = inventory.build_action_inventory(root).actions
    graph = build_mediation_graph(root, actions, maf_adapter.MAFAdapter())

    by_mode = {path.mode: path for path in graph.paths}
    for mode in ("batch", "background"):
        path = by_mode[mode]
        assert path.discovered is True
        assert path.executed is False
        assert path.covered is False
        assert path.status == "not-verified"
        assert path.pre_action_seam is None
        assert "pre-action-seam" not in path.nodes
        assert "tool-service" in path.nodes


def test_all_generated_paths_respect_the_canonical_node_order(fixture_root: Path):
    order_index = {name: index for index, name in enumerate(CANONICAL_NODE_ORDER)}
    root = fixture_root / "unmediated-background"
    actions = inventory.build_action_inventory(root).actions
    graph = build_mediation_graph(root, actions, maf_adapter.MAFAdapter())

    for path in graph.paths:
        indices = [order_index[node] for node in path.nodes]
        assert indices == sorted(indices), path


def test_path_id_matches_the_defined_hash_formula(fixture_root: Path):
    root = fixture_root / "unmediated-background"
    actions = inventory.build_action_inventory(root).actions
    graph = build_mediation_graph(root, actions, maf_adapter.MAFAdapter())

    for path in graph.paths:
        expected = hashlib.sha256(
            canonical.canonical_bytes(
                {
                    "action_id": path.action_id,
                    "mode": path.mode,
                    "nodes": list(path.nodes),
                }
            )
        ).hexdigest()[:16]
        assert path.path_id == expected


def test_build_mediation_graph_is_deterministic(fixture_root: Path):
    root = fixture_root / "unmediated-background"
    actions = inventory.build_action_inventory(root).actions
    adapter = maf_adapter.MAFAdapter()

    first = build_mediation_graph(root, actions, adapter)
    second = build_mediation_graph(root, actions, adapter)

    assert first.paths == second.paths
    assert first.findings == second.findings


# ---------------------------------------------------------------------------
# build_mediation_graph: evidence-absent (not-verified) mode
# ---------------------------------------------------------------------------


def test_mode_with_no_discoverable_dispatch_evidence_is_not_verified(
    tmp_path: Path,
):
    """Every required non-provider family is enumerated, even the ones a
    target never declared. ``orders.cancel`` here declares only ``batch``,
    but ``build_mediation_graph`` must still assess interactive, subagent,
    and direct-tool as required families (per MED-002's own requirement)
    and report each as ``not-verified`` in the total absence of any
    dispatch evidence — indeterminate, never silently omitted.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [batch]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    assert {path.mode for path in graph.paths} == {
        "interactive",
        "batch",
        "background",
        "subagent",
        "direct-tool",
    }
    for path in graph.paths:
        assert path.action_id == "orders.cancel"
        assert path.discovered is False
        assert path.executed is False
        assert path.covered is False
        assert path.status == "not-verified"

    assert len(graph.findings) == 1
    finding = graph.findings[0]
    assert finding.finding_id == "MED-002"
    assert finding.status == "not-verified"
    assert finding.evidence_refs != ()
    assert "agent.yaml" in finding.evidence_refs


def test_static_decoy_path_never_passes_without_executed_proof(tmp_path: Path):
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [batch]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _ToolService:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            class _Provider:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            agent_hooks = _AgentHooks()
            tool_service = _ToolService()
            provider = _Provider()


            def batch_orders_cancel(**kwargs):
                # Convention-shaped decoy: static AST markers look perfect,
                # but nothing proves this is the path that actually runs.
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.cancel_order(**kwargs)


            def real_worker_entrypoint(**kwargs):
                return provider.cancel_order(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    batch_path = next(path for path in graph.paths if path.mode == "batch")
    assert batch_path.discovered is True
    assert batch_path.executed is False
    assert batch_path.covered is True
    assert batch_path.status == "not-verified"
    assert any(f.finding_id == "MED-002" for f in graph.findings)


def test_correlated_executed_receipt_turns_a_discovered_path_into_pass():
    path = PathRecord(
        path_id="path-1",
        action_id="payments.refund",
        mode="direct-tool",
        nodes=("entry", "tool-router", "pre-action-seam", "tool-service"),
        pre_action_seam="hook:pre",
        equivalent_control_ref=None,
        covered=True,
        status="not-verified",
        evidence_refs=("app/agent.py",),
        discovered=True,
        executed=False,
    )

    updated_paths, findings = apply_execution_receipts(
        (path,),
        (
            _probe_result(
                "transform",
                "payments.refund",
                "path-1",
                status="pass",
                observed="tool_received_transformed_arguments",
                evidence_refs=("EVID-receipt", "audit-1"),
            ),
        ),
    )

    updated = updated_paths[0]
    assert updated.discovered is True
    assert updated.executed is True
    assert updated.covered is True
    assert updated.status == "pass"
    assert updated.evidence_refs == ("EVID-receipt", "app/agent.py", "audit-1")
    assert findings == ()


def test_executed_bypass_receipt_remains_must_fix():
    path = PathRecord(
        path_id="path-1",
        action_id="payments.refund",
        mode="direct-tool",
        nodes=("entry", "tool-router", "tool-service"),
        pre_action_seam=None,
        equivalent_control_ref=None,
        covered=False,
        status="not-verified",
        evidence_refs=("app/agent.py",),
        discovered=True,
        executed=False,
    )

    updated_paths, findings = apply_execution_receipts(
        (path,),
        (
            _probe_result(
                "deny",
                "payments.refund",
                "path-1",
                status="must-fix",
                observed="tool_invoked_despite_fault",
            ),
        ),
    )

    updated = updated_paths[0]
    assert updated.executed is True
    assert updated.covered is False
    assert updated.status == "must-fix"
    assert len(findings) == 2
    assert {finding.finding_id for finding in findings} == {"MED-001", "MED-002"}


def test_wrong_action_or_path_receipt_is_ignored():
    path = PathRecord(
        path_id="path-1",
        action_id="payments.refund",
        mode="direct-tool",
        nodes=("entry", "tool-router", "pre-action-seam", "tool-service"),
        pre_action_seam="hook:pre",
        equivalent_control_ref=None,
        covered=True,
        status="not-verified",
        evidence_refs=("app/agent.py",),
        discovered=True,
        executed=False,
    )

    updated_paths, findings = apply_execution_receipts(
        (path,),
        (
            _probe_result(
                "transform",
                "payments.refund",
                "other-path",
                status="pass",
                observed="tool_received_transformed_arguments",
            ),
            _probe_result(
                "transform",
                "other.action",
                "path-1",
                status="pass",
                observed="tool_received_transformed_arguments",
            ),
        ),
    )

    updated = updated_paths[0]
    assert updated.executed is False
    assert updated.status == "not-verified"
    assert len(findings) == 1
    assert findings[0].finding_id == "MED-002"


def test_conflicting_receipts_fail_closed():
    path = PathRecord(
        path_id="path-1",
        action_id="payments.refund",
        mode="direct-tool",
        nodes=("entry", "tool-router", "pre-action-seam", "tool-service"),
        pre_action_seam="hook:pre",
        equivalent_control_ref=None,
        covered=True,
        status="not-verified",
        evidence_refs=("app/agent.py",),
        discovered=True,
        executed=False,
    )

    updated_paths, findings = apply_execution_receipts(
        (path,),
        (
            _probe_result(
                "transform",
                "payments.refund",
                "path-1",
                status="pass",
                observed="tool_received_transformed_arguments",
                evidence_refs=("EVID-pass",),
            ),
            _probe_result(
                "deny",
                "payments.refund",
                "path-1",
                status="must-fix",
                observed="tool_invoked_despite_fault",
                evidence_refs=("EVID-fail",),
            ),
        ),
    )

    updated = updated_paths[0]
    assert updated.executed is True
    assert updated.status == "must-fix"
    assert ("EVID-fail" in updated.evidence_refs) and ("EVID-pass" in updated.evidence_refs)
    assert {finding.finding_id for finding in findings} == {"MED-001", "MED-002"}


def test_undeclared_execution_mode_is_still_assessed_from_static_evidence(
    tmp_path: Path,
):
    """A mode absent from the registry's ``execution_modes`` list is still
    assessed if the target's own code implements it. ``orders.cancel``
    here only declares ``interactive`` (structurally mediated), but its
    module also defines a ``batch_orders_cancel`` dispatch function that
    structurally bypasses mediation entirely. Both remain discovery only
    until correlated execution evidence exists, so the assessor must
    still enumerate both modes but leave them ``not-verified``.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [interactive]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _Provider:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            agent_hooks = _AgentHooks()
            provider = _Provider()


            def interactive_orders_cancel(**kwargs):
                agent_hooks.pre_tool_call(action="orders.cancel", **kwargs)
                return provider.cancel_order(**kwargs)


            def batch_orders_cancel(**kwargs):
                # Undeclared in agent.yaml, but very much real: a batch
                # worker bypasses mediation entirely.
                return provider.cancel_order(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    by_mode = {path.mode: path for path in graph.paths}
    assert by_mode["interactive"].covered is True
    assert by_mode["interactive"].status == "not-verified"
    assert by_mode["batch"].covered is False
    assert by_mode["batch"].status == "not-verified"
    assert by_mode["batch"].covered is False

    assert {(finding.finding_id, finding.summary) for finding in graph.findings} == {
        ("MED-002", "declared mediation coverage is incomplete"),
    }


def test_build_mediation_graph_skips_exclusively_provider_hosted_actions(
    fixture_root: Path,
):
    """Provider-hosted assessment stays entirely inside
    ``assess_provider_paths``. An action that only ever declares
    ``provider-hosted-tool`` has no interactive/batch/background/subagent/
    direct-tool family to assess at all, so ``build_mediation_graph`` must
    not manufacture five spurious ``not-verified`` paths (and a matching
    MED-002) for it -- while the same fixture's ordinary, non-provider-
    hosted actions are still assessed exactly as they are everywhere else.
    """
    root = fixture_root / "provider-hosted-side-effect"
    actions = inventory.build_action_inventory(root).actions

    graph = build_mediation_graph(root, actions, maf_adapter.MAFAdapter())

    provider_hosted = {"mail.send", "search.lookup"}
    assert {path.action_id for path in graph.paths} & provider_hosted == set()
    affected_actions = {
        action_id
        for finding in graph.findings
        for action_id in finding.affected_actions
    }
    assert affected_actions & provider_hosted == set()


def test_adapter_declared_status_is_never_trusted_and_is_recomputed(
    tmp_path: Path,
):
    """An adapter's own ``covered``/``status``/``equivalent_control_ref`` on
    a path it declares through ``discover_mediation`` are observations,
    not verdicts: the core recomputes every one of them from the path's
    own ``nodes`` (and, when applicable, independently-checked equivalent
    control evidence) exactly as it would for a path built from static
    call evidence. This proves a non-conforming adapter's false ``pass``
    claim cannot suppress the resulting MED findings, and that a
    genuinely covered adapter-declared path is confirmed by the same
    recomputation rather than merely echoed.
    """
    bogus_bypass = PathRecord(
        path_id="bogus0000000001",
        action_id="reports.export",
        mode="direct-tool",
        # No "pre-action-seam" node: this is a proven bypass by the node
        # evidence alone, no matter what the adapter itself claims below.
        nodes=("entry", "tool-router", "tool-service"),
        pre_action_seam=None,
        equivalent_control_ref="adapter-fabricated-control-ref",
        covered=True,
        status="pass",
        evidence_refs=("app/agent.py",),
    )
    genuinely_covered = PathRecord(
        path_id="genuine00000001",
        action_id="reports.export",
        mode="subagent",
        nodes=(
            "entry",
            "agent/subagent",
            "tool-router",
            "pre-action-seam",
            "tool-service",
            "caller",
        ),
        pre_action_seam="acs-policy",
        equivalent_control_ref=None,
        covered=True,
        status="pass",
        evidence_refs=("governance/acs-policy.yaml",),
    )

    class _FakeAdapter:
        def discover_entry_points(self, target):
            return ()

        def discover_mediation(self, target):
            return (bogus_bypass, genuinely_covered)

    action = _action_record(
        "reports.export", consequence="write", execution_modes=()
    )
    graph = build_mediation_graph(tmp_path, (action,), _FakeAdapter())

    by_mode = {path.mode: path for path in graph.paths}

    recomputed_bogus = by_mode["direct-tool"]
    assert recomputed_bogus.covered is False
    assert recomputed_bogus.executed is False
    assert recomputed_bogus.status == "not-verified"
    assert recomputed_bogus.equivalent_control_ref is None
    assert any(f.finding_id == "MED-002" for f in graph.findings)

    recomputed_genuine = by_mode["subagent"]
    assert recomputed_genuine.covered is True
    assert recomputed_genuine.executed is False
    assert recomputed_genuine.status == "not-verified"


def test_adapter_declared_paths_never_supply_provider_hosted_mode(
    tmp_path: Path,
):
    """Provider-hosted paths stay exclusively ``assess_provider_paths``'
    domain: even if an adapter's ``discover_mediation`` declares a
    ``provider-hosted-tool`` path, ``build_mediation_graph`` must not fold
    it in as one of its own five non-provider families.
    """
    declared_provider_path = PathRecord(
        path_id="providerpathid01",
        action_id="mail.send",
        mode="provider-hosted-tool",
        nodes=("entry", "tool-router", "tool-service"),
        pre_action_seam=None,
        equivalent_control_ref=None,
        covered=False,
        status="not-verified",
        evidence_refs=("agent.yaml",),
    )

    class _FakeAdapter:
        def discover_entry_points(self, target):
            return ()

        def discover_mediation(self, target):
            return (declared_provider_path,)

    action = _action_record(
        "mail.send",
        consequence="external-egress",
        execution_modes=("provider-hosted-tool",),
        provider_hosted=True,
    )
    graph = build_mediation_graph(tmp_path, (action,), _FakeAdapter())

    assert declared_provider_path not in graph.paths
    assert graph.paths == ()


# ---------------------------------------------------------------------------
# build_mediation_graph: declared equivalent server-side control
# ---------------------------------------------------------------------------


def test_unverified_equivalent_control_never_masks_a_non_provider_bypass(
    tmp_path: Path,
):
    """Three target-controlled strings cannot suppress a proven bypass."""
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: payments.settle
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
                equivalent_control:
                  authorization_ref: policies/acs-authz.yaml#payments.settle
                  idempotency_ref: policies/acs-idempotency.yaml#payments.settle
                  transaction_ref: policies/acs-transaction.yaml#payments.settle
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _Provider:
                def settle(self, **kwargs):
                    return {"settled": True}


            provider = _Provider()


            def direct_tool_payments_settle(**kwargs):
                # No agent_hooks.pre_tool_call at all: coverage here can
                # only come from the declared equivalent server control.
                return provider.settle(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    direct_tool_path = next(
        path for path in graph.paths if path.mode == "direct-tool"
    )
    assert direct_tool_path.discovered is True
    assert direct_tool_path.executed is False
    assert direct_tool_path.status == "not-verified"
    assert direct_tool_path.covered is False
    assert direct_tool_path.equivalent_control_ref is None
    assert any(finding.finding_id == "MED-002" for finding in graph.findings)


def test_incomplete_equivalent_control_does_not_cover_a_bypassed_path(
    tmp_path: Path,
):
    """Equivalent control evidence missing even one of the three required
    refs must never count: an incomplete declaration leaves a proven
    bypass exactly as ``must-fix`` as it would be with no declaration at
    all.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: payments.settle
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
                equivalent_control:
                  authorization_ref: policies/acs-authz.yaml#payments.settle
                  idempotency_ref: policies/acs-idempotency.yaml#payments.settle
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _Provider:
                def settle(self, **kwargs):
                    return {"settled": True}


            provider = _Provider()


            def direct_tool_payments_settle(**kwargs):
                return provider.settle(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    direct_tool_path = next(
        path for path in graph.paths if path.mode == "direct-tool"
    )
    assert direct_tool_path.discovered is True
    assert direct_tool_path.executed is False
    assert direct_tool_path.status == "not-verified"
    assert direct_tool_path.covered is False
    assert direct_tool_path.equivalent_control_ref is None
    assert any(finding.finding_id == "MED-002" for finding in graph.findings)


# ---------------------------------------------------------------------------
# build_mediation_graph: static-evidence robustness (source-order, duplicate
# dispatch definitions, AST caching, path-escape, and malformed-input
# degradation)
# ---------------------------------------------------------------------------


def test_post_hoc_pre_action_seam_call_does_not_cover_a_bypass_path(
    fixture_root: Path,
):
    """A real call to the Agent Hooks seam that happens *after* the state
    change already ran must never be credited as pre-action mediation,
    even though ``pre-action-seam`` sorts before ``tool-service`` in
    ``CANONICAL_NODE_ORDER``. Coverage must come from each call's actual
    source position, not merely from both node names being present
    somewhere in the path.

    Uses the ``unmediated-background`` fixture's own ``background`` mode,
    whose dispatch function genuinely calls ``agent_hooks.pre_tool_call``
    — but only after ``provider.charge_refund`` has already executed the
    effect (see that fixture's own docstring) — rather than a dedicated
    fixture, so Task 4's declared fixture set stays exactly the two named
    fixtures (``unmediated-background``, ``provider-hosted-side-effect``).
    """
    root = fixture_root / "unmediated-background"
    source = (root / "app" / "agent.py").read_text(encoding="utf-8")
    assert "agent_hooks.pre_tool_call" in source.split("def background_payments_refund")[1].split("def ")[0]

    actions = inventory.build_action_inventory(root).actions
    graph = build_mediation_graph(root, actions, maf_adapter.MAFAdapter())

    background_path = next(
        path
        for path in graph.paths
        if path.action_id == "payments.refund" and path.mode == "background"
    )
    assert background_path.discovered is True
    assert background_path.executed is False
    assert background_path.status == "not-verified"
    assert background_path.covered is False
    assert background_path.pre_action_seam is None
    assert "pre-action-seam" not in background_path.nodes
    assert "tool-service" in background_path.nodes
    assert any(f.finding_id == "MED-002" for f in graph.findings)


def test_pre_action_seam_call_inside_a_nested_closure_is_not_credited(
    tmp_path: Path,
):
    """A pre-action-seam call that exists only inside a nested closure
    defined by the dispatch function — never proven to actually be
    invoked — must never be credited to the *outer* dispatch function's
    own call trace. The closure could be dead code, a callback stored for
    later, or invoked from somewhere this scanner cannot see; none of
    those possibilities make the outer function's own direct provider
    call mediated.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _Provider:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            agent_hooks = _AgentHooks()
            provider = _Provider()


            def direct_tool_orders_cancel(**kwargs):
                def _unused_helper():
                    # Defined, but never called below. A call that only
                    # exists inside this closure's own body must never be
                    # credited to direct_tool_orders_cancel's own trace.
                    agent_hooks.pre_tool_call(**kwargs)

                return provider.cancel_order(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    path = next(p for p in graph.paths if p.mode == "direct-tool")
    assert path.discovered is True
    assert path.executed is False
    assert path.status == "not-verified"
    assert path.covered is False
    assert path.pre_action_seam is None
    assert "pre-action-seam" not in path.nodes
    assert "tool-service" in path.nodes
    assert any(f.finding_id == "MED-002" for f in graph.findings)


def test_conditionally_executed_pre_action_seam_call_never_produces_a_false_pass(
    tmp_path: Path,
):
    """A pre-action-seam call reached only through an ``if`` branch that
    might not execute must never be treated as unconditional coverage —
    the branch could be skipped entirely at runtime while the direct
    provider call below it always runs, which is exactly the bypass this
    scanner exists to catch.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _Provider:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            agent_hooks = _AgentHooks()
            provider = _Provider()


            def direct_tool_orders_cancel(**kwargs):
                if kwargs.get("flag"):
                    # This seam call only runs on one branch; it must not
                    # be trusted to always run before the provider call.
                    agent_hooks.pre_tool_call(**kwargs)
                return provider.cancel_order(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    path = next(p for p in graph.paths if p.mode == "direct-tool")
    assert path.discovered is True
    assert path.executed is False
    assert path.status == "not-verified"
    assert path.covered is False
    assert path.pre_action_seam is None
    assert "pre-action-seam" not in path.nodes
    assert "tool-service" in path.nodes
    assert any(f.finding_id == "MED-002" for f in graph.findings)


def test_within_file_duplicate_dispatch_bypass_wins_over_later_mediated_definition(
    tmp_path: Path,
):
    """When the *same file* defines the same dispatch name twice, an
    evidenced bypass among the definitions must win even when it is not
    the last (i.e. not the one whose name binding a caller would actually
    resolve at runtime) — a bypass anywhere in that set is real,
    exploitable behavior regardless of which definition currently "wins"
    the name.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [batch]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _ToolService:
                def orders_cancel(self, **kwargs):
                    return {"cancelled": True}


            class _Provider:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            agent_hooks = _AgentHooks()
            tool_service = _ToolService()
            provider = _Provider()


            def batch_orders_cancel(**kwargs):
                # First definition in the file: bypasses mediation.
                return provider.cancel_order(**kwargs)


            def batch_orders_cancel(**kwargs):
                # Second, later (actual-binding) definition: fully
                # mediated. The earlier bypass above must still win.
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.orders_cancel(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    path = next(p for p in graph.paths if p.mode == "batch")
    assert path.discovered is True
    assert path.executed is False
    assert path.status == "not-verified"
    assert path.covered is False
    assert path.pre_action_seam is None
    assert any(f.finding_id == "MED-002" for f in graph.findings)


def test_within_file_duplicate_dispatch_uses_actual_last_binding_when_no_bypass(
    tmp_path: Path,
):
    """When *no* duplicate definition of a dispatch name is a bypass, the
    *last* definition (the one whose name binding a caller would actually
    resolve at runtime) supplies the evidence — not an earlier, merely
    incomplete stub that never reaches a state change at all.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [batch]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _ToolService:
                def orders_cancel(self, **kwargs):
                    return {"cancelled": True}


            agent_hooks = _AgentHooks()
            tool_service = _ToolService()


            def batch_orders_cancel(**kwargs):
                # First definition: a stub with no dispatch logic yet, no
                # state-changing call at all.
                return {"status": "todo"}


            def batch_orders_cancel(**kwargs):
                # Second (last, actual-binding) definition: the real,
                # fully mediated implementation.
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.orders_cancel(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    path = next(p for p in graph.paths if p.mode == "batch")
    assert path.discovered is True
    assert path.executed is False
    assert path.status == "not-verified"
    assert path.covered is True
    assert path.pre_action_seam is not None
    assert "pre-action-seam" in path.nodes
    assert "tool-service" in path.nodes
    assert path.nodes.index("pre-action-seam") < path.nodes.index("tool-service")


def test_candidate_file_discovery_is_memoized_once_per_action_not_per_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``_candidate_files`` — which falls back to a real ``root.rglob``
    filesystem walk when an action declares no ``known_runtime_paths`` —
    must be called at most once per action for the whole assessment, not
    once per ``(action, mode)`` pair: three actions each assessed across
    five required modes must mean three candidate-file lookups, not
    fifteen.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
              - id: orders.refund
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
              - id: orders.void
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _ToolService:
                def handle(self, **kwargs):
                    return {"ok": True}


            agent_hooks = _AgentHooks()
            tool_service = _ToolService()


            def direct_tool_orders_cancel(**kwargs):
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.handle(**kwargs)


            def direct_tool_orders_refund(**kwargs):
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.handle(**kwargs)


            def direct_tool_orders_void(**kwargs):
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.handle(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    assert len(actions) == 3  # 3 actions x 5 required modes = 15 lookups

    discovery_calls: list[str] = []
    original_candidate_files = mediation._candidate_files

    def counting_candidate_files(root, action):
        discovery_calls.append(action.action_id)
        return original_candidate_files(root, action)

    monkeypatch.setattr(mediation, "_candidate_files", counting_candidate_files)

    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    assert len(graph.paths) == 15
    # Exactly one candidate-file lookup per action for the whole
    # assessment, regardless of how many modes consult it.
    assert len(discovery_calls) == 3
    assert sorted(discovery_calls) == ["orders.cancel", "orders.refund", "orders.void"]


def test_duplicate_dispatch_definitions_bypass_evidence_wins_over_mediated_one(
    tmp_path: Path,
):
    """When the same dispatch function name is defined in more than one
    candidate module, an evidenced bypass anywhere among them must win
    over a mediated duplicate — never a false ``pass`` just because the
    *first*-scanned file happened to be the mediated one. The mediated
    definition lives in a file that sorts alphabetically *before* the
    bypassing one specifically so a "return on first match" scanner would
    get this wrong.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [batch]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "a_mediated.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _ToolService:
                def orders_cancel(self, **kwargs):
                    return {"cancelled": True}


            agent_hooks = _AgentHooks()
            tool_service = _ToolService()


            def batch_orders_cancel(**kwargs):
                agent_hooks.pre_tool_call(action="orders.cancel", **kwargs)
                return tool_service.orders_cancel(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (app_dir / "z_bypass.py").write_text(
        textwrap.dedent(
            """
            class _Provider:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            provider = _Provider()


            def batch_orders_cancel(**kwargs):
                # A second, later-sorted definition of the same dispatch
                # name that bypasses mediation entirely.
                return provider.cancel_order(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    batch_path = next(path for path in graph.paths if path.mode == "batch")
    assert batch_path.discovered is True
    assert batch_path.executed is False
    assert batch_path.status == "not-verified"
    assert batch_path.covered is False
    assert batch_path.pre_action_seam is None

    assert any(f.finding_id == "MED-002" for f in graph.findings)


def test_ast_files_are_parsed_once_per_assessment_not_per_action_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Static evidence for every ``(action, mode)`` pair is read from a
    parse-once-per-file index built for the whole ``build_mediation_graph``
    call, not re-parsed from disk on every action/mode combination — three
    actions times five required modes must not mean fifteen re-parses of
    the one file that implements all of them.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
              - id: orders.refund
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
              - id: orders.void
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _ToolService:
                def handle(self, **kwargs):
                    return {"ok": True}


            agent_hooks = _AgentHooks()
            tool_service = _ToolService()


            def direct_tool_orders_cancel(**kwargs):
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.handle(**kwargs)


            def direct_tool_orders_refund(**kwargs):
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.handle(**kwargs)


            def direct_tool_orders_void(**kwargs):
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.handle(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    assert len(actions) == 3  # 3 actions x 5 required modes = 15 lookups

    parse_calls: list[Path] = []
    original_parse = mediation._parse_python

    def counting_parse(path: Path):
        parse_calls.append(path)
        return original_parse(path)

    monkeypatch.setattr(mediation, "_parse_python", counting_parse)

    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    assert len(graph.paths) == 15
    # Exactly one parse per distinct candidate file for the whole
    # assessment, regardless of how many (action, mode) pairs consult it.
    assert len(parse_calls) == 1


def test_known_runtime_paths_ignores_declarations_that_escape_the_project_root(
    tmp_path_factory: pytest.TempPathFactory,
):
    """A declared ``known_runtime_paths`` entry that resolves outside the
    project root must be dropped rather than trusted — even when the
    escaping file would (if wrongly scanned) supply mediated evidence that
    could mask a real, in-root bypass.
    """
    root = tmp_path_factory.mktemp("mediation-root")
    outside = tmp_path_factory.mktemp("mediation-outside")

    escape_file = outside / "escape.py"
    escape_file.write_text(
        textwrap.dedent(
            """
            class _AgentHooks:
                def pre_tool_call(self, **kwargs):
                    return {"decision": "allow"}


            class _ToolService:
                def orders_cancel(self, **kwargs):
                    return {"cancelled": True}


            agent_hooks = _AgentHooks()
            tool_service = _ToolService()


            def batch_orders_cancel(**kwargs):
                agent_hooks.pre_tool_call(**kwargs)
                return tool_service.orders_cancel(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    app_dir = root / "app"
    app_dir.mkdir()
    (app_dir / "real.py").write_text(
        textwrap.dedent(
            """
            class _Provider:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            provider = _Provider()


            def batch_orders_cancel(**kwargs):
                return provider.cancel_order(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    relative_escape = Path(os.path.relpath(escape_file, root)).as_posix()
    assert relative_escape.startswith("..")

    action = _action_record(
        "orders.cancel",
        consequence="write",
        execution_modes=("batch",),
        known_runtime_paths=(relative_escape, "app/real.py"),
    )
    graph = build_mediation_graph(root, (action,), maf_adapter.MAFAdapter())

    batch_path = next(path for path in graph.paths if path.mode == "batch")
    assert batch_path.discovered is True
    assert batch_path.executed is False
    assert batch_path.status == "not-verified"
    assert batch_path.covered is False
    assert not any(
        "escape.py" in ref for ref in batch_path.evidence_refs
    )
    assert any("real.py" in ref for ref in batch_path.evidence_refs)


def test_malformed_python_source_degrades_to_not_verified_without_crashing(
    tmp_path: Path,
):
    """A candidate module that is not even syntactically valid Python must
    never crash the assessor — it is treated exactly like "no evidence in
    this file", leaving the mode ``not-verified`` when no other candidate
    supplies evidence, never a false ``pass`` or an unhandled exception.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: reports.export
                consequence: write
                execution_modes: [direct-tool]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "broken.py").write_text(
        "def direct_tool_reports_export(:\n    return 1\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions

    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    direct_tool_path = next(
        path for path in graph.paths if path.mode == "direct-tool"
    )
    assert direct_tool_path.status == "not-verified"
    assert direct_tool_path.covered is False
    assert any(f.finding_id == "MED-002" for f in graph.findings)


def test_malformed_equivalent_control_yaml_degrades_to_no_control_not_a_crash(
    tmp_path: Path,
):
    """A registry file that is not even valid YAML must never crash the
    equivalent-control reader either — it degrades to "no equivalent
    control declared", so a genuine static-evidence bypass is still
    reported exactly as ``must-fix``, never silently swallowed by a
    raised exception nor wrongly credited with a control that could not
    actually be parsed. Actions are constructed directly here (bypassing
    ``inventory.build_action_inventory``, which is the strict, authoritative
    registry parser and is expected to reject this file on its own) so the
    equivalent-control reader's own independent degradation path is what
    is under test.
    """
    (tmp_path / "agent.yaml").write_text(
        "tools: [ { id: payments.settle, consequence: write\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _Provider:
                def settle(self, **kwargs):
                    return {"settled": True}


            provider = _Provider()


            def direct_tool_payments_settle(**kwargs):
                return provider.settle(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    action = _action_record(
        "payments.settle",
        consequence="write",
        execution_modes=("direct-tool",),
    )

    graph = build_mediation_graph(tmp_path, (action,), maf_adapter.MAFAdapter())

    direct_tool_path = next(
        path for path in graph.paths if path.mode == "direct-tool"
    )
    assert direct_tool_path.discovered is True
    assert direct_tool_path.executed is False
    assert direct_tool_path.status == "not-verified"
    assert direct_tool_path.covered is False
    assert direct_tool_path.equivalent_control_ref is None


def test_med002_stays_not_verified_without_executed_path_proof_even_for_static_bypass(
    tmp_path: Path,
):
    """Static source alone never upgrades ``MED-002`` to ``must-fix``.

    Even when one family is structurally a bypass and the other uncovered
    families are merely absent, the aggregate mediation finding remains
    ``not-verified`` until a correlated executed receipt proves which path
    actually ran.
    """
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: orders.cancel
                consequence: write
                execution_modes: [batch]
                provider_hosted: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "agent.py").write_text(
        textwrap.dedent(
            """
            class _Provider:
                def cancel_order(self, **kwargs):
                    return {"cancelled": True}


            provider = _Provider()


            def batch_orders_cancel(**kwargs):
                # No pre-action seam at all: a proven bypass.
                return provider.cancel_order(**kwargs)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    actions = inventory.build_action_inventory(tmp_path).actions
    graph = build_mediation_graph(tmp_path, actions, maf_adapter.MAFAdapter())

    by_mode = {path.mode: path for path in graph.paths}
    assert by_mode["batch"].status == "not-verified"
    for mode in ("interactive", "background", "subagent", "direct-tool"):
        assert by_mode[mode].status == "not-verified"

    med002 = next(f for f in graph.findings if f.finding_id == "MED-002")
    assert med002.status == "not-verified"


# ---------------------------------------------------------------------------
# assess_provider_paths: provider-hosted-side-effect fixture
# ---------------------------------------------------------------------------


def test_provider_hosted_side_effect_is_unsupported_without_equivalent_control(
    fixture_root: Path,
):
    root = fixture_root / "provider-hosted-side-effect"
    actions = inventory.build_action_inventory(root).actions

    graph = assess_provider_paths(root, actions)

    assert len(graph.findings) == 1
    finding = graph.findings[0]
    assert finding.finding_id == "MED-003"
    assert finding.status == "must-fix"
    assert finding.reason_code == "unsupported"
    assert "mail.send" in finding.affected_actions


def test_read_only_provider_hosted_action_is_not_applicable(fixture_root: Path):
    root = fixture_root / "provider-hosted-side-effect"
    actions = inventory.build_action_inventory(root).actions

    graph = assess_provider_paths(root, actions)

    by_action = {path.action_id: path for path in graph.paths}
    read_only_path = by_action["search.lookup"]
    assert read_only_path.status == "not-applicable"
    assert read_only_path.covered is False
    assert not any(
        "search.lookup" in finding.affected_actions for finding in graph.findings
    )


def test_declared_equivalent_control_requires_independent_verification(
    tmp_path: Path,
):
    complete_root = tmp_path / "complete"
    complete_root.mkdir()
    (complete_root / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: payments.settle
                consequence: write
                execution_modes: [provider-hosted-tool]
                provider_hosted: true
                equivalent_control:
                  authorization_ref: policies/acs-authz.yaml#payments.settle
                  idempotency_ref: policies/acs-idempotency.yaml#payments.settle
                  transaction_ref: policies/acs-transaction.yaml#payments.settle
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    complete_actions = inventory.build_action_inventory(complete_root).actions
    complete_graph = assess_provider_paths(complete_root, complete_actions)

    assert len(complete_graph.findings) == 1
    complete_finding = complete_graph.findings[0]
    assert complete_finding.finding_id == "MED-003"
    assert complete_finding.status == "not-verified"
    assert complete_finding.reason_code == "equivalent-control-not-verified"
    complete_path = complete_graph.paths[0]
    assert complete_path.status == "not-verified"
    assert complete_path.covered is False
    assert complete_path.equivalent_control_ref is None

    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    (partial_root / "agent.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: payments.settle
                consequence: write
                execution_modes: [provider-hosted-tool]
                provider_hosted: true
                equivalent_control:
                  authorization_ref: policies/acs-authz.yaml#payments.settle
                  idempotency_ref: policies/acs-idempotency.yaml#payments.settle
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    partial_actions = inventory.build_action_inventory(partial_root).actions
    partial_graph = assess_provider_paths(partial_root, partial_actions)

    assert len(partial_graph.findings) == 1
    partial_finding = partial_graph.findings[0]
    assert partial_finding.finding_id == "MED-003"
    assert partial_finding.status == "must-fix"
    assert partial_finding.reason_code == "unsupported"
    partial_path = partial_graph.paths[0]
    assert partial_path.status == "must-fix"
    assert partial_path.covered is False
    assert partial_path.equivalent_control_ref is None


def test_assess_provider_paths_ignores_non_provider_hosted_actions(tmp_path: Path):
    action = _action_record(
        "customer.lookup",
        consequence="read",
        execution_modes=("interactive",),
        provider_hosted=False,
    )
    graph = assess_provider_paths(tmp_path, (action,))
    assert graph.paths == ()
    assert graph.findings == ()
