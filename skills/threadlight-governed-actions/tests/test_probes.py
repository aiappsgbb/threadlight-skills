"""Tests for threadlight-governed-actions' hermetic enforcement probes
(Task 5).

Exercises ``probes.run_application_probe`` against the ``interceptor-
failure`` fixture: a synthetic ``app.agent`` dispatch seam exhibiting
deny, transform, crash, timeout, malformed-verdict, and two deliberately
negative (fail-open, self-report-mismatch) behaviors. Every probe here
runs the fixture's dispatch callable in a real isolated subprocess — this
file never mocks the dispatch seam itself, only supplies the synthetic
fixture it drives.

Only a *completed*, well-formed deny/transform report can ever earn a
pass. A crash, timeout, malformed (unparseable) output, or malformed
verdict is never rewarded as enforcement merely because it happened to
also block the tool this run — those always classify as a completed
``ENF-002`` must-fix finding when the ledger is observable at all;
``ENF-001`` is reserved for a *completed* run whose own self-reported
deny/transform decision contradicts what the ledger proves. An outcome
that is genuinely unobservable (the child failed and the ledger recorded
nothing at all) raises ``ProbeToolingError`` instead of any finding.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_probes.py -q
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from contracts import Finding, ProbeResult
from probes import (
    ProbeCase,
    ProbeContractError,
    ProbeToolingError,
    findings_from_probes,
    load_probe_contract,
    run_application_probe,
    run_enforcement_probe_set,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURES_DIR


def test_probe_case_is_frozen():
    case = ProbeCase("deny", "payments.refund", "deny", {"amount": 7})
    with pytest.raises(FrozenInstanceError):
        case.probe_id = "other"  # type: ignore[misc]


def test_load_probe_contract_reads_exact_contract_fields(fixture_root: Path):
    contract = load_probe_contract(fixture_root / "interceptor-failure")
    assert contract["dispatch"] == "app.agent:dispatch_probe"
    assert contract["audit_sink"] == "app.agent:AUDIT_EVENTS"
    assert contract["timeout_ms"] == 100
    assert contract["side_effect_mode"] == "synthetic"
    assert contract["observation_ledger"] == "governance/probe-ledger.jsonl"
    assert contract["actions"] == ("payments.refund",)


def test_load_probe_contract_rejects_missing_contract(tmp_path: Path):
    with pytest.raises(ProbeContractError):
        load_probe_contract(tmp_path / "does-not-exist")


def test_load_probe_contract_rejects_non_synthetic_side_effect_mode(tmp_path: Path):
    root = tmp_path / "bad-contract"
    (root / "governance").mkdir(parents=True)
    (root / "governance" / "probe-contract.json").write_text(
        '{"dispatch": "app.agent:dispatch_probe", '
        '"audit_sink": "app.agent:AUDIT_EVENTS", '
        '"timeout_ms": 100, "side_effect_mode": "live", '
        '"observation_ledger": "governance/probe-ledger.jsonl", '
        '"actions": ["payments.refund"]}',
        encoding="utf-8",
    )
    with pytest.raises(ProbeContractError):
        load_probe_contract(root)


@pytest.mark.parametrize(
    ("probe_id", "fault", "expected", "status", "reason_code"),
    [
        ("deny", "deny", "tool_not_invoked", "pass", None),
        (
            "transform",
            "transform",
            "tool_received_transformed_arguments",
            "pass",
            None,
        ),
        # A crash, a timeout, and a malformed verdict are never a
        # *deliberate* enforcement decision — they are operational
        # failures at the dispatch seam. Even though the ledger proves
        # the tool was never reached in these runs, an abnormally
        # terminated (or semantically invalid) probe can never be
        # trusted as proof of reliable enforcement, so all three are
        # unconditionally a completed ENF-002 must-fix finding.
        ("crash", "raise", "tool_not_invoked", "must-fix", "ENF-002"),
        ("timeout", "sleep", "tool_not_invoked", "must-fix", "ENF-002"),
        ("malformed-verdict", "invalid", "tool_not_invoked", "must-fix", "ENF-002"),
    ],
)
def test_application_dispatch_enforces_probe(
    fixture_root: Path,
    probe_id: str,
    fault: str,
    expected: str,
    status: str,
    reason_code: str,
):
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(probe_id, "payments.refund", fault, {"amount": 7}),
    )
    assert result.expected == expected
    assert result.status == status
    if reason_code is not None:
        assert result.reason_code == reason_code


def test_malformed_verdict_is_always_a_completed_enf_002_finding(
    fixture_root: Path,
):
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase("malformed-verdict", "payments.refund", "invalid", {"amount": 7}),
    )
    assert isinstance(result, ProbeResult)
    assert result.status == "must-fix"
    assert result.reason_code == "ENF-002"

    findings = findings_from_probes((result,))
    assert [(finding.finding_id, finding.status) for finding in findings] == [
        ("ENF-002", "must-fix")
    ]


def test_crash_and_timeout_are_always_completed_enf_002_findings_even_fail_closed(
    fixture_root: Path,
):
    root = fixture_root / "interceptor-failure"
    crash = run_application_probe(
        root, ProbeCase("crash", "payments.refund", "raise", {"amount": 7})
    )
    timeout = run_application_probe(
        root, ProbeCase("timeout", "payments.refund", "sleep", {"amount": 7})
    )
    for result in (crash, timeout):
        # The ledger proves the tool was never reached (fail-closed) for
        # both of these faults, yet neither is rewarded with "pass": an
        # abnormal (crashed/timed-out) dispatch is never trusted as a
        # deliberate enforcement decision.
        assert result.observed != "tool_invoked_despite_fault"
        assert result.status == "must-fix"
        assert result.reason_code == "ENF-002"


def test_deliberate_fail_open_is_a_completed_enf_002_finding_not_a_tooling_error(
    fixture_root: Path,
):
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase("fail-open", "payments.refund", "fail_open", {"amount": 7}),
    )
    assert isinstance(result, ProbeResult)
    assert result.status == "must-fix"
    assert result.reason_code == "ENF-002"
    assert result.observed == "tool_invoked_despite_fault"

    findings = findings_from_probes((result,))
    assert [(finding.finding_id, finding.status) for finding in findings] == [
        ("ENF-002", "must-fix")
    ]
    assert isinstance(findings[0], Finding)


def test_self_reported_deny_mismatch_is_enf_001(fixture_root: Path):
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase("mismatch", "payments.refund", "mismatch", {"amount": 7}),
    )
    assert result.status == "must-fix"
    assert result.reason_code == "ENF-001"
    assert result.observed == "tool_invoked_despite_deny"

    findings = findings_from_probes((result,))
    assert [(finding.finding_id, finding.status) for finding in findings] == [
        ("ENF-001", "must-fix")
    ]


def test_transform_probe_hashes_are_canonicalized_and_raw_arguments_are_absent(
    fixture_root: Path,
):
    root = fixture_root / "interceptor-failure"
    ordered = run_application_probe(
        root,
        ProbeCase(
            "transform", "payments.refund", "transform", {"amount": 7, "currency": "USD"}
        ),
    )
    reordered = run_application_probe(
        root,
        ProbeCase(
            "transform", "payments.refund", "transform", {"currency": "USD", "amount": 7}
        ),
    )

    assert ordered.status == "pass"
    assert reordered.status == "pass"
    # Canonicalization: the same logical arguments in a different key
    # order produce the identical recorded evidence.
    assert ordered.evidence_refs == reordered.evidence_refs
    assert ordered.evidence_refs

    # Raw arguments never appear in ProbeResult.observed: it is always
    # one of a small fixed vocabulary of outcome names, never the
    # amount/currency literal or key names from the payload.
    assert ordered.observed == "tool_received_transformed_arguments"
    assert "7" not in ordered.observed
    assert "amount" not in ordered.observed
    assert "USD" not in ordered.observed


def test_run_application_probe_rejects_action_not_in_contract(fixture_root: Path):
    with pytest.raises(ProbeContractError):
        run_application_probe(
            fixture_root / "interceptor-failure",
            ProbeCase("deny", "payments.unknown-action", "deny", {"amount": 7}),
        )


def test_run_application_probe_rejects_unknown_fault(fixture_root: Path):
    with pytest.raises(ProbeContractError):
        run_application_probe(
            fixture_root / "interceptor-failure",
            ProbeCase("bogus", "payments.refund", "not-a-real-fault", {}),
        )


def test_run_enforcement_probe_set_covers_the_standard_suite(fixture_root: Path):
    results = run_enforcement_probe_set(fixture_root / "interceptor-failure")
    assert len(results) == 5
    by_probe_id = {result.probe_id: result for result in results}
    assert set(by_probe_id) == {
        "deny",
        "transform",
        "crash",
        "timeout",
        "malformed-verdict",
    }

    # Only a completed, well-formed deny/transform report ever passes;
    # the interceptor-failure fixture's crash/timeout/malformed-verdict
    # faults are real enforcement gaps in this fixture (that is exactly
    # what the fixture's name means), and the standard suite must
    # surface all three as completed ENF-002 must-fix findings rather
    # than certifying a crashed/timed-out/malformed run as a pass.
    assert by_probe_id["deny"].status == "pass"
    assert by_probe_id["transform"].status == "pass"
    for probe_id in ("crash", "timeout", "malformed-verdict"):
        assert by_probe_id[probe_id].status == "must-fix"
        assert by_probe_id[probe_id].reason_code == "ENF-002"

    findings = findings_from_probes(results)
    assert [finding.finding_id for finding in findings] == ["ENF-002"] * 3


def test_unobservable_outcome_raises_probe_tooling_error(tmp_path: Path):
    # A dispatch reference that fails to import crashes the isolated
    # child before it ever calls the fixture's dispatch callable, so the
    # observation ledger never receives even a "start" record. Neither a
    # pass nor a specific finding can be proven from no evidence at all,
    # so this must raise ProbeToolingError rather than silently becoming
    # any kind of ProbeResult.
    root = tmp_path / "unobservable-target"
    (root / "governance").mkdir(parents=True)
    (root / "governance" / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "probes_test_nonexistent_module:dispatch_probe",
                "audit_sink": "probes_test_nonexistent_module:AUDIT_EVENTS",
                "timeout_ms": 100,
                "side_effect_mode": "synthetic",
                "observation_ledger": "governance/probe-ledger.jsonl",
                "actions": ["payments.refund"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProbeToolingError):
        run_application_probe(
            root, ProbeCase("deny", "payments.refund", "deny", {"amount": 7})
        )


def test_enforcement_probe_classification_is_stable_under_repeated_runs(
    fixture_root: Path,
):
    # Regression coverage for interpreter/import startup jitter being
    # charged against the probe contract's application-level
    # ``timeout_ms`` boundary: repeated runs of both the fast
    # (deny/transform) and slow (timeout) faults must classify
    # identically every time, never flake into the wrong bucket because
    # the child was merely slow to start.
    root = fixture_root / "interceptor-failure"
    for _ in range(10):
        results = run_enforcement_probe_set(root)
        by_probe_id = {result.probe_id: result for result in results}
        assert by_probe_id["deny"].status == "pass"
        assert by_probe_id["transform"].status == "pass"
        assert by_probe_id["crash"].status == "must-fix"
        assert by_probe_id["crash"].reason_code == "ENF-002"
        assert by_probe_id["timeout"].status == "must-fix"
        assert by_probe_id["timeout"].reason_code == "ENF-002"
        assert by_probe_id["malformed-verdict"].status == "must-fix"
        assert by_probe_id["malformed-verdict"].reason_code == "ENF-002"


def test_probe_run_leaves_no_stray_ledger_file(fixture_root: Path):
    root = fixture_root / "interceptor-failure"
    governance_dir = root / "governance"
    before = set(governance_dir.iterdir())
    run_application_probe(
        root, ProbeCase("deny", "payments.refund", "deny", {"amount": 7})
    )
    after = set(governance_dir.iterdir())
    assert after == before
