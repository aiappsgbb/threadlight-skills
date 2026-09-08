"""Read-only binding assurance, shared by readiness, evidence and Auto.

Saved collector records are scoped evidence, not cryptographic attestations.
No function in this module invokes a model, business tool, Azure or a probe.
"""
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import subprocess

from skills._shared.governance import (
    GovernanceContractError, validate_governance_contract, validate_governance_manifest,
    probe_requirement_dimension,
)
from skills._shared.governance_configuration import configuration_digest
from skills._shared.probe_evidence import ProbeEvidenceError, require, require_target, require_selected_target
from skills._shared.governance_selection import load_contract, read_json, parse_json, source_digest
from skills._shared.probe_evidence import policy_bindings

MANIFEST = "specs/governance-manifest.json"
CONTRACT = "specs/governance-contract.json"
COLLECTION = ".threadlight/governance-live.json"


def selected(document):
    contract = validate_governance_contract(document, deployment_target="demo-sandbox")
    return bool(contract["governance"]["lifecycle_bindings"] or
                any(t["enforcement_path"] != "none" for t in contract["tools"]))


def _collector():
    try:
        return importlib.import_module("governance_references.governance_probe")
    except ModuleNotFoundError as error:
        if error.name not in {"governance_references", "governance_references.governance_probe"}:
            raise
        return importlib.import_module("skills.threadlight-safe-check.references.governance_probe")


def current_context(root):
    """Re-read Task10's frozen package and Task11 inputs; never trust receipt selectors."""
    root = Path(root).resolve()
    document = load_contract(root)
    if not selected(document):
        # Risk acceptance is bound to declared deployment/change, not live proof.
        target = read_json(root / "specs/manifest.json")["deployment_manifest"]
        require_target(target, target)
        return {"contract": document, "expected_target": target}
    collector = _collector()
    config = collector.load_configuration(root, root / ".threadlight/governance-probe.json")
    require(document == config["contract"], "current-packaged-contract-mismatch")
    target = collector.expected_target(config)
    manifest_path = root / "specs/manifest.json"
    if manifest_path.exists():
        deployment = read_json(manifest_path).get("deployment_manifest", {})
        require(isinstance(deployment, dict), "current-deployment-manifest-invalid")
        for key, field in {
            "agent_id": "agent_id", "agent_name": "agent_id", "agent_version": "agent_version",
            "image_digest": "image_digest", "environment": "environment", "tenant_id": "tenant",
            "tenant": "tenant", "subscription_id": "subscription", "subscription": "subscription",
            "resource_group": "resource_group", "subject": "subject", "client_id": "client_id",
        }.items():
            if key in deployment:
                require_selected_target(target, {field: deployment[key]})
    policy = config["native_policy"] if config["producer"] == "native" else config["policy"]
    bindings = policy_bindings(config)
    from govern_control_plane.models import SignedBundle, canonical, parse
    envelope = parse(SignedBundle, canonical(policy["signed"])).envelope.model_dump(mode="json")
    return {
        "contract": config["contract"], "expected_target": target,
        "declared_selection": config["selection"],
        "producer": config["producer"], "configuration": collector.configuration_projection(config),
        "declared_file_digests": config["declared_file_digests"],
        "policy_bindings": bindings,
        "policy_bundle": {"id": envelope["policy_id"], "version": envelope["version"],
                          "digest": envelope["content_digest"],
                          "expires_at": envelope["expires_at"]},
        **({"bootstrap": config["bootstrap"]} if "bootstrap" in config else {}),
        **({"network_evidence": config["network_evidence"]} if "network_evidence" in config else {}),
    }


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "timestamp-needs-timezone")
    return parsed


def _result(status, reason, *, live=False, manifest=None, gaps=None):
    return {"status": status, "reason": reason, "live": live,
            "gaps": gaps if gaps is not None else manifest["gaps"] if manifest else [],
            "coverage": manifest.get("coverage") if isinstance(manifest, dict) else None,
            "policy_bundle": manifest.get("policy_bundle") if isinstance(manifest, dict) else None,
            "live_receipts": [p["decision_receipt_ref"] for p in manifest["live_probes"]] if live else []}


def _subjects(contract):
    return contract["tools"] + [
        {**b, "id": "lifecycle:" + b["lifecycle_point"], "consequence": "read",
         "intervention_points": [b["lifecycle_point"]]}
        for b in contract["governance"]["lifecycle_bindings"]
    ]


def inventory_matches(manifest, document):
    """Validate the producer inventory without asserting deployed enforcement."""
    validate_governance_manifest(manifest)
    contract = validate_governance_contract(document, deployment_target="demo-sandbox")
    require(manifest["agent"]["runtime"] == contract["framework"], "runtime-contract-mismatch")
    subjects = {t["id"]: t for t in _subjects(contract)}
    bindings = {b["tool_id"].replace("lifecycle.", "lifecycle:"): b for b in manifest["bindings"]}
    require(set(subjects) == set(bindings) and len(subjects) == len(manifest["bindings"]),
            "binding-inventory-mismatch")
    for tool_id, tool in subjects.items():
        for key in ("enforcement_path", "intervention_points", "safe_principles"):
            require(bindings[tool_id][key] == tool.get(key, []), "binding-contract-mismatch")
    return subjects, bindings


def _acceptance(tool, document, current, now):
    record = tool.get("acceptance_record")
    require(record is not None and _time(record["review_date"]) <= now < _time(record["expiry"]),
            "current-tool-acceptance-required")
    require(current is not None, "acceptance-current-deployment-required")
    expected = {"tool_id": tool["id"], "contract_sha256": configuration_digest(document),
                "target": current["expected_target"], "source_commit": current["source_commit"]}
    require(isinstance(current["source_commit"], str) and len(current["source_commit"]) == 40
            and isinstance(current.get("acceptances"), list)
            and current["acceptances"].count(expected) == 1,
            "acceptance-tool-deployment-change-scope-mismatch")


def _effective_gaps(manifest, accepted_unbound):
    """Only exact inventory bindings whose read/acceptance checks passed are exempt."""
    return [gap for gap in manifest["gaps"] if not (
        gap.get("binding_id") in accepted_unbound
        and gap["status"] == "unbound"
        and gap["reason_code"] in {"intentionally-unbound", "explicitly-unbound"}
    )]


def evaluate(manifest, document, *, current=None, now=None):
    """A composite pass is possible only after canonical validation and current scope checks."""
    now = now or datetime.now(timezone.utc)
    is_selected = False
    gaps = []
    try:
        is_selected = selected(document)
        contract = validate_governance_contract(document, deployment_target="production-bound", as_of=now)
        subjects, bindings = inventory_matches(manifest, document)
        gaps = manifest["gaps"]
        # Production validation adds current per-tool acceptance requirements.
        subjects = {t["id"]: t for t in _subjects(contract)}
        accepted_unbound = set()
        for tool_id, tool in subjects.items():
            binding = bindings[tool_id]
            if tool["enforcement_path"] == "none":
                require(binding["status"] == "unbound", "unbound-tool-status-mismatch")
                if tool["consequence"] != "read":
                    _acceptance(tool, document, current, now)
                accepted_unbound.add(binding["binding_id"])
            else:
                require(binding["status"] == "enforced" and binding["mode"] == "enforce",
                        "selected-binding-live-proof-required")
                # The installed collector certifies only its allow/deny noop pair.
                require(all(r == "signed-policy-bundle" or probe_requirement_dimension(r) == "durable_audit"
                            for r in tool["requires"]),
                        "selected-requirement-evidence-unavailable")
        gaps = _effective_gaps(manifest, accepted_unbound)
        require(not gaps, "binding-gaps-remain")
        if not is_selected:
            # Offline "explicitly unbound" entries are inventory, not enforcement gaps.
            return _result("pass", "Explicit unbound inventory; no enforcement asserted.",
                           manifest=manifest, gaps=gaps)
        require(manifest["enforcement"]["mode"] == "enforce"
                and manifest["coverage"]["tools_unverified"] == 0
                and manifest["coverage"]["tools_bypassable"] == 0,
                "binding-coverage-not-satisfied")
        require(current is not None and current["contract"] == document, "current-deployment-context-required")
        environment = current["expected_target"]["environment"]
        require(environment in {"staging", "preproduction"}
                and contract["governance"]["environment_modes"][environment] == "enforce",
                "noop-proof-requires-enforcing-staging-target")
        evidence = manifest.get("collection_evidence")
        require(isinstance(evidence, dict), "authenticated-collection-evidence-required")
        if "network_evidence" in current or "network_evidence" in evidence:
            from skills._shared.governance_configuration import validate_network_evidence
            require(evidence.get("network_evidence") == current.get("network_evidence"),
                    "current-network-disclosure-required")
            validate_network_evidence(evidence.get("network_evidence"))
        if "bootstrap" in current or "bootstrap" in evidence:
            require(evidence.get("bootstrap") is not None
                    and evidence.get("bootstrap") == current.get("bootstrap"),
                    "current-bootstrap-chain-required")
            binding = evidence["bootstrap"]["binding"]
            require(_time(binding["issued_at"]) <= now < _time(binding["expires_at"]),
                    "bootstrap-chain-expired")
        require_target(evidence["observed_target"], current["expected_target"],
                       evidence["registration_scope"]["registration"]["deployment"])
        require(evidence["expected_target"] == current["expected_target"], "current-target-mismatch")
        require(evidence["declared_selection"] == current["declared_selection"], "current-selection-mismatch")
        require({k: v for k, v in manifest["policy_bundle"].items() if k != "signature_verified"}
                == current["policy_bundle"], "current-policy-provenance-mismatch")
        require(evidence["verified_policies"] == current["policy_bindings"], "current-signed-policy-chain-mismatch")
        require(all(now < _time(p["expires_at"]) for p in evidence["verified_policies"].values()),
                "signed-policy-chain-expired")
        require(now < _time(manifest["policy_bundle"]["expires_at"]), "policy-expired")
        require(evidence["configuration"]["declared"] == current["configuration"]
                and evidence["configuration"]["declared_file_digests"] == current["declared_file_digests"],
                "current-configuration-mismatch")
        require(evidence["registration_scope"]["producer"] == current["producer"],
                "producer-path-mismatch")
        path = "local-agent-hooks" if current["producer"] == "native" else "governed-tool-gateway"
        require(manifest["enforcement"]["adapter"] == path, "adapter-path-mismatch")
        require(_time(evidence["started_at"]) <= _time(evidence["finished_at"]) <= now
                and now - _time(evidence["finished_at"]) < timedelta(minutes=10),
                "live-collection-not-current")
        for binding in manifest["bindings"]:
            if binding["enforcement_path"] == "none":
                continue
            tool = subjects[binding["tool_id"]]
            require(binding["enforcement_path"] == path
                    and binding["binding_id"] == binding["tool_id"] == "governance_probe_noop"
                    and binding["intervention_points"] == ["pre_tool_call"]
                    and tool["policy_binding"] == evidence["registration_scope"]["binding"],
                    "probe-cannot-certify-other-binding")
        for record in evidence["records"]:
            for service in ("producer", "fixture"):
                require(now < _time(record[service]["expires_at"]), "probe-registration-expired")
        result = _result("pass", "Current exact binding evidence; not whole-agent certification.",
                         live=True, manifest=manifest, gaps=gaps)
        if "network_evidence" in evidence:
            result["network_evidence"] = dict(evidence["network_evidence"])
            result["reason"] += " Public authenticated proof; network isolation not established."
        return result
    except (GovernanceContractError, ProbeEvidenceError, ValueError, TypeError, KeyError, ImportError):
        # Never echo raw JSON / payload-bearing validation errors.
        return _result("must-fix" if is_selected or isinstance(document, dict) else "not-verified",
                       "Binding evidence unverified: contract, scope, freshness, acceptance or required tooling failed.",
                       gaps=gaps)


def assess(root, *, now=None, required_target=None):
    root = Path(root)
    path = root / MANIFEST
    is_selected = False
    if not path.exists():
        try:
            if selected(load_contract(root)):
                return _result("must-fix", "Selected bindings lack specs/governance-manifest.json and live proof.")
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return _result("not-verified", "Missing binding manifest; legacy govern-manifest v2 is provenance only.")
    try:
        manifest = read_json(path)
        if manifest.get("schema") != "threadlight-governance-manifest/v1":
            return _result("not-verified", "Legacy or unknown governance schema; not enforcement.")
        document = load_contract(root)
        is_selected = selected(document)
        if is_selected and (root / COLLECTION).exists():
            latest = read_json(root / COLLECTION)
            require(latest.get("governance_gaps") == []
                    and latest.get("governance_manifest") == manifest,
                    "latest-collection-failed-or-not-published")
        for evidence in manifest.get("offline_evidence", []):
            if evidence["reason_code"] == "contract-declared-only":
                source = evidence["source"]
                require(source in {CONTRACT, "specs/SPEC.md", "specs/manifest.json"}, "inventory-source-invalid")
                actual = source_digest(root, source)
                require(actual == evidence["sha256"], "inventory-source-changed")
        current = None
        needs_context = is_selected or any(
            t.get("consequence", "unknown") != "read" for t in document["tools"] if isinstance(t, dict))
        if needs_context:
            try:
                current = current_context(root)
                require_selected_target(current["expected_target"], required_target or {})
                acceptance_path = root / "specs/governance-acceptances.json"
                if acceptance_path.exists():
                    current["acceptances"] = read_json(acceptance_path)["acceptances"]
                    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                          capture_output=True, text=True, timeout=5, check=True)
                    current["source_commit"] = head.stdout.strip()
            except (OSError, ValueError, TypeError, KeyError, ImportError, subprocess.SubprocessError):
                current = None
        return evaluate(manifest, document, current=current, now=now)
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        return _result("must-fix" if is_selected else "not-verified",
                       "Governance manifest or contract missing, malformed, or tooling unavailable.")


def publish_collection(root):
    """Consume Task11's actual CLI report; never manufacture probe records."""
    root = Path(root)
    try:
        report = read_json(root / COLLECTION)
        manifest = report["governance_manifest"]
        validate_governance_manifest(manifest)
        path = root / MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
                "manifest-path-outside-project")
        path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
        path.chmod(0o600)
        result = assess(root)
        return result["status"] == "pass" and result["live"]
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return False
