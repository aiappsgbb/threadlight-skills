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
import textwrap
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import canonical
import contracts
import inventory
import maf_adapter
from contracts import ActionRecord, Finding, PathRecord
from mediation import (
    CANONICAL_NODE_ORDER,
    MediationGraph,
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


# ---------------------------------------------------------------------------
# build_mediation_graph: unmediated-background fixture
# ---------------------------------------------------------------------------


def test_every_consequential_mode_becomes_a_graph_path(fixture_root: Path):
    root = fixture_root / "unmediated-background"
    actions = inventory.build_action_inventory(root).actions
    adapter = maf_adapter.MAFAdapter()

    graph = build_mediation_graph(root, actions, adapter)

    assert {(path.action_id, path.mode) for path in graph.paths} == {
        ("payments.refund", "interactive"),
        ("payments.refund", "batch"),
        ("payments.refund", "background"),
        ("payments.refund", "subagent"),
        ("payments.refund", "direct-tool"),
    }
    assert {(finding.finding_id, finding.summary) for finding in graph.findings} == {
        (
            "MED-001",
            "batch path for payments.refund lacks pre-action mediation",
        ),
        (
            "MED-001",
            "background path for payments.refund lacks pre-action mediation",
        ),
        ("MED-002", "declared mediation coverage is incomplete"),
    }


def test_covered_paths_route_through_pre_action_seam_before_tool_service(
    fixture_root: Path,
):
    root = fixture_root / "unmediated-background"
    actions = inventory.build_action_inventory(root).actions
    graph = build_mediation_graph(root, actions, maf_adapter.MAFAdapter())

    by_mode = {path.mode: path for path in graph.paths}
    for mode in ("interactive", "subagent", "direct-tool"):
        path = by_mode[mode]
        assert path.covered is True
        assert path.status == "pass"
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
        assert path.covered is False
        assert path.status == "must-fix"
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

    assert len(graph.paths) == 1
    path = graph.paths[0]
    assert path.action_id == "orders.cancel"
    assert path.mode == "batch"
    assert path.covered is False
    assert path.status == "not-verified"

    assert len(graph.findings) == 1
    finding = graph.findings[0]
    assert finding.finding_id == "MED-002"
    assert finding.status == "not-verified"


def test_build_mediation_graph_merges_declared_adapter_mediation_paths(
    tmp_path: Path,
):
    """An adapter's own ``discover_mediation`` output is folded into the graph.

    ``build_mediation_graph`` is specified to build paths from
    ``adapter.discover_entry_points``, ``adapter.discover_mediation``, known
    runtime paths, *and* static call evidence — not from static evidence
    alone. A fake adapter here declares one additional, already-covered path
    that no static scan of ``tmp_path`` could ever find, proving the merge
    actually happens.
    """
    declared_path = PathRecord(
        path_id="declaredpathid0001",
        action_id="reports.export",
        mode="direct-tool",
        nodes=("entry", "tool-router", "pre-action-seam", "tool-service"),
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
            return (declared_path,)

    action = _action_record(
        "reports.export", consequence="write", execution_modes=()
    )
    graph = build_mediation_graph(tmp_path, (action,), _FakeAdapter())

    assert declared_path in graph.paths
    assert not any(
        finding.finding_id in ("MED-001", "MED-002")
        and "reports.export" in finding.affected_actions
        for finding in graph.findings
    )


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


def test_equivalent_control_evidence_must_name_all_three_server_side_refs(
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

    assert complete_graph.findings == ()
    complete_path = complete_graph.paths[0]
    assert complete_path.status == "pass"
    assert complete_path.covered is True
    assert complete_path.equivalent_control_ref is not None
    for required in ("authorization_ref", "idempotency_ref", "transaction_ref"):
        assert required in complete_path.equivalent_control_ref

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
