"""Regression tests for probe-evidence binding and conditional APR-001.

Two orchestration defects made a faithful, fixture-driven assessment
impossible to report honestly:

* ``ProbeResult.evidence_refs`` were never converted into real
  ``contracts.EvidenceRef`` entries, so *every* passing probe cited
  evidence that did not exist in the assessment and
  ``render._required_evidence_is_untrustworthy`` correctly refused to
  ever call the result ``governed``; and
* ``APR-001`` was emitted unconditionally as ``not-verified``, even for
  a target that declared a complete, deterministic approval binding the
  real ``probes.run_approval_probe`` could have proven for itself.

These tests pin the corrected behavior and, just as importantly, pin
what must *never* happen: evidence synthesized from an id, a probe
whose underlying artifact is missing quietly passing, an invented
approver/tenant/policy, or a raw probe payload reaching an artifact.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_probe_evidence_binding.py -q
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import canonical
import contracts
import governed_actions
import probes
import render


REPOSITORY = "octo-org/probe-evidence"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
NOW = "2026-09-01T12:00:00Z"

# A synthetic, deterministic approval binding a *target* declares for
# itself. Nothing here is ever supplied by the orchestrator.
DECLARED_BINDING = {
    "target_scope": "account:synthetic-001",
    "requesting_subject": "subject:pseudonymous-requester",
    "approving_subject": "subject:pseudonymous-approver",
    "approving_role": "role:synthetic-reviewer",
    "tenant": "tenant:synthetic-001",
    "policy_id": "policy:refund-v1",
    "policy_hash": "sha256:" + "a" * 64,
    "action_id": "payments.refund",
    "arguments": {"amount": 7, "currency": "USD"},
    "issued_at": "2026-09-01T11:55:00Z",
    "expires_at": "2026-09-01T12:05:00Z",
    "nonce": "nonce-0001",
}

SECRET_MARKER = "sk-live-do-not-leak-0001"

AGENT_MODULE = '''\
"""Synthetic dispatch seam: enforcement, approval, and output in one module."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

AUDIT_EVENTS = []

SECRET_ARGUMENT = "{secret}"


def _append(ledger_path, record):
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\\n")
        handle.flush()


def _argument_hash(arguments):
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _enforce(case, ledger_path):
    fault = case["probe_id"]
    arguments = dict(case.get("arguments") or {{}})
    arguments["api_key"] = SECRET_ARGUMENT
    digest = _argument_hash(arguments)
    _append(ledger_path, {{"event": "start", "action_id": case["action_id"]}})
    AUDIT_EVENTS.append({{"audit_id": "audit-" + fault, "decision": "recorded"}})
    if fault == "transform":
        transformed = dict(arguments)
        transformed["redacted"] = True
        digest = _argument_hash(transformed)
        _append(
            ledger_path,
            {{
                "event": "invocation",
                "action_id": case["action_id"],
                "argument_hash": digest,
                "original_argument_hash": _argument_hash(arguments),
            }},
        )
        _append(
            ledger_path,
            {{"event": "decision", "action_id": case["action_id"], "decision": "transform"}},
        )
        return {{
            "decision": "transform",
            "invocation_count": 1,
            "argument_hash": digest,
            "exception_class": None,
        }}
    _append(
        ledger_path,
        {{"event": "decision", "action_id": case["action_id"], "decision": "deny"}},
    )
    if fault == "raise":
        raise RuntimeError("synthetic fault")
    if fault == "sleep":
        import time

        time.sleep(30)
    if fault == "invalid":
        return {{
            "decision": "not-a-verdict",
            "invocation_count": 0,
            "argument_hash": None,
            "exception_class": None,
        }}
    return {{
        "decision": "deny",
        "invocation_count": 0,
        "argument_hash": None,
        "exception_class": None,
    }}


def redeem(nonce, digest, expires_at, now, ledger_path):
    records = []
    path = Path(ledger_path)
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    accepted = now < expires_at and not any(
        record.get("event") == "decision"
        and record.get("nonce") == nonce
        and record.get("accepted") is True
        for record in records
    )
    _append(
        ledger_path,
        {{"event": "decision", "nonce": nonce, "digest": digest, "accepted": accepted}},
    )
    AUDIT_EVENTS.append({{"audit_id": "audit-approval-" + nonce, "decision": "recorded"}})
    if accepted:
        _append(ledger_path, {{"event": "invocation", "nonce": nonce}})
    return {{"accepted": accepted}}


def emit_output(verdict, ledger_path):
    _append(ledger_path, {{"event": "output", "verdict": verdict, "released_bytes": 0}})
    AUDIT_EVENTS.append({{"audit_id": "audit-output-" + verdict, "decision": "recorded"}})
    return {{"verdict": verdict, "released_bytes": 0}}


def dispatch(*args):
    if len(args) == 5:
        return redeem(*args)
    if len(args) == 2 and isinstance(args[0], str):
        return emit_output(*args)
    return _enforce(*args)
'''


def _contract(*, approval: bool) -> dict:
    contract = {
        "dispatch": "app.agent:dispatch",
        "audit_sink": "app.agent:AUDIT_EVENTS",
        "observation_ledger": "governance/observation-ledger.jsonl",
        "nonce_ledger": "governance/nonce-ledger.jsonl",
        "timeout_ms": 1500,
        "side_effect_mode": "synthetic",
        "actions": ["payments.refund"],
    }
    if approval:
        contract["approval_binding"] = DECLARED_BINDING
    return contract


def _write_target(root: Path, *, approval: bool) -> Path:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "governance").mkdir(parents=True, exist_ok=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "agent.py").write_text(
        AGENT_MODULE.format(secret=SECRET_MARKER), encoding="utf-8"
    )
    (root / "governance" / "probe-contract.json").write_text(
        json.dumps(_contract(approval=approval), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        },
    )


@pytest.fixture
def target(tmp_path: Path) -> Path:
    root = _write_target(tmp_path / "target", approval=False)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    return root


@pytest.fixture
def approval_target(tmp_path: Path) -> Path:
    root = _write_target(tmp_path / "approval-target", approval=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    return root


def _source() -> contracts.SourceRef:
    return contracts.SourceRef(repository=REPOSITORY, commit=COMMIT, dirty=False)


def _options(root: Path) -> contracts.AssessmentOptions:
    return contracts.AssessmentOptions(root=root, phase="pre-deploy", now=NOW)


def _assess(root: Path) -> contracts.AssessmentResult:
    return governed_actions._assess_pre_deploy(root, _source(), _options(root))


# ---------------------------------------------------------------------------
# Probe-side provenance
# ---------------------------------------------------------------------------


def test_probe_evidence_items_are_real_artifact_digests(target: Path):
    # Every cited evidence id must resolve to provenance describing an
    # artifact the probe actually observed, and its sha256 must be that
    # artifact's own digest -- never a hash of the evidence id itself.
    results = probes.run_enforcement_probe_set(target) + probes.run_privacy_probe_set(target)
    citing = [probe for probe in results if probe.evidence_refs]
    assert citing, "expected at least one probe citing evidence"
    for probe in citing:
        resolved = {item.evidence_id for item in probe.evidence_items}
        assert set(probe.evidence_refs) <= resolved
        for item in probe.evidence_items:
            assert item.sha256.startswith("sha256:")
            forged = "sha256:" + canonical.sha256_hex(item.evidence_id.encode("utf-8"))
            assert item.sha256 != forged
            assert item.source


# ---------------------------------------------------------------------------
# Orchestration binding
# ---------------------------------------------------------------------------


def test_pre_deploy_binds_every_probe_citation_to_real_evidence(target: Path):
    result = _assess(target)

    cited = {ref for probe in result.probes for ref in probe.evidence_refs}
    assert cited, "expected the probe suite to cite evidence"
    evidence_by_id = {ref.evidence_id: ref for ref in result.evidence}
    for evidence_id in cited:
        ref = evidence_by_id[evidence_id]
        assert ref.repository == REPOSITORY
        assert ref.source_commit == COMMIT
        assert ref.phase == "pre-deploy"
        assert ref.collected_at == NOW
        assert ref.live_verified is False
        assert ref.policy_set_sha256 == render.canonical_policy_set_sha256(result.policy_hashes)
    assert render._required_evidence_is_untrustworthy(result, render._try_parse_rfc3339(NOW)) is False


def test_unresolvable_probe_citation_can_never_pass(target: Path, monkeypatch):
    # A probe citing evidence whose underlying artifact was never
    # observed must be downgraded to not-verified, never bound to
    # synthesized provenance.
    real = probes.run_enforcement_probe_set

    def _strip_items(root):
        return tuple(replace(probe, evidence_items=()) for probe in real(root))

    monkeypatch.setattr(probes, "run_enforcement_probe_set", _strip_items)
    result = _assess(target)

    evidence_ids = {ref.evidence_id for ref in result.evidence}
    for probe in result.probes:
        # No probe -- passing or failing -- may cite an id this
        # assessment could not bind to a real observed artifact.
        assert set(probe.evidence_refs) <= evidence_ids
    downgraded = [
        finding for finding in result.findings if finding.reason_code == "probe-evidence-unresolved"
    ]
    assert downgraded and all(finding.status == "not-verified" for finding in downgraded)
    for evidence_id in {ref for probe in result.probes for ref in probe.evidence_refs}:
        assert any(ref.evidence_id == evidence_id for ref in result.evidence)


# ---------------------------------------------------------------------------
# APR-001
# ---------------------------------------------------------------------------


def test_declared_approval_binding_yields_honest_apr001_pass(approval_target: Path):
    result = _assess(approval_target)

    assert [finding for finding in result.findings if finding.finding_id == "APR-001"] == []
    approval_probes = [
        probe for probe in result.probes if probe.probe_id == probes._APPROVAL_PROBE_ID
    ]
    assert len(approval_probes) == 1
    assert approval_probes[0].status == "pass"
    assert approval_probes[0].observed == "approval_accepted"
    assert approval_probes[0].evidence_refs
    assert set(approval_probes[0].evidence_refs) <= {ref.evidence_id for ref in result.evidence}


def test_replaying_a_declared_binding_yields_apr001_must_fix(approval_target: Path):
    binding = governed_actions._declared_approval_binding(approval_target)
    assert binding is not None
    first = probes.run_approval_probe(approval_target, binding, now=NOW)
    assert first.status == "pass"

    result = _assess(approval_target)
    approval_probes = [
        probe for probe in result.probes if probe.probe_id == probes._APPROVAL_PROBE_ID
    ]
    assert len(approval_probes) == 1
    assert approval_probes[0].status == "pass"
    assert approval_probes[0].observed == "replay_rejected"

    # A target that fails open on replay is a real must-fix.
    ledger = approval_target / "governance" / "nonce-ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    agent = approval_target / "app" / "agent.py"
    agent.write_text(
        agent.read_text(encoding="utf-8").replace(
            "accepted = now < expires_at and not any(", "accepted = now < expires_at or any("
        ),
        encoding="utf-8",
    )
    probes.run_approval_probe(approval_target, binding, now=NOW)
    replayed = _assess(approval_target)
    failing = [
        probe for probe in replayed.probes if probe.probe_id == probes._APPROVAL_PROBE_ID
    ]
    assert failing[0].status == "must-fix"
    assert failing[0].reason_code == "APR-001"


def test_absent_approval_binding_remains_not_verified(target: Path):
    result = _assess(target)

    approval_findings = [finding for finding in result.findings if finding.finding_id == "APR-001"]
    assert len(approval_findings) == 1
    assert approval_findings[0].status == "not-verified"
    assert approval_findings[0].reason_code == "approval-binding-unavailable"
    assert not [
        probe for probe in result.probes if probe.probe_id == probes._APPROVAL_PROBE_ID
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        {"approving_subject": ""},
        {"arguments": {"nested": {"amount": 7}}},
        {"expires_at": None},
    ],
)
def test_incomplete_declared_binding_is_never_completed_by_the_orchestrator(
    tmp_path: Path, mutation
):
    root = _write_target(tmp_path / "partial", approval=True)
    contract = json.loads((root / "governance" / "probe-contract.json").read_text(encoding="utf-8"))
    contract["approval_binding"].update(mutation)
    (root / "governance" / "probe-contract.json").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert governed_actions._declared_approval_binding(root) is None


def test_missing_binding_field_is_never_defaulted(tmp_path: Path):
    root = _write_target(tmp_path / "missing-field", approval=True)
    contract = json.loads((root / "governance" / "probe-contract.json").read_text(encoding="utf-8"))
    del contract["approval_binding"]["tenant"]
    (root / "governance" / "probe-contract.json").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert governed_actions._declared_approval_binding(root) is None


def test_live_side_effect_mode_never_redeems_a_declared_binding(tmp_path: Path):
    root = _write_target(tmp_path / "live", approval=True)
    contract = json.loads((root / "governance" / "probe-contract.json").read_text(encoding="utf-8"))
    contract["side_effect_mode"] = "live"
    (root / "governance" / "probe-contract.json").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert governed_actions._declared_approval_binding(root) is None


# ---------------------------------------------------------------------------
# Malformed contracts and payload containment
# ---------------------------------------------------------------------------


def test_malformed_probe_contract_still_raises(target: Path):
    (target / "governance" / "probe-contract.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        _assess(target)


def test_tampered_probe_contract_still_raises(target: Path):
    contract = _contract(approval=False)
    del contract["observation_ledger"]
    (target / "governance" / "probe-contract.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        _assess(target)


def test_raw_probe_values_never_enter_artifacts(target: Path):
    result = _assess(target)
    manifest = json.dumps(render.build_manifest(result), sort_keys=True)
    evidence_pack = render.render_evidence_pack(result)

    assert SECRET_MARKER not in manifest
    assert SECRET_MARKER not in evidence_pack
    for ref in result.evidence:
        assert SECRET_MARKER not in json.dumps(ref.__dict__, default=str)
