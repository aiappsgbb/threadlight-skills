#!/usr/bin/env python3
"""Render and build the governed-write policy bundle (Task6) for one deployment.

Inputs are public example files plus an operator JSON (never committed) with the
OBSERVED deployment values. Two modes:

* ``--bootstrap``: policy only, no gateway registry. Used by ``generate.py generate``
  before the agent image exists (the gateway path does not embed it in the image).
* default (final): adds ``gateway-registry.json`` binding the real agent
  principal, version and image digest, the gateway URL and the business API.

The bundle is unsigned. Signing/publication happens only through the Task8
publisher identity (see ``publish_policy.py``); this script never signs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE.parent
CATALOG = EXAMPLE.parents[1]
ACTION = "returns_apply_decision"


def cases_rego(cases):
    facts = {case["case_id"]: {"amount": case["amount"], "eligible": case["eligible"],
                               "high_risk": case["high_risk"]} for case in cases}
    return ("package threadlight.governed_write_cases\n\n"
            "# Generated from governance/cases.json by scripts/build_policy.py. Do not edit.\n"
            f"cases := {json.dumps(facts, indent=2, sort_keys=True)}\n")


def registry(config):
    business = config["business_url"].rstrip("/")
    return {
        "schema": "threadlight-gateway-registry/v1",
        "tenant_id": config["tenant_id"],
        "gateway_url": config["gateway_url"],
        "deployment": {
            "agent_id": config["agent_id"], "agent_version": config["agent_version"],
            "image_digest": config["agent_image"].split("@")[1],
            "environment": config["environment"],
            "subscription": config["subscription"], "resource_group": config["resource_group"],
        },
        "actions": [{
            "name": ACTION, "policy_binding": "returns-write", "post_policy_binding": None,
            "workloads": [config["agent_principal"]], "scope": "returns",
            "approval_roles": ["Approver"], "approval_mode": "deferred",
            "approval_requirement": "policy",
            "approval_timeout_seconds": config.get("approval_timeout_seconds", 900),
            "endpoint": business + "/decisions", "outcome_endpoint": business + "/outcomes",
            "credential_scope": config["business_scope"],
            "input_schema": {
                "type": "object", "additionalProperties": False,
                "required": ["case_id", "expected_etag", "decision", "reason"],
                "properties": {
                    "case_id": {"type": "string", "minLength": 1, "maxLength": 64,
                                "description": "Case identifier read with returns_get_case."},
                    "expected_etag": {"type": "string", "minLength": 1, "maxLength": 128,
                                      "description": "Exact _etag returned by returns_get_case."},
                    "decision": {"type": "string", "enum": [
                        "approve_refund", "deny_refund", "escalate_to_supervisor", "request_more_info"]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 512},
                },
            },
            "output_schema": {
                "type": "object", "additionalProperties": False,
                "required": ["case_id", "decision", "audit_id"],
                "properties": {
                    "case_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "decision": {"type": "string", "enum": [
                        "approve_refund", "deny_refund", "escalate_to_supervisor", "request_more_info"]},
                    "audit_id": {"type": "string", "minLength": 1, "maxLength": 128},
                },
            },
        }],
    }


def build(destination, *, policy_id, version, config=None, cases=None):
    spec = importlib.util.spec_from_file_location(
        "policy_bundle", CATALOG / "skills/threadlight-govern/scripts/policy_bundle.py")
    bundle_api = sys.modules.setdefault("policy_bundle", importlib.util.module_from_spec(spec))
    if not hasattr(bundle_api, "build_bundle"):
        spec.loader.exec_module(bundle_api)
    destination = Path(destination)
    source = destination.with_name(destination.name + "-source")
    if source.exists() or destination.exists():
        raise FileExistsError("refusing_to_overwrite_policy_build")
    source.mkdir(parents=True)
    for name in ("manifest.yaml", "governed_write.rego"):
        shutil.copyfile(EXAMPLE / "governance/policy" / name, source / name)
    cases = cases or json.loads((EXAMPLE / "governance/cases.json").read_text())["cases"]
    (source / "cases.rego").write_text(cases_rego(cases))
    if config is not None:
        (source / "gateway-registry.json").write_text(json.dumps(registry(config), indent=2) + "\n")
    bundle = bundle_api.build_bundle(source=source, destination=destination,
                                     policy_id=policy_id, version=version)
    bundle_api.verify_bundle(bundle.root, expected_digest=bundle.bundle_digest)
    bundle_api.validate_native_manifest(bundle.root)
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--policy-id", default="returns-write")
    parser.add_argument("--version", required=True)
    parser.add_argument("--operator-config", type=Path, help="observed deployment values (final mode)")
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    if args.bootstrap == bool(args.operator_config):
        parser.error("choose exactly one of --bootstrap or --operator-config")
    config = None if args.bootstrap else json.loads(args.operator_config.read_text())
    bundle = build(args.destination, policy_id=args.policy_id, version=args.version, config=config)
    print(json.dumps({"bundle": str(bundle.root), "policy_digest": bundle.bundle_digest,
                      "registry": config is not None, "signed": False}))


if __name__ == "__main__":
    main()
