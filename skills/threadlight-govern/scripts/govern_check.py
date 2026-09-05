#!/usr/bin/env python3
"""Offline binding inventory; neither source markers nor bundles prove enforcement."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from skills._shared.governance import (
    BINDING_STATUSES, validate_governance_contract, validate_governance_manifest,
)
from skills._shared.governance_selection import discover_contract, spec_contract, source_digest

VERSION = "1.0.0"
MANIFEST_SCHEMA = "threadlight-governance-manifest/v1"


def _empty(reason="contract-missing"):
    return {
        "schema": MANIFEST_SCHEMA,
        "agent": {"runtime": None, "version": None, "image_digest": None},
        "policy_bundle": None,
        "enforcement": {
            "adapter": "offline-inventory", "mode": "evaluate_only",
            "agent_hooks_distribution": None, "agent_hooks_artifact_sha256": None,
            "acs_distribution": None, "acs_artifact_sha256": None,
        },
        "coverage": {"tools_total": 0, "tools_bound": 0,
                     **{f"tools_{status}": 0 for status in sorted(BINDING_STATUSES)}},
        "bindings": [], "live_probes": [], "gaps": [],
        "offline_evidence": [{
            "evidence_ref": "EV-inventory", "source": None,
            "sha256": None, "reason_code": reason,
        }],
    }


def _contract(root):
    return discover_contract(root, required=False)


def _spec_contract(root):
    return spec_contract(root)


def evaluate(root: str, freshness_days: int = 90, *, bundle_path: str | None = None) -> dict:
    """Preserve declared framework and unbound tools; never infer runtime wiring."""
    from policy_bundle import checked_path, validate_native_manifest, verify_bundle
    root = checked_path(Path(root))
    man = _empty()
    document, path, data = _contract(root)
    if document is None:
        return validate_governance_manifest(man)
    contract = validate_governance_contract(document, deployment_target="demo-sandbox")
    man["agent"]["runtime"] = contract["framework"]
    evidence = man["offline_evidence"][0]
    evidence.update(source=path.relative_to(root).as_posix(),
                    sha256=source_digest(root, path.relative_to(root).as_posix(), data),
                    reason_code="contract-declared-only")
    policy_root = checked_path(root / (bundle_path or "policies"))
    if not policy_root.is_relative_to(root):
        raise ValueError("bundle path must remain inside target")
    if (policy_root / "bundle-metadata.json").exists():
        try:
            bundle = verify_bundle(policy_root)
            metadata = json.loads((bundle.root / "bundle-metadata.json").read_text())
            man["policy_bundle"] = {
                "id": metadata["policy_id"], "version": metadata["version"],
                "digest": bundle.bundle_digest, "signature_verified": False, "expires_at": None,
            }
            reason = "bundle-integrity-only"
            try:
                validate_native_manifest(bundle.root)
                reason = "native-manifest-valid-not-runtime-proof"
            except (ImportError, ValueError):
                reason = "native-engine-unverified"
        except (ValueError, OSError):
            reason = "bundle-invalid"
        man["offline_evidence"].append({
            "evidence_ref": "EV-bundle", "source": policy_root.relative_to(root).as_posix(),
            "sha256": man["policy_bundle"]["digest"] if man["policy_bundle"] else None,
            "reason_code": reason,
        })

    subjects = list(contract["tools"])
    for lifecycle in contract["governance"]["lifecycle_bindings"]:
        subjects.append({
            **lifecycle, "id": f"lifecycle.{lifecycle['lifecycle_point']}",
            "intervention_points": [lifecycle["lifecycle_point"]],
        })
    for tool in subjects:
        status = "unbound" if tool["enforcement_path"] == "none" else "unverified"
        binding_id = f"binding.{tool['id']}"
        refs = [e["evidence_ref"] for e in man["offline_evidence"]]
        man["bindings"].append({
            "binding_id": binding_id, "tool_id": tool["id"],
            "enforcement_path": tool["enforcement_path"],
            "intervention_points": tool["intervention_points"], "mode": "evaluate_only",
            "safe_principles": tool.get("safe_principles", []), "status": status,
            "policy_digest": man["policy_bundle"]["digest"] if man["policy_bundle"] else None,
            "probe_ids": [], "evidence_refs": refs,
        })
        man["gaps"].append({
            "binding_id": binding_id, "status": status,
            "reason_code": "explicitly-unbound" if status == "unbound" else "runtime-proof-required",
            "evidence_refs": refs,
        })
        man["coverage"]["tools_total"] += 1
        man["coverage"][f"tools_{status}"] += 1
        man["coverage"]["tools_bound"] += int(status != "unbound")
    return validate_governance_manifest(man)


def manifest(root: str, caps: dict, profile: str = "auto", freshness_days: int = 90) -> dict:
    """Legacy call shape retained, but legacy capability booleans are not proof."""
    return validate_governance_manifest(caps)


def render(man: dict) -> str:
    lines = [
        "# Agent governance — offline binding inventory", "",
        "**Offline evidence is not deployment enforcement.** No live probes were run.",
        "Unknown deployment metadata is null; unsigned bundle integrity is not authenticity.",
        "Coverage counts declared tool/lifecycle subjects, not invisible provider tools.", "",
        "| Binding / subject | Status | Declared path | Evidence |",
        "|---|---|---|---|",
    ]
    for binding in man["bindings"]:
        lines.append(f"| `{binding['tool_id']}` | {binding['status']} | "
                     f"{binding['enforcement_path']} | {', '.join(binding['evidence_refs'])} |")
    lines += ["", "## Offline evidence"]
    for evidence in man["offline_evidence"]:
        lines.append(f"- {evidence['evidence_ref']}: {evidence['reason_code']} "
                     f"({evidence['source'] or 'no source'})")
    lines += ["", "Legacy whole-agent verdict consumers must migrate; no legacy pass is emitted.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=".")
    parser.add_argument("--bundle", help="bundle directory relative to target; default policies")
    parser.add_argument("--profile", default="auto", choices=["auto", "v3_7", "v4_preview", "none"],
                        help="legacy compatibility only; never changes declared governance")
    parser.add_argument("--freshness-days", type=int, default=90,
                        help="legacy compatibility only; file mtime is not proof")
    parser.add_argument("--emit", action="store_true")
    parser.add_argument("--gate", action="store_true", help="fail closed: offline inventory is not live proof")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        man = evaluate(args.target, args.freshness_days, bundle_path=args.bundle)
    except Exception:
        man = validate_governance_manifest(_empty("validator-error"))
    if args.emit:
        from policy_bundle import checked_path
        root = checked_path(Path(args.target))
        for relative, text in [
            ("specs/governance-manifest.json", json.dumps(man, indent=2) + "\n"),
            ("docs/agt-governance-report.md", render(man)),
        ]:
            path = checked_path(root / relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    print(json.dumps(man, indent=2) if args.json else render(man))
    return 2 if args.gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
