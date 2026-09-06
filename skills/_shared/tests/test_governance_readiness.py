"""Consumer assurance tests; protocol fixtures are explicitly local, not attestations."""
from copy import deepcopy
from datetime import timedelta
import importlib

import pytest

from skills._shared.tests.governance_consumer_fixtures import (
    contract, inventory, live_fixture, seed_inventory, write,
)


def api():
    return importlib.import_module("skills._shared.governance_readiness")


def test_current_protocol_is_required_and_validated():
    value, document, current, now = live_fixture()
    result = api().evaluate(value, document, current=current, now=now)
    assert result["status"] == "pass"
    assert result["live"] is True


@pytest.mark.parametrize("mutation", ["missing", "expired", "mismatch", "valid"])
def test_remote_bootstrap_chain_is_required_when_current_host_selects_it(mutation):
    value, document, current, now = live_fixture()
    target = current["expected_target"]
    bootstrap = {
        "binding": {
            "schema": "threadlight-hosted-bootstrap/v1", "reference": "attempt-1",
            "tenant_id": target["tenant"], "principal": target["subject"], "client_id": target["client_id"],
            **{key: target[key] for key in (
                "agent_id", "agent_version", "image_digest", "environment", "subscription", "resource_group")},
            "project_endpoint": "https://test.services.ai.azure.com/api/projects/test",
            "key_id": "https://test.vault.azure.net/keys/test/" + "a" * 32,
            "policy_id": "safe", "policy_version": "1", "policy_digest": current["policy_bundle"]["digest"],
            "native_policy_digest": current["policy_bundle"]["digest"],
            "config_digest": current["declared_file_digests"]["host"],
            "issued_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
        },
        "signature": "dGVzdC1zaWduYXR1cmU=",
    }
    current["bootstrap"] = deepcopy(bootstrap)
    if mutation != "missing":
        value["collection_evidence"]["bootstrap"] = deepcopy(bootstrap)
    if mutation == "expired":
        current["bootstrap"]["binding"]["expires_at"] = (now - timedelta(seconds=1)).isoformat()
        value["collection_evidence"]["bootstrap"] = deepcopy(current["bootstrap"])
    elif mutation == "mismatch":
        value["collection_evidence"]["bootstrap"]["binding"]["reference"] = "other-attempt"
    result = api().evaluate(value, document, current=current, now=now)
    assert (result["status"] == "pass") == (mutation == "valid")


@pytest.mark.parametrize("producer", ["native", "gateway"])
def test_collection_schema_requires_exact_signature_dependency_chain(producer):
    import json
    from pathlib import Path
    import jsonschema
    value, _, _, _ = live_fixture()
    evidence = value["collection_evidence"]
    evidence["registration_scope"]["producer"] = producer
    if producer == "gateway":
        evidence["verified_policies"]["native_policy"] = deepcopy(evidence["verified_policies"]["policy"])
    schema = json.loads((Path(__file__).resolve().parents[1] / "governance-manifest.schema.json").read_text())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)


@pytest.mark.parametrize("mutation", [
    "fake-status", "expired", "wrong-target", "wrong-receipt", "business", "lifecycle",
    "coverage", "duplicates", "nonce", "evaluate-only", "gap", "missing-current",
    "policy", "identity", "configuration", "requirement", "missing-tool", "wrong-binding",
])
def test_no_false_green(mutation):
    value, document, current, now = live_fixture()
    if mutation == "fake-status":
        del value["collection_evidence"]
    elif mutation == "expired":
        now += timedelta(minutes=11)
    elif mutation == "wrong-target":
        current["expected_target"] = {**current["expected_target"], "agent_version": "2"}
    elif mutation == "wrong-receipt":
        value["collection_evidence"]["records"][0]["receipt"]["image_digest"] = "sha256:" + "b"*64
    elif mutation == "business":
        document["tools"][0]["id"] = "pay_customer"
        value["bindings"][0]["tool_id"] = "pay_customer"
    elif mutation == "lifecycle":
        value["bindings"][0]["intervention_points"] = ["startup"]
    elif mutation == "coverage":
        value["coverage"]["tools_total"] = 0
    elif mutation == "duplicates":
        value["bindings"].append(deepcopy(value["bindings"][0]))
    elif mutation == "nonce":
        value["collection_evidence"]["records"][1]["run_id"] = value["collection_evidence"]["records"][0]["run_id"]
    elif mutation == "evaluate-only":
        value["enforcement"]["mode"] = "evaluate_only"
    elif mutation == "gap":
        value["gaps"] = [{"tool_id": "hidden", "status": "unverified", "reason_code": "unclassified-tool", "evidence_refs": []}]
    elif mutation == "missing-current":
        current = None
    elif mutation == "policy":
        current["policy_bundle"] = {**current["policy_bundle"], "digest": "sha256:" + "b"*64}
    elif mutation == "identity":
        current["expected_target"] = {**current["expected_target"], "subject": "other"}
    elif mutation == "configuration":
        current["declared_file_digests"] = {"host": "sha256:" + "b"*64}
    elif mutation == "requirement":
        document["tools"][0]["requires"] = ["output-mediation"]
    elif mutation == "missing-tool":
        document["tools"].append(contract(tool_id="other")["tools"][0])
    elif mutation == "wrong-binding":
        document["tools"][0]["policy_binding"] = "other"
    assert api().evaluate(value, document, current=current, now=now)["status"] != "pass"


@pytest.mark.parametrize("consequence", ["write", "external-egress", "irreversible", "unknown"])
def test_unbound_consequential_requires_exact_current_acceptance(consequence):
    document = contract(consequence=consequence)
    assert api().evaluate(inventory(document), document)["status"] == "must-fix"


@pytest.mark.parametrize("shape", ["{", "[]", "null", '{"schema": "unknown"}'])
def test_malformed_manifest_is_explicit_not_verified(tmp_path, shape):
    seed_inventory(tmp_path)
    (tmp_path / "specs/governance-manifest.json").write_text(shape)
    assert api().assess(tmp_path)["status"] == "not-verified"


def test_empty_and_off_contracts_are_not_invented_enforcement(tmp_path):
    document = contract()
    document["tools"] = []
    seed_inventory(tmp_path, document)
    result = api().assess(tmp_path)
    assert result["status"] == "pass" and result["live"] is False


def test_omitted_inventory_cannot_hide_selected_business_action(tmp_path):
    document = contract(selected=True, tool_id="pay")
    seed_inventory(tmp_path, document)
    write(tmp_path, "specs/governance-manifest.json", inventory(contract()))
    assert api().assess(tmp_path)["status"] == "must-fix"


@pytest.mark.parametrize("mutation", [None, "tool", "target", "commit", "reason", "expiry", "future-review"])
def test_acceptance_is_exact_tool_deployment_and_change_scoped(mutation):
    from skills._shared.governance_configuration import configuration_digest
    value, _, current, now = live_fixture()
    document = contract(consequence="write", tool_id="write")
    document["tools"][0]["acceptance_record"] = {
        "owner": "risk-owner", "justification": "Reviewed explicit unbound operation",
        "review_date": (now - timedelta(hours=1)).isoformat(),
        "expiry": (now + timedelta(hours=1)).isoformat(),
    }
    current.update(contract=document, source_commit="a" * 40)
    scope = {"tool_id": "write", "contract_sha256": configuration_digest(document),
             "target": deepcopy(current["expected_target"]), "source_commit": "a" * 40}
    current["acceptances"] = [scope]
    if mutation == "tool":
        scope["tool_id"] = "other"
    elif mutation == "target":
        scope["target"]["image_digest"] = "sha256:" + "b" * 64
    elif mutation == "commit":
        scope["source_commit"] = "b" * 40
    elif mutation == "reason":
        del document["tools"][0]["acceptance_record"]["justification"]
    elif mutation == "expiry":
        document["tools"][0]["acceptance_record"]["expiry"] = (now - timedelta(seconds=1)).isoformat()
    elif mutation == "future-review":
        document["tools"][0]["acceptance_record"]["review_date"] = (now + timedelta(seconds=1)).isoformat()
    assert (api().evaluate(inventory(document), document, current=current, now=now)["status"] == "pass") == (mutation is None)


def test_unbound_acceptance_uses_current_declared_target_without_live_probe(tmp_path):
    _, document, current, now = live_fixture()
    document = contract(consequence="write")
    write(tmp_path, "specs/governance-contract.json", document)
    write(tmp_path, "specs/manifest.json", {"deployment_manifest": current["expected_target"]})
    result = api().current_context(tmp_path)
    assert result["expected_target"] == current["expected_target"]


def test_missing_selected_manifest_is_must_fix(tmp_path):
    write(tmp_path, "specs/governance-contract.json", contract(selected=True))
    assert api().assess(tmp_path)["status"] == "must-fix"


def test_selected_noop_with_explicit_unbound_read_has_no_effective_gaps():
    from skills._shared.tests.governance_consumer_fixtures import coverage
    value, document, current, now = live_fixture()
    read = contract()["tools"][0]
    document["tools"].append(read)
    binding = inventory(contract())["bindings"][0]
    binding.update(policy_digest=value["policy_bundle"]["digest"], evidence_refs=[], mode="enforce")
    value["bindings"].append(binding)
    value["coverage"] = coverage(value["bindings"])
    value["gaps"] = [{"binding_id": binding["binding_id"], "status": "unbound",
                      "reason_code": "intentionally-unbound", "evidence_refs": []}]
    result = api().evaluate(value, document, current=current, now=now)
    assert result["status"] == "pass"
    assert result["gaps"] == []


@pytest.mark.parametrize("reason", ["intentionally-unbound", "explicitly-unbound"])
@pytest.mark.parametrize("tool_id", ["undeclared", "binding.read", "lifecycle.startup"])
def test_unassociated_unbound_gap_cannot_borrow_valid_live_proof(reason, tool_id):
    from skills._shared.governance import validate_governance_manifest
    value, document, current, now = live_fixture()
    assert api().evaluate(value, document, current=current, now=now)["live"]
    value["gaps"].append({"tool_id": tool_id, "status": "unbound",
                          "reason_code": reason, "evidence_refs": []})
    # A schema-valid diagnostic about an unclassified tool is not accepted inventory.
    validate_governance_manifest(value)
    result = api().evaluate(value, document, current=current, now=now)
    assert result["status"] == "must-fix"
    assert result["live"] is False
    assert result["gaps"] == value["gaps"]


@pytest.mark.parametrize("accepted", [False, True])
def test_mixed_live_and_unbound_consequential_gap_requires_exact_acceptance(accepted):
    from skills._shared.tests.governance_consumer_fixtures import coverage, digest
    value, document, current, now = live_fixture()
    tool = contract(consequence="write", tool_id="write")["tools"][0]
    tool["acceptance_record"] = {
        "owner": "owner", "justification": "Reviewed operation",
        "review_date": (now - timedelta(hours=1)).isoformat(),
        "expiry": (now + timedelta(hours=1)).isoformat(),
    }
    document["tools"].append(tool)
    binding = inventory(contract(consequence="write", tool_id="write"))["bindings"][0]
    binding.update(policy_digest=value["policy_bundle"]["digest"], evidence_refs=[], mode="enforce")
    value["bindings"].append(binding)
    value["coverage"] = coverage(value["bindings"])
    value["gaps"] = [{"binding_id": binding["binding_id"], "status": "unbound",
                      "reason_code": "intentionally-unbound", "evidence_refs": []}]
    current.update(source_commit="a" * 40, acceptances=[{
        "tool_id": "write" if accepted else "other", "contract_sha256": digest(document),
        "target": current["expected_target"], "source_commit": "a" * 40,
    }])
    result = api().evaluate(value, document, current=current, now=now)
    assert (result["status"] == "pass") == accepted
    assert result["live"] == accepted


@pytest.mark.parametrize("key", ["agent_id", "agent_version", "image_digest", "environment",
                                "tenant", "subscription", "resource_group", "subject", "client_id"])
def test_each_current_target_field_is_load_bearing(key):
    value, document, current, now = live_fixture()
    current["expected_target"] = {**current["expected_target"], key: "wrong"}
    assert api().evaluate(value, document, current=current, now=now)["status"] == "must-fix"


def test_changed_source_bytes_cannot_reuse_offline_inventory(tmp_path):
    seed_inventory(tmp_path)
    with (tmp_path / "specs/governance-contract.json").open("a") as stream:
        stream.write(" ")
    assert api().assess(tmp_path)["status"] != "pass"


def test_spec_only_contract_reuses_producer_parser(tmp_path):
    import json
    document = contract()
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/SPEC.md").write_text("```yaml\n" + json.dumps(document) + "\n```\n")
    assert api().load_contract(tmp_path) == document


@pytest.mark.parametrize("other", ["invalid", "off", "equivalent"])
def test_spec_mirror_cannot_be_hidden_by_parent_contract(tmp_path, other):
    import json
    document = contract(selected=True)
    write(tmp_path, "specs/governance-contract.json", document)
    spec = deepcopy(document) if other != "off" else contract()
    if other == "invalid":
        spec["governance"]["environment_modes"]["preproduction"] = "enforc"
    (tmp_path / "specs/SPEC.md").write_text("```yaml\n" + json.dumps(spec) + "\n```\n")
    if other == "equivalent":
        assert api().load_contract(tmp_path) == document
    else:
        with pytest.raises(ValueError):
            api().load_contract(tmp_path)


def test_current_declared_selection_is_not_replaced_by_observed_target():
    value, document, current, now = live_fixture()
    current["declared_selection"] = {"requested_version": "2"}
    assert api().evaluate(value, document, current=current, now=now)["status"] == "must-fix"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_noop_cannot_certify_evaluate_only_or_production_target(environment):
    value, document, current, now = live_fixture(environment=environment)
    assert api().evaluate(value, document, current=current, now=now)["status"] == "must-fix"


def test_requirement_not_supported_by_collector_cannot_pass():
    value, document, current, now = live_fixture()
    document["tools"][0]["requires"] = ["authorization"]
    assert api().evaluate(value, document, current=current, now=now)["status"] == "must-fix"


def test_acceptance_invalid_scope_shape_is_not_a_crash():
    value, _, current, now = live_fixture()
    document = contract(consequence="write")
    document["tools"][0]["acceptance_record"] = {
        "owner": "owner", "justification": "reason",
        "review_date": (now - timedelta(hours=1)).isoformat(),
        "expiry": (now + timedelta(hours=1)).isoformat(),
    }
    current.update(source_commit="a" * 40, acceptances={})
    assert api().evaluate(inventory(document), document, current=current, now=now)["status"] == "must-fix"
