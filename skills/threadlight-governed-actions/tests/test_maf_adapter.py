"""Tests for threadlight-governed-actions' MAF-first runtime adapter and pin.

Exercises ``maf_adapter``'s ``RuntimeAdapter`` protocol conformance,
``MAFAdapter.detect`` (real import/call evidence vs. dependency-only vs.
unknown framework), provider-hosted tool disclosure, and the upstream pin
comparison (``load_upstream_pin`` / ``compare_upstream_tuple``) against the
exact complete tested tuple recorded in ``references/upstream-pin.json``.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_inputs.py \
        skills/threadlight-governed-actions/tests/test_maf_adapter.py -q
"""
from __future__ import annotations

import json
import textwrap
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Protocol

import pytest

import contracts
import maf_adapter
from contracts import ActionRecord, DetectionEvidence, Finding, PathRecord, ProbeResult
from maf_adapter import (
    MAFAdapter,
    PinComparison,
    RuntimeAdapter,
    compare_upstream_tuple,
    load_upstream_pin,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REFERENCE_ROOT = Path(__file__).resolve().parent.parent / "references"
PIN_PATH = REFERENCE_ROOT / "upstream-pin.json"


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURES_DIR


# ---------------------------------------------------------------------------
# RuntimeAdapter protocol shape
# ---------------------------------------------------------------------------


def test_runtime_adapter_is_a_runtime_checkable_protocol():
    assert issubclass(RuntimeAdapter, Protocol)
    assert isinstance(MAFAdapter(), RuntimeAdapter)


def test_maf_adapter_id_is_stable():
    assert MAFAdapter.adapter_id == "maf/v1"
    assert MAFAdapter().adapter_id == "maf/v1"


def test_maf_adapter_exposes_every_protocol_method():
    adapter = MAFAdapter()
    for method_name in (
        "detect",
        "resolved_tuple",
        "discover_entry_points",
        "discover_actions",
        "discover_mediation",
        "build_probe_cases",
        "run_local_probe",
    ):
        assert callable(getattr(adapter, method_name))


# ---------------------------------------------------------------------------
# detect(): real import/call vs. dependency-only vs. unknown framework
# ---------------------------------------------------------------------------


def test_detect_true_with_real_agent_framework_import(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "agent.py").write_text(
        textwrap.dedent(
            """\
            import agent_framework
            from agent_framework import ChatAgent

            agent = ChatAgent(name="contoso")
            """
        ),
        encoding="utf-8",
    )
    evidence = MAFAdapter().detect(tmp_path)
    assert isinstance(evidence, DetectionEvidence)
    assert evidence.detected is True
    assert evidence.references == ("app/agent.py",)
    assert evidence.ambiguity is None


def test_detect_false_when_dependency_declared_but_never_imported(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "contoso-claims-assistant"
            dependencies = ["agent-framework-core>=1.13.0"]
            """
        ),
        encoding="utf-8",
    )
    evidence = MAFAdapter().detect(tmp_path)
    assert evidence.detected is False
    assert evidence.references == ("pyproject.toml",)
    assert evidence.ambiguity is not None
    assert "dependency" in evidence.ambiguity


def test_detect_false_for_unknown_framework_with_no_claim(tmp_path: Path):
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    evidence = MAFAdapter().detect(tmp_path)
    assert evidence.detected is False
    assert evidence.references == ()
    assert evidence.ambiguity is None


def test_detect_ignores_import_evidence_inside_vendored_tree(tmp_path: Path):
    vendored = tmp_path / ".venv" / "lib" / "agent_framework_vendor.py"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("import agent_framework\n", encoding="utf-8")
    evidence = MAFAdapter().detect(tmp_path)
    assert evidence.detected is False
    assert evidence.references == ()


# ---------------------------------------------------------------------------
# Provider-hosted tool disclosure
# ---------------------------------------------------------------------------


def test_discover_actions_discloses_provider_hosted_tool(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "agent.py").write_text(
        textwrap.dedent(
            """\
            from agent_framework import HostedWebSearchTool

            web_search = HostedWebSearchTool(name="web.search")
            """
        ),
        encoding="utf-8",
    )
    actions = MAFAdapter().discover_actions(tmp_path)
    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, ActionRecord)
    assert action.action_id == "web.search"
    assert action.provider_hosted is True
    assert action.execution_modes == ("provider-hosted-tool",)
    assert action.inventory_status == "not-verified"


def test_discover_actions_derives_id_from_variable_when_no_literal_name(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "agent.py").write_text(
        "from agent_framework import HostedCodeInterpreterTool\n\n"
        "code_interpreter_tool = HostedCodeInterpreterTool()\n",
        encoding="utf-8",
    )
    actions = MAFAdapter().discover_actions(tmp_path)
    assert len(actions) == 1
    assert actions[0].action_id == "maf.code_interpreter_tool"
    assert actions[0].provider_hosted is True


def test_discover_actions_empty_when_no_hosted_tools(tmp_path: Path):
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    assert MAFAdapter().discover_actions(tmp_path) == ()


# ---------------------------------------------------------------------------
# discover_entry_points / discover_mediation
# ---------------------------------------------------------------------------


def test_discover_entry_points_references_agent_registry(tmp_path: Path):
    (tmp_path / "agent.yaml").write_text("name: contoso\n", encoding="utf-8")
    entry_points = MAFAdapter().discover_entry_points(tmp_path)
    assert entry_points == (
        {
            "kind": "maf-agent-registry",
            "declaration_ref": "agent.yaml",
            "framework": "agent_framework",
        },
    )


def test_discover_entry_points_empty_without_registry(tmp_path: Path):
    assert MAFAdapter().discover_entry_points(tmp_path) == ()


def test_discover_mediation_emits_uncovered_path_for_provider_hosted_action(
    tmp_path: Path,
):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "agent.py").write_text(
        "from agent_framework import HostedWebSearchTool\n\n"
        'web_search = HostedWebSearchTool(name="web.search")\n',
        encoding="utf-8",
    )
    mediation = MAFAdapter().discover_mediation(tmp_path)
    assert len(mediation) == 1
    path = mediation[0]
    assert isinstance(path, PathRecord)
    assert path.action_id == "web.search"
    assert path.mode == "provider-hosted-tool"
    assert path.covered is False
    assert path.status == "not-verified"
    assert path.path_id and len(path.path_id) == 16


def test_discover_mediation_empty_without_provider_hosted_actions(tmp_path: Path):
    assert MAFAdapter().discover_mediation(tmp_path) == ()


# ---------------------------------------------------------------------------
# build_probe_cases / run_local_probe (adapters never assign a passing status)
# ---------------------------------------------------------------------------


class _FakeGraph:
    def __init__(self, paths):
        self.paths = paths


def test_build_probe_cases_derives_deny_case_per_path(tmp_path: Path):
    path_record = PathRecord(
        path_id="0123456789abcdef",
        action_id="payments.refund",
        mode="interactive",
        nodes=("entry", "tool-router", "tool-service"),
        pre_action_seam=None,
        equivalent_control_ref=None,
        covered=False,
        status="not-verified",
        evidence_refs=(),
    )
    cases = MAFAdapter().build_probe_cases(tmp_path, (), _FakeGraph((path_record,)))
    assert cases == (
        {
            "probe_id": "maf-deny-payments.refund-interactive",
            "action_id": "payments.refund",
            "path_id": "0123456789abcdef",
            "mode": "interactive",
            "probe_kind": "deny",
        },
    )


def test_build_probe_cases_empty_without_graph_paths(tmp_path: Path):
    assert MAFAdapter().build_probe_cases(tmp_path, (), _FakeGraph(())) == ()
    assert MAFAdapter().build_probe_cases(tmp_path, (), None) == ()


def test_run_local_probe_never_returns_pass(tmp_path: Path):
    case = {
        "probe_id": "maf-deny-payments.refund-interactive",
        "action_id": "payments.refund",
        "path_id": "0123456789abcdef",
        "mode": "interactive",
        "probe_kind": "deny",
    }
    result = MAFAdapter().run_local_probe(case)
    assert isinstance(result, ProbeResult)
    assert result.status != "pass"
    assert result.probe_id == "maf-deny-payments.refund-interactive"
    assert result.action_id == "payments.refund"


# ---------------------------------------------------------------------------
# resolved_tuple(): prefer recorded evidence, never guess unresolvable keys
# ---------------------------------------------------------------------------


def test_resolved_tuple_reads_recorded_installed_packages_evidence(tmp_path: Path):
    governance = tmp_path / "governance"
    governance.mkdir()
    recorded = {
        "agent-hooks-spec": "0.1.0-alpha@0821ebbae252c45cd225304a464d1130963b82a8",
        "agent-hooks-sdk": (
            "0.1.0a5@sha256:"
            "4ae452b0a1d51540a1b74b0005b0a51f75fd4b80e9aca1a7403dece4dd6f9e46"
        ),
        "agent-framework-core": "1.13.0@4b1afd90520310547cb0e9cdc70f644d80161e82",
        "ctk-vectors": "4f7af786c2757e26711b141e69144b6a336f403b",
        "conformance-python": "3.12.3",
        "acs-policy-schema": "not-applicable",
    }
    (governance / "installed-packages.json").write_text(
        json.dumps(recorded), encoding="utf-8"
    )
    assert MAFAdapter().resolved_tuple(tmp_path) == recorded


def test_resolved_tuple_reports_not_verified_when_no_evidence_recorded(tmp_path: Path):
    resolved = MAFAdapter().resolved_tuple(tmp_path)
    assert resolved["agent-hooks-spec"] == "not-verified"
    assert resolved["ctk-vectors"] == "not-verified"


# ---------------------------------------------------------------------------
# load_upstream_pin / compare_upstream_tuple
# ---------------------------------------------------------------------------


def test_load_upstream_pin_returns_the_complete_tested_tuple():
    pin = load_upstream_pin(PIN_PATH)
    assert pin["pin_schema_version"] == "1.0.0"
    assert pin["status"] == "alpha-experimental"
    assert pin["agent_hooks"]["spec_version"] == "0.1.0-alpha"
    assert pin["maf"]["version"] == "1.13.0"
    assert pin["drift_policy"] == "exact-tuple-rerun-ctk-and-application-probes"


def test_load_upstream_pin_rejects_malformed_json(tmp_path: Path):
    bad = tmp_path / "upstream-pin.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_upstream_pin(bad)


def test_maf_adapter_accepts_only_complete_tested_tuple(fixture_root):
    pin = load_upstream_pin(REFERENCE_ROOT / "upstream-pin.json")
    observed = {
        "agent-hooks-spec": (
            "0.1.0-alpha@0821ebbae252c45cd225304a464d1130963b82a8"
        ),
        "agent-hooks-sdk": (
            "0.1.0a5@sha256:"
            "4ae452b0a1d51540a1b74b0005b0a51f75fd4b80e9aca1a7403dece4dd6f9e46"
        ),
        "agent-framework-core": (
            "1.13.0@4b1afd90520310547cb0e9cdc70f644d80161e82"
        ),
        "ctk-vectors": "4f7af786c2757e26711b141e69144b6a336f403b",
        "conformance-python": "3.12.3",
        "acs-policy-schema": "not-applicable",
    }
    assert compare_upstream_tuple(observed, pin).status == "pass"


def test_any_tuple_drift_requires_ctk_and_application_probe_rerun(fixture_root):
    observed = json.loads(
        (fixture_root / "upstream-version-drift/installed-packages.json").read_text()
    )
    result = compare_upstream_tuple(observed, load_upstream_pin(PIN_PATH))
    assert result.status == "must-fix"
    assert result.finding.finding_id == "PIN-001"
    assert "rerun CTK and all application-path probes" in result.finding.details


def test_missing_observed_key_is_also_drift(fixture_root):
    pin = load_upstream_pin(PIN_PATH)
    observed = {
        "agent-hooks-spec": "0.1.0-alpha@0821ebbae252c45cd225304a464d1130963b82a8",
        # agent-hooks-sdk intentionally missing.
        "agent-framework-core": "1.13.0@4b1afd90520310547cb0e9cdc70f644d80161e82",
        "ctk-vectors": "4f7af786c2757e26711b141e69144b6a336f403b",
        "conformance-python": "3.12.3",
        "acs-policy-schema": "not-applicable",
    }
    result = compare_upstream_tuple(observed, pin)
    assert result.status == "must-fix"
    assert result.finding.finding_id == "PIN-001"


def test_pin_comparison_is_frozen():
    comparison = PinComparison(status="pass", finding=None, observed={}, expected={})
    with pytest.raises(FrozenInstanceError):
        comparison.status = "must-fix"  # type: ignore[misc]
