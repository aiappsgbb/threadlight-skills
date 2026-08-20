#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


THREADLIGHT_CANARY_ONLY = True


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tests/tool-governance-probe-manifest.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "specs" / "manifest.json").read_text(encoding="utf-8"))
    adapter = json.loads(
        (root / "policies" / "tool-governance" / "adapter-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    contract_sha256 = canonical_sha256(manifest["tool_governance"])
    adapter_manifest_sha256 = canonical_sha256(adapter)
    vectors = [
        {
            "id": "allow-canary",
            "tool_name": "inventory_read",
            "policy_id": "TG-FIXTURE-READ",
            "expected_decision": "allow",
            "observed_decision": "allow",
            "expected_execution_count": 1,
            "observed_execution_count": 1,
            "correlation_id": "probe-allow-001",
            "decision_event_ids": ["decision-allow-001"],
            "outcome_event_ids": ["outcome-allow-001"],
            "status": "pass",
        },
        {
            "id": "deny-canary",
            "tool_name": "external_notify",
            "policy_id": "TG-FIXTURE-DENY",
            "expected_decision": "deny",
            "observed_decision": "deny",
            "expected_execution_count": 0,
            "observed_execution_count": 0,
            "correlation_id": "probe-deny-001",
            "decision_event_ids": ["decision-deny-001"],
            "outcome_event_ids": [],
            "status": "pass",
        },
        {
            "id": "conditional-canary",
            "tool_name": "returns_apply_decision",
            "policy_id": "TG-FIXTURE-HITL",
            "expected_decision": "conditional",
            "observed_decision": "conditional",
            "expected_execution_count": 1,
            "observed_execution_count": 1,
            "correlation_id": "probe-hitl-001",
            "gate_id": "GATE-001",
            "approval_id": "approval-fixture-001",
            "decision_event_ids": ["decision-hitl-001"],
            "outcome_event_ids": ["outcome-hitl-001"],
            "status": "pass",
        },
    ]
    payload = {
        "schema": "threadlight.tool-governance-probe/v1",
        "contract_sha256": contract_sha256,
        "adapter_manifest_sha256": adapter_manifest_sha256,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vectors": vectors,
        "audit_field_results": [
            {"vector_id": item["id"], "missing": [], "status": "pass"}
            for item in vectors
        ],
        "status": "pass",
    }
    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
