#!/usr/bin/env python3
"""Bind the existing native AgentOps evaluator to an observed release candidate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

import agentops_runtime
import release_runner


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--run-output", default="evals/runs/release-agentops.json")
    args = parser.parse_args(argv)
    try:
        root = args.repo.resolve()
        request_path = os.environ.get("THREADLIGHT_RELEASE_REQUEST")
        if not request_path:
            raise ValueError("the release runner's private request is required")
        request = release_runner.load(Path(request_path))
        if request.get("ci") != release_runner.ci_context(root):
            raise ValueError("native evaluation request does not match the current CI attempt")
        candidate = request.get("candidate")
        targets = candidate.get("evaluation_targets") if isinstance(candidate, dict) else None
        contract = agentops_runtime._contract()
        selected = contract.discover_opted_in_agents(root)
        if (not selected or not isinstance(targets, dict)
                or set(targets) != {item["agent_key"] for item in selected}
                or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                       for value in targets.values())):
            raise ValueError("observer must bind every opted-in native target to the candidate")
        for item in selected:
            binding = contract.read_json(
                root, str(Path(item["root"]) / ".threadlight/agentops-binding.json"))
            if binding.get("target_sha256") != targets[item["agent_key"]]:
                raise ValueError("native binding targets a different candidate")
        started = datetime.now(timezone.utc)
        status = agentops_runtime.main(["--repo", str(root), "--run-eval"])
        if status not in {0, 2}:
            raise ValueError("native runtime/approval/observation failed; no substitute evaluator is permitted")
        document = contract.load_manifest(root)
        receipts = {}
        finished = []
        for agent in document["agents"]:
            identity = agent["agent_key"]
            if agent["provenance"]["target_sha256"] != targets.get(identity):
                raise ValueError("native receipt targets a different observed deployment")
            stamp = agent["domains"]["evals"]["summary"]["finished_at"]
            release_runner._gate()._release_stamp(stamp, datetime.now(timezone.utc), started, 86400)
            finished.append(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
            receipts[identity] = agent["provenance"]["receipt_sha256"]
        if not finished or set(receipts) != set(targets):
            raise ValueError("missing current native receipts")
        output = release_runner.local_path(root, args.run_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "schema": "threadlight-native-release-run/v1",
            "captured_at": max(finished).isoformat(), "finished_at": max(finished).isoformat(),
            "release_binding": {"ci": request["ci"], "candidate": candidate},
            "provider": "agentops-accelerator",
            "provider_manifest_sha256": release_runner.file_digest(root / "specs/agentops-manifest.json"),
            "receipts": receipts, "native_exit_code": status,
        }, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        assessor = agentops_runtime._packaged("threadlight-evals/scripts/evals_check.py")
        assessed = assessor.main(["--target", str(root), "--emit"])
        return assessed or status
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        print(f"Release native evaluation blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
