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
mediator), and ``run_privacy_probe_set`` (which drives one fixed,
synthetic dispatch call of its own against the *same real* target and
independently validates whatever its own ``audit_sink`` actually
produced). Every approval scenario that proves the anti-replay/binding
control worked — a first-time acceptance, a replay, a mutated-field
reuse, or an expired binding correctly rejected by the target's own
recorded decision — is itself a *passing* probe; only a genuine
violation (the ledger failing to durably record exactly one new
decision, a decision directly reporting a fail-open acceptance of a
replay or mutated or expired binding, or any tool-invocation evidence
recorded despite — or without — a matching acceptance decision) is
``must-fix`` with reason ``APR-001``. Every rejection, including
expiry, is always dispatched to the target and proven from its own
durable ledger evidence — never short-circuited by the probe's own
Python-side judgment before the target is ever consulted.
Payload-freeness of the decision-audit trail (``AUD-001``) is judged
*only* by ``run_privacy_probe_set``, never by ``run_approval_probe`` or
``run_output_probe`` themselves, so an audit violation can never mask,
or be masked by, either probe's own independent finding. Both
``redeem``/``emit_output`` dispatch calls always run isolated in a
sanitized, bounded child subprocess, exactly like Task 5's own
enforcement-probe dispatch, so a hung or crashed target can never hang
or kill the assessor.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_probes.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import inventory
import maf_adapter
import mediation
import probes
from contracts import Finding, ProbeResult, UnsafeTargetError
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
    run_staging_canary,
    validate_post_deploy_target,
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
    destination = tmp_path / "conformant-maf"
    shutil.copytree(FIXTURES_DIR / "conformant-maf", destination)
    return destination


def test_probe_case_is_frozen():
    case = ProbeCase("deny", "payments.refund", "deny", {"amount": 7})
    with pytest.raises(FrozenInstanceError):
        case.probe_id = "other"  # type: ignore[misc]


def test_load_probe_contract_reads_exact_contract_fields(fixture_root: Path):
    contract = load_probe_contract(fixture_root / "conformant-maf")
    assert contract["dispatch"] == "app.agent:dispatch"
    assert contract["execution_dispatch"] == "app.agent:dispatch_execution_path"
    assert contract["audit_sink"] == "app.agent:AUDIT_EVENTS"
    assert contract["timeout_ms"] == 500
    assert contract["side_effect_mode"] == "synthetic"
    assert contract["observation_ledger"] == "governance/probe-ledger.jsonl"
    assert contract["actions"] == ("payments.refund",)
    assert len(contract["execution_paths"]) == 10
    assert all(
        set(binding) == {"action_id", "mode", "path_id"}
        for binding in contract["execution_paths"]
    )


def _discovered_fixture_paths(root: Path):
    actions = inventory.build_action_inventory(root).actions
    return mediation.build_mediation_graph(root, actions, maf_adapter.MAFAdapter()).paths


def test_execution_path_bindings_match_assessor_discovery(fixture_root: Path):
    root = fixture_root / "conformant-maf"
    contract = load_probe_contract(root)

    bindings = probes.validate_execution_paths(
        contract, _discovered_fixture_paths(root)
    )

    assert {
        (binding["action_id"], binding["mode"], binding["path_id"])
        for binding in bindings
    } == {
        (path.action_id, path.mode, path.path_id)
        for path in _discovered_fixture_paths(root)
        if path.discovered
    }


def test_execution_path_probes_exercise_allow_and_deny_receipts(
    fixture_root: Path,
):
    root = fixture_root / "conformant-maf"
    paths = _discovered_fixture_paths(root)

    results = probes.run_execution_path_probe_set(root, paths)

    assert len(results) == 2 * len([path for path in paths if path.discovered])
    denied = [result for result in results if result.probe_id == "path-dispatch-deny"]
    assert len(denied) == len(paths)
    assert {result.status for result in denied} == {"pass"}
    assert {result.observed for result in denied} == {
        "deny_decision_without_invocation"
    }
    assert all(
        {"path-resolved-function", "path-pre-action-decision"}
        <= {item.kind for item in result.evidence_items}
        for result in denied
    )


def test_async_execution_path_uses_awaitable_synthetic_namespaces(
    tmp_path: Path,
):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "agent.py").write_text(
        """
class _AsyncNamespace:
    async def pre_tool_call(self, **kwargs):
        return {"decision": "allow"}

    async def invoke(self, *args, **kwargs):
        return {"cancelled": True}

agent_hooks = _AsyncNamespace()
tool_service = _AsyncNamespace()
AUDIT_EVENTS = []

async def interactive_orders_cancel(order_id):
    await agent_hooks.pre_tool_call(
        action_id="orders.cancel", mode="interactive"
    )
    await agent_hooks.require_approval(
        action_id="orders.cancel", mode="interactive"
    )
    result = await tool_service.invoke("orders.cancel", order_id=order_id)
    await audit_sink.record(action_id="orders.cancel", mode="interactive")
    await agent_hooks.post_tool_call(
        action_id="orders.cancel", mode="interactive"
    )
    return result

EXECUTION_ROUTES = {
    ("orders.cancel", "interactive"): interactive_orders_cancel,
}

def dispatch_execution_path(action_id, mode, case, ledger_path):
    target = EXECUTION_ROUTES[(action_id, mode)]
    resolved_path = f"{target.__module__}:{target.__qualname__}"
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(__import__("json").dumps({
            "event": "resolved_path",
            "evidence_id": f"path-resolved-{case['path_id']}",
            "action_id": action_id,
            "mode": mode,
            "path_id": case["path_id"],
            "resolved_path": resolved_path,
        }) + "\\n")
        handle.flush()
        __import__("os").fsync(handle.fileno())
    return {
        "action_id": action_id,
        "mode": mode,
        "resolved_path": resolved_path,
        "result": target(order_id=case["arguments"]["order_id"]),
    }

def dispatch(case, ledger_path):
    return {
        "decision": "deny",
        "invocation_count": 0,
        "argument_hash": None,
        "exception_class": None,
    }
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "agent.yaml").write_text(
        """
tools:
  - id: orders.cancel
    consequence: write
    execution_modes: [interactive]
    provider_hosted: false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "governance").mkdir()
    actions = inventory.build_action_inventory(tmp_path).actions
    paths = mediation.build_mediation_graph(
        tmp_path, actions, maf_adapter.MAFAdapter()
    ).paths
    interactive_path = next(path for path in paths if path.mode == "interactive")
    (tmp_path / "governance" / "probe-contract.json").write_text(
        json.dumps(
            {
                "actions": ["orders.cancel"],
                "audit_sink": "app.agent:AUDIT_EVENTS",
                "dispatch": "app.agent:dispatch",
                "execution_dispatch": "app.agent:dispatch_execution_path",
                "execution_paths": [
                    {
                        "action_id": "orders.cancel",
                        "mode": "interactive",
                        "path_id": interactive_path.path_id,
                    }
                ],
                "observation_ledger": "governance/probe-ledger.jsonl",
                "side_effect_mode": "synthetic",
                "timeout_ms": 500,
            }
        ),
        encoding="utf-8",
    )

    receipts = probes.run_execution_path_probe_set(tmp_path, paths)
    updated, findings = mediation.apply_execution_receipts(paths, receipts)

    assert next(path for path in updated if path.mode == "interactive").status == "pass"
    assert all(
        finding.finding_id != "MED-001"
        for finding in findings
        if interactive_path.path_id in finding.affected_paths
    )


def test_execution_path_runner_preserves_partial_results_on_child_start_failure(
    fixture_root: Path, monkeypatch
):
    root = fixture_root / "conformant-maf"
    paths = _discovered_fixture_paths(root)
    calls = 0

    def _dispatch(_root, _contract, binding, _target_ledger, decision):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ProbeToolingError("synthetic child start failure")
        event = {
            "event": "pre_action_decision",
            "evidence_id": f"decision-{calls}",
            "action_id": binding["action_id"],
            "mode": binding["mode"],
            "path_id": binding["path_id"],
            "decision": decision,
        }
        events = [event]
        if decision == "allow":
            events.append(
                {
                    "event": "invocation",
                    "evidence_id": f"invocation-{calls}",
                    "action_id": binding["action_id"],
                    "mode": binding["mode"],
                    "path_id": binding["path_id"],
                }
            )
        return {"events": events, "child_error": None}

    monkeypatch.setattr(probes, "_dispatch_path_child", _dispatch)

    with pytest.raises(probes.PartialProbeToolingError) as caught:
        probes.run_execution_path_probe_set(root, paths)

    assert len(caught.value.partial_results) == 2


def test_execution_path_runner_reports_proof_pipe_creation_failure(
    fixture_root: Path,
    monkeypatch,
):
    root = fixture_root / "conformant-maf"
    paths = _discovered_fixture_paths(root)

    def fail_pipe():
        raise OSError("synthetic pipe exhaustion")

    monkeypatch.setattr(probes.os, "pipe", fail_pipe)

    with pytest.raises(probes.PartialProbeToolingError, match="proof channel"):
        probes.run_execution_path_probe_set(root, paths)


def test_execution_path_proof_decoder_ignores_non_ascii_nonce_event():
    proof_nonce = "a" * 64
    malformed = {
        "event": "pre_action_decision",
        "evidence_id": "target-malformed-nonce",
        "proof_nonce": "café",
    }
    valid = {
        "event": "routing_target",
        "evidence_id": "assessor-valid",
        "proof_nonce": proof_nonce,
    }
    raw = b"\n".join(
        json.dumps(event, ensure_ascii=False).encode("utf-8")
        for event in (malformed, valid)
    )

    assert probes._decode_path_proof_events(raw, proof_nonce) == [valid]


@pytest.mark.skipif(os.name != "posix", reason="proof pipes are POSIX-only")
def test_proof_pipe_drain_deadline_survives_inherited_writer():
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - assertions run in the parent
        try:
            os.close(read_fd)
            os.write(write_fd, b'{"event":"complete"}\n')
            time.sleep(1.0)
        except OSError:
            pass
        finally:
            try:
                os.close(write_fd)
            finally:
                os._exit(0)

    os.close(write_fd)
    started = time.monotonic()
    try:
        with pytest.raises(probes.ProofChannelReadError, match="deadline") as caught:
            probes._read_all_fd(read_fd)
        drain_elapsed = time.monotonic() - started
    finally:
        os.close(read_fd)
        os.waitpid(pid, 0)

    assert drain_elapsed < 0.75
    assert caught.value.partial_bytes == b'{"event":"complete"}\n'
    assert caught.value.reason == "timeout"


@pytest.mark.skipif(os.name != "posix", reason="proof pipes are POSIX-only")
def test_proof_pipe_drain_caps_oversized_stream_memory():
    max_bytes = probes._MAX_PATH_PROOF_BYTES
    read_fd, write_fd = os.pipe()
    complete_event = b'{"event":"complete"}\n'
    payload = complete_event + b"x" * (max_bytes + 1)

    def _write_oversized_stream() -> None:
        view = memoryview(payload)
        try:
            while view:
                written = os.write(write_fd, view)
                view = view[written:]
        except BrokenPipeError:
            pass
        finally:
            os.close(write_fd)

    writer = threading.Thread(target=_write_oversized_stream)
    writer.start()
    try:
        with pytest.raises(probes.ProofChannelReadError, match="byte limit") as caught:
            probes._read_all_fd(read_fd)
    finally:
        os.close(read_fd)
        writer.join(timeout=1.0)

    assert not writer.is_alive()
    assert caught.value.reason == "oversized"
    assert len(caught.value.partial_bytes) <= max_bytes
    assert caught.value.partial_bytes.startswith(complete_event)


@pytest.mark.skipif(os.name != "posix", reason="proof pipes are POSIX-only")
def test_partial_proof_drain_keeps_complete_events_as_not_verified(
    fixture_root: Path, tmp_path: Path, monkeypatch
):
    root = fixture_root / "conformant-maf"
    contract = load_probe_contract(root)
    binding = contract["execution_paths"][0]
    original_read = probes._read_all_fd

    def _fail_after_complete_read(fd: int) -> bytes:
        raw = original_read(fd)
        raise probes.ProofChannelReadError(
            "synthetic proof drain deadline",
            reason="timeout",
            partial_bytes=raw,
        )

    monkeypatch.setattr(probes, "_read_all_fd", _fail_after_complete_read)
    outcome = probes._dispatch_path_child(
        root.resolve(),
        contract,
        binding,
        tmp_path / "target-ledger.jsonl",
        "allow",
    )
    result = probes._build_path_probe_result(
        binding,
        outcome,
        "assessor:execution-path-proof-channel",
        "path-dispatch-allow",
    )

    assert outcome["child_error"] == "proof_timeout"
    assert outcome["events"]
    assert result.status == "not-verified"
    assert result.evidence_items


def test_execution_path_binding_rejects_target_path_id_lie(
    approval_root: Path,
):
    contract_path = approval_root / "governance" / "probe-contract.json"
    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    raw["execution_paths"][0]["path_id"] = "target-declared-lie"
    contract_path.write_text(json.dumps(raw), encoding="utf-8")
    contract = load_probe_contract(approval_root)

    with pytest.raises(ProbeContractError, match="does not match assessor-discovered"):
        probes.validate_execution_paths(
            contract, _discovered_fixture_paths(approval_root)
        )


def test_execution_path_binding_rejects_per_path_dispatch_callable(
    approval_root: Path,
):
    contract_path = approval_root / "governance" / "probe-contract.json"
    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    raw["execution_paths"][0]["dispatch"] = "app.agent:dispatch"
    contract_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ProbeContractError, match="must contain exactly"):
        load_probe_contract(approval_root)


def test_execution_path_binding_rejects_duplicate_path(
    approval_root: Path,
):
    contract_path = approval_root / "governance" / "probe-contract.json"
    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    raw["execution_paths"].append(dict(raw["execution_paths"][0]))
    contract_path.write_text(json.dumps(raw), encoding="utf-8")
    contract = load_probe_contract(approval_root)

    with pytest.raises(ProbeContractError, match="duplicate execution path binding"):
        probes.validate_execution_paths(
            contract, _discovered_fixture_paths(approval_root)
        )


def test_execution_path_binding_rejects_unlisted_discovered_path(
    approval_root: Path,
):
    contract_path = approval_root / "governance" / "probe-contract.json"
    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    raw["execution_paths"].pop()
    contract_path.write_text(json.dumps(raw), encoding="utf-8")
    contract = load_probe_contract(approval_root)

    with pytest.raises(ProbeContractError, match="unlisted assessor-discovered path"):
        probes.validate_execution_paths(
            contract, _discovered_fixture_paths(approval_root)
        )


@pytest.mark.parametrize("value", [None, "", "not-importable"])
def test_load_probe_contract_rejects_missing_or_malformed_execution_dispatch(
    approval_root: Path,
    value: object,
):
    contract_path = approval_root / "governance" / "probe-contract.json"
    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    if value is None:
        raw.pop("execution_dispatch", None)
    else:
        raw["execution_dispatch"] = value
    contract_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ProbeContractError, match="execution_dispatch"):
        load_probe_contract(approval_root)


def _path_event(
    binding: dict[str, str],
    event: str,
    evidence_id: str,
    **fields: object,
) -> dict[str, object]:
    return {
        "event": event,
        "evidence_id": evidence_id,
        "action_id": binding["action_id"],
        "mode": binding["mode"],
        "path_id": binding["path_id"],
        **fields,
    }


def test_execution_path_deny_with_any_invocation_is_fail_open():
    binding = {
        "action_id": "payments.refund",
        "mode": "background",
        "path_id": "path-1",
    }
    outcome = {
        "child_error": None,
        "resolved_path": "app.agent:background_payments_refund",
        "expected_resolved_path": "app.agent:background_payments_refund",
        "events": [
            _path_event(
                binding,
                "routing_target",
                "routing-1",
                expected_resolved_path="app.agent:background_payments_refund",
            ),
            _path_event(
                binding,
                "resolved_path",
                "resolved-1",
                resolved_path="app.agent:background_payments_refund",
            ),
            _path_event(
                binding,
                "pre_action_decision",
                "decision-1",
                decision="deny",
            ),
            _path_event(binding, "invocation", "invocation-1"),
        ],
    }

    result = probes._build_path_probe_result(
        binding, outcome, "governance/probe-ledger.jsonl", "path-dispatch-deny"
    )

    assert result.status == "must-fix"
    assert result.reason_code == "ENF-002"
    assert result.observed == "tool_invoked_despite_deny"


@pytest.mark.parametrize(
    ("child_error", "reason_code"),
    [
        ("startup_failed", "path-dispatch-startup-failed"),
        ("timeout", "path-dispatch-timeout"),
        ("nonzero_exit", "path-dispatch-nonzero-exit"),
        ("malformed_output", "path-dispatch-malformed-output"),
    ],
)
def test_execution_path_child_errors_remain_not_verified_without_ledger_bypass(
    child_error: str,
    reason_code: str,
):
    binding = {
        "action_id": "payments.refund",
        "mode": "background",
        "path_id": "path-1",
    }
    resolved_path = "app.agent:background_payments_refund"
    outcome = {
        "child_error": child_error,
        "resolved_path": resolved_path,
        "expected_resolved_path": resolved_path,
        "events": [
            _path_event(
                binding,
                "routing_target",
                "routing-1",
                expected_resolved_path=resolved_path,
            ),
            _path_event(
                binding,
                "resolved_path",
                "resolved-1",
                resolved_path=resolved_path,
            )
        ],
    }

    result = probes._build_path_probe_result(
        binding, outcome, "governance/probe-ledger.jsonl", "path-dispatch-allow"
    )

    assert result.status == "not-verified"
    assert result.reason_code == reason_code


def test_execution_path_startup_failure_without_ledger_reflects_child_error():
    binding = {
        "action_id": "payments.refund",
        "mode": "background",
        "path_id": "path-1",
    }

    result = probes._build_path_probe_result(
        binding,
        {
            "child_error": "startup_failed",
            "resolved_path": None,
            "expected_resolved_path": None,
            "events": [],
        },
        "governance/probe-ledger.jsonl",
        "path-dispatch-allow",
    )

    assert result.status == "not-verified"
    assert result.reason_code == "path-dispatch-startup-failed"


@pytest.mark.parametrize("decision", ["allow", "transform"])
def test_execution_path_allow_or_transform_may_invoke_after_decision(
    decision: str,
):
    binding = {
        "action_id": "payments.refund",
        "mode": "background",
        "path_id": "path-1",
    }
    resolved_path = "app.agent:background_payments_refund"
    outcome = {
        "child_error": None,
        "resolved_path": resolved_path,
        "expected_resolved_path": resolved_path,
        "events": [
            _path_event(
                binding,
                "routing_target",
                "routing-1",
                expected_resolved_path=resolved_path,
            ),
            _path_event(
                binding,
                "resolved_path",
                "resolved-1",
                resolved_path=resolved_path,
            ),
            _path_event(
                binding,
                "pre_action_decision",
                "decision-1",
                decision=decision,
            ),
            _path_event(binding, "invocation", "invocation-1"),
        ],
    }

    result = probes._build_path_probe_result(
        binding, outcome, "governance/probe-ledger.jsonl", "path-dispatch-allow"
    )

    assert result.status == "pass"
    assert result.observed == "pre_action_decision_before_invocation"


def test_execution_path_resolved_identity_mismatch_cannot_pass():
    binding = {
        "action_id": "payments.refund",
        "mode": "background",
        "path_id": "path-1",
    }
    outcome = {
        "child_error": None,
        "resolved_path": "app.agent:lying_decoy",
        "expected_resolved_path": "app.agent:actual_background_payments_refund",
        "events": [
            _path_event(
                binding,
                "routing_target",
                "routing-1",
                expected_resolved_path="app.agent:actual_background_payments_refund",
            ),
            _path_event(
                binding,
                "resolved_path",
                "resolved-1",
                resolved_path="app.agent:lying_decoy",
            ),
            _path_event(
                binding,
                "pre_action_decision",
                "decision-1",
                decision="allow",
            ),
            _path_event(binding, "invocation", "invocation-1"),
        ],
    }

    result = probes._build_path_probe_result(
        binding, outcome, "governance/probe-ledger.jsonl", "path-dispatch-allow"
    )

    assert result.status == "must-fix"
    assert result.reason_code == "ENF-002"
    assert result.observed == "resolved_path_identity_mismatch"


def test_execution_path_child_error_does_not_mask_proven_identity_mismatch():
    binding = {
        "action_id": "payments.refund",
        "mode": "background",
        "path_id": "path-1",
    }
    outcome = {
        "child_error": "timeout",
        "resolved_path": "app.agent:lying_decoy",
        "expected_resolved_path": "app.agent:actual_background_payments_refund",
        "events": [
            _path_event(
                binding,
                "routing_target",
                "routing-1",
                expected_resolved_path="app.agent:actual_background_payments_refund",
            ),
            _path_event(
                binding,
                "resolved_path",
                "resolved-1",
                resolved_path="app.agent:lying_decoy",
            ),
        ],
    }

    result = probes._build_path_probe_result(
        binding, outcome, "governance/probe-ledger.jsonl", "path-dispatch-allow"
    )

    assert result.status == "must-fix"
    assert result.observed == "resolved_path_identity_mismatch"


def test_target_ledger_forgery_does_not_create_assessor_receipt(
    approval_root: Path,
):
    source_path = approval_root / "app" / "agent.py"
    source = source_path.read_text(encoding="utf-8")
    marker = "    target = EXECUTION_ROUTES[(action_id, mode)]\n"
    forged = """    if mode == "background":
        resolved_path = f"{target.__module__}:{target.__qualname__}"
        _append_ledger(
            ledger_path,
            "resolved_path",
            evidence_id=f"path-resolved-{case['path_id']}",
            action_id=action_id,
            mode=mode,
            path_id=case["path_id"],
            resolved_path=resolved_path,
        )
        _append_ledger(
            ledger_path,
            "pre_action_decision",
            evidence_id=f"path-decision-{case['path_id']}",
            action_id=action_id,
            mode=mode,
            path_id=case["path_id"],
            decision="allow",
        )
        _append_ledger(
            ledger_path,
            "invocation",
            evidence_id=f"path-invocation-{case['path_id']}",
            action_id=action_id,
            mode=mode,
            path_id=case["path_id"],
        )
        return {
            "action_id": action_id,
            "mode": mode,
            "resolved_path": resolved_path,
            "result": {"forged": True},
        }
"""
    assert marker in source
    source_path.write_text(source.replace(marker, marker + forged, 1), encoding="utf-8")

    paths = _discovered_fixture_paths(approval_root)
    results = probes.run_execution_path_probe_set(approval_root, paths)
    result = next(
        result
        for result in results
        if result.action_id == "payments.refund"
        and result.mode == "background"
        and result.probe_id == "path-dispatch-allow"
    )

    assert result.status == "not-verified"
    assert result.reason_code == "path-dispatch-malformed-output"


def test_target_cannot_glob_and_forge_assessor_path_receipt(
    approval_root: Path,
):
    source_path = approval_root / "app" / "agent.py"
    source = source_path.read_text(encoding="utf-8")
    original = """def background_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="background")
    agent_hooks.require_approval(action_id="payments.refund", mode="background")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="background")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="background")
    return result
"""
    forged = """def background_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    del payment_id, amount
    for assessor_path in __import__("pathlib").Path("governance").glob(".path-probe-*"):
        if "-allow-" not in assessor_path.name:
            continue
        events = [
            json.loads(line)
            for line in assessor_path.read_text(encoding="utf-8").splitlines()
        ]
        context = events[0]
        for event, fields in (
            ("pre_action_decision", {"decision": "allow"}),
            ("invocation", {}),
        ):
            _append_ledger(
                str(assessor_path),
                event,
                evidence_id=f"forged-{event}-{context['path_id']}",
                action_id=context["action_id"],
                mode=context["mode"],
                path_id=context["path_id"],
                **fields,
            )
    return {"forged": True}
"""
    assert original in source
    source_path.write_text(source.replace(original, forged, 1), encoding="utf-8")

    paths = _discovered_fixture_paths(approval_root)
    background_path = next(
        path
        for path in paths
        if path.action_id == "payments.refund" and path.mode == "background"
    )
    contract_path = approval_root / "governance" / "probe-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    for binding in contract["execution_paths"]:
        if binding["action_id"] == "payments.refund" and binding["mode"] == "background":
            binding["path_id"] = background_path.path_id
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    results = probes.run_execution_path_probe_set(approval_root, paths)
    result = next(
        result
        for result in results
        if result.action_id == "payments.refund"
        and result.mode == "background"
        and result.probe_id == "path-dispatch-allow"
    )

    assert result.status == "not-verified"
    assert result.reason_code == "path-dispatch-unobservable"
    assert not list(approval_root.rglob(".path-probe-*"))


def test_path_proof_events_require_exact_assessor_nonce():
    proof_nonce = "a" * 64
    expected = {
        "event": "pre_action_decision",
        "proof_nonce": proof_nonce,
    }
    forged = {
        "event": "pre_action_decision",
        "proof_nonce": "b" * 64,
    }
    non_hex = {"event": "pre_action_decision", "proof_nonce": "g" * 64}
    wrong_length = {"event": "pre_action_decision", "proof_nonce": "a" * 63}
    missing = {"event": "pre_action_decision"}
    raw = b"\n".join(
        json.dumps(event, sort_keys=True).encode("utf-8")
        for event in (forged, non_hex, wrong_length, expected, missing)
    )

    assert probes._decode_path_proof_events(raw, proof_nonce) == [expected]


def test_execution_path_cannot_pass_without_resolved_identity_in_ledger():
    binding = {
        "action_id": "payments.refund",
        "mode": "background",
        "path_id": "path-1",
    }
    resolved_path = "app.agent:background_payments_refund"
    outcome = {
        "child_error": None,
        "resolved_path": resolved_path,
        "expected_resolved_path": resolved_path,
        "events": [
            _path_event(
                binding,
                "routing_target",
                "routing-1",
                expected_resolved_path=resolved_path,
            ),
            _path_event(
                binding,
                "pre_action_decision",
                "decision-1",
                decision="allow",
            ),
            _path_event(binding, "invocation", "invocation-1"),
        ],
    }

    result = probes._build_path_probe_result(
        binding, outcome, "governance/probe-ledger.jsonl", "path-dispatch-allow"
    )

    assert result.status == "not-verified"
    assert result.observed == "resolved_path_identity_unobservable"


@pytest.mark.parametrize("timeout_ms", [1, 5_000])
def test_load_probe_contract_accepts_timeout_boundaries(
    approval_root: Path, timeout_ms: int
):
    contract_path = approval_root / "governance" / "probe-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["timeout_ms"] = timeout_ms
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    assert load_probe_contract(approval_root)["timeout_ms"] == timeout_ms


def test_load_probe_contract_rejects_timeout_above_assessor_maximum(
    approval_root: Path,
):
    contract_path = approval_root / "governance" / "probe-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["timeout_ms"] = 5_001
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ProbeContractError, match=r"at most 5000 milliseconds"):
        load_probe_contract(approval_root)


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
                "execution_dispatch": "app.agent:dispatch_probe",
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
                "execution_dispatch": "app.agent:dispatch_probe",
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
    results = run_enforcement_probe_set(fixture_root / "conformant-maf")
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
                "execution_dispatch": "probes_test_nonexistent_module:dispatch_execution_path",
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
    root = fixture_root / "conformant-maf"
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
    # A deliberately broken, fail-open nonce store: it always grants
    # ``accepted: true`` (and always invokes the protected tool as a
    # result), even to an attempt for a nonce that already has an
    # accepted record. ``run_approval_probe`` must never trust the
    # fixture's own self-reported ``accepted`` field blindly for this
    # case either — it cross-checks each new decision's ``accepted``
    # value against the ledger's *own* prior history for that nonce,
    # and reports the catalog's ``APR-001`` must-fix finding the moment
    # a second attempt is fail-open-accepted rather than a laundered
    # pass.
    root = tmp_path / "broken-nonce-store"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": "decision", "nonce": nonce, "digest": digest, "accepted": True}
            )
            + "\\n"
        )
        handle.write(
            json.dumps({"event": "invocation", "nonce": nonce}) + "\\n"
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

    approved = run_approval_probe(root, approval_binding, now="2026-09-01T12:00:00Z")
    replayed = run_approval_probe(root, approval_binding, now="2026-09-01T12:00:01Z")

    assert approved.status == "pass"
    assert approved.observed == "approval_accepted"
    assert replayed.status == "must-fix"
    assert replayed.reason_code == "APR-001"
    assert replayed.observed == "fail_open_replay_or_mutation_accepted"

    findings = findings_from_probes((approved, replayed))
    assert [(f.finding_id, f.status) for f in findings] == [("APR-001", "must-fix")]


def test_approval_probe_reports_apr_001_when_expired_binding_is_fail_open_accepted(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # A target that ignores expiry entirely and always accepts (and
    # always invokes the tool) must be caught even though its own
    # decision record says ``accepted: true`` — this probe
    # independently reconciles that decision against the same
    # ``now >= expires_at`` comparison, never merely trusting an
    # accepted decision for an already-expired binding.
    root = tmp_path / "expiry-ignoring-nonce-store"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": "decision", "nonce": nonce, "digest": digest, "accepted": True}
            )
            + "\\n"
        )
        handle.write(
            json.dumps({"event": "invocation", "nonce": nonce}) + "\\n"
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

    expired_binding = replace(approval_binding, nonce="nonce-expiry-ignored-0001")
    result = run_approval_probe(
        root, expired_binding, now="2026-09-01T12:05:01Z"
    )
    assert result.status == "must-fix"
    assert result.reason_code == "APR-001"
    assert result.observed == "expired_binding_fail_open_accepted"


def test_approval_probe_reports_apr_001_when_rejection_is_invoked_anyway(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # Req3: a fail-open target that correctly *records* a rejection
    # (``accepted: false``) but invokes the protected tool anyway must
    # be caught by observing the ledger's own distinct invocation
    # evidence — never inferred merely from the decision's own
    # ``accepted`` field, which this broken target reports honestly.
    root = tmp_path / "invoke-on-rejection-nonce-store"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": "decision", "nonce": nonce, "digest": digest, "accepted": False}
            )
            + "\\n"
        )
        # Fail-open: invokes the protected tool even though its own
        # decision says the attempt was rejected.
        handle.write(
            json.dumps({"event": "invocation", "nonce": nonce}) + "\\n"
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
    assert result.reason_code == "APR-001"
    assert result.observed == "fail_open_invocation_on_rejection"


def test_approval_probe_reports_apr_001_when_acceptance_has_no_invocation_evidence(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # A target that grants ``accepted: true`` but never actually
    # records invoking the protected tool cannot be trusted on its
    # decision's word alone — no corroborating invocation evidence
    # means the acceptance itself cannot be proven to have unlocked
    # anything, so it is reported as must-fix rather than a laundered
    # pass.
    root = tmp_path / "accept-without-invocation-nonce-store"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": "decision", "nonce": nonce, "digest": digest, "accepted": True}
            )
            + "\\n"
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
    assert result.reason_code == "APR-001"
    assert result.observed == "invocation_not_recorded_after_acceptance"


def test_approval_probe_reports_apr_001_when_ledger_contains_malformed_event(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # Req2: a ledger line that is valid JSON but not a JSON object
    # (here, a bare string) must never crash the probe with a raw
    # ``AttributeError`` from calling ``.get(...)`` on it — it is a
    # typed ``ProbeToolingError`` instead, exactly as unobservable as
    # no evidence at all.
    root = tmp_path / "malformed-ledger-event-nonce-store"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps("not-an-object") + "\\n")
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

    with pytest.raises(ProbeToolingError):
        run_approval_probe(root, approval_binding, now="2026-09-01T12:00:00Z")


def test_approval_probe_dispatch_hang_does_not_hang_the_assessor(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # Req5: a target whose ``redeem`` hangs indefinitely must not be
    # able to hang the assessor — the bounded child subprocess is
    # killed at its own dispatch timeout, and since nothing was ever
    # durably recorded to the ledger, the probe truthfully reports a
    # must-fix outcome rather than a fabricated pass.
    root = tmp_path / "approval-hanging-dispatch"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import time

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    time.sleep(3600)
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

    started = time.monotonic()
    result = run_approval_probe(root, approval_binding, now="2026-09-01T12:00:00Z")
    elapsed = time.monotonic() - started
    assert elapsed < probes._TASK6_DISPATCH_TIMEOUT_S + 30
    assert result.status == "must-fix"
    assert result.reason_code == "APR-001"
    assert result.observed == "nonce_redemption_not_recorded"


def test_approval_probe_concurrent_same_nonce_redemption_has_exactly_one_winner(
    approval_root: Path,
):
    # Req4: proves the fixture's own service-side nonce store is
    # genuinely atomic under real concurrency, not merely
    # sequentially-correct — many concurrent redemption attempts for
    # the exact same nonce/digest must yield exactly one durably
    # accepted decision in the shared ledger, however many total
    # decision records are appended, and exactly one corresponding
    # invocation record — proving the protected tool itself is reached
    # exactly once too, never once per racing caller.
    contract = probes.load_approval_contract(approval_root)
    ledger_path = approval_root / str(contract["nonce_ledger"])
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    nonce = "nonce-concurrent-0001"
    digest = "sha256:" + "c" * 64
    expires_at = "2026-09-01T12:05:00Z"
    now = "2026-09-01T12:00:00Z"
    worker_count = 8
    barrier = threading.Barrier(worker_count)
    errors = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            probes._dispatch_task6_child(
                approval_root,
                str(contract["dispatch"]),
                str(contract["audit_sink"]),
                (nonce, digest, expires_at, now, str(ledger_path)),
            )
        except Exception as error:  # pragma: no cover - surfaced via errors list
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors

    records = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decision_records = [record for record in records if record.get("event") == "decision"]
    invocation_records = [
        record for record in records if record.get("event") == "invocation"
    ]
    assert len(decision_records) == worker_count
    accepted_records = [
        record for record in decision_records if record.get("accepted") is True
    ]
    assert len(accepted_records) == 1
    assert all(record["nonce"] == nonce for record in decision_records)
    assert len(invocation_records) == 1
    assert invocation_records[0]["nonce"] == nonce


# --------------------------------------------------------------------
# Task 6 (final review): the bounded, isolated approval anti-replay
# *sequence*.
#
# A single redemption attempt only ever proves a first use was accepted;
# it can never prove the control is single-use. ``run_approval_probe_
# sequence`` is the one place that actually exercises anti-replay end to
# end within a single assessment -- first use, byte-identical replay,
# and one mutated-binding variant per design-required dimension, all
# against one private, per-assessment nonce ledger that never touches
# the target's own declared, target-owned ledger and never survives the
# call.
# --------------------------------------------------------------------


def _tree_snapshot(root: Path) -> dict:
    """A ``relative-path -> sha256`` map of every regular file under
    *root*, used to prove a probe run left the target byte-identical."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_fail_open_approval_target(root: Path) -> Path:
    """A deliberately fail-open, non-atomic nonce store: it grants every
    attempt (and invokes the protected tool every time), never consulting
    the prior decisions already recorded for the nonce."""
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": "decision", "nonce": nonce, "digest": digest, "accepted": True}
            )
            + "\\n"
        )
        handle.write(json.dumps({"event": "invocation", "nonce": nonce}) + "\\n")
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
    return root


def test_approval_sequence_covers_every_design_required_binding_dimension():
    # Design section 7.3: "An approval for one canonical action hash
    # cannot authorize changed arguments, target, tenant, actor, policy
    # version, or expiry." Every one of those dimensions -- plus the
    # action identity the canonical hash itself binds -- must be an
    # exercised mutation variant, never a documented intention.
    assert set(probes._APPROVAL_MUTATION_FIELDS) == {
        "arguments",
        "action_id",
        "target_scope",
        "tenant",
        "requesting_subject",
        "approving_subject",
        "approving_role",
        "policy_id",
        "policy_hash",
        "expires_at",
    }


def test_approval_sequence_accepts_first_use_then_rejects_replay_and_mutations(
    approval_root: Path, approval_binding: ApprovalBinding
):
    results = probes.run_approval_probe_sequence(
        approval_root, approval_binding, now="2026-09-01T12:00:00Z"
    )

    assert len(results) == 2 + len(probes._APPROVAL_MUTATION_FIELDS)
    assert results[0].observed == "approval_accepted"
    assert results[1].observed == "replay_rejected"
    assert [probe.observed for probe in results[2:]] == [
        "binding_mismatch_rejected"
    ] * len(probes._APPROVAL_MUTATION_FIELDS)
    assert {probe.status for probe in results} == {"pass"}
    assert findings_from_probes(results) == ()

    # Every attempt is reported against the action actually under test,
    # never against a synthetic mutated action identifier.
    assert {probe.action_id for probe in results} == {approval_binding.action_id}
    assert {probe.probe_id for probe in results} == {probes._APPROVAL_PROBE_ID}

    # The replay cites the exact same binding digest as the accepted
    # first use; every mutation cites a genuinely different one.
    digests = [probe.evidence_refs[0] for probe in results]
    assert digests[0] == digests[1] == approval_digest(approval_binding)
    assert len(set(digests[1:])) == 1 + len(probes._APPROVAL_MUTATION_FIELDS)


def test_approval_sequence_never_writes_target_state(
    approval_root: Path, approval_binding: ApprovalBinding
):
    before = _tree_snapshot(approval_root)
    probes.run_approval_probe_sequence(
        approval_root, approval_binding, now="2026-09-01T12:00:00Z"
    )
    assert _tree_snapshot(approval_root) == before
    contract = probes.load_approval_contract(approval_root)
    assert not (approval_root / str(contract["nonce_ledger"])).exists()
    assert not list((approval_root / "governance").glob(".approval-probe-*"))


def test_consecutive_approval_sequences_are_identical_and_residue_free(
    approval_root: Path, approval_binding: ApprovalBinding
):
    first = probes.run_approval_probe_sequence(
        approval_root, approval_binding, now="2026-09-01T12:00:00Z"
    )
    after_first = _tree_snapshot(approval_root)
    second = probes.run_approval_probe_sequence(
        approval_root, approval_binding, now="2026-09-01T12:00:00Z"
    )
    assert [(p.status, p.observed, p.evidence_refs) for p in first] == [
        (p.status, p.observed, p.evidence_refs) for p in second
    ]
    assert _tree_snapshot(approval_root) == after_first


def test_approval_sequence_flags_fail_open_replay_and_every_mutation(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    root = _write_fail_open_approval_target(tmp_path / "fail-open-nonce-store")
    results = probes.run_approval_probe_sequence(
        root, approval_binding, now="2026-09-01T12:00:00Z"
    )

    assert results[0].status == "pass"
    assert results[0].observed == "approval_accepted"
    replays_and_mutations = results[1:]
    assert len(replays_and_mutations) == 1 + len(probes._APPROVAL_MUTATION_FIELDS)
    assert {probe.status for probe in replays_and_mutations} == {"must-fix"}
    assert {probe.reason_code for probe in replays_and_mutations} == {"APR-001"}
    assert {probe.observed for probe in replays_and_mutations} == {
        "fail_open_replay_or_mutation_accepted"
    }
    findings = findings_from_probes(results)
    assert findings
    assert {(f.finding_id, f.status) for f in findings} == {("APR-001", "must-fix")}


def test_approval_sequence_flags_rejection_that_invokes_the_tool_anyway(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # A target that correctly records a rejection but reaches the
    # protected tool regardless is still fail-open: a replay/mutation
    # must be rejected *without* invocation, never merely "rejected".
    root = tmp_path / "invoke-on-rejection-sequence"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def _records(ledger_path):
    try:
        with open(ledger_path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def redeem(nonce, digest, expires_at, now, ledger_path):
    already = any(
        record.get("event") == "decision"
        and record.get("nonce") == nonce
        and record.get("accepted") is True
        for record in _records(ledger_path)
    )
    accepted = not already and now < expires_at
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"event": "decision", "nonce": nonce, "digest": digest, "accepted": accepted}
            )
            + "\\n"
        )
        # Always reaches the protected tool, even for a rejection.
        handle.write(json.dumps({"event": "invocation", "nonce": nonce}) + "\\n")
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

    results = probes.run_approval_probe_sequence(
        root, approval_binding, now="2026-09-01T12:00:00Z"
    )
    assert results[0].status == "pass"
    rejected = results[1:]
    assert {probe.status for probe in rejected} == {"must-fix"}
    assert {probe.observed for probe in rejected} == {
        "fail_open_invocation_on_rejection"
    }


def test_approval_sequence_cleans_up_its_private_ledger_on_failure(
    monkeypatch, approval_root: Path, approval_binding: ApprovalBinding
):
    def _explode(*_args, **_kwargs):
        raise ProbeToolingError("simulated dispatch failure")

    monkeypatch.setattr(probes, "_dispatch_task6_child", _explode)
    before = _tree_snapshot(approval_root)
    with pytest.raises(ProbeToolingError):
        probes.run_approval_probe_sequence(
            approval_root, approval_binding, now="2026-09-01T12:00:00Z"
        )
    assert _tree_snapshot(approval_root) == before
    assert not list((approval_root / "governance").glob(".approval-probe-*"))


def test_enforcement_probe_set_raises_with_partial_results_on_late_tooling_error(
    monkeypatch: pytest.MonkeyPatch,
):
    first = ProbeResult(
        probe_id="deny",
        action_id="payments.refund",
        path_id=None,
        status="must-fix",
        reason_code="ENF-002",
        expected="tool_not_invoked",
        observed="tool_invoked_despite_fault",
        evidence_refs=(),
    )
    call_count = {"value": 0}

    monkeypatch.setattr(
        probes,
        "load_probe_contract",
        lambda _root: {"actions": ("payments.refund",)},
    )

    def _runner(_root, _case):
        call_count["value"] += 1
        if call_count["value"] == 1:
            return first
        raise ProbeToolingError("synthetic late enforcement failure")

    monkeypatch.setattr(probes, "run_application_probe", _runner)

    with pytest.raises(ProbeToolingError) as excinfo:
        run_enforcement_probe_set(Path("."))
    assert getattr(excinfo.value, "partial_results", ()) == (first,)


def test_approval_sequence_raises_with_partial_results_on_late_tooling_error(
    monkeypatch: pytest.MonkeyPatch, approval_root: Path, approval_binding: ApprovalBinding
):
    first = ProbeResult(
        probe_id=probes._APPROVAL_PROBE_ID,
        action_id=approval_binding.action_id,
        path_id=None,
        status="must-fix",
        reason_code="APR-001",
        expected="anti_replay_enforced",
        observed="fail_open_replay_or_mutation_accepted",
        evidence_refs=("sha256:deadbeef",),
    )
    call_count = {"value": 0}
    real = probes.run_approval_probe

    def _runner(root, binding, now, *, ledger_path=None):
        call_count["value"] += 1
        if call_count["value"] == 1:
            return first
        if call_count["value"] == 2:
            raise ProbeToolingError("synthetic late approval failure")
        return real(root, binding, now, ledger_path=ledger_path)

    monkeypatch.setattr(probes, "run_approval_probe", _runner)

    with pytest.raises(ProbeToolingError) as excinfo:
        probes.run_approval_probe_sequence(
            approval_root, approval_binding, now="2026-09-01T12:00:00Z"
        )
    assert getattr(excinfo.value, "partial_results", ()) == (first,)
    assert not list((approval_root / "governance").glob(".approval-probe-*"))


def test_approval_sequence_rejects_a_target_without_an_approval_contract(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    with pytest.raises(ProbeContractError):
        probes.run_approval_probe_sequence(
            tmp_path / "not-a-target", approval_binding, now="2026-09-01T12:00:00Z"
        )


@pytest.mark.parametrize("relative", ["governance/nonce-ledger.jsonl", "ledger.jsonl"])
def test_approval_probe_rejects_a_relative_ledger_override(
    approval_root: Path, approval_binding: ApprovalBinding, relative: str
):
    with pytest.raises(ProbeContractError):
        run_approval_probe(
            approval_root,
            approval_binding,
            now="2026-09-01T12:00:00Z",
            ledger_path=relative,
        )


def test_approval_probe_rejects_a_missing_ledger_override(
    approval_root: Path, approval_binding: ApprovalBinding, tmp_path: Path
):
    with pytest.raises(ProbeContractError):
        run_approval_probe(
            approval_root,
            approval_binding,
            now="2026-09-01T12:00:00Z",
            ledger_path=tmp_path / "never-created.jsonl",
        )


def test_approval_probe_override_never_targets_the_declared_nonce_ledger(
    approval_root: Path, approval_binding: ApprovalBinding
):
    # The target-owned contract path stays strictly read-only: an
    # override that resolves onto it -- directly or through a symlink --
    # is refused rather than silently writing target state.
    contract = probes.load_approval_contract(approval_root)
    declared = approval_root / str(contract["nonce_ledger"])
    declared.parent.mkdir(parents=True, exist_ok=True)
    declared.write_text("", encoding="utf-8")
    with pytest.raises(ProbeContractError):
        run_approval_probe(
            approval_root,
            approval_binding,
            now="2026-09-01T12:00:00Z",
            ledger_path=declared.resolve(),
        )

    link = approval_root / "governance" / "linked-ledger.jsonl"
    link.symlink_to(declared)
    with pytest.raises(ProbeContractError):
        run_approval_probe(
            approval_root,
            approval_binding,
            now="2026-09-01T12:00:00Z",
            ledger_path=link,
        )
    assert declared.read_text(encoding="utf-8") == ""


def test_output_is_buffered_until_output_verdict(fixture_root: Path):
    result = run_output_probe(fixture_root / "conformant-maf", verdict="deny")
    assert result.status == "pass"
    assert result.action_id == "payments.refund"
    assert result.observed == "zero_bytes_egressed"
    assert findings_from_probes((result,)) == ()


def test_output_probe_allows_buffered_release_after_verdict(fixture_root: Path):
    result = run_output_probe(fixture_root / "conformant-maf", verdict="allow")
    assert result.status == "pass"
    assert result.observed == "buffered_release_after_verdict"


def test_output_probe_uses_a_private_ledger_and_never_touches_declared_target_files(
    fixture_root: Path, tmp_path: Path
):
    # The declared observation_ledger is provenance only. Even a hostile
    # contract pointing it at a tracked workflow file must not let the
    # probe delete or rewrite that file: the output probe uses its own
    # exclusive private ledger and leaves the full target tree byte-
    # identical.
    root = tmp_path / "output-private-ledger"
    shutil.copytree(fixture_root / "conformant-maf", root)
    contract_path = root / "governance" / "probe-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["observation_ledger"] = ".github/workflows/governed-actions.yml"
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    untracked = root / "scratch" / "notes.txt"
    untracked.parent.mkdir(parents=True)
    untracked.write_text("leave me alone\n", encoding="utf-8")

    workflow_path = root / ".github" / "workflows" / "governed-actions.yml"
    workflow_before = workflow_path.read_bytes()
    before = _tree_snapshot(root)

    result = run_output_probe(root, verdict="deny")

    assert result.status == "pass"
    assert result.observed == "zero_bytes_egressed"
    assert workflow_path.read_bytes() == workflow_before
    assert _tree_snapshot(root) == before


def test_output_probe_passes_when_stream_is_chunk_mediated_within_declared_bound(
    fixture_root: Path,
):
    result = run_output_probe(fixture_root / "conformant-maf", verdict="stream")
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


def test_output_probe_rejects_stream_exceeding_total_exposure_across_many_chunks(
    tmp_path: Path,
):
    # Req1/req2: the exposure bound is a *total* budget across every
    # release event, not a per-chunk ceiling re-checked in isolation.
    # Three individually-within-bound, properly mediated 40-byte chunks
    # summing to 120 bytes must still be rejected against a declared
    # 96-byte total bound — many small chunks can never launder
    # unlimited total exposure.
    root = tmp_path / "output-many-small-chunks"
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
        for _ in range(3):
            handle.write(
                json.dumps({"event": "chunk", "bytes": 40, "mediated": True}) + "\\n"
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
                "exposure_bound_bytes": 96,
                "chunk_mediation": True,
            }
        ),
        encoding="utf-8",
    )

    result = run_output_probe(root, verdict="stream")
    assert result.status == "must-fix"
    assert result.reason_code == "OUT-001"
    assert result.observed == "unmediated_or_oversized_chunk_release"


def test_output_probe_rejects_stream_with_trailing_unmediated_egress(
    tmp_path: Path,
):
    # Req1/req2: reproduces the exact false-pass this redesign fixes —
    # properly mediated chunks within the declared bound, followed by
    # one additional *unmediated* egress event the old per-chunk-only
    # check never even inspected. Counting every release event (both
    # ``chunk`` and ``egress``) as egress means this trailing leak
    # alone must fail the whole stream.
    root = tmp_path / "output-trailing-unmediated-egress"
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
        for _ in range(3):
            handle.write(
                json.dumps({"event": "chunk", "bytes": 32, "mediated": True}) + "\\n"
            )
        handle.write(json.dumps({"event": "egress", "bytes": 8}) + "\\n")
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
                "exposure_bound_bytes": 96,
                "chunk_mediation": True,
            }
        ),
        encoding="utf-8",
    )

    result = run_output_probe(root, verdict="stream")
    assert result.status == "must-fix"
    assert result.reason_code == "OUT-001"
    assert result.observed == "unmediated_or_oversized_chunk_release"


def test_output_probe_rejects_allow_verdict_with_incidental_chunk_event(
    tmp_path: Path,
):
    # Req2: an "allow" (buffered) verdict must only ever release via a
    # single buffered, non-incremental event. Any incidental ``chunk``
    # event recorded under an ``allow`` verdict — even one that would
    # individually look mediated and within-bound — proves incremental
    # release without the declared stream posture ``allow`` never
    # grants, and must reject as OUT-001 rather than pass by omission.
    root = tmp_path / "output-allow-with-chunk"
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
            json.dumps({"event": "chunk", "bytes": 8, "mediated": True}) + "\\n"
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
    assert result.observed == "incremental_release_without_declared_stream_posture"


def test_output_probe_reports_out_001_when_no_evidence_is_ever_recorded(
    tmp_path: Path,
):
    # A dispatch callable that runs to completion but writes nothing
    # to the ledger at all — no verdict record, no release record —
    # is genuinely unobservable evidence, not a fabricated pass.
    root = tmp_path / "output-no-evidence"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    pass
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
    assert result.observed == "no_verdict_evidence_recorded"


def test_output_probe_dispatch_hang_does_not_hang_the_assessor(tmp_path: Path):
    # Req5: a target whose dispatch callable hangs indefinitely must
    # not be able to hang (or otherwise take down) the assessor — the
    # bounded child subprocess is killed at its own dispatch timeout,
    # and the probe returns a sane must-fix outcome from whatever
    # ledger evidence (none) was actually recorded.
    root = tmp_path / "output-hanging-dispatch"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import time

AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    time.sleep(3600)
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

    started = time.monotonic()
    result = run_output_probe(root, verdict="deny")
    elapsed = time.monotonic() - started
    assert elapsed < probes._TASK6_DISPATCH_TIMEOUT_S + 30
    assert result.status == "must-fix"
    assert result.reason_code == "OUT-001"
    assert result.observed == "no_verdict_evidence_recorded"


def test_output_probe_rejects_unknown_verdict(fixture_root: Path):
    with pytest.raises(ProbeContractError):
        run_output_probe(fixture_root / "output-streaming", verdict="bogus")


def test_output_probe_with_multiple_declared_actions_and_no_attribution_is_not_verified(
    tmp_path: Path,
):
    root = tmp_path / "output-unattributed"
    shutil.copytree(FIXTURES_DIR / "conformant-maf", root)
    contract_path = root / "governance" / "probe-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract.pop("action_id", None)
    contract.pop("approval_binding", None)
    contract["actions"] = ["payments.refund", "customer.lookup"]
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = run_output_probe(root, verdict="deny")

    assert result.status == "not-verified"
    assert result.reason_code == "probe-action-unattributed"
    assert result.action_id is None
    assert result.evidence_refs == ()
    assert findings_from_probes((result,)) == ()


def test_audit_probe_rejects_payload_bearing_record(tmp_path: Path):
    # A target whose nonce store behaves correctly but whose audit
    # sink leaks a raw argument payload: ``run_privacy_probe_set``
    # drives its own fixed synthetic redemption against this real
    # target and independently validates the real ``AUDIT_EVENTS`` it
    # actually produced, rather than trusting a declared ``audit_sink``
    # as a dead, unchecked seam.
    root = tmp_path / "audit-probe-set-leaky"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"nonce": nonce, "digest": digest, "accepted": True}) + "\\n"
        )
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

    findings = findings_from_probes(run_privacy_probe_set(root))
    assert [(f.finding_id, f.status) for f in findings] == [("AUD-001", "must-fix")]

    # Never mutates the target: the real, declared nonce ledger is
    # never created by this probe set's own private, temporary
    # dispatch, and no stray artifacts are left behind in governance/.
    assert not (root / "governance" / "nonce-ledger.jsonl").exists()
    assert list(governance_dir.iterdir()) == [governance_dir / "probe-contract.json"]


def test_audit_probe_passes_payload_free_record(fixture_root: Path):
    # Drives its own synthetic call against the real, checked-in,
    # conformant approval-replay fixture and independently validates
    # what it actually produced.
    results = run_privacy_probe_set(fixture_root / "conformant-maf")
    passing = [result for result in results if result.status == "pass"]
    assert passing
    assert {result.action_id for result in passing} == {"payments.refund"}
    assert all(result.observed == "payload_free_audit_record" for result in passing)
    assert findings_from_probes(tuple(passing)) == ()

    # Never mutates the real, checked-in fixture.
    assert not (
        fixture_root / "conformant-maf" / "governance" / "nonce-ledger.jsonl"
    ).exists()
    assert sorted(
        (fixture_root / "conformant-maf" / "governance").iterdir()
    ) == [
        fixture_root / "conformant-maf" / "governance" / name
        for name in (
            "alerts.json",
            "change-plane.json",
            "installed-packages.json",
            "probe-contract.json",
        )
    ]


def test_audit_probe_set_assesses_real_output_target_evidence(fixture_root: Path):
    # Same non-hardcoded-sample proof, for the output-mediation family:
    # drives its own synthetic "deny" call against the real,
    # checked-in, conformant output-streaming fixture.
    results = run_privacy_probe_set(fixture_root / "conformant-maf")
    passing = [result for result in results if result.status == "pass"]
    assert passing
    assert all(result.observed == "payload_free_audit_record" for result in passing)
    assert findings_from_probes(tuple(passing)) == ()
    assert not (
        fixture_root / "conformant-maf" / "governance" / "output-ledger.jsonl"
    ).exists()


def test_audit_probe_set_is_idempotent_and_never_mutates_target(fixture_root: Path):
    root = fixture_root / "conformant-maf"
    first = run_privacy_probe_set(root)
    second = run_privacy_probe_set(root)
    summarize = lambda results: [  # noqa: E731
        (r.status, r.reason_code, r.observed) for r in results
    ]
    assert summarize(first) == summarize(second)
    assert not (root / "governance" / "nonce-ledger.jsonl").exists()
    assert sorted((root / "governance").iterdir()) == [
        root / "governance" / name
        for name in (
            "alerts.json",
            "change-plane.json",
            "installed-packages.json",
            "probe-contract.json",
        )
    ]


def test_audit_probe_set_rejects_root_with_no_recognized_contract(tmp_path: Path):
    root = tmp_path / "not-a-task6-target"
    (root / "governance").mkdir(parents=True)
    (root / "governance" / "probe-contract.json").write_text(
        json.dumps({"dispatch": "app.agent:noop", "audit_sink": "app.agent:EVENTS"}),
        encoding="utf-8",
    )
    with pytest.raises(ProbeContractError):
        run_privacy_probe_set(root)


def test_audit_probe_with_multiple_declared_actions_and_no_attribution_is_not_verified(
    tmp_path: Path,
):
    root = tmp_path / "audit-unattributed"
    shutil.copytree(FIXTURES_DIR / "conformant-maf", root)
    contract_path = root / "governance" / "probe-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract.pop("action_id", None)
    contract.pop("approval_binding", None)
    contract["actions"] = ["payments.refund", "customer.lookup"]
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    results = run_privacy_probe_set(root)

    assert len(results) == 1
    result = results[0]
    assert result.status == "not-verified"
    assert result.reason_code == "probe-action-unattributed"
    assert result.action_id is None
    assert result.evidence_refs == ()
    assert findings_from_probes(results) == ()


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


_MALFORMED_DENY_BYTES_TEMPLATE = '''
import json

AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event": "verdict_received", "verdict": verdict}) + "\\n"
        )
        __BYTES_LINE__
'''


@pytest.mark.parametrize(
    "case_id,malformed_value",
    [
        ("bool-true", True),
        ("bool-false", False),
        ("float", 40.5),
        ("string", "40"),
        ("missing", None),
        ("negative", -1),
    ],
)
def test_output_probe_never_trusts_malformed_egress_byte_evidence_on_deny(
    tmp_path: Path, case_id: str, malformed_value: object
):
    # Req1: a deny-verdict release event whose "bytes" field is a bool,
    # float, string, missing, or negative can never be trusted as
    # proof of zero egress — never defaulted to 0, never summed, never
    # coerced through a bare ``int()`` that would itself crash on a
    # non-numeric value. This must be reported as a distinct,
    # non-silent must-fix, never a laundered pass and never an
    # unhandled crash.
    root = tmp_path / f"output-deny-malformed-bytes-{case_id}"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    if case_id == "missing":
        bytes_line = 'handle.write(json.dumps({"event": "egress"}) + "\\n")'
    else:
        bytes_line = (
            'handle.write(json.dumps({"event": "egress", "bytes": '
            + repr(malformed_value)
            + '}) + "\\n")'
        )
    agent_source = _MALFORMED_DENY_BYTES_TEMPLATE.replace(
        "__BYTES_LINE__", bytes_line
    )
    (app_dir / "agent.py").write_text(agent_source, encoding="utf-8")
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
    assert result.observed == "malformed_egress_byte_evidence"


def test_output_probe_reports_out_001_for_malformed_ledger_event(tmp_path: Path):
    # Req2: a ledger line that is valid JSON but not a JSON object
    # (here, a bare number) must never crash the probe with a raw
    # ``AttributeError``/``ValueError`` from calling ``.get(...)`` on
    # it — it is a typed ``ProbeToolingError`` instead, exactly as
    # unobservable as no evidence at all.
    root = tmp_path / "output-malformed-ledger-event"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import json

AUDIT_EVENTS = []


def emit_output(verdict, ledger_path):
    with open(ledger_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(12345) + "\\n")
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

    with pytest.raises(ProbeToolingError):
        run_output_probe(root, verdict="deny")


def test_approval_probe_never_masks_audit_leak_and_privacy_probe_set_reports_it(
    tmp_path: Path, approval_binding: ApprovalBinding
):
    # The nonce store itself behaves correctly (atomic, one-time
    # redemption identical to the checked-in fixture, using the
    # conformant explicit ``accepted`` field), but its audit sink
    # leaks a raw argument payload. ``run_approval_probe`` never judges
    # audit-sink payload-freeness at all — it reports its own,
    # unmasked anti-replay verdict — while a companion
    # ``run_privacy_probe_set`` call against the very same root
    # independently reports the leak as ``AUD-001``. Neither finding
    # ever masks the other.
    root = tmp_path / "approval-leaky-audit"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "agent.py").write_text(
        '''
import fcntl
import json
import os

AUDIT_EVENTS = []


def redeem(nonce, digest, expires_at, now, ledger_path):
    with open(ledger_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = [
                json.loads(line)
                for line in handle.read().splitlines()
                if line.strip()
            ]
            already_accepted = any(
                record.get("event") == "decision"
                and record.get("nonce") == nonce
                and record.get("accepted") is True
                for record in existing
            )
            expired = now >= expires_at
            accepted = (not expired) and (not already_accepted)
            handle.write(
                json.dumps(
                    {
                        "event": "decision",
                        "nonce": nonce,
                        "digest": digest,
                        "accepted": accepted,
                    }
                )
                + "\\n"
            )
            handle.flush()
            if accepted:
                handle.write(
                    json.dumps({"event": "invocation", "nonce": nonce}) + "\\n"
                )
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

    approval_result = run_approval_probe(
        root, approval_binding, now="2026-09-01T12:00:00Z"
    )
    assert approval_result.status == "pass"
    assert approval_result.reason_code == probes._APPROVAL_PASS_REASON
    assert approval_result.observed == "approval_accepted"

    audit_results = run_privacy_probe_set(root)
    audit_findings = findings_from_probes(audit_results)
    assert [(f.finding_id, f.status) for f in audit_findings] == [
        ("AUD-001", "must-fix")
    ]

    # Aggregating both together surfaces both independently: the audit
    # violation never masks (and is never masked by) the approval
    # probe's own passing verdict.
    combined = findings_from_probes((approval_result,) + audit_results)
    assert [(f.finding_id, f.status) for f in combined] == [("AUD-001", "must-fix")]


def test_output_probe_never_masks_audit_leak_and_privacy_probe_set_reports_it(
    tmp_path: Path,
):
    # Otherwise-correct, well-ordered output mediation (verdict
    # recorded before the denial's zero-byte egress) is still a
    # target whose audit trail leaks raw output content —
    # ``run_output_probe`` never judges that at all; it reports its
    # own unmasked mediation verdict, while a companion
    # ``run_privacy_probe_set`` call independently reports ``AUD-001``.
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

    output_result = run_output_probe(root, verdict="deny")
    assert output_result.status == "pass"
    assert output_result.reason_code == probes._OUTPUT_PASS_REASON
    assert output_result.observed == "zero_bytes_egressed"

    audit_results = run_privacy_probe_set(root)
    audit_findings = findings_from_probes(audit_results)
    assert [(f.finding_id, f.status) for f in audit_findings] == [
        ("AUD-001", "must-fix")
    ]

    combined = findings_from_probes((output_result,) + audit_results)
    assert [(f.finding_id, f.status) for f in combined] == [("AUD-001", "must-fix")]


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


# ---------------------------------------------------------------------------
# Task 8: staging-only post-deploy guards.
#
# ``validate_post_deploy_target`` and ``run_staging_canary`` are the only
# two functions this project ever lets touch anything resembling a live
# target during ``post-deploy``. Neither ever spawns a real process or
# opens a real socket in these tests: ``run_staging_canary``'s HTTP runner
# is always an injected test double, and every unsafe contract must be
# rejected with :class:`UnsafeTargetError` before that runner is ever
# invoked.
# ---------------------------------------------------------------------------


#: Sentinel meaning "the caller of ``_FakeHttpResponse`` did not name a
#: ``url`` at all", distinct from an explicit ``url=None`` (used by the
#: dedicated "missing url attribute" adversarial test to build a response
#: that genuinely has no ``url`` attribute).
_DEFAULT_FAKE_RESPONSE_URL = object()


class _FakeHttpResponse:
    def __init__(self, status_code, headers=None, body=b"", url=_DEFAULT_FAKE_RESPONSE_URL):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.body = body
        # Every test's ``safe_canary``/``trusted_staging_origin`` fixture
        # names the same fixed same-origin URL, so defaulting to it here
        # lets every test that does not itself care about the final
        # response URL still exercise the (mandatory) same-origin check
        # without having to repeat it. Pass ``url=None`` explicitly to
        # build a response that has *no* ``url`` attribute at all.
        if url is _DEFAULT_FAKE_RESPONSE_URL:
            url = "https://staging.example.invalid/governance/health"
        if url is not None:
            self.url = url


class _BareHttpResponse:
    """A response object exposing only the attributes explicitly passed
    to it -- used to prove that a genuinely *missing*
    ``url``/``headers``/``body`` attribute is reported ``not-verified``
    rather than the canary silently skipping the same-origin check or
    defaulting to an empty mapping/body and treating that fabricated
    default as real evidence.
    """

    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)


class _FakeHttpRunner:
    def __init__(self, response):
        self.requests = []
        self._response = response

    def __call__(self, request):
        self.requests.append(dict(request))
        return self._response


@pytest.fixture
def fake_http_runner():
    return _FakeHttpRunner(
        _FakeHttpResponse(204, headers={"X-Deployment-Id": "dep-123"}, body=b"")
    )


@pytest.fixture
def trusted_staging_origin():
    return "https://staging.example.invalid"


@pytest.fixture
def safe_canary():
    return {
        "environment": "staging",
        "destructive": False,
        "method": "HEAD",
        "url": "https://staging.example.invalid/governance/health",
        "expected_status": 204,
    }


def test_post_deploy_refuses_non_staging_target():
    with pytest.raises(UnsafeTargetError, match="staging"):
        validate_post_deploy_target(phase="post-deploy", staging=False, destructive=False)


def test_post_deploy_refuses_destructive_target_even_when_staging():
    with pytest.raises(UnsafeTargetError):
        validate_post_deploy_target(phase="post-deploy", staging=True, destructive=True)


def test_post_deploy_allows_staging_nondestructive_target():
    validate_post_deploy_target(phase="post-deploy", staging=True, destructive=False)


def test_non_post_deploy_phase_is_unaffected_by_staging_flag():
    # The staging-only guard is specific to the post-deploy phase; design
    # and pre-deploy assessment never touch a live target at all, so
    # ``staging``/``destructive`` are simply irrelevant there.
    validate_post_deploy_target(phase="pre-deploy", staging=False, destructive=False)
    validate_post_deploy_target(phase="design", staging=False, destructive=True)


def test_staging_canary_allows_only_nondestructive_https_read(
    safe_canary, fake_http_runner, trusted_staging_origin
):
    result = run_staging_canary(
        safe_canary, run=fake_http_runner, trusted_origin=trusted_staging_origin
    )
    assert result.status == "pass"
    assert fake_http_runner.requests[0]["method"] == "HEAD"


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "production"),
        ("destructive", True),
        ("method", "POST"),
        ("url", "http://staging.example.invalid/governance/health"),
    ],
)
def test_staging_canary_rejects_unsafe_contract(field, value, safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, field: value},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_rejects_request_body(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "body": "some-body"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_rejects_query_string(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "url": safe_canary["url"] + "?token=abc"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_rejects_literal_authorization_header(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "headers": {"Authorization": "Bearer xyz"}},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_records_status_duration_deployment_id_and_response_hash_only(
    safe_canary, fake_http_runner, trusted_staging_origin
):
    result = run_staging_canary(safe_canary, run=fake_http_runner, trusted_origin=trusted_staging_origin)
    assert "204" in result.observed
    assert "dep-123" in result.observed
    assert "sha256" in result.observed


def test_staging_canary_never_records_raw_response_body(safe_canary, trusted_staging_origin):
    def _runner(request):
        return _FakeHttpResponse(204, headers={}, body=b"super-secret-customer-payload")

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert "super-secret-customer-payload" not in result.observed
    assert "super-secret-customer-payload" not in (result.reason_code or "")


def test_staging_canary_get_method_is_also_allowed(safe_canary, fake_http_runner, trusted_staging_origin):
    result = run_staging_canary(
        {**safe_canary, "method": "GET"}, run=fake_http_runner, trusted_origin=trusted_staging_origin
    )
    assert result.status == "pass"
    assert fake_http_runner.requests[0]["method"] == "GET"


def test_staging_canary_without_expected_status_accepts_2xx_4xx_status(safe_canary, trusted_staging_origin):
    # A contract that never declares an ``expected_status`` still documents
    # (and must actually enforce) an "HTTP 2xx-4xx" acceptance range --
    # never silently accepting *any* status merely because none was named.
    contract = {key: value for key, value in safe_canary.items() if key != "expected_status"}

    def _runner(request):
        return _FakeHttpResponse(404, headers={}, body=b"")

    result = run_staging_canary(contract, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "pass"
    assert result.expected == "HTTP 2xx-4xx"


def test_staging_canary_without_expected_status_rejects_5xx_status(safe_canary, trusted_staging_origin):
    contract = {key: value for key, value in safe_canary.items() if key != "expected_status"}

    def _runner(request):
        return _FakeHttpResponse(503, headers={}, body=b"")

    result = run_staging_canary(contract, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-unexpected-status"


def test_staging_canary_rejects_url_with_userinfo(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "url": "https://user:pass@staging.example.invalid/governance/health"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_rejects_url_with_fragment(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "url": safe_canary["url"] + "#section"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_rejects_url_not_matching_trusted_origin(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "url": "https://other-host.invalid/governance/health"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_never_infers_staging_from_hostname_naming(safe_canary):
    # A URL host that merely *contains* "staging" must never itself be
    # trusted as staging -- only an exact origin match against the
    # independently supplied ``trusted_origin`` counts.
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "url": "https://staging.evil.invalid/governance/health"},
            run=lambda request: None,
            trusted_origin="https://staging.example.invalid",
        )


def test_staging_canary_rejects_malformed_trusted_origin(safe_canary):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            safe_canary,
            run=lambda request: None,
            trusted_origin="not-a-url",
        )


def test_staging_canary_rejects_trusted_origin_with_query(safe_canary):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            safe_canary,
            run=lambda request: None,
            trusted_origin="https://staging.example.invalid?x=1",
        )


def test_staging_canary_requires_trusted_origin_keyword(safe_canary):
    with pytest.raises(TypeError):
        run_staging_canary(safe_canary, run=lambda request: None)


def test_staging_canary_rejects_non_allowlisted_header(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "headers": {"X-Custom-Trace": "abc"}},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_allows_allowlisted_header(safe_canary, fake_http_runner, trusted_staging_origin):
    result = run_staging_canary(
        {**safe_canary, "headers": {"X-Request-Id": "abc"}},
        run=fake_http_runner,
        trusted_origin=trusted_staging_origin,
    )
    assert result.status == "pass"
    assert fake_http_runner.requests[0]["headers"] == {"X-Request-Id": "abc"}


def test_staging_canary_request_always_disables_redirects(
    safe_canary, fake_http_runner, trusted_staging_origin
):
    run_staging_canary(safe_canary, run=fake_http_runner, trusted_origin=trusted_staging_origin)
    assert fake_http_runner.requests[0]["allow_redirects"] is False


def test_staging_canary_off_origin_response_redirect_is_not_verified(
    safe_canary, trusted_staging_origin
):
    def _runner(request):
        return _FakeHttpResponse(
            204, headers={}, body=b"", url="https://attacker.invalid/other"
        )

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-off-origin-redirect"
    assert "attacker.invalid" not in result.observed


def test_staging_canary_same_origin_response_url_passes(safe_canary, trusted_staging_origin):
    def _runner(request):
        return _FakeHttpResponse(
            204,
            headers={},
            body=b"",
            url="https://staging.example.invalid/governance/health",
        )

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "pass"


@pytest.mark.parametrize("malformed_status", [True, "204", 204.0, 1000, 0])
def test_staging_canary_rejects_malformed_status(
    safe_canary, trusted_staging_origin, malformed_status
):
    def _runner(request):
        return _FakeHttpResponse(malformed_status, headers={}, body=b"")

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert str(malformed_status) not in result.observed


def test_staging_canary_rejects_non_mapping_headers(safe_canary, trusted_staging_origin):
    def _runner(request):
        response = _FakeHttpResponse(204, headers={}, body=b"")
        response.headers = ["not", "a", "mapping"]
        return response

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"


def test_staging_canary_rejects_non_string_header_values(safe_canary, trusted_staging_origin):
    def _runner(request):
        response = _FakeHttpResponse(204, headers={}, body=b"")
        response.headers = {"X-Deployment-Id": 12345}
        return response

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"


def test_staging_canary_rejects_non_bytes_str_body(safe_canary, trusted_staging_origin):
    def _runner(request):
        response = _FakeHttpResponse(204, headers={}, body=b"")
        response.body = 12345
        return response

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert "12345" not in result.observed


def test_staging_canary_rejects_out_of_range_expected_status(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "expected_status": 1000},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_rejects_boolean_expected_status(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "expected_status": True},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


# ---------------------------------------------------------------------------
# Round 5, issue 1: a response missing ``url``/``headers``/``body``
# entirely must never be treated the same as one that supplies an empty
# (but present) value for it -- especially ``body``, where the previous
# behavior computed a hash of ``b""`` and returned it as if that were a
# genuinely observed response, when the runner never actually reported a
# body at all.
# ---------------------------------------------------------------------------


def test_staging_canary_missing_url_attribute_is_not_verified_and_never_skips_origin_check(
    safe_canary, trusted_staging_origin
):
    def _runner(request):
        return _BareHttpResponse(status_code=204, headers={}, body=b"")

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert result.observed == "missing_response_field=url"


def test_staging_canary_missing_headers_attribute_is_not_verified(
    safe_canary, trusted_staging_origin
):
    def _runner(request):
        return _BareHttpResponse(
            status_code=204,
            body=b"",
            url="https://staging.example.invalid/governance/health",
        )

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert result.observed == "missing_response_field=headers"


def test_staging_canary_missing_body_attribute_is_not_verified_and_never_fabricates_empty_hash(
    safe_canary, trusted_staging_origin
):
    def _runner(request):
        return _BareHttpResponse(
            status_code=204,
            headers={},
            url="https://staging.example.invalid/governance/health",
        )

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert result.observed == "missing_response_field=body"
    # The sha256 of an empty body must never appear as fabricated "proof"
    # that a body was actually observed.
    empty_body_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert empty_body_sha256 not in result.observed
    assert "response_sha256" not in result.observed


def test_staging_canary_explicit_none_headers_value_is_not_verified(
    safe_canary, trusted_staging_origin
):
    # An explicit ``headers=None`` (attribute *present* but ``None``) is
    # just as unusable as a missing attribute -- it must never be
    # silently coerced into an empty mapping and treated as "no headers
    # were sent".
    def _runner(request):
        return _BareHttpResponse(
            status_code=204,
            headers=None,
            body=b"",
            url="https://staging.example.invalid/governance/health",
        )

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"


# ---------------------------------------------------------------------------
# Round 5, issue 5: a syntactically malformed port must be translated into
# ``UnsafeTargetError`` for contract-side URLs, never left to propagate as
# a raw, uncaught ``ValueError``; the same malformed port on a *response*
# URL must be reported ``not-verified``, never crash.
# ---------------------------------------------------------------------------


def test_staging_canary_rejects_malformed_port_in_contract_url(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            {**safe_canary, "url": "https://staging.example.invalid:99999999/governance/health"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )


def test_staging_canary_rejects_malformed_port_in_trusted_origin(safe_canary):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary(
            safe_canary,
            run=lambda request: None,
            trusted_origin="https://staging.example.invalid:not-a-port",
        )


def test_staging_canary_malformed_port_in_response_url_is_not_verified(
    safe_canary, trusted_staging_origin
):
    def _runner(request):
        return _FakeHttpResponse(
            204,
            headers={},
            body=b"",
            url="https://staging.example.invalid:99999999/governance/health",
        )

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-off-origin-redirect"


# ---------------------------------------------------------------------------
# Round 6, issue 2: every ``UnsafeTargetError`` message raised while
# validating a canary contract/URL must be fixed and redacted -- never
# interpolating the raw URL, the underlying parser error text, embedded
# userinfo, a port number, a query string, or any other contract-supplied
# value. A secret-looking marker planted in any of these positions must
# never appear in the raised message.
# ---------------------------------------------------------------------------

_SECRET_MARKER = "sekrit-token-should-never-leak-9f3c"


def test_unsafe_target_error_never_echoes_non_string_contract_url(
    safe_canary, trusted_staging_origin
):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {**safe_canary, "url": {_SECRET_MARKER: True}},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    assert _SECRET_MARKER not in str(excinfo.value)


def test_unsafe_target_error_never_echoes_http_scheme_url(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {**safe_canary, "url": f"http://staging.example.invalid/{_SECRET_MARKER}"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    assert _SECRET_MARKER not in str(excinfo.value)


def test_unsafe_target_error_never_echoes_malformed_port_or_parser_text(
    safe_canary, trusted_staging_origin
):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {**safe_canary, "url": "https://staging.example.invalid:99999999/governance/health"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    message = str(excinfo.value)
    assert "99999999" not in message
    assert "out of range" not in message.lower()
    assert "cast to integer" not in message.lower()


def test_unsafe_target_error_never_echoes_userinfo(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {
                **safe_canary,
                "url": f"https://{_SECRET_MARKER}:{_SECRET_MARKER}@staging.example.invalid/health",
            },
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    assert _SECRET_MARKER not in str(excinfo.value)


def test_unsafe_target_error_never_echoes_query_string(safe_canary, trusted_staging_origin):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {**safe_canary, "url": safe_canary["url"] + f"?token={_SECRET_MARKER}"},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    assert _SECRET_MARKER not in str(excinfo.value)


def test_unsafe_target_error_never_echoes_contract_environment_value(
    safe_canary, trusted_staging_origin
):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {**safe_canary, "environment": _SECRET_MARKER},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    assert _SECRET_MARKER not in str(excinfo.value)


def test_unsafe_target_error_never_echoes_contract_method_value(
    safe_canary, trusted_staging_origin
):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {**safe_canary, "method": _SECRET_MARKER},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    assert _SECRET_MARKER not in str(excinfo.value)


def test_unsafe_target_error_never_echoes_disallowed_header_name(
    safe_canary, trusted_staging_origin
):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {**safe_canary, "headers": {_SECRET_MARKER: "value"}},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    assert _SECRET_MARKER not in str(excinfo.value)


def test_unsafe_target_error_never_echoes_expected_status_value(
    safe_canary, trusted_staging_origin
):
    with pytest.raises(UnsafeTargetError) as excinfo:
        run_staging_canary(
            {**safe_canary, "expected_status": _SECRET_MARKER},
            run=lambda request: None,
            trusted_origin=trusted_staging_origin,
        )
    assert _SECRET_MARKER not in str(excinfo.value)


# --- Round 7, issue 1: bound canary execution/result -- a technical
# request timeout must be passed to the runner, the response body must
# be capped before it is hashed, response headers must be capped by
# count/name-length/value-length before being searched for a
# deployment-id, and the recorded deployment-id itself must be capped in
# length -- every bound here is a fixed technical safety constant, never
# a customer-tunable policy value, and every one of these limits being
# exceeded is reported "not-verified" without ever echoing the
# oversized/malformed raw value. ------------------------------------


def test_staging_canary_request_includes_fixed_timeout(
    safe_canary, fake_http_runner, trusted_staging_origin
):
    run_staging_canary(safe_canary, run=fake_http_runner, trusted_origin=trusted_staging_origin)
    request = fake_http_runner.requests[0]
    assert request["timeout_seconds"] == probes._STAGING_CANARY_REQUEST_TIMEOUT_SECONDS
    assert isinstance(probes._STAGING_CANARY_REQUEST_TIMEOUT_SECONDS, (int, float))
    assert probes._STAGING_CANARY_REQUEST_TIMEOUT_SECONDS > 0


def test_staging_canary_oversized_body_is_not_verified_without_echo(
    safe_canary, trusted_staging_origin
):
    oversized_body = b"A" * (probes._STAGING_CANARY_MAX_RESPONSE_BODY_BYTES + 1)

    def _runner(request):
        return _FakeHttpResponse(204, headers={}, body=oversized_body)

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert "A" * 100 not in result.observed
    assert "response_sha256" not in result.observed


def test_staging_canary_body_at_cap_is_still_hashed(safe_canary, trusted_staging_origin):
    body_at_cap = b"B" * probes._STAGING_CANARY_MAX_RESPONSE_BODY_BYTES

    def _runner(request):
        return _FakeHttpResponse(204, headers={}, body=body_at_cap)

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "pass"
    assert "response_sha256" in result.observed


def test_staging_canary_oversized_response_header_count_is_not_verified(
    safe_canary, trusted_staging_origin
):
    too_many_headers = {
        f"x-header-{i}": "v" for i in range(probes._STAGING_CANARY_MAX_RESPONSE_HEADER_COUNT + 1)
    }

    def _runner(request):
        return _FakeHttpResponse(204, headers=too_many_headers, body=b"")

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"


def test_staging_canary_oversized_response_header_name_is_not_verified(
    safe_canary, trusted_staging_origin
):
    oversized_name = "h" * (probes._STAGING_CANARY_MAX_RESPONSE_HEADER_NAME_LENGTH + 1)

    def _runner(request):
        return _FakeHttpResponse(204, headers={oversized_name: "v"}, body=b"")

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert oversized_name not in result.observed


def test_staging_canary_oversized_response_header_value_is_not_verified(
    safe_canary, trusted_staging_origin
):
    oversized_value = "v" * (probes._STAGING_CANARY_MAX_RESPONSE_HEADER_VALUE_LENGTH + 1)

    def _runner(request):
        return _FakeHttpResponse(204, headers={"x-header": oversized_value}, body=b"")

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert oversized_value not in result.observed


def test_staging_canary_oversized_deployment_id_is_not_verified_without_echo(
    safe_canary, trusted_staging_origin
):
    oversized_deployment_id = "d" * (probes._STAGING_CANARY_MAX_DEPLOYMENT_ID_LENGTH + 1)

    def _runner(request):
        return _FakeHttpResponse(
            204, headers={"X-Deployment-Id": oversized_deployment_id}, body=b""
        )

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert oversized_deployment_id not in result.observed


def test_staging_canary_deployment_id_at_cap_is_recorded(safe_canary, trusted_staging_origin):
    deployment_id_at_cap = "e" * probes._STAGING_CANARY_MAX_DEPLOYMENT_ID_LENGTH

    def _runner(request):
        return _FakeHttpResponse(204, headers={"X-Deployment-Id": deployment_id_at_cap}, body=b"")

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "pass"
    assert deployment_id_at_cap in result.observed


# --- Round 8, issue 1: a ``str`` canary response body must never be
# fully UTF-8-encoded (and thus fully allocated) before the byte-size
# cap is enforced -- ``_bounded_utf8_body_bytes`` bounds worst-case
# memory via a cheap character-count precheck plus incremental,
# fixed-size-chunk encoding that bails out the instant the running byte
# total would exceed the cap. -----------------------------------------


def test_bounded_utf8_body_bytes_matches_full_encode_for_ascii_under_cap():
    text = "hello world" * 10
    result = probes._bounded_utf8_body_bytes(text, max_bytes=1024)
    assert result == text.encode("utf-8")


def test_bounded_utf8_body_bytes_matches_full_encode_across_chunk_boundary():
    # A run of 2-byte-per-character text long enough to straddle the
    # internal chunking boundary at least once, to prove chunk-wise
    # encoding never mis-splits a multi-byte code point.
    chunk = probes._STAGING_CANARY_BODY_ENCODE_CHUNK_CHARS
    text = "\u00e9" * (chunk + 5)
    max_bytes = len(text.encode("utf-8")) + 1
    result = probes._bounded_utf8_body_bytes(text, max_bytes=max_bytes)
    assert result == text.encode("utf-8")


def test_bounded_utf8_body_bytes_rejects_obviously_oversized_char_count():
    # Character count alone already exceeds max_bytes, so this must be
    # rejected by the cheap precheck without ever encoding.
    text = "a" * 2000
    assert probes._bounded_utf8_body_bytes(text, max_bytes=1000) is None


def test_bounded_utf8_body_bytes_rejects_multibyte_body_under_char_precheck():
    # 600_000 characters is under a 1_048_576-byte cap in character-count
    # terms (so the cheap precheck alone would let it through), but each
    # character is 2 UTF-8 bytes, so the true encoded size
    # (1_200_000 bytes) exceeds the cap -- this must only be caught by
    # the incremental running-total check.
    max_bytes = 1_048_576
    text = "\u00e9" * 600_000
    assert len(text) <= max_bytes  # precheck alone would not reject this
    assert probes._bounded_utf8_body_bytes(text, max_bytes=max_bytes) is None


def test_bounded_utf8_body_bytes_accepts_exact_cap_with_multibyte_chars():
    max_bytes = 1_048_576
    assert max_bytes % 2 == 0
    text = "\u00e9" * (max_bytes // 2)
    result = probes._bounded_utf8_body_bytes(text, max_bytes=max_bytes)
    assert result == text.encode("utf-8")
    assert len(result) == max_bytes


def test_staging_canary_oversized_multibyte_str_body_is_not_verified_without_echo(
    safe_canary, trusted_staging_origin
):
    oversized_text = "\u00e9" * 600_000  # 1_200_000 UTF-8 bytes, > 1 MiB cap

    def _runner(request):
        return _FakeHttpResponse(204, headers={}, body=oversized_text)

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert oversized_text[:100] not in result.observed
    assert "response_sha256" not in result.observed


def test_staging_canary_str_body_at_cap_is_still_hashed(safe_canary, trusted_staging_origin):
    body_at_cap = "\u00e9" * (probes._STAGING_CANARY_MAX_RESPONSE_BODY_BYTES // 2)

    def _runner(request):
        return _FakeHttpResponse(204, headers={}, body=body_at_cap)

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "pass"
    assert "response_sha256" in result.observed
    assert result.observed.endswith(
        f"response_sha256=sha256:{hashlib.sha256(body_at_cap.encode('utf-8')).hexdigest()}"
    )


# --- Round 9: an unpaired Unicode surrogate in a canary response body
# (a lone high/low surrogate code point, which Python's ``str`` type can
# perfectly well hold -- for example decoded from a runner's own
# permissive text handling -- cannot be encoded to UTF-8 with the
# strict error handler; encoding it must be *contained*, never allowed
# to propagate an unhandled ``UnicodeEncodeError`` out of this module.
# ------------------------------------------------------------------


def test_bounded_utf8_body_bytes_rejects_lone_surrogate():
    assert probes._bounded_utf8_body_bytes("\ud800", max_bytes=1_000_000) is None


def test_bounded_utf8_body_bytes_rejects_lone_surrogate_within_chunk():
    chunk = probes._STAGING_CANARY_BODY_ENCODE_CHUNK_CHARS
    text = ("a" * (chunk + 5)) + "\ud800"
    assert probes._bounded_utf8_body_bytes(text, max_bytes=1_000_000) is None


def test_staging_canary_lone_surrogate_str_body_is_not_verified_without_echo(
    safe_canary, trusted_staging_origin
):
    def _runner(request):
        return _FakeHttpResponse(204, headers={}, body="\ud800")

    result = run_staging_canary(safe_canary, run=_runner, trusted_origin=trusted_staging_origin)
    assert result.status == "not-verified"
    assert result.reason_code == "staging-canary-malformed-response"
    assert "\\ud800" not in result.observed
    assert "response_sha256" not in result.observed
