"""Tests for threadlight-governed-actions' hermetic enforcement probes
(Task 5) and approval anti-replay, output mediation, and payload-free
audit probes (Task 6).

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

The approval/output/audit probes below exercise ``run_approval_probe``
against the ``approval-replay`` fixture (a synthetic, service-side
atomic nonce store), ``run_output_probe`` against the
``output-streaming`` fixture (a synthetic buffered/streaming output
mediator), and ``run_privacy_probe_set`` (a fixed, in-code sample of a
payload-free and a payload-bearing audit record). Every approval
scenario that proves the anti-replay/binding control worked — a
first-time acceptance, a replay, a mutated-field reuse, or an expired
attempt rejected before ever reaching the nonce store — is itself a
*passing* probe; only a genuine violation (the ledger proving a
non-atomic double acceptance) is ``must-fix`` with reason ``APR-001``.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_probes.py -q
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import probes
from contracts import Finding, ProbeResult
from probes import (
    ApprovalBinding,
    ProbeCase,
    ProbeContractError,
    ProbeToolingError,
    approval_digest,
    findings_from_probes,
    load_probe_contract,
    run_application_probe,
    run_approval_probe,
    run_enforcement_probe_set,
    run_output_probe,
    run_privacy_probe_set,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "references" / "finding-catalog.json"
)


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def approval_binding() -> ApprovalBinding:
    return ApprovalBinding(
        target_scope="account:synthetic-001",
        requesting_subject="subject:pseudonymous-requester",
        approving_subject="subject:pseudonymous-approver",
        approving_role="role:synthetic-reviewer",
        tenant="tenant:synthetic-001",
        policy_id="policy:refund-v1",
        policy_hash="sha256:" + "a" * 64,
        action_id="payments.refund",
        arguments={"amount": 7, "currency": "USD"},
        issued_at="2026-09-01T12:00:00Z",
        expires_at="2026-09-01T12:05:00Z",
        nonce="nonce-0001",
    )


@pytest.fixture
def approval_root(tmp_path: Path) -> Path:
    # A fresh, isolated copy of the checked-in fixture per test: the
    # anti-replay nonce ledger is deliberately *persistent* across
    # separate ``run_approval_probe`` calls within one test (that is
    # the whole point — atomic one-time redemption), so each test must
    # start from its own untouched nonce space rather than share one
    # with every other test in this module.
    destination = tmp_path / "approval-replay"
    shutil.copytree(FIXTURES_DIR / "approval-replay", destination)
    return destination


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

    # A "not-verified" probe never produces a finding — exactly like a
    # pass — since its outcome genuinely cannot be confirmed either way
    # and is never turned into an invented violation.
    assert findings_from_probes((result,)) == ()


def test_double_invoke_transform_is_enf_001_not_a_laundered_pass(fixture_root: Path):
    # The fixture invokes the tool *twice* (once raw, once correctly
    # transformed) but self-reports a single clean invocation with the
    # correctly transformed hash. The harness must count the ledger's
    # own invocation records for the action rather than trust the
    # self-reported count or only the last recorded hash, so this extra
    # raw invocation the fixture never admits to must never be
    # laundered into a clean pass.
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(
            "double-invoke-transform",
            "payments.refund",
            "double_invoke_transform",
            {"amount": 7},
        ),
    )
    assert result.status == "must-fix"
    assert result.reason_code == "ENF-001"
    assert result.observed not in (
        "tool_received_transformed_arguments",
        "tool_not_invoked",
    )

    findings = findings_from_probes((result,))
    assert [(finding.finding_id, finding.status) for finding in findings] == [
        ("ENF-001", "must-fix")
    ]


def test_raw_passthrough_claiming_transform_is_enf_001_for_reference_arguments(
    fixture_root: Path,
):
    # The fixture self-reports a clean "transform" decision, fully
    # corroborated by matching ledger start/decision records and a
    # real audit id, but never actually applies the transform policy
    # at all: the tool receives the raw arguments completely
    # unchanged. Self-report/ledger *consistency* alone is never proof
    # a transform happened — the ledger's own recorded original vs.
    # received argument hashes must actually differ. These are the
    # standard deterministic transform probe's reference arguments
    # ({"amount": 7}), which this harness's own fixed contract knows
    # require an actual change, so an unchanged hash here is a
    # definite ENF-001 violation, never merely unconfirmed.
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(
            "raw-passthrough-transform",
            "payments.refund",
            "raw_passthrough_transform",
            {"amount": 7},
        ),
    )
    assert result.status == "must-fix"
    assert result.reason_code == "ENF-001"
    assert result.observed not in (
        "tool_received_transformed_arguments",
        "tool_not_invoked",
    )

    findings = findings_from_probes((result,))
    assert [(finding.finding_id, finding.status) for finding in findings] == [
        ("ENF-001", "must-fix")
    ]


def test_raw_passthrough_claiming_transform_is_not_verified_for_arbitrary_arguments(
    fixture_root: Path,
):
    # The exact same self-report/ledger-consistent-but-unchanged-hash
    # shape as above, but for arguments this generic harness has no
    # fixed reference contract for (unlike the standard {"amount": 7}
    # case). Whether a no-op was actually required for these
    # particular arguments is a business-policy question this harness
    # never embeds, so the truthful outcome is "not-verified", never a
    # fabricated ENF-001.
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(
            "raw-passthrough-transform-arbitrary",
            "payments.refund",
            "raw_passthrough_transform",
            {"amount": 3},
        ),
    )
    assert result.status == "not-verified"
    assert result.reason_code != "ENF-001"
    assert result.reason_code != "ENF-002"

    assert findings_from_probes((result,)) == ()


def test_transform_missing_original_hash_raises_probe_tooling_error(
    fixture_root: Path,
):
    # The fixture correctly applies the transform policy and reaches
    # the tool with the right arguments, and every other ledger/self-
    # report field is fully consistent (matching start/decision
    # records, a real audit id, a single invocation whose received
    # hash matches the self-report) — but the ledger's own "invocation"
    # record for this action is missing the mandatory
    # "original_argument_hash" field entirely. Without that field the
    # harness has no way to prove whether the arguments actually
    # changed, so this incomplete evidence must never be laundered
    # into a pass (or even a truthful "not-verified" no-op finding)
    # just because everything else about the self-report looks clean —
    # it must raise ProbeToolingError instead.
    with pytest.raises(ProbeToolingError):
        run_application_probe(
            fixture_root / "interceptor-failure",
            ProbeCase(
                "transform-missing-original-hash",
                "payments.refund",
                "transform_missing_original_hash",
                {"amount": 7},
            ),
        )


def test_crash_before_transform_invoke_is_not_verified_never_a_finding(
    fixture_root: Path,
):
    # A transform-family fault that crashes *before* ever reaching the
    # tool proves nothing either way: reaching the tool is what a
    # correct run of this fault is supposed to do, so an incomplete run
    # that never even got that far can neither confirm a pass nor be
    # blamed for a violation it never demonstrably committed.
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(
            "crash-before-transform-invoke",
            "payments.refund",
            "crash_before_transform_invoke",
            {"amount": 7},
        ),
    )
    assert result.status == "not-verified"
    assert result.reason_code != "ENF-001"
    assert result.reason_code != "ENF-002"
    assert result.observed == "tool_not_invoked"

    assert findings_from_probes((result,)) == ()


def test_fail_closed_crash_and_timeout_passes_carry_visible_audit_evidence(
    fixture_root: Path,
):
    # A fail-closed crash/timeout pass is never granted on silence
    # alone: it must be backed by a visible operational audit signal
    # the ledger recorded. Both "raise" and "sleep" durably mirror an
    # audit event to the ledger immediately before crashing/hanging, so
    # a passing probe for either must carry nonempty evidence_refs.
    root = fixture_root / "interceptor-failure"
    crash = run_application_probe(
        root, ProbeCase("crash", "payments.refund", "raise", {"amount": 7})
    )
    timeout = run_application_probe(
        root, ProbeCase("timeout", "payments.refund", "sleep", {"amount": 7})
    )
    for result in (crash, timeout):
        assert result.status == "pass"
        assert result.evidence_refs


def test_silent_crash_with_no_audit_signal_raises_probe_tooling_error(
    fixture_root: Path,
):
    # The fixture crashes immediately after only a "start" record, with
    # no audit event recorded at all. A "start" record alone is not a
    # visible operational audit signal, so a pass must never be granted
    # on that silence — this must raise ProbeToolingError instead.
    with pytest.raises(ProbeToolingError):
        run_application_probe(
            fixture_root / "interceptor-failure",
            ProbeCase("silent-crash", "payments.refund", "silent_crash", {"amount": 7}),
        )


def _canonical_hash(value: object) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_transform_evidence_records_both_original_and_transformed_hashes(
    fixture_root: Path,
):
    # A transform pass's evidence must include both the hash of what
    # the tool actually received (the transformed arguments) and a hash
    # of the case's original, pre-transform arguments — payload-free,
    # never the raw values themselves — so a transform can be proven to
    # have actually changed something without ever needing the payload.
    original_arguments = {"amount": 7}
    transformed_arguments = {"amount": 5}
    expected_original_hash = _canonical_hash(original_arguments)
    expected_transformed_hash = _canonical_hash(transformed_arguments)

    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(
            "transform", "payments.refund", "transform", dict(original_arguments)
        ),
    )
    assert result.status == "pass"
    assert expected_original_hash != expected_transformed_hash
    assert expected_original_hash in result.evidence_refs
    assert expected_transformed_hash in result.evidence_refs


def test_load_probe_contract_rejects_observation_ledger_symlink_escape(
    tmp_path: Path,
):
    # An ``observation_ledger`` whose directory resolves — following a
    # symlink along the way — outside the target root must be rejected
    # outright as an unsafe contract, even though the lexical path
    # itself (``governance/probe-ledger.jsonl``) never contains ``..``.
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "governance").symlink_to(outside, target_is_directory=True)
    (outside / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:dispatch_probe",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "timeout_ms": 100,
                "side_effect_mode": "synthetic",
                "observation_ledger": "governance/probe-ledger.jsonl",
                "actions": ["payments.refund"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProbeContractError):
        load_probe_contract(root)


def test_probe_run_cleans_up_newly_created_nested_ledger_directories(
    tmp_path: Path,
):
    # A minimal, fully self-contained fixture whose contract's
    # ``observation_ledger`` points at a nested directory that does not
    # exist yet. The probe run must create it for the duration of the
    # run and remove it again afterward — leaving the pre-existing
    # ``governance`` directory (which holds the contract itself) and
    # everything else in the target root untouched.
    root = tmp_path / "cleanup-target"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "agent.py").write_text(
        '''
import json


AUDIT_EVENTS = []


def dispatch_probe(case, ledger_path):
    action_id = case["action_id"]
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event": "start", "action_id": action_id}) + "\\n"
        )
    AUDIT_EVENTS.append(
        {"audit_id": "audit-0001", "action_id": action_id, "decision": "deny"}
    )
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": "decision", "action_id": action_id, "decision": "deny"}
            )
            + "\\n"
        )
    return {
        "decision": "deny",
        "invocation_count": 0,
        "argument_hash": None,
        "exception_class": None,
    }
''',
        encoding="utf-8",
    )
    governance_dir = root / "governance"
    governance_dir.mkdir()
    (governance_dir / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:dispatch_probe",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "timeout_ms": 1000,
                "side_effect_mode": "synthetic",
                "observation_ledger": "governance/nested/deep/probe-ledger.jsonl",
                "actions": ["payments.refund"],
            }
        ),
        encoding="utf-8",
    )

    result = run_application_probe(
        root, ProbeCase("deny", "payments.refund", "deny", {"amount": 7})
    )
    assert result.status == "pass"

    assert not (governance_dir / "nested").exists()
    assert governance_dir.is_dir()
    assert (governance_dir / "probe-contract.json").is_file()


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


# --------------------------------------------------------------------
# Task 6: approval anti-replay, output mediation, and payload-free audit
# --------------------------------------------------------------------


def test_approval_binding_is_frozen(approval_binding: ApprovalBinding):
    with pytest.raises(FrozenInstanceError):
        approval_binding.nonce = "other"  # type: ignore[misc]


def test_approval_is_single_use_and_bound_to_canonical_action(
    approval_root: Path, approval_binding: ApprovalBinding
):
    approved = run_approval_probe(
        approval_root, approval_binding, now="2026-09-01T12:00:00Z"
    )
    replayed = run_approval_probe(
        approval_root, approval_binding, now="2026-09-01T12:00:01Z"
    )
    mutated = run_approval_probe(
        approval_root,
        replace(approval_binding, arguments={"amount": 8, "currency": "USD"}),
        now="2026-09-01T12:00:02Z",
    )
    assert [p.status for p in (approved, replayed, mutated)] == [
        "pass",
        "pass",
        "pass",
    ]
    assert approved.observed == "approval_accepted"
    assert replayed.observed == "replay_rejected"
    assert mutated.observed == "binding_mismatch_rejected"

    findings = findings_from_probes((approved, replayed, mutated))
    assert findings == ()


def test_approval_rejects_expired_binding(
    approval_root: Path, approval_binding: ApprovalBinding
):
    # A binding submitted after its own ``expires_at`` must be rejected
    # before the nonce store is ever consulted at all — a fresh, never-
    # before-seen nonce still fails, purely on expiry.
    expired_binding = replace(approval_binding, nonce="nonce-expired-0001")
    result = run_approval_probe(
        approval_root, expired_binding, now="2026-09-01T12:05:01Z"
    )
    assert result.status == "pass"
    assert result.observed == "expired_rejected"
    assert findings_from_probes((result,)) == ()


def test_approval_binding_at_exact_expiry_instant_is_rejected(
    approval_root: Path, approval_binding: ApprovalBinding
):
    # ``now`` equal to (not just past) ``expires_at`` is still expired —
    # an approval window is exclusive of its own expiry instant.
    boundary_binding = replace(approval_binding, nonce="nonce-boundary-0001")
    result = run_approval_probe(
        approval_root, boundary_binding, now=boundary_binding.expires_at
    )
    assert result.status == "pass"
    assert result.observed == "expired_rejected"


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("requesting_subject", "subject:attacker"),
        ("approving_subject", "subject:rogue-approver"),
        ("approving_role", "role:unauthorized"),
        ("target_scope", "account:synthetic-999"),
        ("tenant", "tenant:synthetic-999"),
        ("policy_id", "policy:refund-v2"),
        ("policy_hash", "sha256:" + "b" * 64),
        ("action_id", "payments.transfer"),
        ("arguments", {"amount": 999, "currency": "USD"}),
    ],
)
def test_approval_rejects_every_mutated_field_reusing_the_same_nonce(
    approval_root: Path,
    approval_binding: ApprovalBinding,
    field: str,
    new_value: object,
):
    # "Mutated action" per the design spec: an approval for one
    # canonical action hash cannot authorize changed arguments, target,
    # tenant, actor, policy version, or (here) action itself. Every one
    # of subject/role/target/tenant/policy/action/args is exercised as
    # its own mutation reusing the exact same, already-consumed nonce.
    approved = run_approval_probe(
        approval_root, approval_binding, now="2026-09-01T12:00:00Z"
    )
    mutated = run_approval_probe(
        approval_root,
        replace(approval_binding, **{field: new_value}),
        now="2026-09-01T12:00:01Z",
    )
    assert approved.status == "pass"
    assert approved.observed == "approval_accepted"
    assert mutated.status == "pass"
    assert mutated.observed == "binding_mismatch_rejected"


def test_approval_digest_ignores_argument_key_order(
    approval_binding: ApprovalBinding,
):
    reordered = replace(
        approval_binding, arguments={"currency": "USD", "amount": 7}
    )
    # Proves the two bindings' arguments genuinely differ in iteration
    # order (not merely comparing incomparable types, which would be
    # true regardless of order and prove nothing) while remaining
    # value-equal as mappings — making the digest equality below a real
    # proof of key-order independence.
    assert list(approval_binding.arguments.items()) != list(
        reordered.arguments.items()
    )
    assert dict(approval_binding.arguments) == dict(reordered.arguments)
    assert approval_digest(approval_binding) == approval_digest(reordered)


def test_approval_digest_changes_when_any_bound_field_changes(
    approval_binding: ApprovalBinding,
):
    baseline = approval_digest(approval_binding)
    for field, new_value in (
        ("target_scope", "account:synthetic-999"),
        ("requesting_subject", "subject:attacker"),
        ("approving_subject", "subject:rogue-approver"),
        ("approving_role", "role:unauthorized"),
        ("tenant", "tenant:synthetic-999"),
        ("policy_id", "policy:refund-v2"),
        ("policy_hash", "sha256:" + "b" * 64),
        ("action_id", "payments.transfer"),
        ("arguments", {"amount": 999, "currency": "USD"}),
        ("issued_at", "2026-09-01T12:00:01Z"),
        ("expires_at", "2026-09-01T12:05:01Z"),
        ("nonce", "nonce-9999"),
    ):
        mutated = replace(approval_binding, **{field: new_value})
        assert approval_digest(mutated) != baseline, field


def test_approval_binds_to_transformed_arguments_not_raw_arguments(
    approval_root: Path, approval_binding: ApprovalBinding
):
    # "Transform-before-approval binding": when a pre-action seam
    # transforms arguments before requesting approval, the approval
    # must be bound to the already-transformed (final) arguments, never
    # to the original raw ones — an attempt to redeem the same nonce
    # with the pre-transform raw arguments must be rejected exactly
    # like any other mutated-field reuse.
    transformed_binding = replace(
        approval_binding,
        arguments={"amount": 5, "currency": "USD"},
        nonce="nonce-transform-0001",
    )
    approved = run_approval_probe(
        approval_root, transformed_binding, now="2026-09-01T12:00:00Z"
    )
    raw_binding = replace(transformed_binding, arguments={"amount": 7, "currency": "USD"})
    raw_replay = run_approval_probe(
        approval_root, raw_binding, now="2026-09-01T12:00:01Z"
    )
    assert approved.status == "pass"
    assert approved.observed == "approval_accepted"
    assert raw_replay.status == "pass"
    assert raw_replay.observed == "binding_mismatch_rejected"


def test_approval_probe_reports_apr_001_when_ledger_proves_non_atomic_reuse(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # A deliberately broken nonce store: it always appends a fresh
    # record instead of atomically rejecting an already-consumed
    # nonce. ``run_approval_probe`` must never trust the fixture's own
    # silence about this — it independently proves the violation from
    # the ledger's own contents (more records after the call than
    # before, for the same nonce) and reports the catalog's ``APR-001``
    # must-fix finding rather than a laundered pass.
    root = tmp_path / "broken-nonce-store"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def redeem(nonce, digest, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"nonce": nonce, "digest": digest}) + "\\n")
''',
        encoding="utf-8",
    )
    governance_dir = root / "governance"
    governance_dir.mkdir()
    (governance_dir / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:redeem",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "nonce_ledger": "governance/nonce-ledger.jsonl",
            }
        ),
        encoding="utf-8",
    )

    approved = run_approval_probe(root, approval_binding, now="2026-09-01T12:00:00Z")
    replayed = run_approval_probe(root, approval_binding, now="2026-09-01T12:00:01Z")

    assert approved.status == "pass"
    assert replayed.status == "must-fix"
    assert replayed.reason_code == "APR-001"
    assert replayed.observed == "nonce_reuse_not_atomic"

    findings = findings_from_probes((approved, replayed))
    assert [(f.finding_id, f.status) for f in findings] == [("APR-001", "must-fix")]


def test_output_is_buffered_until_output_verdict(fixture_root: Path):
    result = run_output_probe(fixture_root / "output-streaming", verdict="deny")
    assert result.status == "pass"
    assert result.observed == "zero_bytes_egressed"
    assert findings_from_probes((result,)) == ()


def test_output_probe_allows_buffered_release_after_verdict(fixture_root: Path):
    result = run_output_probe(fixture_root / "output-streaming", verdict="allow")
    assert result.status == "pass"
    assert result.observed == "buffered_release_after_verdict"


def test_output_probe_passes_when_stream_is_chunk_mediated_within_declared_bound(
    fixture_root: Path,
):
    result = run_output_probe(fixture_root / "output-streaming", verdict="stream")
    assert result.status == "pass"
    assert result.observed == "chunk_mediated_within_bound"
    assert findings_from_probes((result,)) == ()


def test_output_probe_rejects_incremental_stream_without_declared_bound(
    tmp_path: Path,
):
    # Incremental output only ever passes with an *explicit*, nonzero
    # exposure bound and evidenced chunk-level mediation declared in
    # the probe contract; a contract that omits both must reject a
    # "stream" verdict as OUT-001 without ever even invoking the
    # dispatch callable (a raise here would fail the test if it were).
    root = tmp_path / "output-missing-bound"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    raise AssertionError(
        "dispatch must never be invoked for an undeclared exposure bound"
    )
''',
        encoding="utf-8",
    )
    governance_dir = root / "governance"
    governance_dir.mkdir()
    (governance_dir / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:emit_output",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "observation_ledger": "governance/output-ledger.jsonl",
            }
        ),
        encoding="utf-8",
    )

    result = run_output_probe(root, verdict="stream")
    assert result.status == "must-fix"
    assert result.reason_code == "OUT-001"
    assert result.observed == "incremental_output_without_declared_bound"

    findings = findings_from_probes((result,))
    assert [(f.finding_id, f.status) for f in findings] == [("OUT-001", "must-fix")]


def test_output_probe_rejects_oversized_unmediated_chunk_release(tmp_path: Path):
    # A contract that *does* declare a bound and chunk mediation, but
    # whose fixture actually releases an oversized, unmediated chunk
    # anyway, must still be caught: the harness independently verifies
    # the ledger's own chunk evidence against the declared bound rather
    # than trusting the contract's declaration alone.
    root = tmp_path / "output-oversized-chunk"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event": "verdict_received", "verdict": verdict}) + "\\n"
        )
        handle.write(
            json.dumps({"event": "chunk", "bytes": 64, "mediated": False}) + "\\n"
        )
        handle.write(json.dumps({"event": "egress", "bytes": 64}) + "\\n")
''',
        encoding="utf-8",
    )
    governance_dir = root / "governance"
    governance_dir.mkdir()
    (governance_dir / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:emit_output",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "observation_ledger": "governance/output-ledger.jsonl",
                "exposure_bound_bytes": 16,
                "chunk_mediation": True,
            }
        ),
        encoding="utf-8",
    )

    result = run_output_probe(root, verdict="stream")
    assert result.status == "must-fix"
    assert result.reason_code == "OUT-001"
    assert result.observed == "unmediated_or_oversized_chunk_release"


def test_output_probe_rejects_unknown_verdict(fixture_root: Path):
    with pytest.raises(ProbeContractError):
        run_output_probe(fixture_root / "output-streaming", verdict="bogus")


def test_audit_probe_rejects_payload_bearing_record(fixture_root: Path):
    findings = findings_from_probes(run_privacy_probe_set(fixture_root))
    assert [(f.finding_id, f.status) for f in findings] == [("AUD-001", "must-fix")]


def test_audit_probe_passes_payload_free_record(fixture_root: Path):
    results = run_privacy_probe_set(fixture_root)
    passing = [result for result in results if result.status == "pass"]
    assert passing
    assert all(result.observed == "payload_free_audit_record" for result in passing)
    assert findings_from_probes(tuple(passing)) == ()


def test_output_probe_never_unconditionally_passes_allow_before_verdict(
    tmp_path: Path,
):
    # An "allow" verdict releasing output *before* its own governing
    # verdict is ever recorded to the ledger must never be an
    # unconditional pass: ``run_output_probe`` proves no egress/chunk
    # event precedes the ledger's own ``verdict_received`` record,
    # using the ledger's event ordering, not just its final tallies.
    root = tmp_path / "output-premature-allow"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "egress", "bytes": 96}) + "\\n")
        handle.write(
            json.dumps({"event": "verdict_received", "verdict": verdict}) + "\\n"
        )
''',
        encoding="utf-8",
    )
    governance_dir = root / "governance"
    governance_dir.mkdir()
    (governance_dir / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:emit_output",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "observation_ledger": "governance/output-ledger.jsonl",
            }
        ),
        encoding="utf-8",
    )

    result = run_output_probe(root, verdict="allow")
    assert result.status == "must-fix"
    assert result.reason_code == "OUT-001"
    assert result.observed == "output_released_before_verdict"


def test_output_probe_treats_leaked_chunk_bytes_as_egress_on_deny(tmp_path: Path):
    # A buggy fixture that reports zero bytes on its "egress" event
    # while still leaking the actual output through a "chunk" event
    # must not be laundered into a pass: chunk bytes count toward the
    # released total exactly like egress bytes do.
    root = tmp_path / "output-deny-chunk-leak"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event": "verdict_received", "verdict": verdict}) + "\\n"
        )
        handle.write(
            json.dumps({"event": "chunk", "bytes": 40, "mediated": True}) + "\\n"
        )
        handle.write(json.dumps({"event": "egress", "bytes": 0}) + "\\n")
''',
        encoding="utf-8",
    )
    governance_dir = root / "governance"
    governance_dir.mkdir()
    (governance_dir / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:emit_output",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "observation_ledger": "governance/output-ledger.jsonl",
            }
        ),
        encoding="utf-8",
    )

    result = run_output_probe(root, verdict="deny")
    assert result.status == "must-fix"
    assert result.reason_code == "OUT-001"
    assert result.observed == "nonzero_bytes_egressed_on_deny"


def test_approval_probe_reports_aud_001_when_audit_sink_leaks_payload(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # The nonce store itself behaves correctly (atomic, one-time
    # redemption identical to the checked-in fixture), but its audit
    # sink leaks a raw argument payload. ``run_approval_probe`` must
    # actually validate the drained ``AUDIT_EVENTS`` through the
    # payload-free auditor rather than treating a declared
    # ``audit_sink`` as a dead, unchecked seam — and the audit
    # violation must override whatever the anti-replay evidence would
    # otherwise have concluded.
    root = tmp_path / "approval-leaky-audit"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import fcntl
import json
import os

AUDIT_EVENTS = []


def redeem(nonce, digest, ledger_path):
    with open(ledger_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            already_seen = any(
                json.loads(line).get("nonce") == nonce
                for line in handle.read().splitlines()
                if line.strip()
            )
            if not already_seen:
                handle.write(json.dumps({"nonce": nonce, "digest": digest}) + "\\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    AUDIT_EVENTS.append(
        {
            "audit_id": f"audit-approval-{nonce}",
            "event": "approval_redemption_attempt",
            "arguments": {"amount": 7, "currency": "USD"},
        }
    )
''',
        encoding="utf-8",
    )
    governance_dir = root / "governance"
    governance_dir.mkdir()
    (governance_dir / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:redeem",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "nonce_ledger": "governance/nonce-ledger.jsonl",
            }
        ),
        encoding="utf-8",
    )

    result = run_approval_probe(root, approval_binding, now="2026-09-01T12:00:00Z")
    assert result.status == "must-fix"
    assert result.reason_code == "AUD-001"
    assert result.observed == "payload_bearing_audit_event"


def test_output_probe_reports_aud_001_when_audit_sink_leaks_payload(tmp_path: Path):
    # Otherwise-correct, well-ordered output mediation (verdict
    # recorded before the denial's zero-byte egress) is still a
    # violation when the audit trail it produced leaks raw output
    # content.
    root = tmp_path / "output-leaky-audit"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event": "verdict_received", "verdict": verdict}) + "\\n"
        )
        handle.write(json.dumps({"event": "egress", "bytes": 0}) + "\\n")

    AUDIT_EVENTS.append(
        {
            "audit_id": "audit-output-deny",
            "event": "output_mediation_decision",
            "output": "denied content",
        }
    )
''',
        encoding="utf-8",
    )
    governance_dir = root / "governance"
    governance_dir.mkdir()
    (governance_dir / "probe-contract.json").write_text(
        json.dumps(
            {
                "dispatch": "app.agent:emit_output",
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "observation_ledger": "governance/output-ledger.jsonl",
            }
        ),
        encoding="utf-8",
    )

    result = run_output_probe(root, verdict="deny")
    assert result.status == "must-fix"
    assert result.reason_code == "AUD-001"
    assert result.observed == "payload_bearing_audit_event"


def test_finding_templates_agree_with_catalog_plane_for_shared_ids():
    # The design's finding-ID taxonomy (design spec §13.2) is the
    # single source of truth for each finding's "plane". This proves
    # ``probes._FINDING_TEMPLATES`` and the checked-in
    # ``references/finding-catalog.json`` never silently drift apart
    # on that fixed classification for any finding id both define.
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog_planes = {
        entry["finding_id"]: entry["plane"] for entry in catalog["findings"]
    }
    shared_ids = set(catalog_planes) & set(probes._FINDING_TEMPLATES)
    assert shared_ids  # sanity: the two sources do overlap
    mismatches = {
        finding_id: (probes._FINDING_TEMPLATES[finding_id]["plane"], catalog_planes[finding_id])
        for finding_id in shared_ids
        if probes._FINDING_TEMPLATES[finding_id]["plane"] != catalog_planes[finding_id]
    }
    assert mismatches == {}
