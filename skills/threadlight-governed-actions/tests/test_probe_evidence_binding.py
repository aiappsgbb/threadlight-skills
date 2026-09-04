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
import shutil
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

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

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
        "execution_dispatch": "app.agent:dispatch",
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


def test_unresolvable_path_receipt_maps_to_med002_with_path_evidence(
    tmp_path: Path,
):
    probe = contracts.ProbeResult(
        probe_id="path-dispatch-allow",
        action_id="payments.refund",
        path_id="path-1",
        mode="background",
        status="pass",
        reason_code="path-dispatch-mediated",
        expected="pre_action_decision_before_invocation_or_deny",
        observed="pre_action_decision_before_invocation",
        evidence_refs=("missing-proof",),
        evidence_items=(),
    )

    kept, findings, evidence = governed_actions._bind_probe_evidence(
        (probe,),
        contracts.SourceRef(repository=REPOSITORY, commit=COMMIT, dirty=False),
        contracts.AssessmentOptions(root=tmp_path, phase="pre-deploy", now=NOW),
        (),
        path_evidence_by_id={"path-1": ("agent.yaml", "app/agent.py")},
    )

    assert kept[0].status == "not-verified"
    assert evidence == ()
    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_id == "MED-002"
    assert finding.affected_paths == ("path-1",)
    assert finding.evidence_refs == ("agent.yaml", "app/agent.py")


# ---------------------------------------------------------------------------
# APR-001
# ---------------------------------------------------------------------------


def test_declared_approval_binding_yields_honest_apr001_pass(approval_target: Path):
    result = _assess(approval_target)

    assert [finding for finding in result.findings if finding.finding_id == "APR-001"] == []
    approval_probes = [
        probe for probe in result.probes if probe.probe_id == probes._APPROVAL_PROBE_ID
    ]
    # One assessment drives the whole anti-replay sequence: the first
    # use, a byte-identical replay of it, and one mutated-binding
    # attempt per design-required dimension -- never a lone first use,
    # which could only ever have proven the acceptance.
    assert len(approval_probes) == 2 + len(probes._APPROVAL_MUTATION_FIELDS)
    assert {probe.status for probe in approval_probes} == {"pass"}
    observed = [probe.observed for probe in approval_probes]
    assert observed[0] == "approval_accepted"
    assert observed.count("replay_rejected") == 1
    assert observed.count("binding_mismatch_rejected") == len(
        probes._APPROVAL_MUTATION_FIELDS
    )
    evidence_ids = {ref.evidence_id for ref in result.evidence}
    for probe in approval_probes:
        assert probe.evidence_refs
        assert set(probe.evidence_refs) <= evidence_ids


def test_assessment_ignores_prior_target_side_nonce_state(approval_target: Path):
    # Probe redemption state is this assessment's own, never the
    # target's: a nonce ledger already sitting in the target -- even one
    # that already recorded the declared nonce as consumed -- must not
    # change a single thing about the assessment, and must itself be
    # left exactly as it was found.
    ledger = approval_target / "governance" / "nonce-ledger.jsonl"
    binding = governed_actions._declared_approval_binding(approval_target)
    assert binding is not None
    stale = (
        json.dumps(
            {
                "event": "decision",
                "nonce": binding.nonce,
                "digest": probes.approval_digest(binding),
                "accepted": True,
            }
        )
        + "\n"
        + json.dumps({"event": "invocation", "nonce": binding.nonce})
        + "\n"
    )
    ledger.write_text(stale, encoding="utf-8")

    result = _assess(approval_target)

    assert [finding for finding in result.findings if finding.finding_id == "APR-001"] == []
    approval_probes = [
        probe for probe in result.probes if probe.probe_id == probes._APPROVAL_PROBE_ID
    ]
    assert approval_probes[0].observed == "approval_accepted"
    assert ledger.read_text(encoding="utf-8") == stale
    assert not list((approval_target / "governance").glob(".approval-probe-*"))


def test_fail_open_redemption_yields_apr001_must_fix_on_the_first_assessment(
    approval_target: Path,
):
    # The defect this pins: a redemption seam that grants every attempt
    # must be caught by the very first assessment of an untouched
    # target, with no prior probe run and nothing pre-seeded -- the
    # assessment itself replays and mutates the binding.
    agent = approval_target / "app" / "agent.py"
    agent.write_text(
        agent.read_text(encoding="utf-8").replace(
            "accepted = now < expires_at and not any(",
            "accepted = now < expires_at or any(",
        ),
        encoding="utf-8",
    )

    result = _assess(approval_target)

    failing = [
        probe
        for probe in result.probes
        if probe.probe_id == probes._APPROVAL_PROBE_ID and probe.status == "must-fix"
    ]
    assert failing
    assert {probe.reason_code for probe in failing} == {"APR-001"}
    approval_findings = [
        finding for finding in result.findings if finding.finding_id == "APR-001"
    ]
    assert approval_findings
    assert {finding.status for finding in approval_findings} == {"must-fix"}
    assert not (approval_target / "governance" / "nonce-ledger.jsonl").exists()


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


# ---------------------------------------------------------------------------
# Colliding evidence ids: separate probe processes reusing the same id
# ---------------------------------------------------------------------------
#
# Separate probe child processes commonly each emit the *same* evidence
# id (e.g. the checked-in ``conformant-maf`` fixture's own audit-sequence
# counter is a fresh module global in every subprocess, so each probe
# case's first durable audit record is always literally "audit-0001")
# while backing it with a *different* real ledger record and digest.
# ``_bind_probe_evidence`` must never silently publish the first such
# digest as if it were every colliding probe's own provenance.


def _copy_git_fixture(tmp_path: Path, fixture_name: str) -> Path:
    """Copy a checked-in ``tests/fixtures`` root into an isolated temp
    directory and commit it as a disposable git repo, exactly as
    ``test_golden_fixtures.py`` does -- never mutating the checked-in
    tree itself.
    """
    dest = tmp_path / fixture_name
    shutil.copytree(FIXTURES_DIR / fixture_name, dest)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "remote", "add", "origin", f"https://github.com/{REPOSITORY}.git")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-qm", "fixture")
    return dest


def test_conformant_maf_fixture_still_reproduces_the_colliding_id(tmp_path: Path):
    # Pins the defect's own reproduction: pure probe-side provenance,
    # independent of any orchestration binding, must still show at
    # least one evidence id backed by more than one distinct
    # (kind, source, sha256) tuple -- five, for "audit-0001" -- or this
    # regression would no longer be testing what it claims to.
    root = _copy_git_fixture(tmp_path, "conformant-maf")
    raw = probes.run_enforcement_probe_set(root) + probes.run_privacy_probe_set(root)
    provenance_by_id = {}
    for probe in raw:
        for item in probe.evidence_items:
            provenance_by_id.setdefault(item.evidence_id, set()).add(
                (item.kind, item.source, item.sha256)
            )
    conflicting = {ref: provs for ref, provs in provenance_by_id.items() if len(provs) > 1}
    assert conflicting.keys() == {"audit-0001"}
    assert len(conflicting["audit-0001"]) == 5


def _fake_collect_live_evidence(root, options, default_branch):
    """Fixture-local stand-in for a live GitHub/Azure read, exactly as
    ``test_golden_fixtures.py`` uses: reads ``conformant-maf``'s own
    ``governance/change-plane.json`` in place of a real API call.
    Production code never reads this file on its own.
    """
    change_plane_path = Path(root) / "governance" / "change-plane.json"
    if not change_plane_path.is_file():
        return None, None, ()
    data = json.loads(change_plane_path.read_text(encoding="utf-8"))
    github = data.get("github")
    azure = data.get("azure")
    return (
        github if isinstance(github, dict) else None,
        azure if isinstance(azure, dict) else None,
        (),
    )


def test_pre_deploy_binds_every_probe_citation_to_its_own_exact_provenance(
    tmp_path: Path, monkeypatch
):
    # The core regression: every final probe citation in the assessed
    # result must resolve to an EvidenceRef whose kind/source/sha256
    # matches *that exact probe's* own observed provenance -- never
    # another colliding probe's -- and a probe whose citation was
    # rewritten to a new canonical id must have that id, not the
    # original, as its own citation (no dangling old id).
    root = _copy_git_fixture(tmp_path, "conformant-maf")
    monkeypatch.setattr(
        governed_actions, "_collect_selected_live_evidence", _fake_collect_live_evidence
    )
    result = governed_actions._assess_pre_deploy(
        root,
        contracts.SourceRef(repository=REPOSITORY, commit=COMMIT, dirty=False),
        contracts.AssessmentOptions(
            root=root,
            phase="pre-deploy",
            live_github=True,
            live_azure=True,
            repository=REPOSITORY,
            now=NOW,
        ),
    )

    citing = [probe for probe in result.probes if probe.evidence_refs]
    assert citing, "expected at least one probe citing evidence"
    evidence_by_id = {ref.evidence_id: ref for ref in result.evidence}
    for probe in citing:
        assert len(probe.evidence_items) == len(probe.evidence_refs)
        for ref, item in zip(probe.evidence_refs, probe.evidence_items):
            # No dangling old id: the probe's own citation and its own
            # provenance item agree on the id actually being cited.
            assert item.evidence_id == ref
            bound = evidence_by_id[ref]
            assert (bound.kind, bound.source, bound.sha256) == (
                item.kind,
                item.source,
                item.sha256,
            )

    # The original colliding id must never be a live citation once its
    # provenance actually conflicted -- every citation that used to
    # share it now carries its own distinct, rewritten id.
    rewritten = {
        ref
        for probe in citing
        for ref in probe.evidence_refs
        if ref != "audit-0001" and ref.startswith("audit-0001")
    }
    assert len(rewritten) >= 2

    # No probe was falsely downgraded merely because independent probes
    # collided on an id: every distinct observed artifact is honestly
    # represented. Every discovered path is now backed by its own executed
    # receipt, so this conformant fixture remains governed.
    assert not [
        finding
        for finding in result.findings
        if finding.reason_code == "probe-evidence-unresolved"
    ]
    manifest = render.build_manifest(result)
    assert manifest["summary"]["verdict"] == "governed"


def test_repeated_identical_provenance_is_still_deduplicated_to_one_id():
    # Two probes legitimately citing the exact same real artifact under
    # the same id must keep sharing that one id and one EvidenceRef --
    # collision handling must never rewrite an id that was never
    # actually in conflict.
    tuple_ = ("probe-audit-ledger-record", "governance/ledger.jsonl", "sha256:" + "a" * 64)
    item = contracts.ProbeEvidence(evidence_id="audit-0001", kind=tuple_[0], source=tuple_[1], sha256=tuple_[2])
    probe_a = contracts.ProbeResult(
        probe_id="deny", action_id="payments.refund", path_id=None, status="pass",
        reason_code="ENF-PASS", expected="tool_not_invoked", observed="tool_not_invoked",
        evidence_refs=("audit-0001",), evidence_items=(item,),
    )
    probe_b = contracts.ProbeResult(
        probe_id="raise", action_id="payments.refund", path_id=None, status="pass",
        reason_code="ENF-PASS", expected="tool_not_invoked", observed="tool_not_invoked",
        evidence_refs=("audit-0001",), evidence_items=(item,),
    )
    kept, findings, evidence = governed_actions._bind_probe_evidence(
        (probe_a, probe_b),
        contracts.SourceRef(repository=REPOSITORY, commit=COMMIT, dirty=False),
        contracts.AssessmentOptions(root=Path("."), phase="pre-deploy", now=NOW),
        (),
    )
    assert [probe.evidence_refs for probe in kept] == [("audit-0001",), ("audit-0001",)]
    assert [ref.evidence_id for ref in evidence] == ["audit-0001"]
    assert (evidence[0].kind, evidence[0].source, evidence[0].sha256) == tuple_
    assert not [f for f in findings if f.reason_code == "probe-evidence-unresolved"]


def _synthetic_probe(probe_id: str, evidence_id: str, tuple_) -> contracts.ProbeResult:
    kind, source, sha256 = tuple_
    return contracts.ProbeResult(
        probe_id=probe_id,
        action_id="payments.refund",
        path_id=None,
        status="pass",
        reason_code="ENF-PASS",
        expected="tool_not_invoked",
        observed="tool_not_invoked",
        evidence_refs=(evidence_id,),
        evidence_items=(
            contracts.ProbeEvidence(evidence_id=evidence_id, kind=kind, source=source, sha256=sha256),
        ),
    )


def test_conflicting_ids_are_deterministically_separated_never_reusing_false_evidence():
    # Two probes citing the *same* id but backed by genuinely different
    # observed provenance must each resolve to their own unique,
    # rewritten canonical id -- reusing the first probe's digest for
    # the second is exactly the defect this regression pins.
    tuple_a = ("probe-audit-ledger-record", "governance/ledger.jsonl", "sha256:" + "a" * 64)
    tuple_b = ("probe-audit-ledger-record", "governance/ledger.jsonl", "sha256:" + "b" * 64)
    probe_a = _synthetic_probe("deny", "audit-0001", tuple_a)
    probe_b = _synthetic_probe("transform", "audit-0001", tuple_b)

    source = contracts.SourceRef(repository=REPOSITORY, commit=COMMIT, dirty=False)
    options = contracts.AssessmentOptions(root=Path("."), phase="pre-deploy", now=NOW)
    kept, findings, evidence = governed_actions._bind_probe_evidence(
        (probe_a, probe_b), source, options, ()
    )

    assert not [f for f in findings if f.reason_code == "probe-evidence-unresolved"]
    ref_a = kept[0].evidence_refs[0]
    ref_b = kept[1].evidence_refs[0]
    assert ref_a != ref_b
    assert ref_a != "audit-0001" or ref_b != "audit-0001"
    evidence_by_id = {ref.evidence_id: ref for ref in evidence}
    assert (evidence_by_id[ref_a].kind, evidence_by_id[ref_a].source, evidence_by_id[ref_a].sha256) == tuple_a
    assert (evidence_by_id[ref_b].kind, evidence_by_id[ref_b].source, evidence_by_id[ref_b].sha256) == tuple_b

    # Deterministic and payload-free: rerunning with the exact same
    # (unordered) inputs -- and with the process's own hash seed
    # varying -- must produce byte-identical rewritten ids.
    kept_again, _, _ = governed_actions._bind_probe_evidence(
        (probe_b, probe_a), source, options, ()
    )
    ids_again = {probe.probe_id: probe.evidence_refs[0] for probe in kept_again}
    assert ids_again["deny"] == ref_a
    assert ids_again["transform"] == ref_b


def test_conflicting_id_fails_closed_when_canonical_rewrite_would_collide(tmp_path: Path):
    # If a deterministically-rewritten id would collide with a
    # different id's already-bound (and differently-provenanced)
    # evidence, that specific probe's citation is never silently
    # reused as if it were safe: it is downgraded through the existing
    # probe-evidence-unresolved semantics instead, while a sibling
    # colliding probe whose rewrite does not collide is still honestly
    # bound and kept passing.
    colliding_tuple = ("probe-audit-ledger-record", "governance/ledger.jsonl", "sha256:" + "a" * 64)
    disambiguated_id = governed_actions._disambiguated_evidence_id(
        "audit-0001",
        contracts.ProbeEvidence(evidence_id="audit-0001", kind=colliding_tuple[0], source=colliding_tuple[1], sha256=colliding_tuple[2]),
    )
    # An unrelated, non-conflicting probe whose own (real) evidence id
    # happens to already equal the string our rewrite would produce.
    preexisting_tuple = ("static-file-hash", "specs/SPEC.md", "sha256:" + "f" * 64)
    probe_pre = _synthetic_probe("pre-existing", disambiguated_id, preexisting_tuple)
    probe_a = _synthetic_probe("deny", "audit-0001", colliding_tuple)
    other_tuple = ("probe-audit-ledger-record", "governance/ledger.jsonl", "sha256:" + "b" * 64)
    probe_b = _synthetic_probe("transform", "audit-0001", other_tuple)

    source = contracts.SourceRef(repository=REPOSITORY, commit=COMMIT, dirty=False)
    options = contracts.AssessmentOptions(root=Path("."), phase="pre-deploy", now=NOW)
    kept, findings, evidence = governed_actions._bind_probe_evidence(
        (probe_pre, probe_a, probe_b), source, options, ()
    )

    by_probe_id = {probe.probe_id: probe for probe in kept}
    assert by_probe_id["pre-existing"].evidence_refs == (disambiguated_id,)
    # The colliding rewrite is never reused as if it were safe: "deny"
    # is downgraded rather than silently bound to "pre-existing"'s
    # evidence.
    assert by_probe_id["deny"].status == "not-verified"
    assert by_probe_id["deny"].reason_code == "probe-evidence-unresolved"
    assert by_probe_id["deny"].evidence_refs == ()
    downgraded = [f for f in findings if f.reason_code == "probe-evidence-unresolved"]
    assert len(downgraded) == 1
    assert downgraded[0].affected_actions == ("payments.refund",)
    # The sibling conflicting probe, whose rewrite does not collide,
    # still gets its own distinct, correctly-bound id.
    assert by_probe_id["transform"].status == "pass"
    ref_b = by_probe_id["transform"].evidence_refs[0]
    evidence_by_id = {ref.evidence_id: ref for ref in evidence}
    assert (evidence_by_id[ref_b].kind, evidence_by_id[ref_b].source, evidence_by_id[ref_b].sha256) == other_tuple
    assert evidence_by_id[disambiguated_id].sha256 == preexisting_tuple[2]


def test_conflicting_id_fails_closed_when_literal_probe_arrives_after_the_rewrite(tmp_path: Path):
    # Same fixture as
    # test_conflicting_id_fails_closed_when_canonical_rewrite_would_collide,
    # but with the non-conflicting, literal-id probe processed *last*
    # instead of first. Once "deny" has already been rewritten to
    # disambiguated_id and bound into evidence_by_id, "pre-existing"
    # citing that exact string literally must never be silently
    # accepted as a dedup of "deny"'s evidence just because
    # `final_id in evidence_by_id` was already true: the two disagree
    # on (kind, source, sha256), so "pre-existing" must itself be
    # downgraded through probe-evidence-unresolved -- never bound to
    # "deny"'s artifact -- regardless of which probe happened to bind
    # the id first.
    colliding_tuple = ("probe-audit-ledger-record", "governance/ledger.jsonl", "sha256:" + "a" * 64)
    disambiguated_id = governed_actions._disambiguated_evidence_id(
        "audit-0001",
        contracts.ProbeEvidence(evidence_id="audit-0001", kind=colliding_tuple[0], source=colliding_tuple[1], sha256=colliding_tuple[2]),
    )
    preexisting_tuple = ("static-file-hash", "specs/SPEC.md", "sha256:" + "f" * 64)
    probe_pre = _synthetic_probe("pre-existing", disambiguated_id, preexisting_tuple)
    probe_a = _synthetic_probe("deny", "audit-0001", colliding_tuple)
    other_tuple = ("probe-audit-ledger-record", "governance/ledger.jsonl", "sha256:" + "b" * 64)
    probe_b = _synthetic_probe("transform", "audit-0001", other_tuple)

    source = contracts.SourceRef(repository=REPOSITORY, commit=COMMIT, dirty=False)
    options = contracts.AssessmentOptions(root=Path("."), phase="pre-deploy", now=NOW)
    # Only the ordering changes: the literal-id probe now arrives after
    # both conflicting probes have already been resolved and bound.
    kept, findings, evidence = governed_actions._bind_probe_evidence(
        (probe_a, probe_b, probe_pre), source, options, ()
    )

    by_probe_id = {probe.probe_id: probe for probe in kept}
    # "deny" resolves its rewrite first (nothing bound yet) and is kept
    # passing, bound to its own real provenance.
    assert by_probe_id["deny"].status == "pass"
    ref_deny = by_probe_id["deny"].evidence_refs[0]
    assert ref_deny == disambiguated_id
    # "pre-existing" cites that same literal string but with different,
    # real provenance -- it must be downgraded, never silently folded
    # into "deny"'s already-bound entry.
    assert by_probe_id["pre-existing"].status == "not-verified"
    assert by_probe_id["pre-existing"].reason_code == "probe-evidence-unresolved"
    assert by_probe_id["pre-existing"].evidence_refs == ()
    downgraded = [f for f in findings if f.reason_code == "probe-evidence-unresolved"]
    assert len(downgraded) == 1
    assert downgraded[0].affected_actions == ("payments.refund",)
    # The evidence actually published under disambiguated_id must be
    # "deny"'s real provenance, never invented or overwritten by the
    # rejected "pre-existing" citation.
    evidence_by_id = {ref.evidence_id: ref for ref in evidence}
    assert (
        evidence_by_id[disambiguated_id].kind,
        evidence_by_id[disambiguated_id].source,
        evidence_by_id[disambiguated_id].sha256,
    ) == colliding_tuple
    assert by_probe_id["transform"].status == "pass"
    ref_b = by_probe_id["transform"].evidence_refs[0]
    assert (evidence_by_id[ref_b].kind, evidence_by_id[ref_b].source, evidence_by_id[ref_b].sha256) == other_tuple
