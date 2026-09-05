"""Local protocol fixtures only: these records are NOT claimed live observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4


DIGEST = "sha256:" + "a" * 64


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def contract(*, selected=False, consequence="read", tool_id=None):
    return {
        "framework": "github-copilot-sdk",
        "governance": {
            "mode": "selective" if selected else "off",
            "environment_modes": {"development": "evaluate_only", "staging": "evaluate_only",
                                  "preproduction": "enforce", "production": "enforce"},
            "lifecycle_bindings": [],
        },
        "tools": [{
            "id": tool_id or ("governance_probe_noop" if selected else "read"),
            "consequence": consequence, "policy_binding": "safe" if selected else "none",
            "enforcement_path": "governed-tool-gateway" if selected else "none",
            "intervention_points": ["pre_tool_call"] if selected else [],
            "safe_principles": ["accountability"], "requires": [],
        }],
    }


def inventory(document):
    bindings = [{
        "binding_id": "binding." + t["id"], "tool_id": t["id"],
        "enforcement_path": t["enforcement_path"], "intervention_points": t["intervention_points"],
        "mode": "evaluate_only", "safe_principles": t["safe_principles"],
        "status": "unbound" if t["enforcement_path"] == "none" else "unverified",
        "policy_digest": None, "probe_ids": [], "evidence_refs": ["EV-inventory"],
    } for t in document["tools"]]
    return {
        "schema": "threadlight-governance-manifest/v1",
        "agent": {"runtime": document["framework"], "version": None, "image_digest": None},
        "policy_bundle": None,
        "enforcement": {"adapter": "offline-inventory", "mode": "evaluate_only",
                        "agent_hooks_distribution": None, "agent_hooks_artifact_sha256": None,
                        "acs_distribution": None, "acs_artifact_sha256": None},
        "bindings": bindings, "live_probes": [],
        "coverage": coverage(bindings),
        "gaps": [{"binding_id": b["binding_id"], "status": b["status"],
                  "reason_code": "explicitly-unbound" if b["status"] == "unbound" else "runtime-proof-required",
                  "evidence_refs": ["EV-inventory"]} for b in bindings],
        "offline_evidence": [{"evidence_ref": "EV-inventory", "source": "specs/governance-contract.json",
                              "sha256": DIGEST, "reason_code": "contract-declared-only"}],
    }


def coverage(bindings):
    return {"tools_total": len(bindings),
            "tools_bound": sum(b["enforcement_path"] != "none" for b in bindings),
            **{"tools_" + s: sum(b["status"] == s for b in bindings)
               for s in ("enforced", "observed", "unbound", "unsupported", "unverified", "bypassable")}}


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def seed_inventory(root, document=None):
    document = document or contract()
    write(root, "specs/governance-contract.json", document)
    value = inventory(document)
    # The real producer hashes the exact source bytes.
    value["offline_evidence"][0]["sha256"] = "sha256:" + hashlib.sha256(
        (root / "specs/governance-contract.json").read_bytes()).hexdigest()
    write(root, "specs/governance-manifest.json", value)
    return value


def live_fixture(*, environment="preproduction", now=None, target=None):
    """Construct the current strict gateway protocol, never a cloud claim."""
    now = now or datetime.now(timezone.utc)
    started, finished = now - timedelta(seconds=3), now - timedelta(seconds=1)
    document = contract(selected=True)
    target = deepcopy(target) if target is not None else {
              "agent_id": "agent-1", "agent_version": "1", "image_digest": DIGEST,
              "environment": environment, "subscription": str(uuid4()), "resource_group": "rg-staging",
              "tenant": str(uuid4()), "subject": str(uuid4()), "client_id": str(uuid4())}
    deployment = {k: target[k] for k in ("agent_id", "agent_version", "image_digest",
                                       "environment", "subscription", "resource_group")}
    registration = {"subject": target["subject"], "action": "governance_probe_noop",
                    "deployment": deployment, "policy_digest": DIGEST}
    scope = {"registration": registration, "tenant": target["tenant"], "binding": "safe",
             "fixture_id": "noop-fixture", "producer": "gateway"}
    records, summaries = [], []
    for variant in ("allow", "deny"):
        run, receipt_id = str(uuid4()), uuid4().hex
        context = {"probe_run_id": run, "tenant": target["tenant"], "subject": target["subject"],
                   "action": "governance_probe_noop", "binding": "safe", "fixture_id": "noop-fixture",
                   "policy_digest": DIGEST, "deployment": deployment,
                   "session_id": "session", "call_id": "call", "interception_point": "pre_tool_call"}
        facts = {"tenant": target["tenant"], "subject": target["subject"], "client": target["client_id"],
                 "action": "governance_probe_noop", "scope": "governance-probe",
                 "policy": DIGEST, "deployment": deployment}
        action_hash = digest({"facts": facts, "arguments": {"probe_run_id": run, "variant": variant}})
        record = {"variant": variant, "run_id": run, "receipt": {
            "receipt_id": receipt_id, "correlation_id": run, "action_id": "governance_probe_noop",
            "action_hash": action_hash, "policy_digest": DIGEST, "decision": variant,
            "reason_code": "unit-protocol-fixture", "agent_version": "1", "image_digest": DIGEST,
            "recorded_at": started.isoformat(), "probe": context,
        }}
        for service in ("producer", "fixture"):
            phases = (["received", "intercepted", "dispatch", "completed"] if variant == "allow"
                      else ["received", "intercepted", "completed"]) if service == "producer" else (
                          ["received", "effect", "completed"] if variant == "allow" else [])
            before = {"kind": "threadlight-probe/v1", "producer": "gateway" if service == "producer" else "fixture",
                      "probe_run_id": run, "tenant": target["tenant"], "binding": "safe",
                      "fixture_id": "noop-fixture", "registration": {**registration, "variant": variant},
                      "registered_at": started.isoformat(), "expires_at": (started + timedelta(minutes=10)).isoformat(),
                      "context": None, "counts": dict.fromkeys(("received", "intercepted", "dispatch", "effect", "completed"), 0),
                      "events": [], "terminal": None, "effect_key": None, "action_hash": None}
            after = deepcopy(before)
            after["counts"].update(dict.fromkeys(phases, 1))
            after["events"] = [{"phase": p, "event_id": uuid4().hex, "recorded_at": finished.isoformat(),
                                "receipt_id": receipt_id, "decision": variant if p == "intercepted" else None}
                               for p in phases]
            if phases:
                after.update(context=context, action_hash=action_hash,
                             terminal="denied" if variant == "deny" else "completed")
                if service == "fixture":
                    after["effect_key"] = DIGEST
            record["before_" + service], record[service] = before, after
        records.append(record)
        summaries.append({"probe_id": run, "binding_id": "governance_probe_noop",
                          "environment": target["environment"], "agent_version": "1",
                          "decision": variant, "downstream_effect_delta": int(variant == "allow"),
                          "decision_receipt_ref": "EV-receipt-" + receipt_id,
                          "service_oracle_ref": "EV-fixture-" + run, "status": "pass"})
    binding = {"binding_id": "governance_probe_noop", "tool_id": "governance_probe_noop",
               "enforcement_path": "governed-tool-gateway", "intervention_points": ["pre_tool_call"],
               "mode": "enforce", "safe_principles": ["accountability"], "status": "enforced",
               "policy_digest": DIGEST, "probe_ids": [p["probe_id"] for p in summaries],
               "evidence_refs": [p[k] for p in summaries for k in ("decision_receipt_ref", "service_oracle_ref")]}
    projection = {"agent": {k: DIGEST for k in ("GOV_CONTROL_PLANE_URL", "GOVERNED_TOOL_GATEWAY_URL",
                                               "TL_GOV_IMAGE_DIGEST", "TL_GOV_SPOOL_DIR")},
                  "services": {"control_plane": {"GOV_CONFIG_JSON": DIGEST},
                               "producer": {"GATEWAY_CONFIG_JSON": DIGEST}, "fixture": {}}}
    policy = {"id": "safe", "version": "1", "digest": DIGEST, "signature_verified": True,
              "expires_at": (now + timedelta(minutes=9)).isoformat()}
    value = {
        "schema": "threadlight-governance-manifest/v1",
        "agent": {"runtime": document["framework"], "version": "1", "image_digest": DIGEST},
        "policy_bundle": policy, "enforcement": {"adapter": "governed-tool-gateway", "mode": "enforce"},
        "bindings": [binding], "coverage": coverage([binding]), "live_probes": summaries, "gaps": [],
        "collection_evidence": {
            "source": "authenticated-service-reads-and-azure-observation-not-attestation",
            "declared_selection": {"requested_version": "1"},
            "observed_target": {**target, "source": "azure-arm-and-foundry", "configuration_digests": projection["agent"]},
            "expected_target": target, "started_at": started.isoformat(), "finished_at": finished.isoformat(),
            "registration_scope": scope, "records": records,
            "configuration": {"declared": projection, "observed": projection,
                              "file_visibility": "image-and-mounted-file-interiors-not-observed-by-azure",
                              "declared_file_digests": {"host": DIGEST, "fixture": DIGEST}},
        },
    }
    current = {"contract": document, "expected_target": target, "policy_bundle": policy,
               "declared_selection": {"requested_version": "1"},
               "configuration": projection, "declared_file_digests": {"host": DIGEST, "fixture": DIGEST},
               "producer": "gateway"}
    return value, document, current, now
