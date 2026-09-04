"""Task 5 regression controls; synthetic subprocesses are not live enforcement."""
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import canonical
import contracts
import governed_actions
import probes
import render


NOW = "2026-09-01T12:00:00Z"
BINDING = probes.ApprovalBinding(
    "account:synthetic", "requester", "approver", "reviewer", "tenant",
    "policy", "sha256:" + "a" * 64, "payments.refund", {"amount": 7},
    NOW, "2026-09-01T12:05:00Z", "nonce-1",
)
DEPLOYED = {
    "agent_name": "refund-agent",
    "agent_version": "3",
    "image_digest": "sha256:" + "b" * 64,
    "policy_digest": "sha256:" + "c" * 64,
    "environment": "staging",
    "subscription": "01234567-89ab-cdef-0123-456789abcdef",
    "resource_group": "rg-refund-staging",
}
AUDIT_FIELDS = (
    "audit_id", "correlation_id", "decision", "action_hash", "policy_hash",
    "delivery_status",
)
TARGET = '''
import json
import os
from pathlib import Path

MODE = "valid"
AUDIT_EVENTS = []

def append(path, record):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\\n")
        handle.flush()
        os.fsync(handle.fileno())

def dispatch(*args):
    path = args[-1]
    if len(args) == 5:
        nonce, digest, expires, now, path = args
        previous = [json.loads(line) for line in Path(path).read_text().splitlines()]
        used = next((r for r in previous if r.get("nonce") == nonce
                     and r.get("accepted") is True), None)
        expired = now >= expires
        accepted = not used and not expired
        if MODE == "reject-after-first":
            accepted = not any(r.get("accepted") is True for r in previous)
        if MODE == "ignore-expiry":
            accepted = not used
        reason = ("expiry" if expired else
                  "binding" if used and used["digest"] != digest else
                  "replay" if used else "accepted")
        decision = {"event": "decision", "nonce": nonce, "digest": digest,
                    "accepted": accepted}
        if MODE != "no-reasons":
            decision["reason"] = "replay" if MODE == "wrong-reasons" and used else reason
        if MODE == "wrong-digest":
            decision["digest"] = "sha256:" + "0" * 64
        append(path, decision)
        if accepted:
            append(path, {"event": "invocation", "nonce": nonce})
        verdict = "allow" if accepted else "deny"
    else:
        verdict, path = args
        append(path, {"event": "verdict_received",
                      "verdict": "allow" if MODE == "wrong-verdict" else verdict})
        if verdict != "deny" and MODE != "empty-allow":
            append(path, {"event": "egress", "bytes": 32, "mediated": True})
    record = {
        "audit_id": "audit-1", "correlation_id": "correlation-1",
        "decision": verdict, "action_hash": "sha256:" + "a" * 64,
        "policy_hash": "sha256:" + "b" * 64, "delivery_status": "persisted",
    }
    if MODE.startswith("missing-"):
        record.pop(MODE[len("missing-"):])
    if MODE.startswith("raw-"):
        record[MODE[len("raw-"):]] = "SENSITIVE-SENTINEL"
    if MODE == "delivery-failed":
        record["delivery_status"] = "failed"
    AUDIT_EVENTS.append(record)
    if MODE != "unpersisted":
        append(path, record)
'''


@pytest.fixture
def target(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "agent.py").write_text(TARGET)
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance" / "probe-contract.json").write_text(json.dumps({
        "dispatch": "app.agent:dispatch", "audit_sink": "app.agent:AUDIT_EVENTS",
        "nonce_ledger": "governance/nonces.jsonl",
        "observation_ledger": "governance/output.jsonl",
        "side_effect_mode": "synthetic", "actions": ["payments.refund"],
    }))
    return tmp_path


def set_mode(target, mode):
    (target / "app" / "agent.py").write_text(
        TARGET.replace('MODE = "valid"', f"MODE = {mode!r}")
    )


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_reject_everything_after_first_is_not_anti_replay(target):
    set_mode(target, "reject-after-first")
    results = probes.run_approval_probe_sequence(target, BINDING, NOW)
    assert any(p.status == "must-fix" and p.reason_code == "APR-001" for p in results)


@pytest.mark.parametrize("mode", ("valid", "reject-after-first"))
def test_stale_baseline_cannot_certify_an_approval_sequence(target, mode):
    set_mode(target, mode)
    before = snapshot(target)
    stale = replace(BINDING, issued_at="2026-09-01T11:55:00Z",
                    expires_at="2026-09-01T11:59:00Z")
    with pytest.raises(probes.ProbeContractError, match="baseline"):
        probes.run_approval_probe_sequence(target, stale, NOW)
    assert snapshot(target) == before


def test_sequence_enforces_expected_acceptance_at_each_position(target, monkeypatch):
    original = probes.run_approval_probe

    def wrong_success(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, status="pass", observed="expired_rejected")

    monkeypatch.setattr(probes, "run_approval_probe", wrong_success)
    results = probes.run_approval_probe_sequence(target, BINDING, NOW)
    assert all(p.status == "must-fix" and p.reason_code == "APR-001"
               for p in results[:-1])


def test_sequence_has_second_valid_acceptance_and_fresh_expired_rejection(target):
    before = snapshot(target)
    results = probes.run_approval_probe_sequence(target, BINDING, NOW)
    assert len(results) == 4 + len(probes._APPROVAL_MUTATION_FIELDS)
    assert [p.observed for p in results[-2:]] == ["approval_accepted", "expired_rejected"]
    assert all(p.status == "pass" for p in results)
    assert len({results[i].evidence_refs[0] for i in (0, -2, -1)}) == 3
    assert snapshot(target) == before


def test_sequence_detects_fresh_expired_acceptance(target):
    set_mode(target, "ignore-expiry")
    results = probes.run_approval_probe_sequence(target, BINDING, NOW)
    assert results[-1].status == "must-fix"
    assert results[-1].observed == "expired_binding_fail_open_accepted"


def test_reasonless_store_does_not_claim_binding_discrimination(target):
    set_mode(target, "no-reasons")
    results = probes.run_approval_probe_sequence(target, BINDING, NOW)
    assert {p.observed for p in results[1:2 + len(probes._APPROVAL_MUTATION_FIELDS)]} == {
        "reused_nonce_rejected"
    }


def test_reported_wrong_binding_reason_cannot_pass(target):
    set_mode(target, "wrong-reasons")
    results = probes.run_approval_probe_sequence(target, BINDING, NOW)
    assert all(p.status != "pass" for p in results[2:2 + len(probes._APPROVAL_MUTATION_FIELDS)])


def test_approval_evidence_includes_observed_decision_not_just_input_hash(target):
    results = probes.run_approval_probe_sequence(target, BINDING, NOW)
    assert all(any(i.kind == "approval-ledger-records" for i in p.evidence_items) for p in results)


def test_approval_requires_the_target_to_record_the_actual_binding_digest(target):
    set_mode(target, "wrong-digest")
    assert probes.run_approval_probe_sequence(target, BINDING, NOW)[0].status != "pass"


@pytest.mark.parametrize("field", AUDIT_FIELDS)
def test_missing_audit_field_never_passes(target, field):
    set_mode(target, "missing-" + field)
    results = probes.run_privacy_probe_set(target)
    assert results and all(p.status != "pass" for p in results)


def test_incomplete_legacy_audit_is_not_verified_rather_than_payload_violation(target):
    set_mode(target, "missing-correlation_id")
    results = probes.run_privacy_probe_set(target)
    assert all(p.status == "not-verified" for p in results)


@pytest.mark.parametrize("field", (
    "prompt", "arguments", "output", "secrets", "raw_prompt", "raw_arguments",
    "raw_output", "client_secret", "unrecognized_metadata",
))
def test_raw_audit_payload_is_rejected_without_export(target, field):
    set_mode(target, "raw-" + field)
    results = probes.run_privacy_probe_set(target)
    assert all(p.status == "must-fix" and p.reason_code == "AUD-001" for p in results)
    assert "SENSITIVE-SENTINEL" not in repr(results)


@pytest.mark.parametrize("field", ("raw_prompt", "raw_arguments", "raw_output", "client_secret"))
def test_output_rejects_unknown_payload_fields_in_persisted_records(target, field):
    set_mode(target, "raw-" + field)
    result = probes.run_output_probe(target, "allow")
    assert result.status != "pass"
    assert not result.evidence_items
    assert "SENSITIVE-SENTINEL" not in repr(result)


@pytest.mark.parametrize("trailing_record", (
    b'{"raw_prompt":"SENSITIVE-SENTINEL",}\n',
    b'not-json\n',
    b'\xff\n',
))
def test_corrupt_ledger_cannot_certify_filtered_output_or_audit(target, trailing_record):
    (target / "app" / "agent.py").write_text(TARGET + (
        "\n_original_dispatch = dispatch\n"
        "def dispatch(*args):\n"
        "    _original_dispatch(*args)\n"
        "    with open(args[-1], 'ab') as handle:\n"
        f"        handle.write({trailing_record!r})\n"
    ))
    before = snapshot(target)
    with pytest.raises(probes.ProbeToolingError) as output_error:
        probes.run_output_probe(target, "allow")
    with pytest.raises(probes.ProbeToolingError) as audit_error:
        probes.run_privacy_probe_set(target)
    assert "SENSITIVE-SENTINEL" not in str(output_error.value)
    assert "SENSITIVE-SENTINEL" not in str(audit_error.value)
    assert snapshot(target) == before


_DUPLICATE_AUDIT = (
    '{"audit_id":"RAW PROMPT SENTINEL","audit_id":"audit-1",'
    '"correlation_id":"correlation-1","decision":"deny",'
    '"action_hash":"sha256:' + "a" * 64 + '",'
    '"policy_hash":"sha256:' + "b" * 64 + '","delivery_status":"persisted"}'
)
_DUPLICATE_EGRESS = '{"event":"egress","bytes":32,"bytes":0,"mediated":true}'


@pytest.mark.parametrize("record", (
    _DUPLICATE_AUDIT, _DUPLICATE_EGRESS,
    '{"metadata":{"RAW PROMPT SENTINEL":1,"RAW PROMPT SENTINEL":2}}',
    '{"metadata":[{"audit_id":"RAW PROMPT SENTINEL","audit_id":"audit-1"}]}',
))
def test_duplicate_jsonl_keys_are_rejected_at_every_object_level(target, record):
    ledger = target / "governance" / "duplicate.jsonl"
    ledger.write_text(record + "\n", encoding="utf-8")
    before = snapshot(target)
    with pytest.raises(probes.ProbeToolingError, match="duplicate") as error:
        probes._read_ledger_events(ledger)
    assert "RAW PROMPT SENTINEL" not in str(error.value)
    assert snapshot(target) == before


def _inject_duplicate_output_record(target, record):
    agent = target / "app" / "agent.py"
    agent.write_text(agent.read_text() + (
        "\n_original_dispatch = dispatch\n"
        "def dispatch(*args):\n"
        "    result = _original_dispatch(*args)\n"
        "    with open(args[-1], 'a', encoding='utf-8') as handle:\n"
        f"        handle.write({(record + chr(10))!r})\n"
        "    return result\n"
    ))


@pytest.mark.parametrize("record", (_DUPLICATE_AUDIT, _DUPLICATE_EGRESS))
def test_duplicate_jsonl_keys_cannot_certify_output_or_audit(target, record):
    _inject_duplicate_output_record(target, record)
    before = snapshot(target)
    for run in (lambda: probes.run_output_probe(target, "deny"),
                lambda: probes.run_privacy_probe_set(target)):
        with pytest.raises(probes.ProbeToolingError, match="duplicate") as error:
            run()
        assert "RAW PROMPT SENTINEL" not in str(error.value)
    assert snapshot(target) == before


@pytest.mark.parametrize("record", (_DUPLICATE_AUDIT, _DUPLICATE_EGRESS))
def test_assess_rejects_duplicate_jsonl_keys_without_payload_or_residue(tmp_path, monkeypatch, record):
    root = tmp_path / "pilot"
    shutil.copytree(Path(__file__).parent / "fixtures" / "conformant-maf", root)
    _inject_duplicate_output_record(root, record)
    monkeypatch.setattr(governed_actions, "resolve_source",
                        lambda _: contracts.SourceRef("owner/repo", "0" * 40, False))
    before = snapshot(root)
    result = governed_actions.assess(contracts.AssessmentOptions(root, "pre-deploy", now=NOW))
    for control in ("AUD-001", "OUT-001"):
        assert any(f.finding_id == control and f.status == "not-verified" for f in result.findings)
    manifest = render.build_manifest(result)
    assert manifest["summary"]["verdict"] != "governed"
    assert "RAW PROMPT SENTINEL" not in json.dumps(manifest)
    assert "RAW PROMPT SENTINEL" not in render.render_evidence_pack(result)
    assert governed_actions.exit_code(result, gate=True) == 1
    assert snapshot(root) == before


@pytest.mark.parametrize("field,value", [
    ("audit_id", "x" * 257), ("correlation_id", "x" * 257),
    ("decision", "arbitrary prose"), ("decision", {"nested": "payload"}),
    ("action_hash", "sha256:invalid"), ("policy_hash", 1),
    ("delivery_status", True),
])
def test_audit_fields_have_bounded_types(target, field, value):
    (target / "app" / "agent.py").write_text(TARGET.replace(
        "AUDIT_EVENTS.append(record)", f"record[{field!r}] = {value!r}\n    AUDIT_EVENTS.append(record)"
    ))
    assert all(p.status != "pass" for p in probes.run_privacy_probe_set(target))


@pytest.mark.parametrize("field,value", [("bytes", 2**64), ("mediated", "yes")])
def test_output_record_types_are_bounded(target, field, value):
    (target / "app" / "agent.py").write_text(TARGET.replace(
        '{"event": "egress", "bytes": 32, "mediated": True}',
        repr({"event": "egress", "bytes": 32, "mediated": True, field: value}),
    ))
    assert probes.run_output_probe(target, "allow").status != "pass"


def test_in_memory_audit_alone_does_not_prove_durable_delivery(target):
    set_mode(target, "unpersisted")
    assert all(p.status != "pass" for p in probes.run_privacy_probe_set(target))


def test_output_and_audit_bind_persisted_items_before_cleanup(target):
    before = snapshot(target)
    results = (probes.run_output_probe(target, "allow"), *probes.run_privacy_probe_set(target))
    assert all(p.status == "pass" for p in results)
    for probe in results:
        assert probe.evidence_refs
        assert set(probe.evidence_refs) == {item.evidence_id for item in probe.evidence_items}
        assert all("assessment-isolated" in item.source for item in probe.evidence_items)
        assert all(item.sha256 != "sha256:" + canonical.sha256_hex(item.evidence_id.encode())
                   for item in probe.evidence_items)
    assert snapshot(target) == before


@pytest.mark.parametrize("mode,verdict", [
    ("wrong-verdict", "deny"), ("empty-allow", "allow"), ("raw-output", "allow"),
])
def test_output_cannot_pass_wrong_empty_or_payload_bearing_evidence(target, mode, verdict):
    set_mode(target, mode)
    assert probes.run_output_probe(target, verdict).status != "pass"


def test_output_ids_distinguish_actual_verdict_evidence(target):
    denied = probes.run_output_probe(target, "deny")
    allowed = probes.run_output_probe(target, "allow")
    assert denied.evidence_refs and allowed.evidence_refs
    assert set(denied.evidence_refs).isdisjoint(allowed.evidence_refs)


@pytest.mark.parametrize("rg", ("rg-prod", "rg-production-west", "PROD", "rg-prod-staging"))
def test_cli_rejects_production_resource_group(rg):
    with pytest.raises(ValueError, match="staging|production"):
        governed_actions.parse_args(["--phase", "post-deploy", "--staging-resource-group", rg])


@pytest.mark.parametrize("field", tuple(DEPLOYED))
def test_staging_scope_rejects_evidence_selector_mismatch(field):
    validator = getattr(probes, "validate_staging_scope", None)
    assert callable(validator), "missing pure scope validator"
    with pytest.raises(contracts.UnsafeTargetError, match="mismatch"):
        validator(DEPLOYED, {**DEPLOYED, field: "different"})


def test_valid_staging_scope_and_options_binding(target):
    fields = contracts.AssessmentOptions.__dataclass_fields__
    assert set(DEPLOYED) - {"resource_group"} <= set(fields)
    options = contracts.AssessmentOptions(
        target, "post-deploy", staging=True, staging_resource_group=DEPLOYED["resource_group"],
        **{k: v for k, v in DEPLOYED.items() if k != "resource_group"},
    )
    assert probes.validate_staging_scope(DEPLOYED, DEPLOYED) is None
    assert governed_actions._deployment_target(options) == DEPLOYED


@pytest.mark.parametrize("field", ("agent_version", "image_digest", "policy_digest", "environment"))
def test_live_evidence_mismatch_invalidates_manifest(field):
    assert "deployed_target" in contracts.EvidenceRef.__dataclass_fields__
    ref = contracts.EvidenceRef(
        "live-1", "staging-observation", "https://staging.invalid", "sha256:" + "d" * 64,
        NOW, 0, True, "post-deploy", "owner/repo", "0" * 40, "staging", None,
        deployed_target={**DEPLOYED, field: "different"},
    )
    finding = contracts.Finding("ENF-001", "pass", "post-deploy", "runtime", "test", "test", "",
                                evidence_refs=("live-1",))
    result = contracts.AssessmentResult(
        contracts.SourceRef("owner/repo", "0" * 40, False), (), (), (), (finding,), (ref,),
        captured_at=NOW, phase="post-deploy", deployed_target=DEPLOYED,
    )
    manifest = render.build_manifest(result)
    assert manifest["deployed_target"] == DEPLOYED
    assert manifest["freshness"]["status"] != "fresh"
    assert manifest["summary"]["verdict"] != "governed"


def test_local_probe_stays_non_live_even_with_deployment_selection(target):
    assert "agent_version" in contracts.AssessmentOptions.__dataclass_fields__
    options = contracts.AssessmentOptions(
        target, "post-deploy", staging=True, now=NOW,
        staging_resource_group=DEPLOYED["resource_group"],
        **{k: v for k, v in DEPLOYED.items() if k != "resource_group"},
    )
    result = probes.run_output_probe(target, "allow")
    _, _, refs = governed_actions._bind_probe_evidence(
        (result,), contracts.SourceRef("owner/repo", "0" * 40, False), options, (),
    )
    assert refs and all(not ref.live_verified for ref in refs)
    assert all(ref.deployed_target == DEPLOYED for ref in refs)


def test_deployment_binding_cli_round_trip(target):
    argv = ["--target", str(target), "--phase", "post-deploy"]
    for key, value in DEPLOYED.items():
        flag = "staging-resource-group" if key == "resource_group" else key.replace("_", "-")
        argv += ["--" + flag, value]
    options = governed_actions._options_from_namespace(governed_actions.parse_args(argv))
    assert governed_actions._deployment_target(options) == DEPLOYED


def test_manifest_with_deployed_target_validates_schema(target):
    options = contracts.AssessmentOptions(
        target, "post-deploy", staging=True, now=NOW,
        staging_resource_group=DEPLOYED["resource_group"],
        **{k: v for k, v in DEPLOYED.items() if k != "resource_group"},
    )
    source = contracts.SourceRef("owner/repo", "0" * 40, False)
    results, findings, refs = governed_actions._bind_probe_evidence(
        (probes.run_output_probe(target, "allow"),), source, options, (),
    )
    manifest = render.build_manifest(contracts.AssessmentResult(
        source, (), (), results, findings, refs, captured_at=NOW, phase="post-deploy",
        deployed_target=DEPLOYED,
    ))
    render._validate_manifest(manifest)
    assert manifest["deployed_target"] == DEPLOYED
    assert all(ref["deployed_target"] == DEPLOYED for ref in manifest["evidence"])


@pytest.mark.parametrize("field", ("subscription", "resource_group"))
def test_live_collector_checks_its_actual_flat_scope(target, monkeypatch, field):
    options = contracts.AssessmentOptions(
        target, "post-deploy", subscription=DEPLOYED["subscription"],
        staging_resource_group=DEPLOYED["resource_group"], deploy_identity="identity",
    )
    monkeypatch.setattr(governed_actions.ghcp, "collect_live_azure", lambda *args: SimpleNamespace(
        data={**DEPLOYED, field: "different"}, finding=None,
    ))
    with pytest.raises(contracts.UnsafeTargetError, match="mismatch"):
        governed_actions._collect_selected_live_evidence(target, options, None)


def collect_injected_azure(target, monkeypatch, credentials, assignments):
    responses = iter((credentials, assignments, [{"roleName": "Reader"}]))

    def run(command):
        assert command[0] == "az"
        return SimpleNamespace(returncode=0, stdout=json.dumps(next(responses)), stderr="")

    monkeypatch.setattr(governed_actions, "_default_command_runner", run)
    options = contracts.AssessmentOptions(
        target, "post-deploy", deploy_identity="identity", staging=True,
        staging_resource_group=DEPLOYED["resource_group"],
        **{k: v for k, v in DEPLOYED.items() if k != "resource_group"},
    )
    return governed_actions._collect_selected_live_evidence(target, options, None)


@pytest.mark.parametrize("record_type", ("credential", "assignment"))
def test_real_collector_rejects_observed_resource_scope_mismatch(target, monkeypatch, record_type):
    wrong = "/subscriptions/not-selected/resourceGroups/rg-production"
    credentials = [{"id": wrong + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/identity/federatedIdentityCredentials/one"}]
    assignments = [{"roleDefinitionName": "Reader", "scope": wrong}]
    with pytest.raises(contracts.UnsafeTargetError, match="mismatch|production"):
        collect_injected_azure(target, monkeypatch,
                               credentials if record_type == "credential" else [],
                               assignments if record_type == "assignment" else [])


@pytest.mark.parametrize("field", ("agent_name", "agent_version", "image_digest", "policy_digest"))
def test_real_collector_rejects_available_deployment_identity_mismatch(target, monkeypatch, field):
    scope = f"/subscriptions/{DEPLOYED['subscription']}/resourceGroups/{DEPLOYED['resource_group']}"
    with pytest.raises(contracts.UnsafeTargetError, match="mismatch"):
        collect_injected_azure(target, monkeypatch, [], [
            {"roleDefinitionName": "Reader", "scope": scope, field: "different"},
        ])


def test_real_collector_rejects_wrong_observed_managed_identity(target, monkeypatch):
    scope = f"/subscriptions/{DEPLOYED['subscription']}/resourceGroups/{DEPLOYED['resource_group']}"
    credentials = [{
        "id": scope + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
        "OTHER-IDENTITY/federatedIdentityCredentials/one",
        **{k: v for k, v in DEPLOYED.items() if k not in {"subscription", "resource_group"}},
    }]
    with pytest.raises(contracts.UnsafeTargetError, match="deploy_identity mismatch"):
        collect_injected_azure(target, monkeypatch, credentials, [
            {"roleDefinitionName": "Reader", "scope": scope},
        ])


def test_real_collector_preserves_matching_observed_managed_identity(target, monkeypatch):
    scope = f"/subscriptions/{DEPLOYED['subscription']}/resourceGroups/{DEPLOYED['resource_group']}"
    credentials = [{
        "id": scope + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
        "identity/federatedIdentityCredentials/one",
        **{k: v for k, v in DEPLOYED.items() if k not in {"subscription", "resource_group"}},
    }]
    _, data, findings = collect_injected_azure(target, monkeypatch, credentials, [
        {"roleDefinitionName": "Reader", "scope": scope},
    ])
    assert {"deploy_identity": "identity"} in data["observed_identities"]
    assert not findings


def test_unobserved_managed_identity_does_not_inherit_the_selector(target, monkeypatch):
    scope = f"/subscriptions/{DEPLOYED['subscription']}/resourceGroups/{DEPLOYED['resource_group']}"
    _, _, findings = collect_injected_azure(target, monkeypatch, [], [{
        "roleDefinitionName": "Reader", "scope": scope,
        **{k: v for k, v in DEPLOYED.items() if k not in {"subscription", "resource_group"}},
    }])
    assert any(f.reason_code == "azure-observed-target-not-verified" for f in findings)


@pytest.mark.parametrize("observed_scope", (False, True))
def test_selector_echoes_never_independently_verify_deployment(target, monkeypatch, observed_scope):
    scope = f"/subscriptions/{DEPLOYED['subscription']}/resourceGroups/{DEPLOYED['resource_group']}"
    _, data, findings = collect_injected_azure(target, monkeypatch, [], [
        {"roleDefinitionName": "Reader", **({"scope": scope} if observed_scope else {})},
    ])
    assert any(f.status == "not-verified" for f in findings)
    assert data["selected_scope"]["subscription"] == DEPLOYED["subscription"]
    assert "subscription" not in data
    assert data.get("observed_identity", {}) == {}


def test_renderer_rejects_local_evidence_marked_live(target):
    options = contracts.AssessmentOptions(
        target, "post-deploy", staging=True, now=NOW,
        staging_resource_group=DEPLOYED["resource_group"],
        **{k: v for k, v in DEPLOYED.items() if k != "resource_group"},
    )
    source = contracts.SourceRef("owner/repo", "0" * 40, False)
    results, findings, refs = governed_actions._bind_probe_evidence(
        (probes.run_output_probe(target, "allow"),), source, options, (),
    )
    result = contracts.AssessmentResult(
        source, (), (), results, findings, tuple(replace(ref, live_verified=True) for ref in refs),
        captured_at=NOW, phase="post-deploy", deployed_target=DEPLOYED,
    )
    assert render.build_manifest(result)["summary"]["verdict"] != "governed"


def test_producer_and_schema_require_persisted_evidence_contract(target):
    source = contracts.SourceRef("owner/repo", "0" * 40, False)
    options = contracts.AssessmentOptions(target, "pre-deploy", now=NOW)
    results, findings, refs = governed_actions._bind_probe_evidence(
        (probes.run_output_probe(target, "allow"),), source, options, (),
    )
    result = contracts.AssessmentResult(
        source, (), (), results, findings, refs, captured_at=NOW, phase="pre-deploy",
    )
    manifest = render.build_manifest(result)
    assert manifest.get("evidence_contract") == "governance-ledger/v2"
    render._validate_manifest(manifest)
    manifest.pop("evidence_contract")
    with pytest.raises(render.ArtifactWriteError):
        render._validate_manifest(manifest)
    stripped = replace(result, probes=tuple(replace(p, evidence_refs=()) for p in results))
    assert render.build_manifest(stripped)["summary"]["verdict"] != "governed"


def test_renderer_cannot_certify_partial_approval_sequence(target):
    source = contracts.SourceRef("owner/repo", "0" * 40, False)
    options = contracts.AssessmentOptions(target, "pre-deploy", now=NOW)
    results, findings, refs = governed_actions._bind_probe_evidence(
        probes.run_approval_probe_sequence(target, BINDING, NOW), source, options, (),
    )
    result = contracts.AssessmentResult(
        source, (), (), results[:-1], findings, refs, captured_at=NOW, phase="pre-deploy",
    )
    assert render.build_manifest(result)["summary"]["verdict"] != "governed"


def test_failed_audit_delivery_cannot_pass(target):
    set_mode(target, "delivery-failed")
    assert all(p.status != "pass" for p in probes.run_privacy_probe_set(target))


def test_post_deploy_binds_selection_without_claiming_live_enforcement(tmp_path):
    root = tmp_path / "pilot"
    shutil.copytree(Path(__file__).parent / "fixtures" / "conformant-maf", root)
    before = snapshot(root)
    options = contracts.AssessmentOptions(
        root, "post-deploy", staging=True, now=NOW,
        staging_resource_group=DEPLOYED["resource_group"],
        **{k: v for k, v in DEPLOYED.items() if k != "resource_group"},
    )
    result = governed_actions._assess_post_deploy(
        root, contracts.SourceRef("owner/repo", "0" * 40, False), options,
    )
    assert any(f.reason_code == "live-runtime-enforcement-not-verified"
               and f.status == "not-verified" for f in result.findings)
    assert all(not ref.live_verified and ref.deployed_target == DEPLOYED for ref in result.evidence)
    assert render.build_manifest(result)["deployed_target"] == DEPLOYED
    assert governed_actions.exit_code(result, gate=True) == 1
    assert snapshot(root) == before


_SUBSCRIPTION_ID = "abcdef01-2345-6789-abcd-ef0123456789"
_SUBSCRIPTION_NAME = "Governance staging"
_ACCOUNT_LIST = ["az", "account", "list", "--all", "--query", "[].{id:id,name:name}", "-o", "json"]


def _subscription_options(root, selector=_SUBSCRIPTION_NAME, **changes):
    return contracts.AssessmentOptions(
        root, "post-deploy", staging=True, now=NOW,
        staging_resource_group=DEPLOYED["resource_group"],
        **{**{k: v for k, v in DEPLOYED.items() if k != "resource_group"},
           "subscription": selector, **changes},
    )


def _subscription_runner(monkeypatch, accounts=None, observed_id=_SUBSCRIPTION_ID):
    calls = []
    if accounts is None:
        accounts = [{"name": _SUBSCRIPTION_NAME, "id": _SUBSCRIPTION_ID}]
    scope = f"/subscriptions/{observed_id}/resourceGroups/{DEPLOYED['resource_group']}"

    def run(command):
        calls.append(command)
        if command[:3] == ["az", "account", "list"]:
            assert command == _ACCOUNT_LIST
            data = accounts
        else:
            assert command[command.index("--subscription") + 1] == _SUBSCRIPTION_ID
            if command[:4] == ["az", "identity", "federated-credential", "list"]:
                data = [{"id": scope + "/providers/Microsoft.ManagedIdentity/"
                         "userAssignedIdentities/identity/federatedIdentityCredentials/one"}]
            elif command[:4] == ["az", "role", "assignment", "list"]:
                data = [{"roleDefinitionName": "Reader", "scope": scope}]
            else:
                assert command[:4] == ["az", "role", "definition", "list"]
                data = [{"roleName": "Reader"}]
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

    monkeypatch.setattr(governed_actions, "_default_command_runner", run)
    return calls


def test_deployment_binding_resolves_subscription_display_name(target, monkeypatch):
    calls = _subscription_runner(monkeypatch)
    options = _subscription_options(target)
    assert governed_actions._deployment_target(options)["subscription"] == _SUBSCRIPTION_ID
    assert options.subscription == _SUBSCRIPTION_NAME
    assert calls == [_ACCOUNT_LIST]


def test_internal_live_collection_resolves_subscription_before_comparison(target, monkeypatch):
    calls = _subscription_runner(monkeypatch)
    _, data, findings = governed_actions._collect_selected_live_evidence(
        target, _subscription_options(target, deploy_identity="identity"), None,
    )
    assert calls[0] == _ACCOUNT_LIST
    assert data["selected_scope"]["subscription"] == _SUBSCRIPTION_ID
    assert any(f.reason_code == "azure-observed-target-not-verified" for f in findings)


def test_observed_subscription_guid_is_compared_in_canonical_form(target, monkeypatch):
    _subscription_runner(monkeypatch, observed_id=_SUBSCRIPTION_ID.upper())
    _, data, findings = governed_actions._collect_selected_live_evidence(
        target, _subscription_options(target, deploy_identity="identity"), None,
    )
    assert data["selected_scope"]["subscription"] == _SUBSCRIPTION_ID
    assert any(f.reason_code == "azure-observed-target-not-verified" for f in findings)


def test_resolved_subscription_still_rejects_real_observed_mismatch(target, monkeypatch):
    calls = _subscription_runner(monkeypatch, observed_id="11111111-2222-3333-4444-555555555555")
    with pytest.raises(contracts.UnsafeTargetError, match="subscription mismatch"):
        governed_actions._collect_selected_live_evidence(
            target, _subscription_options(target, deploy_identity="identity"), None,
        )
    assert calls[0] == _ACCOUNT_LIST


@pytest.mark.parametrize("accounts", (
    [], {}, [{"name": "different", "id": _SUBSCRIPTION_ID}],
    [{"name": _SUBSCRIPTION_NAME, "id": "arbitrary-id"}],
    [{"name": _SUBSCRIPTION_NAME, "id": _SUBSCRIPTION_ID},
     {"name": _SUBSCRIPTION_NAME, "id": "11111111-2222-3333-4444-555555555555"}],
))
def test_subscription_name_resolution_rejects_missing_ambiguous_or_invalid_accounts(target, monkeypatch, accounts):
    calls = _subscription_runner(monkeypatch, accounts=accounts)
    with pytest.raises(ValueError, match="subscription"):
        governed_actions._deployment_target(_subscription_options(target))
    assert calls == [_ACCOUNT_LIST]


@pytest.mark.parametrize("failure", ("missing-cli", "cli-error", "malformed-json"))
def test_subscription_resolution_errors_are_sanitized(target, monkeypatch, failure):
    def run(command):
        assert command == _ACCOUNT_LIST
        if failure == "missing-cli":
            raise FileNotFoundError("RAW PROMPT SENTINEL")
        return SimpleNamespace(returncode=1 if failure == "cli-error" else 0,
                               stdout="RAW PROMPT SENTINEL", stderr="RAW PROMPT SENTINEL")
    monkeypatch.setattr(governed_actions, "_default_command_runner", run)
    with pytest.raises(ValueError, match="subscription") as error:
        governed_actions._deployment_target(_subscription_options(target))
    assert "RAW PROMPT SENTINEL" not in str(error.value)


@pytest.mark.parametrize("entry", ("assess", "cli"))
def test_public_assessment_binds_resolved_subscription_without_inventing_live_identity(tmp_path, monkeypatch, entry):
    root = tmp_path / "pilot"
    shutil.copytree(Path(__file__).parent / "fixtures" / "conformant-maf", root)
    monkeypatch.setattr(governed_actions, "resolve_source",
                        lambda _: contracts.SourceRef("owner/repo", "0" * 40, False))
    calls = _subscription_runner(monkeypatch)
    before = snapshot(root)
    if entry == "assess":
        result = governed_actions.assess(_subscription_options(root, deploy_identity="identity"))
        manifest = render.build_manifest(result)
        assert snapshot(root) == before
    else:
        argv = ["--target", str(root), "--phase", "post-deploy", "--emit", "--gate",
                "--deploy-identity", "identity"]
        for key, value in {**DEPLOYED, "subscription": _SUBSCRIPTION_NAME}.items():
            flag = "staging-resource-group" if key == "resource_group" else key.replace("_", "-")
            argv += ["--" + flag, value]
        assert governed_actions.main(argv) == 1
        manifest = json.loads((root / "tests/governed-actions-manifest.json").read_text())
    assert calls.count(_ACCOUNT_LIST) == 1
    assert manifest["deployed_target"]["subscription"] == _SUBSCRIPTION_ID
    assert all(e["deployed_target"]["subscription"] == _SUBSCRIPTION_ID and not e["live_verified"]
               for e in manifest["evidence"])
    assert any(f["reason_code"] == "azure-observed-target-not-verified" for f in manifest["findings"])
    assert any(f["reason_code"] == "live-runtime-enforcement-not-verified" for f in manifest["findings"])
    assert manifest["summary"]["verdict"] != "governed"


def test_local_guid_assessment_needs_no_account_lookup(tmp_path, monkeypatch):
    root = tmp_path / "pilot"
    shutil.copytree(Path(__file__).parent / "fixtures" / "conformant-maf", root)
    monkeypatch.setattr(governed_actions, "resolve_source",
                        lambda _: contracts.SourceRef("owner/repo", "0" * 40, False))
    def no_azure(command):
        pytest.fail("local GUID assessment must not query Azure")
    monkeypatch.setattr(governed_actions, "_default_command_runner", no_azure)
    before = snapshot(root)
    result = governed_actions.assess(_subscription_options(root, _SUBSCRIPTION_ID.upper()))
    assert result.deployed_target["subscription"] == _SUBSCRIPTION_ID
    assert all(not e.live_verified for e in result.evidence)
    assert snapshot(root) == before


@pytest.mark.parametrize("entry", ("assess", "cli", "collect"))
def test_unresolved_subscription_cannot_emit_or_collect_evidence(tmp_path, monkeypatch, entry, capsys):
    root = tmp_path / "pilot"
    shutil.copytree(Path(__file__).parent / "fixtures" / "conformant-maf", root)
    monkeypatch.setattr(governed_actions, "resolve_source",
                        lambda _: contracts.SourceRef("owner/repo", "0" * 40, False))
    calls = _subscription_runner(monkeypatch, accounts=[])
    before = snapshot(root)
    if entry == "cli":
        argv = ["--target", str(root), "--phase", "post-deploy", "--emit",
                "--subscription", _SUBSCRIPTION_NAME, "--deploy-identity", "identity",
                "--staging-resource-group", DEPLOYED["resource_group"]]
        assert governed_actions.main(argv) == 2
        assert "subscription" in capsys.readouterr().err
    else:
        with pytest.raises(ValueError, match="subscription"):
            if entry == "assess":
                governed_actions.assess(_subscription_options(root))
            else:
                options = contracts.AssessmentOptions(
                    root, "post-deploy", subscription=_SUBSCRIPTION_NAME,
                    staging_resource_group=DEPLOYED["resource_group"], deploy_identity="identity",
                )
                governed_actions._collect_selected_live_evidence(root, options, None)
    assert calls == [_ACCOUNT_LIST]
    assert snapshot(root) == before
