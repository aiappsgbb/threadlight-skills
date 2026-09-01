"""Tests for threadlight-governed-actions' hermetic enforcement probes
(Task 5).

Exercises ``probes.run_application_probe`` against the ``interceptor-
failure`` fixture: a synthetic ``app.agent`` dispatch seam exhibiting
deny, transform, crash, timeout, malformed-verdict, and several
deliberately negative (fail-open, self-report-mismatch) behaviors. Every
probe here runs the fixture's dispatch callable in a real isolated
subprocess — this file never mocks the dispatch seam itself, only
supplies the synthetic fixture it drives.

A crash, timeout, malformed (unparseable) output, or malformed verdict is
judged from the observation ledger exactly like a deny/transform outcome:
fail-closed (the ledger proves the tool was never reached) still passes.
Fail-open (the ledger proves the tool was reached anyway) is a completed
``ENF-002`` must-fix finding *only* for a fault whose own contract forbids
invocation on a normal completion (deny/crash/timeout/malformed-verdict);
for a transform-family fault, whose own contract expects invocation on a
normal completion, an incomplete run that reached the tool is truthfully
``not-verified`` instead — never a fabricated fail-open finding. ``ENF-001``
is reserved for a *completed*, well-formed run whose own self-reported
deny/transform decision contradicts what the ledger proves, and even then
only when that self-report is itself corroborated by a matching ledger
start/decision record and at least one audit id. An outcome that is
genuinely unobservable — the child failed and the ledger recorded nothing
at all, *or* the child completed but the ledger does not corroborate its
self-report — raises ``ProbeToolingError`` instead of any finding.

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
    ("probe_id", "fault", "expected"),
    [
        ("deny", "deny", "tool_not_invoked"),
        ("transform", "transform", "tool_received_transformed_arguments"),
        ("crash", "raise", "tool_not_invoked"),
        ("timeout", "sleep", "tool_not_invoked"),
        ("malformed-verdict", "invalid", "tool_not_invoked"),
    ],
)
def test_application_dispatch_enforces_probe(
    fixture_root: Path, probe_id: str, fault: str, expected: str
):
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(probe_id, "payments.refund", fault, {"amount": 7}),
    )
    assert result.expected == expected
    assert result.status == "pass"


def test_malformed_verdict_passes_when_ledger_proves_fail_closed(
    fixture_root: Path,
):
    # The fixture reports its malformed verdict as the literal
    # out-of-schema decision string, never normalized to a clean "deny"
    # — but the ledger proves the tool was never reached, so this is a
    # fail-closed outcome and still passes. A malformed/unparseable
    # verdict is judged the same way a crash or timeout is: by what the
    # ledger proves happened, not by whether the self-report happens to
    # look tidy.
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase("malformed-verdict", "payments.refund", "invalid", {"amount": 7}),
    )
    assert isinstance(result, ProbeResult)
    assert result.status == "pass"
    assert result.observed == "tool_not_invoked"

    assert findings_from_probes((result,)) == ()


def test_crash_timeout_and_malformed_verdict_fail_open_variants_are_enf_002(
    fixture_root: Path,
):
    # Fail-open variants of crash/timeout/malformed-verdict: the fixture
    # reaches the tool *first*, then crashes/hangs/reports a malformed
    # verdict. Proves ENF-002 is reachable for every abnormal-
    # termination shape whenever the ledger proves fail-open — not only
    # for the generic "fail_open" (crash-after-invoke) control case.
    root = fixture_root / "interceptor-failure"
    crash_fail_open = run_application_probe(
        root, ProbeCase("crash-fail-open", "payments.refund", "fail_open", {"amount": 7})
    )
    timeout_fail_open = run_application_probe(
        root,
        ProbeCase(
            "timeout-fail-open", "payments.refund", "timeout_fail_open", {"amount": 7}
        ),
    )
    malformed_fail_open = run_application_probe(
        root,
        ProbeCase(
            "malformed-fail-open",
            "payments.refund",
            "malformed_fail_open",
            {"amount": 7},
        ),
    )
    for result in (crash_fail_open, timeout_fail_open, malformed_fail_open):
        assert result.observed == "tool_invoked_despite_fault"
        assert result.status == "must-fix"
        assert result.reason_code == "ENF-002"

    findings = findings_from_probes(
        (crash_fail_open, timeout_fail_open, malformed_fail_open)
    )
    assert [finding.finding_id for finding in findings] == ["ENF-002"] * 3


def test_slow_correct_transform_timeout_is_not_verified_not_fabricated_fail_open(
    fixture_root: Path,
):
    # The fixture correctly applies the transform policy and reaches
    # the tool with the correctly transformed arguments, then hangs
    # past timeout_ms before it can ever report a decision. A
    # transform-family fault's own contract *expects* the tool to be
    # reached on a normal completion, so the ledger proving reach here
    # is not itself proof of fail-open: only a completed self-report
    # could confirm the transform was correct, and this run never
    # produced one. The truthful classification is "not-verified", not
    # a fabricated ENF-002.
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(
            "slow-correct-transform",
            "payments.refund",
            "slow_correct_transform",
            {"amount": 7},
        ),
    )
    assert result.status == "not-verified"
    assert result.reason_code != "ENF-002"
    assert result.reason_code != "ENF-001"
    assert result.observed != "tool_invoked_despite_fault"

    findings = findings_from_probes((result,))
    assert len(findings) == 1
    assert findings[0].status == "not-verified"
    assert findings[0].reason_code == result.reason_code


def test_ledger_less_self_report_raises_probe_tooling_error(fixture_root: Path):
    # The fixture returns a clean, well-formed "deny" report backed by
    # a real audit id, but never writes a single record to the
    # observation ledger — not even a "start" record. A self-report is
    # never trusted on its own, no matter how clean it looks: without a
    # correlated ledger start/decision record this is exactly as
    # unobservable as a crashed child that left no ledger evidence, and
    # must raise ProbeToolingError rather than ever becoming a pass.
    with pytest.raises(ProbeToolingError):
        run_application_probe(
            fixture_root / "interceptor-failure",
            ProbeCase("stub", "payments.refund", "stub", {"amount": 7}),
        )


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


def test_run_enforcement_probe_set_covers_the_standard_suite_and_all_pass(
    fixture_root: Path,
):
    results = run_enforcement_probe_set(fixture_root / "interceptor-failure")
    assert len(results) == 5
    assert {result.probe_id for result in results} == {
        "deny",
        "transform",
        "crash",
        "timeout",
        "malformed-verdict",
    }
    assert all(result.status == "pass" for result in results)
    assert findings_from_probes(results) == ()


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
    # the child was merely slow to start. The standard suite's
    # crash/timeout/malformed-verdict faults are all fail-closed in this
    # fixture, so they must consistently pass, never spuriously flip to
    # ENF-002 because a slow machine ate into the fault's own budget.
    root = fixture_root / "interceptor-failure"
    for _ in range(10):
        results = run_enforcement_probe_set(root)
        by_probe_id = {result.probe_id: result for result in results}
        assert by_probe_id["deny"].status == "pass"
        assert by_probe_id["transform"].status == "pass"
        assert by_probe_id["crash"].status == "pass"
        assert by_probe_id["timeout"].status == "pass"
        assert by_probe_id["malformed-verdict"].status == "pass"
        assert findings_from_probes(results) == ()


def test_probe_run_leaves_no_stray_ledger_file(fixture_root: Path):
    root = fixture_root / "interceptor-failure"
    governance_dir = root / "governance"
    before = set(governance_dir.iterdir())
    run_application_probe(
        root, ProbeCase("deny", "payments.refund", "deny", {"amount": 7})
    )
    after = set(governance_dir.iterdir())
    assert after == before
