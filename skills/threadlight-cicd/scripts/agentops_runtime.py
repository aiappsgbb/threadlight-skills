#!/usr/bin/env python3
"""Run the packaged native observer and independently validate local evidence.

Native execution and same-process receipts have one implementation, shared with
the AgentOps skill. No external observer, signing authority or login is created.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import os
from pathlib import Path
import sys


def _packaged(relative):
    path = Path(__file__).resolve().parents[2] / relative
    bundle_root = str(Path(__file__).resolve().parents[3])
    if bundle_root not in sys.path:
        sys.path.insert(0, bundle_root)
    if not path.is_file():
        raise ImportError(f"Missing packaged AgentOps component: {path.name}")
    name = "skills." + relative.removesuffix(".py").replace("/", ".")
    module = importlib.import_module(name)
    if Path(module.__file__).resolve() != path:
        raise ImportError("AgentOps component resolved outside the installed bundle")
    return module


def _contract():
    return _packaged("_shared/agentops.py")


def _observer():
    return _packaged("threadlight-agentops/scripts/native_observer.py")


def observe(repo, *, operation, agentops_bin=None):
    return _observer().observe(repo, operation=operation, agentops_bin=agentops_bin)


def operation_exit_code(document, operation, *, repo=None):
    """Preserve threshold gate 2 without conflating it with execution failure 1."""
    required = {"config", "pin", "binding", "integrity"}
    gate = 0
    if operation == "doctor":
        required.update({"doctor_freshness", "release_consistency"})
    for agent in document["agents"]:
        if any(agent["capabilities"][key]["status"] != "verified" for key in required):
            return 1
        if operation == "eval" and agent["domains"]["evals"]["status"] != "verified":
            return 1
        if any(finding["severity"] == "must-fix" and finding["owner"] == "agentops"
               for finding in agent["findings"]):
            if operation != "doctor":
                return 1
            gate = 2
        if operation == "eval":
            domain = agent["domains"]["evals"]
            summary = domain["summary"]
            total = summary.get("items_total")
            if type(total) is not int or total <= 0 or summary.get("items_passed_all") != total:
                return 1
            if domain["verdict"] == "fail":
                gate = 2
        if repo is not None:
            contract = _contract()
            root = contract.safe_path(repo, agent["root"], exists=False)
            record, _, _, digest, fresh = contract.verify_receipt(
                repo, root, now=datetime.now(timezone.utc), hours=24,
            )
            if not fresh or digest != agent["provenance"]["receipt_sha256"] or record["operation"] != operation:
                return 1
            observed = record.get("observation", {}).get("exit_code")
            if type(observed) is not int or observed not in {0, 2}:
                return 1
            if (operation == "eval" and "baseline" in record["artifacts"]
                    and agent["domains"]["evals"]["summary"]["comparison"]["status"] != "verified"):
                return 1
            gate = max(gate, observed)
    return gate


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--agentops-bin", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--run-eval", action="store_true")
    actions.add_argument("--refresh-doctor", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    repo = args.repo.resolve()
    previous_umask = os.umask(0o077)
    try:
        contract = _contract()
        if not contract.discover_opted_in_agents(repo):
            print("AgentOps not applicable: no opted-in roots.")
            return 0
        operation = "eval" if args.run_eval else "doctor"
        result = observe(repo, operation=operation, agentops_bin=args.agentops_bin)
        contract.validate_manifest(result, repo=repo)
        writer = _packaged("_shared/manifest.py")
        writer.atomic_write_json(
            contract.safe_path(repo, "specs/agentops-manifest.json", exists=False), result,
        )
        print(f"AgentOps local observation: {result['verdict']}; not remote attestation or certification.")
        return operation_exit_code(result, operation, repo=repo)
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        print(
            "AgentOps runtime refused: provide explicit owner approval for the pinned native operation, "
            "current binding and private capture/retention scope in the existing runner context. "
            "See docs/threadlight-cicd/agentops-runtime.md. No raw diagnostics are published.",
            file=sys.stderr,
        )
        return 1
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
